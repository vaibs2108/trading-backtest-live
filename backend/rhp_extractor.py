"""
rhp_extractor.py — pulls real, structured content out of a company's own
Red Herring Prospectus (RHP) PDF for the IPO Review tab: the numbered Risk
Factor headings (bold-italic in SEBI's standard RHP typesetting), the
3-year Summary Financial Information (restated assets/liabilities, P&L,
cash flow), and the Objects of the Offer.

This is what makes IPO Review a genuine fundamentals read instead of a
subscription/GMP tracker: the restated financials extracted here feed
investment_scores.compute_piotroski_from_rhp / compute_altman_zprime the
same way Tickertape's 10-year data feeds the listed-company scores.

SEBI ICDR mandates the same section names/order in every mainboard RHP
("SECTION I: GENERAL", "SECTION II: RISK FACTORS", "SUMMARY OF FINANCIAL
INFORMATION", "OBJECTS OF THE OFFER" ...), so locating sections via the
RHP's own Table of Contents (rather than guessing fixed page numbers) is
reliable across different companies/issuers. Validated end-to-end against
a real, current RHP (Hero Motors Limited, Sep 2026, 582 pages) before
being wired into anything else.

Isolation: no imports from broker.py/cas_broker.py/config.py. Uses
PyMuPDF (fitz), already a dependency of this project.
"""
import re
import hashlib
import logging
import threading
import zipfile
import io
from pathlib import Path

import fitz
import requests

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent / "data" / "investment" / "rhp_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# PyMuPDF/MuPDF's C layer isn't guaranteed safe for concurrent use across
# threads even with separate Document objects (FastAPI runs sync endpoints
# in a threadpool, so two /ipo/*/analysis requests can genuinely overlap).
# A crash here is a native-level process crash, not a catchable Python
# exception — it took down the whole app, not just this request, the one
# time it was hit concurrently during testing. Serializing all fitz work
# process-wide trades a little parallelism for not crashing live trading
# alongside it.
_FITZ_LOCK = threading.Lock()

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

_TOC_LINE_RE = re.compile(r"^([A-Z][A-Z0-9 ,:\-–—'&/()]+?)\s*\.{3,}\s*(\d+)\s*$")
_TOC_SECTION_RE = re.compile(r"^SECTION\s*[-–—]*\s*([IVXLC]+)\s*[-–—:]*\s*(.*)$")

_TOC_TARGETS = [
    "SECTION I: GENERAL",
    "SECTION II: RISK FACTORS",
    "SECTION III: INTRODUCTION",
    "SUMMARY OF FINANCIAL INFORMATION",
    "SUMMARY OF CONTINGENT LIABILITIES",
    "OBJECTS OF THE OFFER",
    "BASIS FOR OFFER PRICE",
]

# Different issuers/law firms word these the same section differently —
# every variant observed on a real filing so far maps to the canonical key
# used everywhere below. "SECTION ..." headings are handled separately (see
# _TOC_SECTION_RE) since the number/dash punctuation between "SECTION II"
# and the name varies far more than the wording does (single dash, double
# dash, no space, colon...).
_TOC_ALIASES = {
    "SUMMARY OF RESTATED FINANCIAL INFORMATION": "SUMMARY OF FINANCIAL INFORMATION",
    "SUMMARY OF OUR FINANCIAL INFORMATION": "SUMMARY OF FINANCIAL INFORMATION",
    "SUMMARY FINANCIAL INFORMATION": "SUMMARY OF FINANCIAL INFORMATION",
    "SUMMARY OF FINANCIAL STATEMENT": "SUMMARY OF FINANCIAL INFORMATION",
    "OBJECTS OF THE ISSUE": "OBJECTS OF THE OFFER",
    "BASIS FOR ISSUE PRICE": "BASIS FOR OFFER PRICE",
}

