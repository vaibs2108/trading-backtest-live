"""
cas_broker.py — Deliberately separate, minimal Dhan connection for the CAS
spike scanner (backend/cas_scanner.py).

This is NOT a wrapper around broker.py's global _dhan_client -- it holds its
own dhanhq client, authenticated with its own dedicated client_id/access_token
(cfg.dhan_cas_client_code / cfg.dhan_cas_access_token, sourced from .env only),
and its own independent rate-limiter state. The scanner's request cadence
must never contend with the live trading engine's own market_data budget in
broker.py -- see STRATEGY_REGISTRY.md / the CAS scanner plan for why.

Reuses broker.py's _load_instrument_df() for the scrip master CSV -- that's
static reference data (a daily-cached file on disk), not a rate-limited API
call, so sharing it carries no isolation risk.
"""

import logging
import threading
import time as _time
from typing import Optional

import pandas as pd
from dhanhq import dhanhq, DhanContext

logger = logging.getLogger(__name__)

# ── Independent rate limiter -- separate lock/state from broker.py's ──────────
_lock = threading.Lock()
_last_call_ts = 0.0
# Dhan's docs: "Rate limit for Option Chain API is set to one unique request every 3
# seconds" (dhanhq.co/docs/v2/option-chain). At the old 1.05 s spacing thousands of calls
# a day failed (10,325 on 2026-10-01, 2,230 of them 09:00-10:00), and the same account's
# Market Context option chain was refused too. 3.05 s: a full stock sweep takes ~15 min.
_MIN_SPACING = 3.05

_dhan_client = None
_connected = False
_connection_error = ""


def _today_ist() -> str:
    """Today's date in IST (the server may run in UTC: local date is wrong 00:00-05:30 IST)."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    return _dt.now(_tz(_td(hours=5, minutes=30))).date().isoformat()


def dhan_cas_api_call(func, *args, **kwargs):
    """Rate-limited call wrapper, spaced at the CAS scanner's own 1 req/sec
    budget -- entirely independent of broker.py's dhan_api_call/_dhan_api_lock."""
    global _last_call_ts
    with _lock:
        now = _time.time()
        elapsed = now - _last_call_ts
        if elapsed < _MIN_SPACING:
            _time.sleep(_MIN_SPACING - elapsed)
        _last_call_ts = _time.time()
        return func(*args, **kwargs)


def connect(client_code: str, access_token: str) -> bool:
    """Connect using the scanner's own dedicated credentials. Returns success."""
    global _dhan_client, _connected, _connection_error
    if not client_code or not access_token:
        _connected = False
        return False
    try:
        context = DhanContext(client_code, access_token)
        client = dhanhq(context)
        res = client.get_fund_limits()
        if isinstance(res, dict) and res.get("status") == "failure":
            raise RuntimeError(res.get("remarks") or "CAS scanner Dhan auth failed")
        _dhan_client = client
        _connected = True
        _connection_error = ""
        logger.info("CAS scanner: connected to Dhan (dedicated token)")
        return True
    except Exception as e:
        _connected = False
        _connection_error = str(e)
        logger.warning(f"CAS scanner: Dhan connection failed: {e}")
        return False


def is_connected() -> bool:
    return _connected


_index_expiry_cache = {}  # (security_id, date) -> nearest expiry


def nearest_expiry_for_index(under_security_id: int, under_exchange_segment: str) -> Optional[str]:
    """Live expiry_list lookup for index underlyings (unlike stocks, whose
    nearest expiry is derivable straight from the static scrip master).
    Cached per day: it used to be called on every 30-s index sweep, using half of
    the option-chain budget (expiry list + chain per index)."""
    if not _connected or _dhan_client is None:
        return None
    cache_key = (under_security_id, _today_ist())
    if cache_key in _index_expiry_cache:
        return _index_expiry_cache[cache_key]
    try:
        res = dhan_cas_api_call(
            _dhan_client.expiry_list,
            under_security_id=under_security_id,
            under_exchange_segment=under_exchange_segment,
        )
        if isinstance(res, dict) and res.get("status") == "success":
            dates = res.get("data", [])
            if isinstance(dates, dict):
                dates = dates.get("data", [])
            if isinstance(dates, list) and dates:
                today = _today_ist()
                future = [d for d in sorted(dates) if d >= today]
                if future:
                    _index_expiry_cache[cache_key] = future[0]
                    return future[0]
        return None
    except Exception as e:
        logger.debug(f"CAS scanner: expiry_list failed for sec_id={under_security_id}: {e}")
        return None


