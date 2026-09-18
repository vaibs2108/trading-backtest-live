"""
yahoo_client.py — BACKUP source for multi-year financial statements, price
history and a company profile, used only when Tickertape has no data for a
company (many small/SME stocks and some renamed ones, e.g. Eternal/Zomato).

It returns statements in the SAME shape and field codes as
tickertape_client.get_financial_statements (income_statement_annual /
_quarterly, balance_sheet_annual, cash_flow_annual, values in ₹ crore, FY rows
labelled 'FY 2026', quarters 'JUN 2026', plus a synthesised TTM row), so
Piotroski/Altman/valuation/freshness code needs no changes and can't tell the
sources apart — except that the API tags the source so the user is told.

Honest limits, surfaced to the user rather than hidden: Yahoo gives ~4 fiscal
years and ~5 quarters (Tickertape gives 10 years), and its figures are not
cross-checked against filings — so anything built on it is graded at most
'medium' reliability.

WRONG-COMPANY GUARD (the same failure mode fixed in the Tickertape lookup):
a Yahoo ticker is accepted only if BOTH its company name resembles the BSE
name AND its price is within 10% of BSE's live price. Ticker candidates, in
order: '<BSE id>.BO', '<BSE id>.NS', '<BSE code>.BO' (the numeric form was
observed returning wrong prices for some stocks, so it is the last resort).

Auth: Yahoo needs a session cookie + 'crumb' token (no API key). Fetched from
fc.yahoo.com / getcrumb, cached, refreshed on an auth failure.

Isolation: imports nothing from the trading app.
"""
import re
import json
import time
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
_TS_URL = "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"
_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}"

CACHE_DIR = Path(__file__).parent / "data" / "investment"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL = {"yh_symbol": 7 * 24 * 3600, "yh_symbol_none": 24 * 3600, "yh_statements": 6 * 3600, "yh_prices": 6 * 3600, "yh_info": 6 * 3600}

CRORE = 1e7   # Yahoo reports absolute rupees; the rest of this feature works in ₹ crore


# ── cache (same pattern as bse_client / tickertape_client) ───────────────

def _cache_path(kind: str, key: str) -> Path:
    return CACHE_DIR / f"{kind}__{re.sub(r'[^A-Za-z0-9_.-]', '_', str(key))}.json"


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
    try:
        _cache_path(kind, key).write_text(json.dumps({"ts": time.time(), "data": data}, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning(f"yahoo_client: cache write failed for {kind}/{key}: {e}")


def _cached(kind: str, key: str, fetch_fn, force: bool = False):
    if not force:
        hit = _cache_get(kind, key)
        if hit is not None:
            return hit
    data = fetch_fn()
    _cache_set(kind, key, data)
    return data


def cache_fetched_at(kind: str, key: str):
    try:
        return json.loads(_cache_path(kind, key).read_text(encoding="utf-8"))["ts"]
    except Exception:
        return None


# ── session + crumb ──────────────────────────────────────────────────────

_lock = threading.Lock()
_session: Optional[requests.Session] = None
_crumb: Optional[str] = None
_crumb_at = 0.0


def _auth(refresh: bool = False):
    global _session, _crumb, _crumb_at
    with _lock:
        if refresh or _session is None or not _crumb or time.time() - _crumb_at > 6 * 3600:
            s = requests.Session()
            s.headers.update(HEADERS)
            s.get("https://fc.yahoo.com", timeout=15)          # sets the cookie (a 404 body is expected)
            r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15)
            r.raise_for_status()
            _session, _crumb, _crumb_at = s, r.text.strip(), time.time()
        return _session, _crumb


def _get(url: str, params: Optional[dict] = None, timeout: int = 20) -> requests.Response:
    params = dict(params or {})
    for attempt in range(2):
        s, crumb = _auth(refresh=attempt == 1)
        params["crumb"] = crumb
        r = s.get(url, params=params, timeout=timeout)
        if r.status_code in (401, 403) and attempt == 0:
            continue                                            # stale crumb: refresh once
        return r
    return r


# ── identity verification ────────────────────────────────────────────────

_NAME_STOP = {"limited", "ltd", "the", "co", "company", "corp", "corporation", "and", "pvt", "private", "of"}


def _name_tokens(s: Optional[str]) -> set:
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("&", " and "))
    return {t for t in s.split() if t not in _NAME_STOP}


def names_match(bse_name: Optional[str], yahoo_name: Optional[str]) -> bool:
    a, b = _name_tokens(bse_name), _name_tokens(yahoo_name)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.6