# Exact aliases above are a fast path for wordings already seen; this is the
# general safety net for the next one, since new SME-RHP wordings for these
# same sections keep turning up on real filings (four variants of "SUMMARY
# ... FINANCIAL ..." alone, plus singular/plural CONTINGENT LIABILITY/-IES).
# (must-contain-all, must-not-contain-any) — checked only for targets the
# alias/exact pass didn't already find, so it can't override a real hit.
_TOC_FUZZY_RULES = {
    "SUMMARY OF FINANCIAL INFORMATION": (["SUMMARY", "FINANCIAL"], ["RELATED PARTY", "CONTINGENT", "TAX BENEFIT"]),
    "SUMMARY OF CONTINGENT LIABILITIES": (["SUMMARY", "CONTINGENT"], []),
    "OBJECTS OF THE OFFER": (["OBJECT", "OFFER"], ["RESTRICTIONS"]),
    "BASIS FOR OFFER PRICE": (["BASIS FOR", "PRICE"], []),
}

_RISK_NUM_RE = re.compile(r"^(\d{1,3})\.$")

def _labels(*alts):
    """Some issuers/law firms word these line items slightly differently
    (e.g. 'Restated profit for the period/year' vs 'Restated profit after
    tax') — each field lists every wording observed so far, tried in
    order."""
    return alts


_BS_LABELS = {
    "total_assets": _labels("Total assets"),
    "total_current_assets": _labels("Total current assets"),
    "total_equity": _labels("Total equity"),
    "equity_share_capital": _labels("Equity share capital"),
    "total_noncurrent_liabilities": _labels("Total non-current liabilities", "Total non current liabilities"),
    "total_current_liabilities": _labels("Total current liabilities"),
}
_PL_LABELS = {
    "revenue": _labels("Revenue from operations"),
    "total_income": _labels("Total income"),
    "total_expenses": _labels("Total expenses"),
    "finance_costs": _labels("Finance costs"),
    "profit_before_tax": _labels("Restated profit before", "Profit before tax", "Profit Before Taxes"),
    "tax_expense": _labels("Total tax expense", "Total tax (expense)"),
    "net_profit": _labels("Restated profit for the period/year", "Restated profit after tax", "Restated profit for the year",
                           "Profit (after tax) for the", "Profit after tax for the", "Profit for the year", "Profit for the period"),
}
_CF_LABELS = {
    "operating_cash_flow": _labels("Net cash generated from operating activities", "Net cash from operating activities",
                                    "Net cash generated from/(used in) operating activities", "Net cash flow from operating activities",
                                    # Some filings never caption a "Net cash from operating
                                    # activities" line at all — they stop at "...Before
                                    # Extraordinary Item" instead, with Extraordinary Items
                                    # always nil right after (confirmed on a real filing) —
                                    # making this row the genuine operating-CF total in
                                    # practice, not a distinct intermediate figure.
                                    "Cash Flow Before Extraordinary Item"),
}

_NUM_LINE_RE = re.compile(r"^(\(?-?[\d,]+\.?\d*\)?|-)$")
_PLAUSIBLE_MARKER_RE = re.compile(r"^\d{1,2}$")


def _is_plausible_marker(s: str) -> bool:
    """A bare 1-2 digit integer with no comma/decimal/parens — matches a
    'Note Nos.' column reference (e.g. the '17' immediately after 'Revenue
    from operations' before its real figures) or the next row's leading
    section-number ('2' before 'Expenditure') far more often than it
    matches a genuine financial figure, both confirmed on a real filing."""
    return bool(_PLAUSIBLE_MARKER_RE.match(s))


def _cache_path(url: str) -> Path:
    h = hashlib.sha1(url.encode()).hexdigest()[:24]
    return _CACHE_DIR / f"{h}.pdf"


def _extract_rhp_pdf_from_zip(content: bytes) -> bytes:
    """Some issuers file the RHP bundled in a ZIP together with the GID
    (General Information Document) rather than as a bare PDF (confirmed
    on a real filing — 'JindalRHPandGID_....zip' containing both
    '04_GID.pdf' and '01_..._RHP.pdf'). Picks the member whose name
    contains 'RHP', falling back to the largest PDF in the archive if
    none matches, since the RHP is always the bigger of the two."""
    z = zipfile.ZipFile(io.BytesIO(content))
    pdf_names = [n for n in z.namelist() if n.lower().endswith(".pdf")]
    if not pdf_names:
        raise ValueError("zip archive contains no PDF")
    rhp_names = [n for n in pdf_names if "rhp" in n.lower()]
    chosen = rhp_names[0] if rhp_names else max(pdf_names, key=lambda n: z.getinfo(n).file_size)
    return z.read(chosen)