def traded_today(today_str: str) -> Optional[bool]:
    """Did NSE trade today? True if NIFTY has 1-minute bars dated today, False if none
    (exchange holiday / not opened yet), None if the check itself failed. Uses the
    historical-data API, not the option-chain budget."""
    if not _connected or _dhan_client is None:
        return None
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        nxt = (_dt.strptime(today_str, "%Y-%m-%d") + _td(days=1)).strftime("%Y-%m-%d")
        res = _dhan_client.intraday_minute_data(security_id="13", exchange_segment="IDX_I",
                                                instrument_type="INDEX", from_date=today_str,
                                                to_date=nxt, interval=1)
        if not isinstance(res, dict) or res.get("status") != "success":
            return None
        data = res.get("data") or {}
        ts = data.get("timestamp", []) if isinstance(data, dict) else []
        ist = _tz(_td(hours=5, minutes=30))
        return any(_dt.fromtimestamp(x, ist).date().isoformat() == today_str for x in ts)
    except Exception as e:
        logger.debug(f"CAS scanner: traded-today check failed: {e}")
        return None


def index_minute_closes(security_id: int, day: str) -> Optional[dict]:
    """1-minute closes of an index for `day` as {"HH:MM": close} (bar start time, IST), or None if
    the call failed. Historical-data API, not the option-chain budget. Used by the paper squeeze."""
    if not _connected or _dhan_client is None:
        return None
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        nxt = (_dt.strptime(day, "%Y-%m-%d") + _td(days=1)).strftime("%Y-%m-%d")
        res = _dhan_client.intraday_minute_data(security_id=str(security_id), exchange_segment="IDX_I",
                                                instrument_type="INDEX", from_date=day, to_date=nxt, interval=1)
        if not isinstance(res, dict) or res.get("status") != "success":
            return None
        data = res.get("data") or {}
        ts, cl = data.get("timestamp") or [], data.get("close") or []
        ist = _tz(_td(hours=5, minutes=30))
        out = {}
        for t, c in zip(ts, cl):
            d = _dt.fromtimestamp(t, ist)
            if d.date().isoformat() == day:
                out[d.strftime("%H:%M")] = c
        return out
    except Exception as e:
        logger.debug(f"CAS scanner: index minute data failed for {security_id}: {e}")
        return None


# Last failure reason per call, exposed so _sweep_once can aggregate WHY
# fetches failed into its summary instead of just a bare count -- found the
# hard way (2026-08-26) that "status != success" and exceptions were both
# being silently swallowed at debug level, making a 100%-of-universe failure
# indistinguishable from a normal quiet sweep in the logs.
last_option_chain_error: Optional[str] = None


def option_chain(under_security_id: int, under_exchange_segment: str, expiry: str) -> Optional[dict]:
    """Rate-limited option_chain call. Returns the raw Dhan response dict, or
    None on any failure -- callers should treat None as 'skip this underlying
    this sweep', not raise. On failure, last_option_chain_error holds the
    actual reason (Dhan's remarks/errorMessage, or the exception text)."""
    global last_option_chain_error
    if not _connected or _dhan_client is None:
        last_option_chain_error = "not connected"
        return None
    try:
        res = dhan_cas_api_call(
            _dhan_client.option_chain,
            under_security_id=under_security_id,
            under_exchange_segment=under_exchange_segment,
            expiry=expiry,
        )
        if isinstance(res, dict) and res.get("status") == "success":
            last_option_chain_error = None
            return res
        reason = res.get("remarks") or res.get("errorMessage") if isinstance(res, dict) else str(res)
        # Dhan's failure "remarks" is sometimes itself a dict
        # ({'error_code','error_type','error_message'}) rather than a string --
        # must flatten to a hashable string, since this value ends up as a
        # dict key in cas_scanner._sweep_once()'s failure_reasons tally.
        if isinstance(reason, dict):
            reason = ", ".join(f"{k}={v}" for k, v in reason.items() if v not in (None, ""))
        last_option_chain_error = reason or f"unexpected response: {res!r}"
        logger.warning(f"CAS scanner: option_chain failed for sec_id={under_security_id}: {last_option_chain_error}")
        return None
    except Exception as e:
        last_option_chain_error = str(e)
        logger.warning(f"CAS scanner: option_chain exception for sec_id={under_security_id}: {e}")
        return None


