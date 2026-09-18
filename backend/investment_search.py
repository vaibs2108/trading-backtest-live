"""
investment_search.py — company-name search over the BSE equity universe, for
Company Analysis's search box ("did you mean ...").

Design rule: it only ever SUGGESTS. It never decides which company the user
meant — every result carries enough detail (name, symbol, BSE code, ISIN,
market cap) for the user to confirm, and the analysis is only loaded for the
exact symbol they click. That is what makes a wrong-company mix-up (the
failure mode of "pick the closest match") impossible here.

Ranking, best first: exact symbol > exact name > name starts with the query >
every query word starts some word of the name > symbol starts with the query >
query appears inside the name > (only when NOTHING else matched) close
spelling, word by word. Ties broken by market cap, so "Tata Motors" lists
the bigger company first.

Renamed companies appear in BSE's universe only under their NEW name (e.g.
Zomato is now Eternal), so a small alias table maps well-known old names to
the current symbol. An alias is used only if its target symbol really exists
in the universe, and is always labelled ("Formerly Zomato") — never silent.

Pure functions, no I/O.
"""
import re
import difflib
from typing import Optional

_STOP = {"limited", "ltd", "pvt", "private", "the", "co", "company", "corp", "corporation"}

# old / common name (normalized) -> (current BSE symbol, note shown to the user)
_ALIASES_RAW = {
    "zomato": ("ETERNAL", "Formerly Zomato"),
    "housing development finance": ("HDFCBANK", "HDFC Ltd merged into HDFC Bank (2023)"),
    "cadila healthcare": ("ZYDUSLIFE", "Formerly Cadila Healthcare"),
    "motherson sumi": ("MOTHERSON", "Formerly Motherson Sumi Systems"),
    "mindtree": ("LTM", "Mindtree merged into LTIMindtree, now listed as LTM"),
    "ltimindtree": ("LTM", "Formerly LTIMindtree"),
    "l and t infotech": ("LTM", "L&T Infotech merged into LTIMindtree, now listed as LTM"),
    "adani transmission": ("ADANIENSOL", "Formerly Adani Transmission"),
}


def _norm(s: Optional[str]) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(t for t in s.split() if t not in _STOP)


def _compact(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# keys normalised exactly like queries are (so 'ltd'/'limited' etc. can't break a match)
_ALIASES = {_norm(k): v for k, v in _ALIASES_RAW.items()}


def _mcap(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def build_index(universe_rows: list) -> list:
    entries = []
    for row in universe_rows or []:
        symbol = str(row.get("scrip_id") or "").strip()
        if not symbol:
            continue
        display = (row.get("Scrip_Name") or row.get("Issuer_Name") or symbol).strip()
        legal = (row.get("Issuer_Name") or "").strip()
        n_name = _norm(f"{display}")
        n_legal = _norm(legal)
        entries.append({
            "symbol": symbol, "sym_compact": _compact(symbol), "name": display, "legal_name": legal,
            "bse_code": str(row.get("SCRIP_CD") or ""), "isin": row.get("ISIN_NUMBER"),
            "mktcap_cr": _mcap(row.get("Mktcap")), "group": row.get("GROUP"),
            "n_name": n_name, "n_legal": n_legal,
            # run-together forms ('tatamotors'), because people type tickers without spaces
            "c_names": tuple(x for x in {n_name.replace(" ", ""), n_legal.replace(" ", ""),
                                         n_name.replace(" and ", " ").replace(" ", ""), n_legal.replace(" and ", " ").replace(" ", "")} if x),
            "tokens": set(n_name.split()) | set(n_legal.split()),
        })
    return entries


def _coverage_flag(e: dict) -> Optional[str]:
    """A heads-up shown next to a suggestion BEFORE the user picks it: funds are
    not companies, and very small companies often have thin or no reporting
    history (so the analysis may be flagged low-reliability or unavailable)."""
    if str(e.get("isin") or "").upper().startswith("INF"):
        return "ETF / fund — not a company, so company analysis does not apply"
    if 0 < e["mktcap_cr"] < 250:
        return "Very small company (under ₹250 cr) — data is often limited, so the analysis may be flagged low-reliability"
    return None


def _public(e: dict, match: str, note: Optional[str] = None) -> dict:
    note = note or _coverage_flag(e)
    return {"symbol": e["symbol"], "name": e["name"], "legal_name": e["legal_name"] if e["legal_name"].lower() != e["name"].lower() else None,
            "bse_code": e["bse_code"], "isin": e["isin"], "mktcap_cr": round(e["mktcap_cr"]) if e["mktcap_cr"] else None,
            "group": e["group"], "match": match, "note": note}


def search(index: list, query: str, limit: int = 8) -> list:
    qc, qn = _compact(query), _norm(query)
    if len(qc) < 2:
        return []
    qtokens = qn.split()
    # words used for "every word starts a word of the name": skip 'and' (in almost every
    # name) and single letters (e.g. the L and T of "L&T" would match half the universe)
    match_tokens = [t for t in qtokens if t != "and" and len(t) >= 2]
    by_symbol = {e["symbol"]: e for e in index}

    scored = {}   # symbol -> (score, mcap, entry, how, note)

    def put(e, score, how, note=None):
        cur = scored.get(e["symbol"])
        if cur is None or score > cur[0]:
            scored[e["symbol"]] = (score, e["mktcap_cr"], e, how, note)

    # renamed companies first: an alias hit is pinned to the top and labelled
    alias = _ALIASES.get(qn)
    if alias and alias[0] in by_symbol:
        put(by_symbol[alias[0]], 110, "alias", alias[1])

    for e in index:
        names = (e["n_name"], e["n_legal"])
        if e["sym_compact"] == qc:
            put(e, 100, "symbol")
        elif qn and qn in names:
            put(e, 95, "name")
        elif qn and any(n.startswith(qn) for n in names if n):
            put(e, 85, "name")
        elif match_tokens and any(all(any(t.startswith(q) for t in n.split()) for q in match_tokens) for n in names if n):
            put(e, 70, "name")
        elif e["sym_compact"].startswith(qc):
            put(e, 65, "symbol")
        elif qn and any(qn in n for n in names if n):
            put(e, 60, "name")
        elif len(qc) >= 4 and any(c == qc for c in e["c_names"]):
            put(e, 92, "name")
        elif len(qc) >= 4 and any(c.startswith(qc) for c in e["c_names"]):
            put(e, 82, "name")
        elif len(qc) >= 5 and any(qc in c for c in e["c_names"]):
            put(e, 55, "name")

    fuzzy_tokens = [t for t in match_tokens if len(t) >= 4]
    if not scored and fuzzy_tokens and len(fuzzy_tokens) == len(match_tokens):
        # Close-spelling fallback, ONLY when nothing matched at all (a typo like
        # 'relaince'): each query word must be a near-spelling of some word of the name.
        for e in index:
            name_tokens = e["tokens"]
            total = 0.0
            for q in fuzzy_tokens:
                best = 0.0
                for t in name_tokens:
                    sm = difflib.SequenceMatcher(None, q, t)
                    if sm.real_quick_ratio() < 0.8 or sm.quick_ratio() < 0.8:
                        continue
                    best = max(best, sm.ratio())
                if best < 0.8:
                    total = 0.0
                    break
                total += best
            if total:
                put(e, 40 + (total / len(fuzzy_tokens)) * 20, "similar")

    ranked = sorted(scored.values(), key=lambda x: (-x[0], -x[1]))
    return [_public(e, how, note) for _, _, e, how, note in ranked[:limit]]