def download_rhp(url: str) -> Path:
    """RHPs never change once filed, so this is a permanent cache keyed by
    URL — unlike bse_client/tickertape_client's TTL caches. Cache always
    holds a plain PDF even when the source is a ZIP bundle."""
    path = _cache_path(url)
    if path.exists():
        return path
    r = requests.get(url, headers=_HEADERS, timeout=90)
    r.raise_for_status()
    content = r.content
    if content[:4] == b"PK\x03\x04":
        content = _extract_rhp_pdf_from_zip(content)
    path.write_bytes(content)
    return path


def _parse_toc(doc) -> dict:
    """Scans the RHP's own Table of Contents (first ~10 pages) for the
    standard SEBI ICDR section headings and their printed page numbers."""
    found = {}
    for pidx in range(min(10, doc.page_count)):
        text = doc[pidx].get_text()
        for line in text.split("\n"):
            m = _TOC_LINE_RE.match(line.strip())
            if not m:
                continue
            title = re.sub(r"\s+", " ", m.group(1)).strip().upper()
            if title.startswith("SECTION"):
                # Punctuation between "SECTION II" and the name varies a lot
                # more than the wording does — single dash, double dash (e.g.
                # "SECTION – II – RISK FACTORS"), no space, colon, ... —
                # so this reconstructs "SECTION <roman>: <name>" from
                # whatever's actually there rather than guessing which
                # dash to swap for a colon.
                sm = _TOC_SECTION_RE.match(title)
                if sm:
                    title = f"SECTION {sm.group(1)}: {sm.group(2)}".strip()
            title = _TOC_ALIASES.get(title, title)
            page_num = int(m.group(2))
            matched = False
            for target in _TOC_TARGETS:
                if title == target or title.startswith(target):
                    found.setdefault(target, page_num)
                    matched = True
            if not matched:
                for target, (must, must_not) in _TOC_FUZZY_RULES.items():
                    if target in found:
                        continue
                    if all(w in title for w in must) and not any(w in title for w in must_not):
                        found.setdefault(target, page_num)
        if len(found) >= len(_TOC_TARGETS) - 1:
            break
    return found


_PAGE_NUM_LINE_RE = re.compile(r"^(?:PAGE\s+)?(\d+)(?:\s+OF\s+\d+)?$")


def _find_page_offset(doc, toc: dict) -> int:
    """Printed page numbers in the RHP body don't match PDF page indices
    (cover/TOC pages come first). Finds the offset by locating the PDF
    page where 'SECTION I: GENERAL' actually begins and comparing to its
    printed page number (from the TOC, normally page 1). Printed page
    numbers are formatted as a bare number on most filings but as
    'Page 65 of 367' on at least one real one — matching only the bare
    form silently fell back to the wrong default offset there, throwing
    off every page range computed from it, so both forms are handled."""
    target_num = toc.get("SECTION I: GENERAL", 1)
    for pidx in range(min(20, doc.page_count)):
        lines = [l.strip() for l in doc[pidx].get_text().split("\n") if l.strip()]
        for i in range(min(3, len(lines) - 1)):
            m = _PAGE_NUM_LINE_RE.match(lines[i].upper())
            if not m or int(m.group(1)) != target_num:
                continue
            candidate = re.sub(r"\s+", " ", lines[i + 1]).strip().upper()
            sm = _TOC_SECTION_RE.match(candidate)
            normalized = f"SECTION {sm.group(1)}: {sm.group(2)}".strip() if sm else candidate
            if normalized == "SECTION I: GENERAL" or normalized.startswith("SECTION I:"):
                return pidx - target_num
    return 4  # observed default across standard SEBI RHP layouts


