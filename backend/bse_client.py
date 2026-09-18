"""
bse_client.py — standalone BSE data client for the Investment Analysis feature.

Deliberately independent of broker.py / cas_broker.py: no Dhan connection,
no shared rate-limiter, no shared config. BSE's own public API
(api.bseindia.com) needs neither cookies nor a session warm-up (verified
directly — plain header-only requests work), unlike NSE's Akamai-protected
API which blocks plain HTTP clients outright (verified and rejected as a
data source for this feature; see the 2026-09-15 investigation notes).

Endpoint map — every path below was found by inspecting real network calls
via performance.getEntriesByType('resource') on live bseindia.com pages
(not guessed), then verified against real data:
  - quote/company info/results/history/universe/announcements: clean JSON
  - shareholding pattern & full financials: a filing-index JSON that links
    to an Inline XBRL (ix:nonFraction) HTML document per quarter, which
    IS parsed here (see _parse_ix_nonfraction)
  - IPO listing/details/subscription/RHP link: clean JSON, RHP is a direct
    PDF URL from BSE itself (Prospectus_GID field)

Known gap (not yet solved): the "Integrated Finance" XBRL filing gives rich
P&L, EPS, and a few pre-computed ratios (notably Debt/Equity) but does NOT
carry classic balance-sheet totals (Total Assets, Total Equity, Working
Capital) as tagged values — those aren't published on this data path at
all, only inside the Annual Report PDF. Full Altman Z-Score and the
asset-based parts of Piotroski F-Score can't be completed from this client
alone yet; everything that doesn't need them (P&L-based ratios, EPS growth,
D/E, quality-from-P&L checks, shareholding trend) works today.
"""
import re
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

BASE = "https://api.bseindia.com/BseIndiaAPI/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.3",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Origin": "https://www.bseindia.com/",
    "Referer": "https://www.bseindia.com/",
    "Connection": "keep-alive",
}

CACHE_DIR = Path(__file__).parent / "data" / "investment"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Financials/shareholding/universe don't change intraday — cache hard.
# Quote/price is cheap and cheap to re-fetch, so it gets a much shorter TTL.
CACHE_TTL = {
    "quote": 60,                    # seconds — near-live
    # Shortened from 24h so a fresh quarterly result (and the ratios/peers that
    # move with it) reaches the analysis within hours, not the next day.
    "company_info": 6 * 3600,
    "financial_results": 6 * 3600,
    "historical_price": 6 * 3600,
    "announcements": 2 * 3600,
    # Was 7 days: a newly listed company was invisible to search (and to a typed
    # symbol) for up to a week. 6h, plus an on-miss forced refresh in the API.
    "universe": 6 * 3600,
    "integrated_financials": 24 * 3600,
    "shareholding": 24 * 3600,
    "ipo_list": 6 * 3600,
    "ipo_live_list": 1800,           # 30 min — bidding status changes day to day, refresh often
    "ipo_details": 3600,
    "results_calendar": 24 * 3600,
    "peer_comparison": 6 * 3600,
    "symbol_index": 7 * 24 * 3600,
}


def _cache_path(kind: str, key: str) -> Path:
    safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", str(key))
    return CACHE_DIR / f"{kind}__{safe_key}.json"