def prices_match(bse_ltp, yahoo_price, tol: float = 0.10) -> bool:
    try:
        b, y = float(bse_ltp), float(yahoo_price)
    except (TypeError, ValueError):
        return False
    return b > 0 and y > 0 and abs(y - b) / b <= tol


def _chart_meta(sym: str) -> Optional[dict]:
    r = _get(_CHART_URL.format(sym=quote(sym, safe="")), {"range": "5d", "interval": "1d"})
    if r.status_code != 200:
        return None
    try:
        m = r.json()["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return {"name": m.get("longName") or m.get("shortName"), "price": m.get("regularMarketPrice"), "currency": m.get("currency")}


def resolve_symbol(scrip_id: str, scripcode: str, bse_name: str, bse_ltp) -> Optional[str]:
    """The verified Yahoo ticker for this BSE company, or None. Cached (a
    'no match' is cached only 24h so it is retried once Yahoo adds the stock)."""
    key = scrip_id
    hit = _cache_get("yh_symbol", key)
    if hit:
        return hit
    if _cache_get("yh_symbol_none", key):
        return None
    for sym in (f"{scrip_id}.BO", f"{scrip_id}.NS", f"{scripcode}.BO"):
        try:
            meta = _chart_meta(sym)
        except Exception as e:
            logger.info(f"yahoo_client: chart lookup {sym} failed: {e}")
            continue
        if not meta or meta.get("currency") not in (None, "INR"):
            continue
        if names_match(bse_name, meta["name"]) and prices_match(bse_ltp, meta["price"]):
            _cache_set("yh_symbol", key, sym)
            return sym
        logger.info(f"yahoo_client: rejected {sym} for {scrip_id}: name={meta.get('name')!r} price={meta.get('price')} vs BSE {bse_name!r} {bse_ltp}")
    _cache_set("yh_symbol_none", key, True)
    return None


# ── statements in Tickertape's shape ─────────────────────────────────────

_INCOME = {"TotalRevenue": "incTrev", "EBITDA": "incEbi", "ReconciledDepreciation": "incDep", "EBIT": "incPbi",
           "InterestExpense": "incIoi", "PretaxIncome": "incPbt", "TaxProvision": "incToi", "NetIncome": "incNinc"}
_BALANCE = {"TotalAssets": "balTota", "CurrentAssets": "balTca", "CurrentLiabilities": "balTcl", "StockholdersEquity": "balTeq",
            "TotalDebt": "balTdeb", "LongTermDebt": "balTltd", "TotalLiabilitiesNetMinorityInterest": "balTotl",
            "RetainedEarnings": "balRtne"}
_CASHFLOW = {"OperatingCashFlow": "cafCfoa", "FreeCashFlow": "cafFcf", "CapitalExpenditure": "cafCexp"}
_QUARTER = {"TotalRevenue": "qIncTrev", "NetIncome": "qIncNinc"}
_EPS_FIELDS = ("DilutedEPS", "BasicEPS")
_SHARES = "OrdinarySharesNumber"


def _timeseries(sym: str, prefix: str, fields: list) -> dict:
    r = _get(_TS_URL.format(sym=quote(sym, safe="")),
             {"type": ",".join(prefix + f for f in fields), "period1": 1400000000, "period2": int(time.time()),
              "merge": "false", "padTimeSeries": "true"})
    r.raise_for_status()
    out = {}
    for item in (r.json().get("timeseries", {}).get("result") or []):
        key = (item.get("meta", {}).get("type") or [None])[0]
        if not key:
            continue
        pts = [(v["asOfDate"], (v.get("reportedValue") or {}).get("raw")) for v in (item.get(key) or []) if v and (v.get("reportedValue") or {}).get("raw") is not None]
        if pts:
            out[key[len(prefix):]] = pts
    return out


def _build_rows(series: dict, mapping: dict, scale: float, period_label, extra_eps: Optional[str] = None,
                shares_code: Optional[str] = None) -> list:
    by_date = {}
    def put(date, code, value):
        by_date.setdefault(date, {})[code] = value
    for field, code in mapping.items():
        for date, v in series.get(field, []):
            put(date, code, v * scale)
    if extra_eps:
        for field in reversed(_EPS_FIELDS):          # Diluted wins over Basic where both exist
            for date, v in series.get(field, []):
                put(date, extra_eps, v)
    if shares_code:
        for date, v in series.get(_SHARES, []):
            put(date, shares_code, v)
    rows = []
    for date in sorted(by_date):
        d = by_date[date]
        end = datetime.strptime(date, "%Y-%m-%d")
        row = {"displayPeriod": period_label(end), "endDate": f"{date}T00:00:00.000Z", "reporting": "", **d}
        rows.append(row)
    return rows


def _fetch_statements(sym: str) -> Optional[dict]:
    ann = _timeseries(sym, "annual", list(_INCOME) + list(_BALANCE) + list(_CASHFLOW) + list(_EPS_FIELDS) + [_SHARES])
    qtr = _timeseries(sym, "quarterly", list(_QUARTER) + list(_EPS_FIELDS))
    if not ann.get("TotalRevenue") and not qtr.get("TotalRevenue"):
        return None

    fy = lambda end: f"FY {end.year}"
    inc = _build_rows(ann, _INCOME, 1 / CRORE, fy, extra_eps="incEps")
    for row in inc:                                    # operating expense = revenue - EBITDA, as Tickertape's incOpe
        if "incTrev" in row and "incEbi" in row:
            row["incOpe"] = row["incTrev"] - row["incEbi"]
    bal = _build_rows(ann, _BALANCE, 1 / CRORE, fy, shares_code="balTcso")
    cfl = _build_rows(ann, _CASHFLOW, 1 / CRORE, fy)
    qrows = _build_rows(qtr, _QUARTER, 1 / CRORE, lambda end: end.strftime("%b %Y").upper(), extra_eps="qIncEps")

    # trailing-twelve-months row, the way Tickertape supplies one (used for TTM EPS)
    last4 = qrows[-4:]
    if len(last4) == 4 and all("qIncTrev" in q and "qIncNinc" in q and "qIncEps" in q for q in last4):
        inc.append({"displayPeriod": "TTM", "endDate": "", "reporting": "",
                    "incTrev": sum(q["qIncTrev"] for q in last4), "incNinc": sum(q["qIncNinc"] for q in last4),
                    "incEps": sum(q["qIncEps"] for q in last4)})
    return {"balance_sheet_annual": bal, "income_statement_annual": inc, "income_statement_quarterly": qrows,
            "cash_flow_annual": cfl, "_source": "yahoo", "_symbol": sym}


def get_financial_statements(sym: str, force_refresh: bool = False) -> Optional[dict]:
    """Tickertape-shaped statements for an already-VERIFIED Yahoo ticker, or
    None if Yahoo has no fundamentals for it."""
    def fetch():
        return _fetch_statements(sym) or {}
    data = _cached("yh_statements", sym, fetch, force=force_refresh)
    return data or None


def get_price_history(sym: str) -> list:
    """Weekly closes, oldest first: [{"ts": ISO, "lp": price}] (split-adjusted)."""
    def fetch():
        r = _get(_CHART_URL.format(sym=quote(sym, safe="")), {"range": "max", "interval": "1wk"})
        if r.status_code != 200:
            return []
        res = r.json()["chart"]["result"][0]
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        return [{"ts": datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT00:00:00.000Z"), "lp": c}
                for t, c in zip(res.get("timestamp") or [], closes) if c is not None]
    return _cached("yh_prices", sym, fetch)


def get_info(sym: str) -> dict:
    """Profile + a few ratios, shaped like tickertape_client.get_info. Fail-soft:
    Yahoo often has no profile for small Indian stocks, in which case {}."""
    def fetch():
        r = _get(_SUMMARY_URL.format(sym=quote(sym, safe="")), {"modules": "assetProfile,summaryDetail,defaultKeyStatistics"})
        if r.status_code != 200:
            return {}
        q = (r.json().get("quoteSummary", {}).get("result") or [{}])[0]
        prof, sd = q.get("assetProfile") or {}, q.get("summaryDetail") or {}
        raw = lambda d, k: (d.get(k) or {}).get("raw") if isinstance(d.get(k), dict) else d.get(k)
        dy = raw(sd, "dividendYield")
        return {"info": {"sector": prof.get("industry") or prof.get("sector"), "description": prof.get("longBusinessSummary")},
                "ratios": {"52wHigh": raw(sd, "fiftyTwoWeekHigh"), "52wLow": raw(sd, "fiftyTwoWeekLow"), "beta": raw(sd, "beta"),
                           "divYield": dy * 100 if dy is not None else None}}
    return _cached("yh_info", sym, fetch)