def _is_heading_style_line(spans: list) -> bool:
    """Risk factor headings are typeset bold-italic in every SEBI RHP
    examined, distinct from bold-only table headers — detected via font
    name rather than guessed from text patterns alone. The leading number
    marker ('3.') is sometimes plain bold rather than bold-italic
    (observed varying by issuer/law-firm typesetting), so it's exempted
    from the italic check as long as it's still some bold variant."""
    for i, s in enumerate(spans):
        font = s["font"]
        if i == 0 and _RISK_NUM_RE.match(s["text"].strip()):
            if "Bold" not in font:
                return False
            continue
        if "BoldItal" not in font:
            return False
    return True


def _extract_risk_headings(doc, start_idx: int, end_idx: int, max_items: int = 20) -> list:
    items = []
    current = None
    for pidx in range(max(start_idx, 0), min(end_idx, doc.page_count)):
        d = doc[pidx].get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s["text"].strip()]
                if not spans:
                    continue
                heading_style = _is_heading_style_line(spans)
                line_text = "".join(s["text"] for s in spans).strip()
                first_txt = spans[0]["text"].strip()
                m = _RISK_NUM_RE.match(first_txt)
                if m and heading_style:
                    if current and current["heading"].strip():
                        items.append(current)
                        if len(items) >= max_items:
                            return items
                    current = {"number": int(m.group(1)), "heading": line_text[len(first_txt):].strip()}
                elif heading_style and current is not None:
                    current["heading"] += " " + line_text
                elif current is not None:
                    if current.get("heading", "").strip():
                        items.append(current)
                        if len(items) >= max_items:
                            return items
                    current = None
    if current and current.get("heading", "").strip() and len(items) < max_items:
        items.append(current)
    return items[:max_items]


def _collect_following_values(lines: list, label_idx: int) -> list:
    """From just after a matched label line, collects the numeric-looking
    lines that follow — the actual data values, stub-period columns and
    'Note Nos.' markers included (see _is_plausible_marker's docstring).

    One extra tolerance: a caption can wrap onto a second line before the
    numbers start (e.g. 'Profit/(Loss) for the period from continuing \\n
    operations (5-6)' — confirmed on a real filing, with 'operations
    (5-6)' as pure continuation text, not a value) — allowed ONLY before
    any real value has been collected yet, so it can't mask a genuinely
    missing row by skipping past unrelated text."""
    vals = []
    continuation_budget = 2
    j = label_idx + 1
    while j < len(lines) and len(vals) < 6 and j < label_idx + 10:
        cand = lines[j].strip()
        if _NUM_LINE_RE.match(cand):
            if _is_plausible_marker(cand):
                # A leading 'Note Nos.' reference (before any real figures)
                # is skipped outright, not kept as a would-be stub-period
                # column. A trailing one (after 3 real figures already
                # collected) is almost always the *next* row's section-
                # number bleeding in, not a genuine 4th/5th period column,
                # so it ends the scan instead of extending it.
                if len(vals) >= 3:
                    break
                j += 1
                continue
            vals.append(cand)
        elif cand:
            # Any other non-empty, non-numeric line — including one that
            # merely starts with "(" (e.g. a lettered row label like
            # "(b) Reserves & Surplus") — is tolerated only as a caption
            # continuation BEFORE any real value has been collected yet.
            # Once real values are in hand, such a line is always a new
            # row's own label and must end the scan, or it would silently
            # swallow that next row's values as if they belonged here.
            if not vals and continuation_budget > 0:
                continuation_budget -= 1
                j += 1
                continue
            break
        j += 1
    return vals