def _cache_get(kind: str, key: str):
    path = _cache_path(kind, key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - payload["ts"] > CACHE_TTL.get(kind, 3600):
            return None
        return payload["data"]
    except Exception:
        return None


def _cache_set(kind: str, key: str, data):
    path = _cache_path(kind, key)
    try:
        path.write_text(json.dumps({"ts": time.time(), "data": data}, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning(f"bse_client: cache write failed for {kind}/{key}: {e}")


_session = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session


def _get(path: str, params: dict, timeout: int = 15):
    s = _get_session()
    r = s.get(f"{BASE}/{path}", params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    # BSE double-encodes several endpoints (JSON string containing JSON).
    if isinstance(data, str):
        stripped = data.strip()
        if stripped:
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                pass
    return data


def _cached(kind: str, key: str, fetch_fn, force: bool = False):
    if not force:
        cached = _cache_get(kind, key)
        if cached is not None:
            return cached
    data = fetch_fn()
    _cache_set(kind, key, data)
    return data


def cache_fetched_at(kind: str, key: str):
    """Unix time this cache entry was fetched, or None."""
    try:
        return json.loads(_cache_path(kind, key).read_text(encoding="utf-8"))["ts"]
    except Exception:
        return None


# ── Quote / company info / results ──────────────────────────────────────

def get_quote(scripcode: str) -> dict:
    """Current OHLC + LTP for a BSE scrip code."""
    def fetch():
        d = _get("getScripHeaderData/w", {"scripcode": scripcode})
        h = d.get("Header", d)
        return {
            "prev_close": float(h.get("PrevClose", 0) or 0),
            "open": float(h.get("Open", 0) or 0),
            "high": float(h.get("High", 0) or 0),
            "low": float(h.get("Low", 0) or 0),
            "ltp": float(h.get("LTP", 0) or 0),
        }
    return _cached("quote", scripcode, fetch)


def get_company_info(scripcode: str) -> dict:
    """EPS, PE, industry, ISIN, market cap band etc."""
    def fetch():
        return _get("ComHeadernew/w", {"quotetype": "EQ", "scripcode": scripcode, "seriesid": ""})
    return _cached("company_info", scripcode, fetch)


def get_financial_results(scripcode: str, force_refresh: bool = False) -> dict:
    """Quarterly + latest-annual P&L snapshot (Revenue, Net Profit, EPS, margins)."""
    def fetch():
        raw = _get("TabResults_PAR/w", {"scripcode": scripcode, "tabtype": "RESULTS"})
        currency_unit = raw.get("col1", "").strip("() ")
        periods = [raw.get(f"col{i}", "") for i in (2, 3, 4) if raw.get(f"col{i}")]

        def rows(key):
            out = []
            for item in raw.get(key, []) or []:
                out.append({"title": item.get("title", ""), "values": [item.get(f"v{i}", "") for i in (1, 2, 3)]})
            return out

        return {
            "currency_unit": currency_unit,
            "periods": periods,  # [latest_qtr, prior_qtr, latest_fy]
            "results_cr": rows("resultinCr"),
            "results_million": rows("resultinM"),
        }
    return _cached("financial_results", scripcode, fetch, force=force_refresh)


def get_historical_price(scripcode: str, months: str = "12M") -> dict:
    """OHLCV-ish price/volume history. months: one of the flags BSE's own
    chart uses (e.g. '1M','3M','6M','12M')."""
    def fetch():
        raw = _get("StockReachGraph/w", {"scripcode": scripcode, "flag": months, "fromdate": "", "todate": "", "seriesid": ""})
        data = raw.get("Data")
        rows = []
        if isinstance(data, str) and data.strip():
            try:
                parsed = json.loads(data)
                for row in parsed:
                    rows.append(row)
            except json.JSONDecodeError:
                pass
        elif isinstance(data, list):
            rows = data
        return {
            "scrip_name": raw.get("Scripname"),
            "prev_close": raw.get("PrevClose"),
            "rows": rows,  # each row typically has date/value/volume fields
        }
    return _cached("historical_price", f"{scripcode}_{months}", fetch)


def get_announcements(scripcode: str, from_date: datetime = None, to_date: datetime = None, category: str = "-1") -> list:
    def fetch():
        _from = from_date or (datetime.now() - timedelta(days=90))
        _to = to_date or datetime.now()
        raw = _get("AnnSubCategoryGetData/w", {
            "pageno": 1, "strCat": category, "subcategory": "-1",
            "strPrevDate": _from.strftime("%Y%m%d"), "strToDate": _to.strftime("%Y%m%d"),
            "strSearch": "P", "strscrip": scripcode, "strType": "C",
        })
        return raw.get("Table", [])
    key = f"{scripcode}_{(to_date or datetime.now()).strftime('%Y%m%d')}"
    return _cached("announcements", key, fetch)


def get_results_calendar() -> list:
    def fetch():
        raw = _get("Corpforthresults/w", {})
        return raw if isinstance(raw, list) else []
    return _cached("results_calendar", "all", fetch)


# ── Universe ─────────────────────────────────────────────────────────────

def get_universe(group: str = "", force_refresh: bool = False) -> list:
    """Full active-equity universe. group='' returns everything (~5,100
    securities); pass a specific BSE group ('A','B','T', etc.) to narrow."""
    def fetch():
        raw = _get("ListofScripData/w", {"scripcode": "", "Group": group, "industry": "", "segment": "Equity", "status": "Active"})
        return raw if isinstance(raw, list) else []
    return _cached("universe", group or "ALL", fetch, force=force_refresh)


# ── XBRL parsing (shared by integrated-financials and shareholding) ──────

_IX_NONFRACTION_RE = re.compile(
    r"<ix:nonFraction\s+name='([^']+)'([^>]*?)>\s*([^<]*?)\s*</ix:nonFraction>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(r"(\w[\w-]*)='([^']*)'")


def _parse_ix_nonfraction(html: str) -> list:
    """Extract every ix:nonFraction tag into {name, context, value, attrs}.
    This is the standard Inline XBRL tagging BSE's own XBRL-rendered pages
    use for both financial-results and shareholding-pattern documents —
    same parser, different tag namespaces (in-capmkt:* vs in-bse-shp:*)."""
    out = []
    for m in _IX_NONFRACTION_RE.finditer(html):
        name, attrs_str, raw_value = m.group(1), m.group(2), m.group(3)
        attrs = dict(_ATTR_RE.findall(attrs_str))
        value_str = raw_value.replace(",", "").strip()
        sign = -1 if attrs.get("sign") == "-" else 1
        try:
            value = sign * float(value_str) if value_str else None
        except ValueError:
            value = None
        out.append({
            "name": name,
            "context": attrs.get("contextRef", ""),
            "value": value,
            "raw": raw_value.strip(),
        })
    return out


def _fetch_xbrl_html(url: str) -> str:
    s = _get_session()
    full_url = url if url.startswith("http") else f"https://www.bseindia.com{url}"
    r = s.get(full_url, timeout=20)
    r.raise_for_status()
    return r.text


def get_integrated_financials(scripcode: str) -> dict:
    """Filing index + parsed latest quarter's Integrated Finance XBRL.
    Gives full P&L line items, EPS, and pre-computed Debt/Equity ratio —
    NOT a traditional balance sheet (see module docstring)."""
    def fetch():
        idx = _get("Integratedfinancedata/w", {"scripcode": scripcode})
        filings = idx.get("Table", []) if isinstance(idx, dict) else []
        if not filings:
            return {"filings": [], "latest": None}
        latest = filings[0]
        xbrl_url = latest.get("xbrlurl") or latest.get("XbrlFile")
        parsed = {}
        if xbrl_url:
            try:
                html = _fetch_xbrl_html(xbrl_url)
                tags = _parse_ix_nonfraction(html)
                for t in tags:
                    short_name = t["name"].split(":")[-1]
                    parsed.setdefault(short_name, []).append({"context": t["context"], "value": t["value"]})
            except Exception as e:
                logger.warning(f"bse_client: XBRL fetch/parse failed for {scripcode}: {e}")
        return {
            "filings": [{"quarter": f.get("Quarter_Name"), "filed": f.get("filing_date_time")} for f in filings[:8]],
            "latest_quarter": latest.get("Quarter_Name"),
            "fields": parsed,  # e.g. fields["RevenueFromOperations"][0]["value"]
        }
    return _cached("integrated_financials", scripcode, fetch)


def get_shareholding_pattern(scripcode: str) -> dict:
    """Filing index + parsed latest quarter's shareholding-pattern XBRL.
    Returns % holding by category (promoter, FII, DII, public, etc.)."""
    def fetch():
        idx = _get("SHPQNewFormat/w", {"scripcode": scripcode})
        filings = idx.get("Table", []) if isinstance(idx, dict) else []
        if not filings:
            return {"filings": [], "latest": None}
        latest = filings[0]
        xbrl_url = latest.get("xbrlurl") or latest.get("XbrlFile")
        categories = {}
        if xbrl_url:
            try:
                html = _fetch_xbrl_html(xbrl_url)
                tags = _parse_ix_nonfraction(html)
                for t in tags:
                    short_name = t["name"].split(":")[-1]
                    if short_name == "ShareholdingAsAPercentageOfTotalNumberOfShares" and t["value"] is not None:
                        categories[t["context"].replace("_ContextI", "")] = t["value"]
            except Exception as e:
                logger.warning(f"bse_client: shareholding XBRL fetch/parse failed for {scripcode}: {e}")
        return {
            "filings": [{"quarter": f.get("qtr"), "filed": f.get("filing_date_time")} for f in filings[:8]],
            "latest_quarter": latest.get("qtr"),
            "holding_pct_by_category": categories,
        }
    return _cached("shareholding", scripcode, fetch)


# ── IPO ────────────────────────────────────────────────────────────────

def get_ipo_list(recent: bool = True) -> list:
    """flag=2 (recent/historical) is the working value — flag=0 (naively
    'live') returned empty against real data during verification; BSE's
    own 'Live' tab only shows same-day issues, 'Historical' (flag=2) is
    what reliably surfaces anything recent. Filter client-side on dates
    if you need strictly-open issues. NOTE: this endpoint's 'recent'
    window does not reliably include every issue currently open for
    bidding (confirmed missing real live issues during verification) —
    use get_live_ipo_list() for "what's open right now"."""
    def fetch():
        raw = _get("HomePage_Issues_BBS_Landing_ng/w", {
            "flag": "2" if recent else "0", "scrip_Name": "", "end_dt": "", "IR_FLAG": "", "Start_DT": "",
        })
        return raw.get("Table", []) if isinstance(raw, dict) else []
    return _cached("ipo_list", "recent" if recent else "live", fetch)


def get_live_ipo_list() -> list:
    """The list BSE's own 'IPOs Listing' page actually renders (found by
    capturing its real network call) — a different, more reliable
    endpoint than get_ipo_list()'s. Each row carries a Status of 'L'
    (currently open for bidding) or 'F' (forthcoming, not yet open).
    Confirmed this includes issues get_ipo_list()'s 'recent' window
    misses entirely (e.g. Hero Motors, Jindal Supreme India — both
    genuinely open the day this was checked but absent from the other
    endpoint's result)."""
    def fetch():
        raw = _get("GetPublicIssue_par_updated/w", {
            "flag": "1", "scrip_Name": "", "ir_flag": "", "status": "", "exchange": "",
        })
        return raw.get("Table", []) if isinstance(raw, dict) else []
    return _cached("ipo_live_list", "all", fetch)


def get_peer_comparison(scripcode: str) -> list:
    """BSE's own peer-group table: the target company plus its real
    sector peers (BSE's own classification, not something we compute),
    each with LTP, Revenue, PAT, OPM/NPM/RONW, EPS, P/E — ready for direct
    comparison, no sector-matching logic needed on our side."""
    def fetch():
        raw = _get("EQPeerGp/w", {"scripcomare": "", "scripcode": scripcode})
        return raw.get("Table", []) if isinstance(raw, dict) else []
    return _cached("peer_comparison", scripcode, fetch)


def get_ipo_details(ipo_no: str) -> dict:
    """Full issue mechanics, lead managers, registrar, RHP PDF link
    (Prospectus_GID), and raw bid-quantity-at-price data."""
    def fetch():
        raw = _get("GetMkt_ISSUE_BBS_IPO/w", {"IPO_NO": ipo_no})
        mechanics = (raw.get("IPONO_0") or [{}])[0]
        return {
            "scrip_name": mechanics.get("ScripName"),
            "symbol": mechanics.get("Symbol"),
            "issue_period": mechanics.get("Issue_Period"),
            "price_band": mechanics.get("Price_Band"),
            "issue_size_shares": mechanics.get("Issue_Size_No_of_shares"),
            "market_lot": mechanics.get("Market_Lot"),
            "registrar": mechanics.get("Registrar"),
            "lead_managers": mechanics.get("Book_Running_Lead_Manager"),
            "rhp_url": mechanics.get("Prospectus_GID"),
            "bid_demand_curve": raw.get("IPONO_2", []),  # [{Price, Quantity, process_dttm}, ...]
            "notices": raw.get("IPONO_4", []),
        }
    return _cached("ipo_details", ipo_no, fetch)
