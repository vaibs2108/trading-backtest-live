"""
tickertape_client.py — standalone Tickertape data client for the Investment
Analysis feature. Fills the one confirmed gap in bse_client.py: BSE's own
XBRL filings don't carry classic balance-sheet totals (Total Assets/Equity/
Working Capital), only P&L + a few ratios. Tickertape's embedded page data
does — 10 full years of balance sheet, income statement and cash flow in
one fetch, no login required (verified directly, not assumed).

Deliberately independent of bse_client.py, broker.py, cas_broker.py: no
shared session, no shared cache namespace collision (separate key prefix
in the same cache directory), no shared config.

Used narrowly and knowingly: Tickertape is a third-party compiled
aggregator, not a primary regulatory filing — the same category of source
as Screener.in, which was explicitly ruled out for the *primary* pipeline.
This client exists ONLY to cover the one gap BSE's own filings don't have;
everything BSE already covers (quotes, P&L, shareholding, IPO data, RHP
links) stays sourced from bse_client.py, not duplicated here.

Endpoint map — found via real network capture (performance.getEntriesByType
+ a page's own __NEXT_DATA__ SSR payload) on live tickertape.in pages, then
verified against real data (RELIANCE):
  - search: clean JSON, gives the "sid" (Tickertape's own internal id,
    e.g. "RELI") and "slug" needed by everything else
  - info / holdings: clean JSON keyed by sid
  - financials (balance sheet / income / cash flow): NOT a JSON API —
    embedded in the stock page's <script id="__NEXT_DATA__"> as
    Next.js SSR state; parsed out of the HTML here
"""
import re
import json
import time
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.tickertape.in/search"
INFO_URL_TMPL = "https://api.tickertape.in/stocks/info/{sid}"
HOLDINGS_URL_TMPL = "https://api.tickertape.in/stocks/holdings/{sid}"
STOCK_PAGE_TMPL = "https://www.tickertape.in/stocks/{slug}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Referer": "https://www.tickertape.in/",
    "Accept": "application/json, text/html, */*",
}