def _scan_labeled_numbers(lines: list, labels: dict) -> dict:
    """For each target field, tries each known label wording in turn,
    finds its line, and takes the following numeric-looking lines as
    [latest FY, prior FY, prior-prior FY].

    Some issuers add one or more stub/interim-period columns before the
    three full fiscal years — confirmed on real filings: one issuer adds
    a single stub ('As on 30th June, 2026 | Fiscal 2026 | Fiscal 2025 |
    Fiscal 2024'), another (NSE) adds two — a stub plus its own prior-year
    comparative stub ('June 30, 2026 | June 30, 2025 | March 31, 2026 |
    March 31, 2025 | March 31, 2024'). Either would get read as 'latest'
    and produce a nonsense YoY comparison (a 3-month stub vs a full prior
    year) if taken at face value. SEBI RHP convention always lists any
    stub column(s) first, so once at least 3 numbers are found, only the
    LAST 3 are kept regardless of how many stub columns preceded them."""
    out = {}
    for key, alt_labels in labels.items():
        for label in alt_labels:
            found = False
            for i, line in enumerate(lines):
                # "Profit/(Loss) for the period..." (a real caption) would
                # otherwise never match a plain "Profit for the..." label —
                # the "/(Loss)" alternate-wording insert is common enough
                # in Indian financial statements (also seen as "before
                # exceptional...") to normalize away rather than alias.
                stripped = re.sub(r"/?\(loss\)", "", line.strip().lower())
                # Removing "(loss)" leaves the space on both sides behind
                # (e.g. "profit (loss) for the" -> "profit  for the"),
                # which then breaks a plain substring match against a
                # single-spaced label like "Profit for the year" —
                # confirmed on a real filing ("IX . Profit (Loss) for the
                # year (VII-VIII)") where this silently dropped net profit.
                stripped = re.sub(r"\s+", " ", stripped)
                label_lower = label.lower()
                if label_lower not in stripped:
                    continue
                # "Total equity" matching the broader "Total equity and
                # liabilities" row is a real confirmed mismatch (both then
                # reported the same figure) — labels here are otherwise
                # deliberately partial (e.g. "Restated profit before" is
                # meant to match "...before tax" OR "...before exceptional
                # items and tax"), so this only rejects the specific
                # 'label AND <something>' continuation pattern rather than
                # any trailing text at all.
                remainder = stripped.split(label_lower, 1)[1].strip()
                if remainder.startswith("and "):
                    continue
                vals = _collect_following_values(lines, i)
                if len(vals) >= 3:
                    out[key] = vals[-3:]  # drop any leading stub/interim-period column(s)
                    found = True
                    break
            if found:
                break
    return out


def _to_float(v) -> float:
    if v is None:
        return None
    s = str(v).strip()
    if s == "-":
        # Indian financial statements use a bare "-" for a nil/zero
        # figure (confirmed on a real filing) — without this, such a
        # value wasn't even recognized as numeric upstream (see
        # _NUM_LINE_RE), silently dropping it and misaligning the
        # remaining columns against the wrong fiscal years.
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")
    try:
        n = float(s)
        return -n if neg else n
    except ValueError:
        return None


_BARE_TOTAL_RE = re.compile(r"^TOTAL(\s*\([A-Za-z0-9]+\))?$")


def _extract_bare_equity_total(lines: list) -> list:
    """Some SME-platform RHPs never caption a distinct 'Total equity' row
    at all — confirmed on real filings — they break shareholders' funds
    into 'Share capital' + 'Reserves and surplus' and either (a) add a
    bare 'Total (A)'/'TOTAL' subtotal right after, no more descriptive
    than that, or (b) don't subtotal it at all and go straight into
    non-current liabilities (also confirmed on a real filing). Standard
    label matching can't find (a) — there's no 'equity' in the text — and
    genuinely can't find (b) at all, so this looks positionally instead:
    first for a bare-TOTAL-style line between 'Shareholders' Funds' and
    where liabilities begin, and failing that, derives it by summing
    Share Capital + Reserves and Surplus directly."""
    anchor = None
    for i, l in enumerate(lines):
        low = l.strip().lower()
        if "shareholders" in low and "fund" in low:
            anchor = i
            break
        if "share capital" in low and anchor is None:
            anchor = i
    if anchor is None:
        return []
    share_capital_vals, reserves_vals = None, None
    for i in range(anchor + 1, min(anchor + 40, len(lines))):
        low = lines[i].strip().lower()
        if "non-current liabilit" in low or "current liabilit" in low or "non current liabilit" in low:
            break
        if _BARE_TOTAL_RE.match(lines[i].strip().upper()):
            vals = _collect_following_values(lines, i)
            if len(vals) >= 3:
                return vals[-3:]
        elif "share capital" in low and share_capital_vals is None:
            share_capital_vals = _collect_following_values(lines, i)
        elif "reserves" in low and "surplus" in low and reserves_vals is None:
            reserves_vals = _collect_following_values(lines, i)
    if share_capital_vals and reserves_vals and len(share_capital_vals) >= 3 and len(reserves_vals) >= 3:
        try:
            return [str(_to_float(a) + _to_float(b)) for a, b in zip(share_capital_vals[-3:], reserves_vals[-3:])]
        except TypeError:
            return []
    return []


