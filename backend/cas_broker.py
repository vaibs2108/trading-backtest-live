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
from datetime import date as _date
from typing import Optional

import pandas as pd
from dhanhq import dhanhq, DhanContext

logger = logging.getLogger(__name__)

# ── Independent rate limiter -- separate lock/state from broker.py's ──────────
_lock = threading.Lock()
_last_call_ts = 0.0
_MIN_SPACING = 1.05  # Dhan's documented option-chain/quote limit is 1 req/sec; small margin

_dhan_client = None
_connected = False
_connection_error = ""


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


def nearest_expiry_for_index(under_security_id: int, under_exchange_segment: str) -> Optional[str]:
    """Live expiry_list lookup for index underlyings (unlike stocks, whose
    nearest expiry is derivable straight from the static scrip master)."""
    if not _connected or _dhan_client is None:
        return None
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
                return sorted(dates)[0]
        return None
    except Exception as e:
        logger.debug(f"CAS scanner: expiry_list failed for sec_id={under_security_id}: {e}")
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


def get_fo_stock_universe() -> list:
    """Return the list of NSE F&O stock underlyings with their equity
    security_id and nearest option expiry, built once from the scrip master
    (static daily file, safe to derive from broker.py's cached DataFrame)."""
    global _fo_stock_universe
    if _fo_stock_universe is not None:
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
    today_str = _date.today().isoformat()

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