# ── F&O stock universe -- built from the same scrip master broker.py uses ────
_fo_stock_universe: Optional[list] = None  # cached: [{"symbol", "security_id", "nearest_expiry"}, ...]
_fo_stock_universe_date: Optional[str] = None  # day it was built -- rebuilt daily


def get_fo_stock_universe() -> list:
    """Return the list of NSE F&O stock underlyings with their equity
    security_id and nearest option expiry, built once from the scrip master
    (static daily file, safe to derive from broker.py's cached DataFrame)."""
    global _fo_stock_universe, _fo_stock_universe_date
    # Rebuilt once a day: each stock's nearest expiry is fixed when the list is built, so
    # a process running across monthly expiry kept asking for the lapsed expiry and every
    # stock's option chain failed until a restart.
    if _fo_stock_universe is not None and _fo_stock_universe_date == _today_ist():
        return _fo_stock_universe

    import broker  # only for _load_instrument_df() -- no client/connection state touched
    df = broker._load_instrument_df()

    opt = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "OPTSTK")]
    opt = opt.copy()
    opt["underlying"] = opt["SEM_TRADING_SYMBOL"].str.split("-").str[0]

    eq = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "EQUITY")]
    eq_sec_id = dict(zip(eq["SEM_TRADING_SYMBOL"], eq["SEM_SMST_SECURITY_ID"]))

    # Found live (2026-08-26, the day after a monthly stock-options expiry):
    # the scrip master CSV still lists the just-lapsed expiry series for
    # stocks (unlike broker.py's own index expiry, which is resolved live via
    # the expiry_list API and never goes stale this way). Taking the raw
    # sorted-unique-dates minimum picked up that already-expired date, so
    # every stock's option_chain call failed identically until this filter --
    # this recurs every month right after stock-options expiry day unless we
    # drop dates that are already in the past.
    today_str = _today_ist()

    universe = []
    for symbol, grp in opt.groupby("underlying"):
        sec_id = eq_sec_id.get(symbol)
        if sec_id is None:
            continue  # can't resolve an equity security_id -- skip, don't guess
        all_dates = sorted(d.split(" ")[0] for d in grp["SEM_EXPIRY_DATE"].unique())
        future_dates = [d for d in all_dates if d >= today_str]
        if not future_dates:
            continue  # every listed expiry for this underlying is already lapsed -- skip, don't guess
        nearest_expiry = future_dates[0]
        universe.append({
            "symbol": symbol,
            "security_id": int(sec_id),
            "exchange_segment": "NSE_EQ",
            "nearest_expiry": nearest_expiry,
        })

    _fo_stock_universe = universe
    _fo_stock_universe_date = today_str
    logger.info(f"CAS scanner: resolved {len(universe)} F&O stock underlyings from scrip master")
    return universe


# Index underlyings the CAS scanner also sweeps for Mode B (Mode A is stock-only).
# Mirrors global_markets.py's OI_SECURITY_IDS/OI_EXCHANGE_SEGMENTS for the same
# instruments -- duplicated here (not imported) to keep cas_broker.py fully
# standalone from the rest of the app's live-trading modules.
INDEX_UNIVERSE = [
    {"symbol": "NIFTY", "security_id": 13, "exchange_segment": "IDX_I"},
    {"symbol": "BANKNIFTY", "security_id": 25, "exchange_segment": "IDX_I"},
    {"symbol": "SENSEX", "security_id": 51, "exchange_segment": "IDX_I"},
]