def _extract_bare_assets_total(lines: list) -> list:
    """Mirror of _extract_bare_equity_total for 'Total assets': some
    SME-platform RHPs never caption 'Total assets' either — they only
    give a bare 'TOTAL' line closing the Assets part of the statement
    (and, redundantly, an identical one closing the Equity & Liabilities
    part above it — confirmed on a real filing, both equal by the
    balance-sheet identity). Anchors on the standalone 'ASSETS' heading
    so it can't accidentally grab the Equity & Liabilities side's total
    instead."""
    anchor = None
    for i, l in enumerate(lines):
        if l.strip().upper() == "ASSETS":
            anchor = i
            break
    if anchor is None:
        return []
    for i in range(anchor + 1, min(anchor + 100, len(lines))):
        if _BARE_TOTAL_RE.match(lines[i].strip().upper()):
            vals = _collect_following_values(lines, i)
            if len(vals) >= 3:
                return vals[-3:]
    return []


_TOP_LEVEL_ITEM_RE = re.compile(r"^\(([a-h])\)\s*\S")
# Nested sub-items appear either as uppercase letters ('(A)', '(B)') or
# lowercase roman numerals ('(i)', '(ii)', ...) — confirmed both forms on
# real filings. A single lowercase 'i' is also a valid roman numeral, so
# it's excluded from _TOP_LEVEL_ITEM_RE's a-h range and matched here
# instead, or a genuine top-level '(a)'-style item could get misread as
# this nested numbering (confirmed: caused '(ii)' — an actual sibling
# row — to go completely unmatched and silently undercount a total).
_NESTED_ITEM_RE = re.compile(r"^\(([A-Z]|i{1,3}|iv|vi{0,3}|ix|x)\)\s*\S")


def _sum_component_values(lines: list, start_idx: int, end_idx: int) -> list:
    """Sums the 3-year values of every lettered sub-item row between
    start_idx and end_idx (exclusive) — for balance-sheet subsections
    that never caption their own subtotal (confirmed on a real SME
    filing, e.g. non-current/current liabilities broken into '(a)
    Long-Term Borrowings', '(b) Deferred Tax Liabilities', ... with no
    'Total non-current liabilities' row anywhere). A top-level '(a)'/
    '(b)' row with no values of its own is a header for further-nested
    '(A)'/'(B)' rows (e.g. '(b) Trade Payables:' broken into '(A)
    ...micro and small enterprises' / '(B) ...other than micro and
    small') — summed via its children instead, so it's neither dropped
    nor double-counted against its own header."""
    totals = None
    i = start_idx
    while i < end_idx:
        stripped = lines[i].strip()
        if _TOP_LEVEL_ITEM_RE.match(stripped) or _NESTED_ITEM_RE.match(stripped):
            vals = _collect_following_values(lines, i)
            if len(vals) >= 3:
                nums = [_to_float(v) for v in vals[-3:]]
                if all(n is not None for n in nums):
                    totals = nums if totals is None else [t + n for t, n in zip(totals, nums)]
        i += 1
    return [str(n) for n in totals] if totals else []


