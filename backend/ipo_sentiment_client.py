"""
ipo_sentiment_client.py — Grey Market Premium (GMP) and category-wise
subscription (QIB/SHNI/BHNI/NII/RII) for live/upcoming IPOs, from
investorgain.com's public JSON API.

This is the one deliberate exception to "our own BSE/NSE client only":
BSE's own IPO endpoints (see bse_client.get_ipo_details) expose the raw
bid-demand curve but not a clean category-wise subscription breakdown,
and GMP has no official source at all — it's an informal secondary
market. investorgain.com (the same site used by most retail IPO trackers)
publishes both from one clean, unauthenticated JSON endpoint, found by
capturing real network calls on investorgain.com's live pages. Always
labelled "unofficial, community-sourced" wherever shown — informational
sentiment context only, never treated as authoritative.

Isolation: no imports from broker.py/cas_broker.py/config.py. Same
file-cache pattern as bse_client.py/tickertape_client.py, short TTL since
these figures move throughout the bidding window.
"""
import re
import json
import html
import time
import logging
import unicodedata
from pathlib import Path
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent / "data" / "investment"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_TTL = {"gmp_list": 1800, "subscription_list": 1800}  # 30 min — live bidding-window data

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.investorgain.com/",
}

_GMP_REPORT_ID = 331
_SUB_REPORT_ID = 333

_TAG_RE = re.compile(r"<[^>]+>")
_NUM_RE = re.compile(r"-?\d+\.?\d*")
_ANCHOR_TITLE_RE = re.compile(r'<a\s[^>]*title="([^"]+)"')


def _cache_path(kind: str) -> Path:
    return _CACHE_DIR / f"ipo_sentiment__{kind}.json"


def _cache_get(kind: str):
    path = _cache_path(kind)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - payload["ts"] < _TTL[kind]:
            return payload["data"]
    except Exception:
        pass
    return None


def _cache_set(kind: str, data):
    _cache_path(kind).write_text(json.dumps({"ts": time.time(), "data": data}), encoding="utf-8")


def _data_read(report_id: int) -> list:
    today = datetime.now()
    url = (f"https://webnodejs.investorgain.com/cloud/v2/report/data-read/"
           f"{report_id}/1/{today.month}/{today.year}/{today.year}-{str(today.year + 1)[-2:]}/0/all")
    r = requests.get(url, headers=_HEADERS, params={"search": "", "v": "20-18"}, timeout=20)
    r.raise_for_status()
    return r.json().get("reportTableData", [])


def _strip_html(s: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", s or "")).strip()


def _extract_name(raw_name_html: str) -> str:
    """The Name column is '<a title="Company Name">Company Name</a> <span
    badges...>' — the anchor's title attribute is the clean company name,
    without the status/category badge text that follows it."""
    m = _ANCHOR_TITLE_RE.search(raw_name_html or "")
    if m:
        return html.unescape(m.group(1)).strip()
    return _strip_html(raw_name_html)


def _normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name or "").lower()
    for suffix in (" limited", " ltd", " ltd.", " india", " (india)"):
        name = name.replace(suffix, "")
    return re.sub(r"[^a-z0-9]", "", name)


_ACRONYM_STOPWORDS = {"of", "the", "and", "india", "limited", "ltd"}


def _acronym(name: str) -> str:
    """Some well-known issuers are listed on investorgain by their bare
    acronym (confirmed: BSE's own full legal name 'National Stock
    Exchange Of India Limited' is listed there simply as 'NSE') rather
    than any recognizable substring of the full name, so normal
    substring/fuzzy matching can't find them — this derives the same
    acronym from the significant words of the full name as a fallback."""
    words = re.findall(r"[a-zA-Z]+", name or "")
    significant = [w for w in words if w.lower() not in _ACRONYM_STOPWORDS]
    if len(significant) < 2:
        return ""
    return "".join(w[0] for w in significant).upper()