# Same cache directory bse_client.py uses (this feature's shared cache
# location) but a distinct key prefix ("tt_") so nothing collides.
CACHE_DIR = Path(__file__).parent / "data" / "investment"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_TTL = {
    "tt_search": 7 * 24 * 3600,      # symbol->sid mapping barely changes
    "tt_info": 6 * 3600,
    "tt_holdings": 24 * 3600,        # quarterly data — a day is plenty
    "tt_price_history": 6 * 3600,
    # Was 3 days. Statements feed the AI analysis's growth rates, trailing EPS and
    # latest quarter, so after a company reports they must not lag by days. One
    # extra page fetch per on-demand run is negligible; get_financial_statements
    # can also be force-refreshed when BSE shows a newer quarter than this cache.
    "tt_financials": 6 * 3600,
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
        logger.warning(f"tickertape_client: cache write failed for {kind}/{key}: {e}")


def _cached(kind: str, key: str, fetch_fn, force: bool = False):
    if not force:
        cached = _cache_get(kind, key)
        if cached is not None:
            return cached
    data = fetch_fn()
    _cache_set(kind, key, data)
    return data


def cache_fetched_at(kind: str, key: str):
    """Unix time this cache entry was fetched, or None — used to tell the
    user how fresh the underlying data is."""
    try:
        return json.loads(_cache_path(kind, key).read_text(encoding="utf-8"))["ts"]
    except Exception:
        return None


_session = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session


# ── Search (symbol/name -> sid + slug) ────────────────────────────────────

def search(text: str) -> list:
    """Find Tickertape's internal sid + slug for a company name/symbol.
    e.g. search("Reliance Industries")[0] -> {"sid": "RELI",
    "slug": "/stocks/reliance-industries-RELI", "ticker": "RELIANCE", ...}"""
    def fetch():
        s = _get_session()
        r = s.get(SEARCH_URL, params={"text": text, "types": "stock"}, timeout=15)
        r.raise_for_status()
        data = r.json()
        return (data.get("data") or {}).get("stocks", [])
    return _cached("tt_search", text, fetch)


def find_sid(symbol_or_name: str) -> dict:
    """Best-effort single match — prefers an EXACT ticker match over fuzzy
    name matches, since search() can return several similarly-named rows
    (e.g. 'Reliance Industries' vs 'Reliance Industries - Partly Paid Up')."""
    results = search(symbol_or_name)
    if not results:
        return {}
    exact = [r for r in results if r.get("match") == "EXACT" and r.get("ticker", "").upper() == symbol_or_name.upper()]
    return (exact or results)[0]


# ── Ratios / info ─────────────────────────────────────────────────────────

def get_info(sid: str) -> dict:
    """P/E, P/B, ROE, EPS, book value, beta, 52-week range, ISIN, GIC
    sector/industry classification, market cap."""
    def fetch():
        s = _get_session()
        r = s.get(INFO_URL_TMPL.format(sid=sid), timeout=15)
        r.raise_for_status()
        return (r.json() or {}).get("data", {})
    return _cached("tt_info", sid, fetch)


def get_price_history(sid: str, duration: str = "max") -> list:
    """Weekly closing prices, oldest first: [{"ts": ISO date, "lp": price}].
    BSE's own price endpoint only serves ~12 months, which is too short to
    place today's P/E against the stock's own multi-year history — this
    endpoint (found by network capture on tickertape.in's chart) serves
    '5y' or 'max' (back to listing). Prices are split/bonus-adjusted."""
    def fetch():
        s = _get_session()
        r = s.get(f"https://api.tickertape.in/stocks/charts/inter/{sid}", params={"duration": duration}, timeout=20)
        r.raise_for_status()
        payload = r.json() or {}
        blocks = payload.get("data") or []
        points = (blocks[0] or {}).get("points", []) if blocks else []
        return [{"ts": p.get("ts"), "lp": p.get("lp")} for p in points if p.get("lp") is not None]
    return _cached("tt_price_history", f"{sid}_{duration}", fetch)


# ── Shareholding (already a multi-quarter time series per response) ──────

def get_holdings(sid: str) -> list:
    """Quarterly promoter/FII/DII/MF/retail % — Tickertape returns several
    historical quarters in a single response already, no per-quarter
    fetching needed (unlike the BSE XBRL path, which is one filing at a
    time)."""
    def fetch():
        s = _get_session()
        r = s.get(HOLDINGS_URL_TMPL.format(sid=sid), timeout=15)
        r.raise_for_status()
        return (r.json() or {}).get("data", [])
    return _cached("tt_holdings", sid, fetch)


# ── Financial statements (the gap-filler) ─────────────────────────────────

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _fetch_next_data(slug: str) -> dict:
    s = _get_session()
    url = STOCK_PAGE_TMPL.format(slug=slug.lstrip("/").removeprefix("stocks/"))
    r = s.get(url, timeout=20)
    r.raise_for_status()
    m = _NEXT_DATA_RE.search(r.text)
    if not m:
        raise ValueError(f"__NEXT_DATA__ not found on {url} — page structure may have changed")
    return json.loads(m.group(1))


def get_financial_statements(slug: str, force_refresh: bool = False) -> dict:
    """10 years of annual balance sheet + income statement + cash flow,
    plus quarterly income, in one page fetch. `slug` comes from search()'s
    'slug' field (e.g. 'reliance-industries-RELI' or the leading-slash form
    Tickertape returns — either works, normalized below).

    Field names are Tickertape's own short codes (e.g. balTota=Total
    Assets, balTeq=Total Equity, balTotl=Total Liabilities, balTca/balTcl=
    Total Current Assets/Liabilities, balRtne=Retained Earnings,
    balTdeb=Total Debt) — kept as-is rather than relabeled, so a schema
    change on their end is easy to notice rather than silently masked."""
    def fetch():
        next_data = _fetch_next_data(slug)
        pp = next_data.get("props", {}).get("pageProps", {})
        return {
            "balance_sheet_annual": pp.get("balancesheet-normal-annual", []),
            "income_statement_annual": pp.get("income-normal-annual", []),
            "income_statement_quarterly": pp.get("income-normal-interim", []),
            "cash_flow_annual": pp.get("cashflow-normal-annual", []),
        }
    return _cached("tt_financials", slug, fetch, force=force_refresh)


def get_full_profile(symbol_or_name: str) -> dict:
    """Convenience wrapper: resolve a symbol/name to its Tickertape sid+slug
    once, then pull info + holdings + financial statements together."""
    match = find_sid(symbol_or_name)
    if not match:
        return {"found": False, "query": symbol_or_name}
    sid = match["sid"]
    slug = match["slug"]
    return {
        "found": True,
        "sid": sid,
        "slug": slug,
        "ticker": match.get("ticker"),
        "name": match.get("name"),
        "info": get_info(sid),
        "holdings": get_holdings(sid),
        "financials": get_financial_statements(slug),
    }