def _extract_bare_liability_subtotals(lines: list) -> dict:
    """Positional fallback for total_noncurrent_liabilities/
    total_current_liabilities when neither is captioned with a
    recognized wording (confirmed alongside the _extract_bare_assets_total
    / _extract_bare_equity_total gaps, all on the same real filing).
    Two tiers, same order of preference as a human reading the
    statement would use: first look for the section's OWN bare
    'TOTAL'/'TOTAL (X)' line (e.g. Shakti captions these as generic
    'Total (B)'/'Total (C)', not by name) — only if that's genuinely
    absent (confirmed on a different real filing, Vama, which never
    subtotals its liability sections at all) fall back to summing the
    section's own lettered line items directly, which is far more
    exposed to a filing's specific quirks (split marker/label lines,
    ambiguous nesting) and so is trusted less."""
    out = {}
    nc_idx = cur_idx = assets_idx = None
    for i, l in enumerate(lines):
        low = l.strip().lower()
        if nc_idx is None and ("non-current liabilit" in low or "non current liabilit" in low):
            nc_idx = i
        elif cur_idx is None and nc_idx is not None and re.match(r"^current liabilit", low):
            cur_idx = i
        elif cur_idx is not None and assets_idx is None and low == "assets":
            assets_idx = i
            break

    def section_total(start, end):
        for i in range(start, end):
            if _BARE_TOTAL_RE.match(lines[i].strip().upper()):
                vals = _collect_following_values(lines, i)
                if len(vals) >= 3:
                    return vals[-3:]
        return _sum_component_values(lines, start, end)

    if nc_idx is not None and cur_idx is not None:
        vals = section_total(nc_idx + 1, cur_idx)
        if vals:
            out["total_noncurrent_liabilities"] = vals
    if cur_idx is not None:
        end = assets_idx if assets_idx is not None else min(cur_idx + 40, len(lines))
        vals = section_total(cur_idx + 1, end)
        if vals:
            out["total_current_liabilities"] = vals
    return out


def _extract_summary_financials(doc, start_idx: int, end_idx: int) -> dict:
    """Locates the three standard summary-statement subsections within
    the Summary of Financial Information section and pulls the fixed set
    of totals needed for Piotroski/Altman scoring — not a full restated
    financial statement, just the headline rows."""
    full_text = "\n".join(doc[i].get_text() for i in range(max(start_idx, 0), min(end_idx, doc.page_count)))
    lines = full_text.split("\n")

    def section_lines(heading_fragment):
        # "RESTATED STATEMENT OF PROFIT & LOSS AS RESTATED" (an "&" instead
        # of "AND" — confirmed on a real filing) would otherwise silently
        # drop the ENTIRE P&L section, not just one field, since this is
        # the section-heading search, not an individual line-item label.
        for i, l in enumerate(lines):
            normalized = re.sub(r"\s*&\s*", " AND ", l.upper())
            if heading_fragment.upper() in normalized:
                return lines[i:i + 400]
        return []

    bs_lines = section_lines("STATEMENT OF ASSETS AND LIABILITIES")
    bs = _scan_labeled_numbers(bs_lines, _BS_LABELS)
    pl = _scan_labeled_numbers(section_lines("STATEMENT OF PROFIT AND LOSS"), _PL_LABELS)
    cf = _scan_labeled_numbers(section_lines("STATEMENT OF CASH FLOW"), _CF_LABELS)

    if "total_equity" not in bs:
        equity_vals = _extract_bare_equity_total(bs_lines)
        if equity_vals:
            bs["total_equity"] = equity_vals

    if "total_assets" not in bs:
        assets_vals = _extract_bare_assets_total(bs_lines)
        if assets_vals:
            bs["total_assets"] = assets_vals

    if "total_noncurrent_liabilities" not in bs or "total_current_liabilities" not in bs:
        for k, v in _extract_bare_liability_subtotals(bs_lines).items():
            bs.setdefault(k, v)

    years = ["latest", "prior", "prior2"]

    def to_year_records(d):
        records = {y: {} for y in years}
        for key, vals in d.items():
            for y, v in zip(years, vals):
                records[y][key] = _to_float(v)
        return records

    return {
        "balance_sheet": to_year_records(bs),
        "profit_and_loss": to_year_records(pl),
        "cash_flow": to_year_records(cf),
        "unit": "INR million (as stated in RHP)",
    }