def get_gmp_list() -> list:
    cached = _cache_get("gmp_list")
    if cached is not None:
        return cached
    rows = _data_read(_GMP_REPORT_ID)
    out = []
    for row in rows:
        name = _extract_name(row.get("Name", ""))
        gmp_text = _strip_html(row.get("GMP", ""))
        nums = _NUM_RE.findall(gmp_text)
        pe_raw = row.get("~P/E")
        try:
            pe_ratio = float(pe_raw) if pe_raw not in (None, "", "--") else None
        except (TypeError, ValueError):
            pe_ratio = None
        out.append({
            "name": name,
            "gmp_value": float(nums[0]) if nums else None,
            "gmp_percent": float(nums[1]) if len(nums) > 1 else None,
            "subscription_x": _strip_html(row.get("Sub", "")) or None,
            "price_band": _strip_html(row.get("Price (₹)", "")) or None,
            "ipo_size": _strip_html(row.get("IPO Size", "")) or None,
            "pe_ratio": pe_ratio,
            "status": row.get("~ipo_status1"),
            "open": _strip_html(row.get("Open", "")).split("GMP:")[0].strip() or None,
            "close": _strip_html(row.get("Close", "")).split("GMP:")[0].strip() or None,
            "listing": _strip_html(row.get("Listing", "")).split("GMP:")[0].strip() or None,
        })
    _cache_set("gmp_list", out)
    return out


def get_subscription_list() -> list:
    cached = _cache_get("subscription_list")
    if cached is not None:
        return cached
    rows = _data_read(_SUB_REPORT_ID)
    out = []
    for row in rows:
        name = _extract_name(row.get("Name", ""))

        def pct(key):
            raw = _strip_html(str(row.get(key, "")))
            nums = _NUM_RE.findall(raw)
            return float(nums[0]) if nums else None

        out.append({
            "name": name,
            "total_x": pct("Total"),
            "qib_x": pct("QIB"),
            "shni_x": pct("SHNI"),
            "bhni_x": pct("BHNI"),
            "nii_x": pct("NII"),
            "rii_x": pct("RII"),
            "closing_date": row.get("Closing Date"),
        })
    _cache_set("subscription_list", out)
    return out


def get_sentiment_for(company_name: str) -> dict:
    """Best-effort fuzzy match by company name (BSE and investorgain don't
    share an ID scheme) — returns None fields rather than a wrong match if
    nothing looks close enough."""
    target = _normalize_name(company_name)
    result = {"matched_name": None, "gmp": None, "subscription": None,
              "source": "investorgain.com (unofficial, community-sourced — informational only)"}
    if not target:
        return result

    try:
        gmp_rows = get_gmp_list()
    except Exception as e:
        logger.warning(f"ipo_sentiment_client: GMP fetch failed: {e}")
        gmp_rows = []
    try:
        sub_rows = get_subscription_list()
    except Exception as e:
        logger.warning(f"ipo_sentiment_client: subscription fetch failed: {e}")
        sub_rows = []

    acronym = _acronym(company_name)

    def best_match(rows):
        best, best_len = None, 0
        for row in rows:
            norm = _normalize_name(row["name"])
            if not norm:
                continue
            if norm == target or norm in target or target in norm:
                if len(norm) > best_len:
                    best, best_len = row, len(norm)
        if best:
            return best
        if acronym:
            for row in rows:
                if row["name"].strip().upper() == acronym:
                    return row
        return None

    gmp_match = best_match(gmp_rows)
    sub_match = best_match(sub_rows)

    if gmp_match:
        result["matched_name"] = gmp_match["name"]
        result["gmp"] = {k: v for k, v in gmp_match.items() if k != "name"}
    if sub_match:
        result["matched_name"] = result["matched_name"] or sub_match["name"]
        result["subscription"] = {k: v for k, v in sub_match.items() if k != "name"}

    return result