def _extract_objects_of_offer(doc, start_idx: int, end_idx: int, max_items: int = 6) -> list:
    full_text = "\n".join(doc[i].get_text() for i in range(max(start_idx, 0), min(end_idx, doc.page_count)))
    lines = [l.strip() for l in full_text.split("\n")]
    anchor = None
    for i, l in enumerate(lines):
        if "following objects" in l.lower() or "towards funding" in l.lower():
            anchor = i
            break
    if anchor is None:
        return []
    items = []
    i = anchor + 1
    # Some issuers put the item text on the same line as its number ("1.
    # Repayment/pre-payment..." — confirmed on a real filing), others put
    # the number alone on its own line with the text following on
    # subsequent lines — group(2) is empty for the latter, non-empty for
    # the former, so one pattern covers both.
    num_re = re.compile(r"^(\d{1,2})\.\s*(.*)$")
    current = None
    while i < len(lines) and len(items) < max_items:
        l = lines[i]
        if "collectively" in l.lower() and "object" in l.lower():
            break
        m = num_re.match(l)
        if m:
            if current:
                items.append(current.strip())
            current = m.group(2)
        elif current is not None:
            current += " " + l
        i += 1
    if current and current.strip():
        items.append(current.strip())
    return items[:max_items]


def extract(rhp_url: str) -> dict:
    """Full extraction pipeline: download (cached), locate sections via
    the RHP's own ToC, pull risk factor headings + summary financials +
    objects of the offer. Returns a dict that degrades field-by-field
    (never raises) since prospectus formatting has some real variance —
    a partial result is still useful, an exception would drop everything."""
    result = {
        "risk_factors": [],
        "summary_financials": None,
        "objects_of_offer": [],
        "pages": None,
        "errors": [],
    }
    try:
        path = download_rhp(rhp_url)
        with _FITZ_LOCK:
            _extract_locked(path, result)
    except Exception as e:
        logger.error(f"rhp_extractor: failed for {rhp_url}: {e}")
        result["errors"].append(str(e))
    return result


def _extract_locked(path: Path, result: dict) -> None:
    """The fitz-touching portion of extract(), always called under
    _FITZ_LOCK — see the lock's docstring for why."""
    try:
        doc = fitz.open(path)
        result["pages"] = doc.page_count
        toc = _parse_toc(doc)
        offset = _find_page_offset(doc, toc)

        if "SECTION II: RISK FACTORS" in toc and "SECTION III: INTRODUCTION" in toc:
            start = toc["SECTION II: RISK FACTORS"] + offset
            end = toc["SECTION III: INTRODUCTION"] + offset
            try:
                result["risk_factors"] = _extract_risk_headings(doc, start, end)
                if not result["risk_factors"]:
                    result["errors"].append("risk_factors: none detected (unrecognized heading typesetting)")
            except Exception as e:
                result["errors"].append(f"risk_factors: {e}")
        else:
            result["errors"].append("risk_factors: RISK FACTORS section not found in table of contents")

        if "SUMMARY OF FINANCIAL INFORMATION" in toc:
            start = toc["SUMMARY OF FINANCIAL INFORMATION"] + offset
            end = toc.get("SUMMARY OF CONTINGENT LIABILITIES", start + 6) + offset
            try:
                result["summary_financials"] = _extract_summary_financials(doc, start, end)
                if not any(result["summary_financials"].get(k) and any(v for v in result["summary_financials"][k].values())
                           for k in ("balance_sheet", "profit_and_loss", "cash_flow")):
                    result["errors"].append("summary_financials: no line items matched (unrecognized statement layout)")
            except Exception as e:
                result["errors"].append(f"summary_financials: {e}")
        else:
            result["errors"].append("summary_financials: SUMMARY OF FINANCIAL INFORMATION section not found in table of contents")

        if "OBJECTS OF THE OFFER" in toc:
            start = toc["OBJECTS OF THE OFFER"] + offset
            end = toc.get("BASIS FOR OFFER PRICE", start + 4) + offset
            try:
                result["objects_of_offer"] = _extract_objects_of_offer(doc, start, end)
            except Exception as e:
                result["errors"].append(f"objects_of_offer: {e}")
        else:
            result["errors"].append("objects_of_offer: OBJECTS OF THE OFFER section not found in table of contents")

        doc.close()
    except Exception as e:
        logger.error(f"rhp_extractor: _extract_locked failed: {e}")
        result["errors"].append(str(e))
