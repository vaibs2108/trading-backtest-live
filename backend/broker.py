"""
broker.py — Direct Dhan API connection, live data, order execution.
"""
import logging
import pandas as pd
import numpy as np
import os
import requests
import time as _time
from datetime import datetime, timedelta
from typing import Optional, Tuple
from config import get_settings, INSTRUMENT_META
from dhanhq import dhanhq, DhanContext

import threading

logger = logging.getLogger(__name__)

# ── Category-Aware Thread-Safe Rate Limiter ──────────────────────────────────
_dhan_api_lock = threading.Lock()
_last_call_by_category = {
    "historical": 0.0,
    "market_data": 0.0,
    "user_data": 0.0,
    "order": 0.0,
    "default": 0.0,
}

_category_min_spacing = {
    "historical": 1.05,   # 1.05s spacing for intraday & daily historical queries (Dhan HQ limit: 1 req/sec)
    "market_data": 0.25,  # 250ms spacing for quotes/ticker/option-chain (Dhan HQ limit: 5 req/sec)
    "user_data": 1.00,    # 1.00s spacing for balance/positions (Dhan HQ limit: 1 req/sec)
    "order": 0.10,        # 100ms spacing for orders (Dhan HQ limit: 10 req/sec)
    "default": 0.50,
}

def dhan_api_call(*args, **kwargs):
    """
    Thread-safe rate-limited wrapper adhering strictly to Dhan HQ API v2 rate limits.
    Can be called as:
      dhan_api_call(func, *args, **kwargs)
      dhan_api_call("historical", func, *args, **kwargs)
      dhan_api_call(category="market_data", func=func, ...)
    """
    global _last_call_by_category
    category = "default"
    func = None

    if args:
        if isinstance(args[0], str):
            category = args[0]
            func = args[1]
            func_args = args[2:]
        else:
            func = args[0]
            func_args = args[1:]
    else:
        category = kwargs.pop("category", "default")
        func = kwargs.pop("func")
        func_args = ()

    # The lock only reserves this call's time slot within its category; the wait and
    # the network call itself happen outside it. It used to be held across the call,
    # so one slow request (e.g. a backtest's multi-month historical fetch) blocked every
    # other Dhan call in the app -- LTP, positions, orders -- until it returned.
    min_spacing = _category_min_spacing.get(category, 0.50)
    with _dhan_api_lock:
        now = _time.time()
        slot = max(now, _last_call_by_category.get(category, 0.0) + min_spacing)
        _last_call_by_category[category] = slot
    wait = slot - _time.time()
    if wait > 0:
        _time.sleep(wait)
    return func(*func_args, **kwargs)

# ── Global client and facade instances ───────────────────────────────────────
_dhan_client = None
_tsl = None  # Facade object mimicking Tradehull for main.py telegram/cancel methods
_connected = False
_connection_error = ""

# Cache structures
_lot_size_cache = {}
_positions_cache = None
_positions_cache_time = None
_balance_cache = None
_balance_cache_time = None
_ltp_cache = {}
_ltp_cache_time = {}
_hist_cache = {}
_hist_cache_time = {}
_instrument_df = None


class DhanFacade:
    """
    A minimal facade wrapping _dhan_client to support Tradehull-specific 
    methods (such as send_telegram_alert and cancel_all_orders) in main.py.
    """
    def __init__(self, client: dhanhq):
        self.client = client

    def cancel_all_orders(self) -> dict:
        """
        Cancel all pending/transit orders and square off any open positions 
        associated with MIS or NRML product types at Dhan.
        """
        order_details = {}
        try:
            # 1. Cancel all pending and transit orders
            res = self.client.get_order_list()
            if isinstance(res, dict) and "data" in res and res["data"]:
                for order in res["data"]:
                    if order.get("orderStatus") in ["PENDING", "TRANSIT"]:
                        order_id = order.get("orderId")
                        if order_id:
                            self.client.cancel_order(order_id)
                            logger.info(f"Cancelled pending order: {order_id}")
        except Exception as e:
            logger.error(f"Error cancelling pending orders in facade: {e}")

        try:
            # 2. Square off any open positions
            pos_res = self.client.get_positions()
            if isinstance(pos_res, dict) and "data" in pos_res and pos_res["data"]:
                for pos in pos_res["data"]:
                    qty = int(pos.get("netQty", 0))
                    if qty != 0:
                        txn_type = "SELL" if qty > 0 else "BUY"
                        order = self.client.place_order(
                            security_id=str(pos.get("securityId")),
                            exchange_segment=pos.get("exchangeSegment"),
                            transaction_type=txn_type,
                            quantity=abs(qty),
                            order_type="MARKET",
                            product_type=pos.get("productType"),
                            price=0.0,
                            trigger_price=0.0
                        )
                        if isinstance(order, dict) and order.get("status") != "failure":
                            order_id = order.get("data", {}).get("orderId")
                            tradingsymbol = pos.get("tradingSymbol", "Unknown")
                            order_details[tradingsymbol] = {"orderid": str(order_id), "price": 0.0}
                            logger.info(f"Closed position for {tradingsymbol} with order {order_id}")
        except Exception as e:
            logger.error(f"Error squaring off positions in facade: {e}")

        return order_details

    def send_telegram_alert(self, message: str, receiver_chat_id: str, bot_token: str) -> bool:
        """Send telegram alert. Returns True if Telegram accepted it."""
        import urllib.parse
        try:
            encoded_message = urllib.parse.quote(message)
            send_text = f"https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={receiver_chat_id}&text={encoded_message}"
            response = requests.get(send_text, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram alert send failed: {e}")
            return False


def _load_instrument_df() -> pd.DataFrame:
    """
    Load daily Dhan scrip master CSV from folder, downloading it if not present.
    Caches the dataframe for instant symbol and security ID resolution.
    """
    global _instrument_df
    if _instrument_df is not None:
        return _instrument_df

    import os
    os.makedirs("Dependencies", exist_ok=True)
    import pytz
    today_str = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
    expected_file = os.path.join("Dependencies", f"all_instrument {today_str}.csv")

    if os.path.exists(expected_file):
        try:
            logger.info(f"Loading cached daily scrip master: {expected_file}")
            _instrument_df = pd.read_csv(expected_file, low_memory=False)
            return _instrument_df
        except Exception as e:
            logger.error(f"Error reading cached instrument CSV: {e}")

    try:
        logger.info("Downloading daily scrip master from Dhan API...")
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()

        temp_file = expected_file + ".tmp"
        with open(temp_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

        df = pd.read_csv(temp_file, low_memory=False)
        if "SEM_CUSTOM_SYMBOL" in df.columns:
            df['SEM_CUSTOM_SYMBOL'] = df['SEM_CUSTOM_SYMBOL'].astype(str).str.strip().str.replace(r'\s+', ' ', regex=True)
        if "SEM_TRADING_SYMBOL" in df.columns:
            df['SEM_TRADING_SYMBOL'] = df['SEM_TRADING_SYMBOL'].astype(str).str.strip().str.replace(r'\s+', ' ', regex=True)

        df.to_csv(expected_file, index=False)
        _instrument_df = df

        if os.path.exists(temp_file):
            os.remove(temp_file)

        # Clean old CSV files in Dependencies
        for item in os.listdir("Dependencies"):
            if item.startswith("all_instrument") and item.endswith(".csv") and today_str not in item:
                try:
                    os.remove(os.path.join("Dependencies", item))
                except Exception as ex:
                    logger.warning(f"Could not remove old file {item}: {ex}")

        logger.info(f"Fetched and saved daily scrip master: {expected_file}")
        return _instrument_df
    except Exception as e:
        logger.error(f"Failed to download daily scrip master: {e}")
        # Find fallback
        for item in os.listdir("Dependencies"):
            if item.startswith("all_instrument") and item.endswith(".csv"):
                try:
                    fallback = os.path.join("Dependencies", item)
                    logger.info(f"Falling back to existing daily scrip master: {fallback}")
                    _instrument_df = pd.read_csv(fallback, low_memory=False)
                    return _instrument_df
                except Exception as ex:
                    logger.error(f"Error loading fallback CSV {item}: {ex}")

        raise RuntimeError("No daily scrip master file found and download failed.")


def connect(client_code: str, access_token: str) -> Tuple[bool, str]:
    """Connect to Dhan via direct dhanhq SDK client. Returns (success, message)."""
    global _dhan_client, _tsl, _connected, _connection_error
    try:
        context = DhanContext(client_code, access_token)
        client = dhanhq(context)

        # Verify by fetching balance/limits
        res = client.get_fund_limits()
        if isinstance(res, dict) and res.get("status") == "failure":
            remarks = res.get("remarks") or "API verification failed"
            raise RuntimeError(remarks)

        _dhan_client = client
        _tsl = DhanFacade(client)
        _connected = True
        _connection_error = ""

        # Trigger instrument master loading in background thread or lazily
        _load_instrument_df()

        # Fetch balance
        bal = get_balance()
        logger.info(f"Dhan connected. Balance: Rs.{bal:,.2f}")
        return True, f"Connected. Available balance: Rs.{bal:,.2f}"
    except Exception as e:
        _connected = False
        _connection_error = str(e)
        logger.error(f"Dhan connection failed: {e}")
        return False, f"Connection failed: {e}"


def is_connected() -> bool:
    return _connected


def get_tsl():
    if not _connected or _tsl is None:
        raise RuntimeError("Not connected to Dhan. Please connect first.")
    return _tsl


# ── Live Data Retrieval ───────────────────────────────────────────────────────

def _get_ltp_data(symbols) -> dict:
    """
    Internal helper to fetch the LTP of a list of symbols from Dhan.
    Resolves symbols dynamically to their exchange segments and security IDs.
    """
    if not _connected or _dhan_client is None:
        return {}

    if not isinstance(symbols, list):
        symbols = [symbols]

    instruments = {
        'NSE_EQ': [], 'IDX_I': [], 'NSE_FNO': [], 'NSE_CURRENCY': [],
        'BSE_EQ': [], 'BSE_FNO': [], 'BSE_CURRENCY': [], 'MCX_COMM': []
    }
    symbol_map = {}

    df = _load_instrument_df()

    # Underlying index tokens
    index_tokens = {
        'NIFTY': 13, 'NIFTY 50': 13,
        'BANKNIFTY': 25, 'NIFTY BANK': 25,
        'FINNIFTY': 27, 'NIFTY FIN SERVICE': 27,
        'MIDCPNIFTY': 442, 'NIFTY MID SELECT': 442,
        'SENSEX': 51, 'BANKEX': 69,
        'INDIA VIX': 21,
        'GIFTNIFTY': 5024, 'GIFT NIFTY': 5024,
    }

    for symbol in symbols:
        sym_upper = symbol.upper()
        if sym_upper in index_tokens:
            sec_id = index_tokens[sym_upper]
            exch_seg = 'IDX_I'
            instruments[exch_seg].append(sec_id)
            symbol_map[str(sec_id)] = symbol
            continue

        match = df[(df['SEM_CUSTOM_SYMBOL'].str.upper() == sym_upper) | (df['SEM_TRADING_SYMBOL'].str.upper() == sym_upper)]
        if not match.empty:
            row = match.iloc[-1]
            sec_id = int(row['SEM_SMST_SECURITY_ID'])
            exch_id = str(row['SEM_EXM_EXCH_ID']).upper()
            inst_name = str(row['SEM_INSTRUMENT_NAME']).upper()

            exch_seg = None
            if inst_name == 'INDEX':
                exch_seg = 'IDX_I'
            elif exch_id == 'MCX':
                exch_seg = 'MCX_COMM'
            elif exch_id == 'NSE':
                if inst_name in ['FUTIDX', 'OPTIDX', 'FUTSTK', 'OPTSTK']:
                    exch_seg = 'NSE_FNO'
                else:
                    exch_seg = 'NSE_EQ'
            elif exch_id == 'BSE':
                if inst_name in ['FUTIDX', 'OPTIDX', 'FUTSTK', 'OPTSTK']:
                    exch_seg = 'BSE_FNO'
                else:
                    exch_seg = 'BSE_EQ'

            if exch_seg:
                instruments[exch_seg].append(sec_id)
                symbol_map[str(sec_id)] = symbol

    try:
        res = dhan_api_call("market_data", _dhan_client.ticker_data, instruments)
        ltps = {}
        if isinstance(res, dict) and res.get("status") == "success":
            inner = res.get("data", {}).get("data", {})
            for segment, sec_dict in inner.items():
                for sec_id, quotes in sec_dict.items():
                    if sec_id in symbol_map:
                        ltps[symbol_map[sec_id]] = float(quotes.get("last_price", 0.0))
        return ltps
    except Exception as e:
        logger.error(f"Error fetching ticker LTP data: {e}")
        return {}


def _sorted_by_nearest_future_expiry(match: pd.DataFrame) -> pd.DataFrame:
    """Sort a scrip-master slice by SEM_EXPIRY_DATE ascending, filtering out
    already-lapsed rows first.

    Confirmed live (2026-08-26): NSE's scrip master kept a stock-options
    series listed for a day after it expired, so a plain sort-ascending
    picked the just-lapsed date and caused real missed trades
    (get_option_symbol, fixed the same day). MCX's FUTCOM listing happened to
    already have the expired contract purged when checked, but the raw
    sort-ascending pattern here carries the identical risk if that ever lags
    the way NSE's did -- filtering is a no-op whenever everything listed is
    still genuinely upcoming, so it costs nothing on the days nothing has
    lapsed. Falls back to the unfiltered sort (with a warning) only if every
    listed row has already lapsed, rather than returning nothing.
    """
    if match.empty:
        return match
    today_str = datetime.now().date().isoformat()
    future = match[match['SEM_EXPIRY_DATE'].astype(str).str.split(" ").str[0] >= today_str]
    if future.empty:
        logger.warning("_sorted_by_nearest_future_expiry: every listed expiry has already lapsed -- falling back to unfiltered sort")
        future = match
    return future.sort_values(by='SEM_EXPIRY_DATE')


def get_ltp(instrument: str) -> Optional[float]:
    global _ltp_cache, _ltp_cache_time
    import pytz
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    if instrument in _ltp_cache and instrument in _ltp_cache_time:
        if (now - _ltp_cache_time[instrument]).total_seconds() < 5:
            return _ltp_cache[instrument]
    try:
        meta = INSTRUMENT_META.get(instrument, INSTRUMENT_META["BANKNIFTY"])
        if meta["exchange_index"] == "MCX":
            df_master = _load_instrument_df()
            sym_idx = meta.get("symbol_index", instrument)
            match = df_master[
                (df_master['SEM_EXM_EXCH_ID'] == 'MCX') &
                (df_master['SEM_INSTRUMENT_NAME'] == 'FUTCOM') &
                ((df_master['SM_SYMBOL_NAME'] == sym_idx) | (df_master['SEM_TRADING_SYMBOL'].str.startswith(sym_idx)))
            ]
            if not match.empty:
                active_sym = str(_sorted_by_nearest_future_expiry(match).iloc[0]['SEM_TRADING_SYMBOL'])
                ltps = _get_ltp_data([active_sym])
                val = ltps.get(active_sym)
            else:
                val = None
        else:
            ltps = _get_ltp_data([instrument])
            val = ltps.get(instrument)

        if val and val > 0:
            _ltp_cache[instrument] = val
            _ltp_cache_time[instrument] = now
            return val

        # Fallback for indices on weekends/holidays when ticker/live data is empty
        if instrument.upper() in ["NIFTY", "BANKNIFTY", "INDIA VIX"]:
            try:
                import pytz
                now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
                from_d = (now_ist - timedelta(days=5)).strftime("%Y-%m-%d")
                to_d   = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
                df = get_historical_data(instrument, "DAY", from_d, to_d, use_index=True)
                if df is not None and not df.empty:
                    val = float(df.iloc[-1]['close'])
                    _ltp_cache[instrument] = val
                    _ltp_cache_time[instrument] = now
                    logger.info(f"LTP fallback for index {instrument}: {val}")
                    return val
            except Exception as ex:
                logger.warning(f"LTP historical fallback failed for {instrument}: {ex}")

        return None
    except Exception as e:
        logger.error(f"get_ltp error for {instrument}: {e}")
        return None


_option_ltp_cache = {}
_option_ltp_cache_time = {}

def get_option_ltp(symbol: str) -> float:
    """Fetch live option premium LTP directly from Dhan with 2s caching.

    Retries on an empty/zero result before falling back to the last
    known-good cached value. Confirmed live (2026-08-26): Dhan's ticker_data
    intermittently returns an empty result for a real, liquid, actively-held
    NSE_FNO symbol -- a single request returning {} moments after (and
    before) other requests for the same symbol succeeded normally, so it's
    a transient blip, not a broken symbol. Without a retry, every caller
    (_safe_pnl_exit_price in particular) silently fell back to a stand-in
    price for as long as the blip lasted -- one real trade saw this on every
    poll for its entire ~10-minute hold, closing at its own entry price
    despite the index having moved, producing a meaningless PnL. A recent
    cached quote, even slightly stale, is a far better estimate than 0.

    Widened from 1 retry to 3 (2026-08-27): the single-retry version still
    failed on every poll for a live position for 2+ minutes straight (~9
    consecutive polls). Reproduced directly: even attempts spaced a full 3s
    apart failed twice before succeeding on the third -- this is the same
    "needs multiple retry rounds, not just one" pattern already confirmed to
    work for the CAS scanner's own option_chain calls (84 -> 35 -> 14 -> 7 ->
    0 failures across 4 rounds), not something a single quick retry bridges."""
    if not symbol:
        return 0.0
    now = _time.time()
    if symbol in _option_ltp_cache and symbol in _option_ltp_cache_time:
        if now - _option_ltp_cache_time[symbol] < 2.0:
            return _option_ltp_cache[symbol]
    max_attempts = 4
    for attempt in range(max_attempts):
        try:
            ltps = _get_ltp_data([symbol])
            val = ltps.get(symbol, 0.0)
            if val > 0:
                _option_ltp_cache[symbol] = val
                _option_ltp_cache_time[symbol] = now
                return val
        except Exception as e:
            logger.error(f"get_option_ltp error for {symbol}: {e}")
        if attempt < max_attempts - 1:
            _time.sleep(0.5)
    if symbol in _option_ltp_cache:
        logger.warning(
            f"get_option_ltp: live fetch failed {max_attempts} times for {symbol}, "
            f"using stale cached value {_option_ltp_cache[symbol]}"
        )
    return _option_ltp_cache.get(symbol, 0.0)


def get_index_security_id(symbol: str) -> Optional[str]:
    """Helper to dynamically resolve index security ID from Dhan instrument master."""
    try:
        df = _load_instrument_df()
        symbol_upper = symbol.upper()
        cond = (df['SEM_TRADING_SYMBOL'] == symbol_upper) & (df['SEM_INSTRUMENT_NAME'] == 'INDEX')
        match = df[cond]
        if not match.empty:
            return str(match.iloc[-1]['SEM_SMST_SECURITY_ID'])
    except Exception as e:
        logger.error(f"Error looking up index security ID for {symbol}: {e}")
    # Static fallbacks
    fallbacks = {"BANKNIFTY": "25", "NIFTY": "13", "SENSEX": "51", "FINNIFTY": "27", "MIDCPNIFTY": "442"}
    return fallbacks.get(symbol.upper())


def get_feed_subscription(instrument: str):
    """Resolve (security_id, exchange_segment) for the live tick feed.

    INDEX instruments -> (index_security_id, "IDX_I").
    MCX commodities  -> nearest FUTCOM contract on "MCX_COMM".
    Returns None when unresolvable — the caller must NOT start the feed with
    a guessed id (a wrong id silently streams another instrument's prices).
    """
    try:
        meta = INSTRUMENT_META.get(instrument, {})
        if meta.get("exchange_index") == "MCX":
            df_master = _load_instrument_df()
            symbol = meta.get("symbol_index", instrument)
            match = df_master[
                (df_master['SEM_EXM_EXCH_ID'] == 'MCX') &
                (df_master['SEM_INSTRUMENT_NAME'] == 'FUTCOM') &
                ((df_master['SM_SYMBOL_NAME'] == symbol) |
                 (df_master['SEM_TRADING_SYMBOL'].str.startswith(symbol + '-')))
            ]
            if not match.empty:
                row = _sorted_by_nearest_future_expiry(match).iloc[0]
                return str(row['SEM_SMST_SECURITY_ID']), "MCX_COMM"
            logger.error(f"get_feed_subscription: no FUTCOM found for {symbol}")
            return None
        sec = get_index_security_id(instrument)
        return (sec, "IDX_I") if sec else None
    except Exception as e:
        logger.error(f"get_feed_subscription failed for {instrument}: {e}")
        return None


def fetch_index_historical_data(
    instrument: str,
    timeframe: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None
) -> Optional[pd.DataFrame]:
    """Fallback route kept for backward compatibility; calls unified get_historical_data."""
    return get_historical_data(instrument, timeframe, from_date, to_date, use_index=True)


def _load_local_parquet_cache(
    instrument: str,
    timeframe: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None
) -> Optional[pd.DataFrame]:
    """Fallback loader when Dhan REST Historical API fails or returns HTTP 451 (DH-902).
    
    Reads from pre-cleaned parquet archives in scratch/research_14L/long_cache/,
    scratch/research_banknifty/live_cache/, Research/data/clean/, or Research/data/raw/.
    Ensures chart rendering and live indicator calculations never crash or return empty.
    """
    try:
        from pathlib import Path
        tf_norm = str(timeframe).upper().replace("MIN", "").replace("M", "").strip()
        if tf_norm in ("DAY", "D", "1D"):
            tf_keys = ["1D", "daily", "DAY"]
        elif tf_norm in ("60", "1H", "H", "60MIN"):
            tf_keys = ["60", "1hour", "60min"]
        elif tf_norm in ("15", "15MIN"):
            tf_keys = ["15", "15min"]
        elif tf_norm in ("5", "5MIN"):
            tf_keys = ["5", "5min"]
        elif tf_norm in ("1", "1MIN"):
            tf_keys = ["1", "1min"]
        else:
            tf_keys = [tf_norm]

        base_dir = Path(__file__).resolve().parent.parent  # repo root
        inst_up = instrument.upper()
        inst_lo = instrument.lower()

        candidates = []
        for k in tf_keys:
            candidates.extend([
                base_dir / "scratch" / "research_14L" / "long_cache" / f"{inst_up}_{k}.parquet",
                base_dir / "scratch" / "research_banknifty" / "live_cache" / f"{inst_up}_{k}.parquet",
                base_dir / "Research" / "data" / "clean" / f"{inst_lo}_{k}.parquet",
                base_dir / "Research" / "data" / "raw" / f"{inst_lo}_{k}.parquet",
                base_dir / "Research" / "data" / "clean" / f"{inst_lo}_{k}min.parquet",
                base_dir / "Research" / "data" / "raw" / f"{inst_lo}_{k}min.parquet",
            ])

        # SENSEX research files only ever stand in for SENSEX itself -- they used to be
        # tried for EVERY instrument, so an instrument with no file of its own could
        # silently get SENSEX prices.
        if inst_up == "SENSEX":
            for k in tf_keys:
                candidates.extend([
                    base_dir / "scratch" / "research_sensex" / "data" / f"sensex_{k}_recent.parquet",
                    base_dir / "scratch" / "research_sensex" / "data" / f"sensex_{k}.parquet",
                ])

        target_file = None
        for c in candidates:
            if c.exists():
                target_file = c
                break

        if not target_file:
            return None

        df = pd.read_parquet(target_file)
        if "timestamp" not in df.columns and "datetime" in df.columns:
            df["timestamp"] = df["datetime"]
        elif "date" in df.columns and "timestamp" not in df.columns:
            df["timestamp"] = df["date"]

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        if getattr(df["timestamp"].dt, "tz", None) is not None:
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)

        df = df.sort_values("timestamp").reset_index(drop=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" not in df.columns:
            df["volume"] = 0
        df.dropna(subset=["open", "high", "low", "close"], inplace=True)

        res = df
        if from_date or to_date:
            cond = pd.Series(True, index=df.index)
            if from_date:
                cond = cond & (df["timestamp"] >= pd.to_datetime(from_date))
            if to_date:
                cond = cond & (df["timestamp"] <= pd.to_datetime(to_date) + pd.Timedelta(days=1))
            sub = df[cond]
            if len(sub) >= 30:
                res = sub
            else:
                res = df.tail(min(len(df), 2000))

        return res[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    except Exception as e:
        logger.error(f"Error reading local parquet cache for {instrument} {timeframe}: {e}")
        return None


_dhan_auth_error: Optional[dict] = None   # set while Dhan rejects our token (DH-901)
_dhan_fail_logged: dict = {}               # error text -> last time it was logged


def dhan_auth_error() -> Optional[dict]:
    """{"since", "message"} while Dhan is rejecting the access token, else None."""
    return _dhan_auth_error


def _note_dhan_response(res, context: str):
    """Log Dhan's own error text for a failed call (once a minute per message) and track
    whether Dhan is rejecting the token. Before this, a failed history call left no trace
    of WHY -- an expired token (DH-901, seen 356 times in past logs) just looked like
    "no data" and the app silently switched to local cache files."""
    global _dhan_auth_error
    if isinstance(res, dict) and res.get("status") == "success":
        if _dhan_auth_error is not None:
            logger.info("Dhan accepted the access token again -- token error cleared")
            _dhan_auth_error = None
        return
    text = str(res.get("remarks") if isinstance(res, dict) and res.get("remarks") else res)[:240]
    now = _time.time()
    if now - _dhan_fail_logged.get(text, 0) >= 60:
        _dhan_fail_logged[text] = now
        logger.warning(f"Dhan {context} failed: {text}")
    if ("DH-901" in text or "Invalid_Authentication" in text) and _dhan_auth_error is None:
        _dhan_auth_error = {"since": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "message": "Dhan access token is invalid or expired (DH-901)"}
        logger.error("Dhan rejected the access token (DH-901): invalid or expired -- update it in .env and restart")


def get_historical_data(
    instrument: str,
    timeframe: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    use_index: bool = True,
    use_cache: bool = True,
    local_fallback: bool = True,
) -> Optional[pd.DataFrame]:
    """
    Fetch historical OHLCV data from Dhan directly using dhanhq client.
    use_cache=False (Backtest page) neither reads nor stores the in-memory cache, so
    backtest downloads don't pile up in the live process's memory.
    local_fallback=False (Backtest page) never substitutes local cache files when Dhan
    returns nothing -- the caller gets None/partial data and can report it.
    Supports daily data (historical_daily_data) and intraday timeframes 
    (intraday_minute_data with 90-day chunk limits).
    Falls back gracefully to local parquet cache if broker API is unavailable or returns 451.
    """
    global _hist_cache, _hist_cache_time
    cache_key = (instrument, timeframe, from_date, to_date, use_index)
    import pytz
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    if use_cache and cache_key in _hist_cache and cache_key in _hist_cache_time:
        # Intraday frames must stay fresh (a 120s-stale 5m frame delays live
        # signals by 1-2 candles); slow frames keep the long TTL.
        _ttl = 20 if str(timeframe).upper() in ("1", "5") else 120
        if (now - _hist_cache_time[cache_key]).total_seconds() < _ttl:
            return _hist_cache[cache_key].copy()

    if not _connected or _dhan_client is None:
        if not local_fallback:
            return None
        cached_fallback = _load_local_parquet_cache(instrument, timeframe, from_date, to_date)
        if cached_fallback is not None and not cached_fallback.empty:
            if use_cache:
                _hist_cache[cache_key] = cached_fallback
                _hist_cache_time[cache_key] = now
            return cached_fallback.copy()
        return None

    try:
        df_master = _load_instrument_df()
        meta = INSTRUMENT_META.get(instrument, INSTRUMENT_META["BANKNIFTY"])

        if use_index and meta.get("exchange_index") == "INDEX":
            sec_id = get_index_security_id(instrument)
            exch_seg = "IDX_I"
            inst_type = "INDEX"
            expiry_code = 0
        else:
            exch = meta["exchange_index"] if use_index else meta["exchange_fut"]
            symbol = meta["symbol_index"]

            if exch == "MCX":
                match = df_master[
                    (df_master['SEM_EXM_EXCH_ID'] == 'MCX') &
                    (df_master['SEM_INSTRUMENT_NAME'] == 'FUTCOM') &
                    ((df_master['SM_SYMBOL_NAME'] == symbol) | (df_master['SEM_TRADING_SYMBOL'].str.startswith(symbol + '-')))
                ]
                if not match.empty:
                    match = _sorted_by_nearest_future_expiry(match)
                    row = match.iloc[0]
                    sec_id = str(row['SEM_SMST_SECURITY_ID'])
                    exch_seg = "MCX_COMM"
                    inst_type = "FUTCOM"
                    expiry_code = int(row['SEM_EXPIRY_CODE'])
                else:
                    logger.error(f"Could not resolve FUTCOM for MCX symbol {symbol}")
                    cached_fallback = _load_local_parquet_cache(instrument, timeframe, from_date, to_date)
                    return cached_fallback.copy() if cached_fallback is not None else None
            else:
                match = df_master[
                    ((df_master['SEM_TRADING_SYMBOL'] == symbol) | (df_master['SEM_CUSTOM_SYMBOL'] == symbol)) &
                    (df_master['SEM_EXM_EXCH_ID'] == ('NSE' if exch in ['NSE', 'NFO'] else ('BSE' if exch in ['BSE', 'BFO'] else exch)))
                ]
                if match.empty:
                    logger.error(f"Could not resolve symbol {symbol} for historical data")
                    cached_fallback = _load_local_parquet_cache(instrument, timeframe, from_date, to_date)
                    return cached_fallback.copy() if cached_fallback is not None else None
                row = match.iloc[-1]
                sec_id = str(row['SEM_SMST_SECURITY_ID'])
                exch_seg = "NSE_FNO" if exch == "NFO" else ("BSE_FNO" if exch == "BFO" else ("MCX_COMM" if exch == "MCX" else "NSE_EQ"))
                inst_type = row['SEM_INSTRUMENT_NAME']
                expiry_code = int(row['SEM_EXPIRY_CODE'])

        if not sec_id:
            logger.error(f"Could not resolve security ID for {instrument}")
            cached_fallback = _load_local_parquet_cache(instrument, timeframe, from_date, to_date)
            return cached_fallback.copy() if cached_fallback is not None else None

        # Build dates
        if not from_date or not to_date:
            import pytz
            days = 365 if timeframe.upper() == "DAY" else 30
            now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
            from_d = (now_ist - timedelta(days=days)).strftime("%Y-%m-%d")
            to_d   = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            from_d = from_date
            to_d = to_date

        from_dt = datetime.strptime(from_d, "%Y-%m-%d").date()
        to_dt   = datetime.strptime(to_d, "%Y-%m-%d").date()

        # Dhan 5 year limit
        max_start = datetime.now().date() - timedelta(days=5 * 365)
        if from_dt < max_start:
            from_dt = max_start

        all_dfs = []
        if timeframe.upper() == "DAY":
            res = dhan_api_call(
                "historical",
                _dhan_client.historical_daily_data,
                security_id=int(sec_id),
                exchange_segment=exch_seg,
                instrument_type=inst_type,
                from_date=from_dt.strftime("%Y-%m-%d"),
                to_date=to_dt.strftime("%Y-%m-%d"),
                expiry_code=int(expiry_code),
                oi=True
            )
            _note_dhan_response(res, f"history {instrument} DAY")
            if isinstance(res, dict) and res.get("status") == "success" and "data" in res:
                all_dfs.append(pd.DataFrame(res["data"]))
        else:
            interval_val = int(timeframe)
            curr_from = from_dt
            while curr_from <= to_dt:
                curr_to = min(curr_from + timedelta(days=89), to_dt)
                chunk_df = None
                for attempt in range(3):
                    res = dhan_api_call(
                        "historical",
                        _dhan_client.intraday_minute_data,
                        security_id=str(sec_id),
                        exchange_segment=exch_seg,
                        instrument_type=inst_type,
                        from_date=curr_from.strftime("%Y-%m-%d"),
                        to_date=curr_to.strftime("%Y-%m-%d"),
                        interval=interval_val,
                        oi=False
                    )
                    _note_dhan_response(res, f"history {instrument} {timeframe}m {curr_from}..{curr_to}")
                    if isinstance(res, dict) and res.get("status") == "success" and "data" in res and res["data"]:
                        chunk_df = pd.DataFrame(res["data"])
                        break
                    else:
                        res_str = str(res).lower()
                        is_rate_limit = any(x in res_str for x in ["dh-904", "rate_limit", "rate limit", "too many requests", "904"])
                        is_unauthorized = any(x in res_str for x in ["dh-902", "invalid_access", "451", "not subscribed"])
                        if is_unauthorized:
                            logger.warning(f"Dhan historical Data API unavailable (DH-902/451). Switching to local cache.")
                            curr_from = to_dt + timedelta(days=1)
                            break
                        elif is_rate_limit:
                            wait_secs = 0.5 * (attempt + 1)
                            logger.warning(f"Rate limited, retry {attempt+1}/3 in {wait_secs}s")
                            _time.sleep(wait_secs)
                        else:
                            break

                if chunk_df is not None and not chunk_df.empty:
                    all_dfs.append(chunk_df)

                curr_from = curr_to + timedelta(days=1)
                _time.sleep(0.3)

        if not all_dfs:
            if not local_fallback:
                logger.warning(f"No historical data from broker API for {instrument} ({timeframe})")
                return None
            logger.warning(f"No historical data from broker API for {instrument} ({timeframe}), checking local parquet cache...")
            cached_fallback = _load_local_parquet_cache(instrument, timeframe, from_d, to_d)
            if cached_fallback is not None and not cached_fallback.empty:
                logger.info(f"Loaded {len(cached_fallback)} rows from local parquet cache for {instrument} ({timeframe})")
                if use_cache:
                    _hist_cache[cache_key] = cached_fallback
                    _hist_cache_time[cache_key] = now
                return cached_fallback.copy()
            logger.error(f"Failed to retrieve historical data for {instrument} (both broker API and local cache empty)")
            return None

        df = pd.concat(all_dfs, ignore_index=True)
        df.columns = [c.lower() for c in df.columns]

        if "timestamp" not in df.columns and "date" in df.columns:
            df.rename(columns={"date": "timestamp"}, inplace=True)

        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s') + pd.Timedelta(hours=5, minutes=30)
        df['timestamp'] = df['timestamp'].dt.tz_localize(None)

        df = df.sort_values("timestamp").reset_index(drop=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" not in df.columns:
            df["volume"] = 0

        df.dropna(subset=["open", "high", "low", "close"], inplace=True)
        result_df = df[["timestamp", "open", "high", "low", "close", "volume"]]

        if use_cache:
            _hist_cache[cache_key] = result_df
            _hist_cache_time[cache_key] = now
        return result_df.copy()
    except Exception as e:
        logger.error(f"Error in get_historical_data: {e}")
        if not local_fallback:
            return None
        cached_fallback = _load_local_parquet_cache(instrument, timeframe, from_date, to_date)
        if cached_fallback is not None and not cached_fallback.empty:
            return cached_fallback.copy()
        return None


def get_multi_timeframe_data(instrument: str, use_index: bool = True) -> dict:
    import pytz
    kolkata_tz = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(kolkata_tz)
    today = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")

    frames = {}
    for tf, days_back in [("DAY", 500), ("60", 180), ("15", 90), ("5", 45), ("1", 10)]:
        from_d = (now_ist - timedelta(days=days_back)).strftime("%Y-%m-%d")
        df = get_historical_data(instrument, tf, from_d, today, use_index)
        if df is not None and len(df) > 50:
            frames[tf if tf != "DAY" else "1D"] = df
            logger.info(f"  {tf}: {len(df)} rows")
        else:
            logger.warning(f"  {tf}: insufficient data")

    return frames


# ── Option/Futures Expiries & Strikes ─────────────────────────────────────────

def get_futures_symbol(instrument: str, expiry: int = 0) -> str:
    try:
        months = ["JAN","FEB","MAR","APR","MAY","JUN",
                  "JUL","AUG","SEP","OCT","NOV","DEC"]
        import calendar
        from datetime import date
        today = date.today()

        meta = INSTRUMENT_META.get(instrument, {})
        exchange = meta.get("exchange_index", "INDEX")
        roll_offset = 0

        if exchange == "MCX":
            if today.day > 19:
                roll_offset = 1
        else:
            cal = calendar.monthcalendar(today.year, today.month)
            last_thu = max(week[calendar.THURSDAY] for week in cal if week[calendar.THURSDAY] != 0)
            if today.day > last_thu:
                roll_offset = 1

        total_offset = expiry + roll_offset
        month_idx = (today.month - 1 + total_offset) % 12
        month_str = months[month_idx]
        return f"{instrument} {month_str} FUT"
    except Exception:
        return f"{instrument} FUT"


def get_option_symbol(instrument: str, direction: str, expiry: int = 0,
                       strike_type: str = "ATM", offset: int = 0) -> Tuple[str, int]:
    """
    Resolve option symbol using scrip master strike indexing offsets.
    CE for LONG, PE for SHORT.
    """
    try:
        df = _load_instrument_df()
        df['SEM_CUSTOM_SYMBOL'] = df['SEM_CUSTOM_SYMBOL'].astype(str).str.strip()
        df['SEM_TRADING_SYMBOL'] = df['SEM_TRADING_SYMBOL'].astype(str).str.strip()

        exchange = "NSE"
        inst_type = "OPTIDX"
        if instrument in ["SENSEX", "BANKEX"]:
            exchange = "BSE"
            inst_type = "OPTIDX"
        elif instrument == "CRUDEOIL":
            exchange = "MCX"
            # Dhan's scrip master tags MCX commodity options as "OPTFUT"
            # (options-on-futures) -- "OPTCOM" never appears in the real data,
            # confirmed against Dependencies/all_instrument CSV (2,084 real
            # CRUDEOIL/CRUDEOILM OPTFUT rows with real strikes/expiries).
            inst_type = "OPTFUT"

        df_opt = df[
            (df['SEM_EXM_EXCH_ID'] == exchange) &
            (df['SEM_INSTRUMENT_NAME'] == inst_type) &
            (df['SEM_TRADING_SYMBOL'].str.startswith(instrument, na=False))
        ]

        expiries = sorted(df_opt['SEM_EXPIRY_DATE'].unique())
        if len(expiries) == 0:
            logger.error(f"No expiries found for option {instrument}")
            return None, None

        # The scrip master CSV keeps a just-lapsed expiry series listed for a
        # day after it expires (confirmed live 2026-08-26: BANKNIFTY's
        # 2026-08-25 monthly was still expiries[0] the morning after it
        # expired), so the raw sorted-unique minimum can point at a dead,
        # unpriceable contract -- this is what caused 3 real LONG signals to
        # be skipped this morning ("Option premium unavailable"). Filter to
        # only expiries that haven't lapsed yet.
        today = datetime.now().date()
        future_expiries = [e for e in expiries if pd.Timestamp(e).date() >= today]
        if not future_expiries:
            logger.error(f"No future (non-lapsed) expiries found for option {instrument}")
            return None, None

        expiry_date = future_expiries[min(expiry, len(future_expiries) - 1)]
        df_expiry = df_opt[df_opt['SEM_EXPIRY_DATE'] == expiry_date]

        sorted_strikes = sorted(df_expiry['SEM_STRIKE_PRICE'].unique())
        if len(sorted_strikes) == 0:
            logger.error(f"No strikes found for expiry {expiry_date}")
            return None, None

        ltp = get_ltp(instrument)
        if not ltp:
            ltp = sorted_strikes[len(sorted_strikes) // 2]

        # ATM selection
        atm_strike = min(sorted_strikes, key=lambda x: abs(x - ltp))
        atm_index = sorted_strikes.index(atm_strike)

        # Dynamic index offsetting
        target_index = atm_index
        option_type = "CE" if direction == "LONG" else "PE"

        if strike_type == "ITM" or (strike_type == "ATM" and offset < 0):
            shift = abs(offset)
            if direction == "LONG":  # CE
                target_index = atm_index - shift
            else:  # PE
                target_index = atm_index + shift
        elif strike_type == "OTM" or (strike_type == "ATM" and offset > 0):
            shift = abs(offset)
            if direction == "LONG":  # CE
                target_index = atm_index + shift
            else:  # PE
                target_index = atm_index - shift

        target_index = max(0, min(target_index, len(sorted_strikes) - 1))
        target_strike = sorted_strikes[target_index]

        match = df_expiry[
            (df_expiry['SEM_OPTION_TYPE'] == option_type) &
            (df_expiry['SEM_STRIKE_PRICE'] == target_strike)
        ]

        if not match.empty:
            custom_symbol = match.iloc[0]['SEM_CUSTOM_SYMBOL']
            # Liquidity fallback (2026-08-24): confirmed directly against live
            # Dhan data that MCX commodity option strikes can exist in the
            # scrip master with zero live quote at all (deep ITM/OTM CrudeOil
            # strikes tested 0.0 LTP; nearby strikes had real premiums). Walk
            # to progressively wider nearby strikes of the SAME option type
            # and use the first one with a real live quote, closest to the
            # originally configured strike. For NSE index options (BankNifty
            # etc.) the configured strike has essentially always had a live
            # quote in practice, so this check passes on its first try and
            # returns the exact same symbol as before -- no behavior change
            # there. If nothing nearby is liquid either, falls back to the
            # originally configured strike/symbol, identical to pre-fallback
            # behavior (downstream code already skips entry safely on a
            # zero-premium symbol).
            if get_option_ltp(custom_symbol) > 0:
                return custom_symbol, int(target_strike)

            max_radius = 6
            for radius in range(1, max_radius + 1):
                for cand_index in (target_index - radius, target_index + radius):
                    if cand_index < 0 or cand_index >= len(sorted_strikes):
                        continue
                    cand_strike = sorted_strikes[cand_index]
                    cand_match = df_expiry[
                        (df_expiry['SEM_OPTION_TYPE'] == option_type) &
                        (df_expiry['SEM_STRIKE_PRICE'] == cand_strike)
                    ]
                    if cand_match.empty:
                        continue
                    cand_symbol = cand_match.iloc[0]['SEM_CUSTOM_SYMBOL']
                    if get_option_ltp(cand_symbol) > 0:
                        logger.info(
                            f"get_option_symbol: configured strike {target_strike} ({instrument}) "
                            f"had no live quote, using nearby liquid strike {cand_strike} instead "
                            f"({cand_symbol})"
                        )
                        return cand_symbol, int(cand_strike)

            return custom_symbol, int(target_strike)

        logger.error(f"No option contract matching {option_type} strike {target_strike} for expiry {expiry_date}")
        return None, None
    except Exception as e:
        logger.error(f"get_option_symbol error: {e}")
        return None, None


# ── Live Order Placements & Fills ─────────────────────────────────────────────

def verify_order_fill(order_id: str, timeout: int = 15) -> dict:
    """
    Poll get_order_by_id until status is FILLED, REJECTED, or CANCELLED.
    Raises TimeoutError or RuntimeError on failures.

    Fast path: if order_update_feed's Order Update WebSocket is connected
    and delivers a push for this order_id, the wait below returns almost
    immediately instead of waiting out the fixed poll interval -- but the
    actual status decision below is UNCHANGED, still the same
    get_order_by_id + orderStatus parsing as before. The WS push is only
    ever used as an early wake-up trigger, never as the status source
    itself (see order_update_feed.py's module docstring for why).
    """
    try:
        import order_update_feed
        order_update_feed.register(order_id)
    except Exception:
        order_update_feed = None

    start_time = _time.time()
    while _time.time() - start_time < timeout:
        try:
            res = _dhan_client.get_order_by_id(order_id)
            if isinstance(res, dict) and "data" in res and res["data"]:
                data = res["data"]
                order_data = data[0] if isinstance(data, list) else data
                status = order_data.get("orderStatus")
                if status in ["TRADED", "FILLED"]:
                    logger.info(f"Order {order_id} filled successfully (status={status}).")
                    if order_update_feed:
                        order_update_feed.unregister(order_id)
                    return order_data
                elif status == "PART_TRADED":
                    logger.info(f"Order {order_id} partially traded. Waiting for complete execution...")
                elif status in ["REJECTED", "CANCELLED", "EXPIRED"]:
                    reason = order_data.get("rejectReason") or "Order cancelled, rejected, or expired."
                    logger.error(f"Order {order_id} failed with status {status}. Reason: {reason}")
                    if order_update_feed:
                        order_update_feed.unregister(order_id)
                    raise RuntimeError(f"Order failed: {status}. Reason: {reason}")
            elif isinstance(res, dict) and res.get("status") == "failure":
                remarks = res.get("remarks") or "Unknown API failure"
                logger.warning(f"API get_order_by_id returned failure for {order_id}: {remarks}")
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"Error querying status for order {order_id}: {e}")

        if order_update_feed:
            order_update_feed.wait_for_update(order_id, timeout=0.5)
        else:
            _time.sleep(0.5)

    if order_update_feed:
        order_update_feed.unregister(order_id)

    raise TimeoutError(f"Order {order_id} fill verification timed out after {timeout} seconds.")


def place_entry_order(
    instrument: str,
    direction: str,
    trade_mode: str,
    expiry: int,
    strike_type: str,
    strike_offset: int,
    lot_multiplier: int,
    sl_price: float,
    t1_price: float,
    t2_price: float,
) -> dict:
    """
    Place a MARKET order (NRML/MIS mapped from product_type settings) via Dhan.
    """
    if not _connected or _dhan_client is None:
        return {"success": False, "error": "Not connected to Dhan broker"}

    lot_size = get_lot_size(instrument)
    qty = lot_size * lot_multiplier

    # 1. Resolve trading symbol name
    if trade_mode == "INDEX":
        symbol = get_futures_symbol(instrument, expiry)
        transaction = "BUY" if direction == "LONG" else "SELL"
    else:
        # OPTIONS: always BUY option to enter
        symbol, strike = get_option_symbol(
            instrument, direction, expiry, strike_type, strike_offset
        )
        transaction = "BUY"
        if symbol is None:
            return {"success": False, "error": "Could not resolve option symbol"}

    # Fetch LTP for slippage calculation
    if trade_mode == "OPTIONS":
        ltp = get_option_ltp(symbol) or 0.0
    else:
        ltp = get_ltp(instrument) or 0.0

    logger.info(f"Placing live entry {direction} {trade_mode}: {symbol} qty={qty} (txn={transaction})")

    try:
        # 2. Get exchange segment and security ID from scrip master
        df = _load_instrument_df()
        match = df[((df['SEM_CUSTOM_SYMBOL'] == symbol) | (df['SEM_TRADING_SYMBOL'] == symbol))]
        if match.empty:
            return {"success": False, "error": f"Symbol {symbol} not found in scrip master"}
        row = match.iloc[-1]
        security_id = str(row['SEM_SMST_SECURITY_ID'])

        exch_id = str(row['SEM_EXM_EXCH_ID']).upper()
        inst_name = str(row['SEM_INSTRUMENT_NAME']).upper()

        exch_seg = None
        if exch_id == 'MCX':
            exch_seg = 'MCX_COMM'
        elif exch_id == 'NSE':
            if inst_name in ['FUTIDX', 'OPTIDX', 'FUTSTK', 'OPTSTK']:
                exch_seg = 'NSE_FNO'
            else:
                exch_seg = 'NSE_EQ'
        elif exch_id == 'BSE':
            if inst_name in ['FUTIDX', 'OPTIDX', 'FUTSTK', 'OPTSTK']:
                exch_seg = 'BSE_FNO'
            else:
                exch_seg = 'BSE_EQ'

        if not exch_seg:
            return {"success": False, "error": f"Could not resolve segment for exchange {exch_id}"}

        cfg = get_settings()
        dhan_product_type = "MARGIN" if cfg.product_type == "NRML" else "INTRADAY"

        # 3. Call Place Order (rate-limited: DhanHQ v2 caps order APIs at ~10/sec)
        res = dhan_api_call(
            "order", _dhan_client.place_order,
            security_id=security_id,
            exchange_segment=exch_seg,
            transaction_type=transaction,
            quantity=int(qty),
            order_type="MARKET",
            product_type=dhan_product_type,
            price=0.0,
            trigger_price=0.0
        )

        if not res or res.get("status") == "failure":
            remarks = res.get("remarks") or "Unknown API failure"
            return {"success": False, "error": f"Dhan order failed: {remarks}"}

        order_id = str(res["data"]["orderId"])

        # 4. Verify Fill Loop
        order_details = verify_order_fill(order_id, timeout=15)
        executed_price = float(order_details.get("averageTradedPrice") or order_details.get("price") or 0.0)
        if executed_price == 0.0:
            executed_price = ltp

        return {
            "success": True,
            "order_id": order_id,
            "symbol": symbol,
            "exchange": exch_id,
            "direction": direction,
            "qty": qty,
            "sl": sl_price,
            "t1": t1_price,
            "t2": t2_price,
            "entry_price": executed_price,
            "expected_price": ltp,
            "entry_slippage": round(executed_price - ltp, 2) if executed_price > 0 and ltp > 0 else 0.0,
        }
    except Exception as e:
        logger.error(f"Entry order placement failed: {e}")
        return {"success": False, "error": str(e)}


def place_exit_order(symbol: str, exchange: str, direction: str, qty: int) -> dict:
    """Market exit of current position."""
    if not _connected or _dhan_client is None:
        return {"success": False, "error": "Not connected to Dhan broker"}

    import re
    is_option = bool(re.search(r'\d+\s*(CE|PE)', symbol, re.IGNORECASE)) or any(x in symbol.upper() for x in ['-CE', '-PE', ' CALL', ' PUT', ' OPTION'])
    if is_option:
        # Options are always SOLD to close
        transaction = "SELL"
    else:
        # Futures: reverse original direction
        transaction = "SELL" if direction == "LONG" else "BUY"

    logger.info(f"Placing live exit for {symbol} qty={qty} (txn={transaction})")

    try:
        df = _load_instrument_df()
        match = df[((df['SEM_CUSTOM_SYMBOL'] == symbol) | (df['SEM_TRADING_SYMBOL'] == symbol))]
        if match.empty:
            return {"success": False, "error": f"Symbol {symbol} not found in scrip master"}
        row = match.iloc[-1]
        security_id = str(row['SEM_SMST_SECURITY_ID'])

        exch_id = str(row['SEM_EXM_EXCH_ID']).upper()
        inst_name = str(row['SEM_INSTRUMENT_NAME']).upper()

        exch_seg = None
        if exch_id == 'MCX':
            exch_seg = 'MCX_COMM'
        elif exch_id == 'NSE':
            if inst_name in ['FUTIDX', 'OPTIDX', 'FUTSTK', 'OPTSTK']:
                exch_seg = 'NSE_FNO'
            else:
                exch_seg = 'NSE_EQ'
        elif exch_id == 'BSE':
            if inst_name in ['FUTIDX', 'OPTIDX', 'FUTSTK', 'OPTSTK']:
                exch_seg = 'BSE_FNO'
            else:
                exch_seg = 'BSE_EQ'

        if not exch_seg:
            return {"success": False, "error": f"Could not resolve segment for exchange {exch_id}"}

        cfg = get_settings()
        dhan_product_type = "MARGIN" if cfg.product_type == "NRML" else "INTRADAY"

        res = dhan_api_call(
            "order", _dhan_client.place_order,
            security_id=security_id,
            exchange_segment=exch_seg,
            transaction_type=transaction,
            quantity=int(qty),
            order_type="MARKET",
            product_type=dhan_product_type,
            price=0.0,
            trigger_price=0.0
        )

        if not res or res.get("status") == "failure":
            remarks = res.get("remarks") or "Unknown API failure"
            return {"success": False, "error": f"Dhan exit order failed: {remarks}"}

        order_id = str(res["data"]["orderId"])

        # Verify Fill
        verify_order_fill(order_id, timeout=15)
        return {"success": True, "order_id": order_id}
    except Exception as e:
        logger.error(f"Exit order placement failed: {e}")
        return {"success": False, "error": str(e)}


# ── Broker Stop Loss Orders ───────────────────────────────────────────────────

def place_broker_sl(symbol: str, exchange: str, direction: str, quantity: int, trigger_price: float) -> str:
    """
    Place a STOPMARKET Stop Loss order at Dhan.
    A position's SL is placed in the opposite direction.
    """
    if not _connected or _dhan_client is None:
        raise RuntimeError("Not connected to Dhan broker")

    import re
    is_option = bool(re.search(r'\d+\s*(CE|PE)', symbol, re.IGNORECASE)) or any(x in symbol.upper() for x in ['-CE', '-PE', ' CALL', ' PUT'])
    if is_option:
        # Options are always bought on entry, so SL order MUST be SELL
        transaction = "SELL"
    else:
        # Futures/Index: reverse original direction
        transaction = "SELL" if direction == "LONG" else "BUY"

    logger.info(f"Placing broker-side SL order for {symbol} qty={quantity} trigger={trigger_price} (txn={transaction})")

    try:
        df = _load_instrument_df()
        match = df[((df['SEM_CUSTOM_SYMBOL'] == symbol) | (df['SEM_TRADING_SYMBOL'] == symbol))]
        if match.empty:
            raise ValueError(f"Symbol {symbol} not found in scrip master")
        row = match.iloc[-1]
        security_id = str(row['SEM_SMST_SECURITY_ID'])

        exch_id = str(row['SEM_EXM_EXCH_ID']).upper()
        inst_name = str(row['SEM_INSTRUMENT_NAME']).upper()

        exch_seg = None
        if exch_id == 'MCX':
            exch_seg = 'MCX_COMM'
        elif exch_id == 'NSE':
            if inst_name in ['FUTIDX', 'OPTIDX', 'FUTSTK', 'OPTSTK']:
                exch_seg = 'NSE_FNO'
            else:
                exch_seg = 'NSE_EQ'
        elif exch_id == 'BSE':
            if inst_name in ['FUTIDX', 'OPTIDX', 'FUTSTK', 'OPTSTK']:
                exch_seg = 'BSE_FNO'
            else:
                exch_seg = 'BSE_EQ'

        if not exch_seg:
            raise ValueError(f"Could not resolve segment for exchange {exch_id}")

        cfg = get_settings()
        dhan_product_type = "MARGIN" if cfg.product_type == "NRML" else "INTRADAY"

        # Round trigger price to 1 decimal place to align with ticks
        rounded_trigger = round(float(trigger_price), 1)

        # NSE & Dhan API v2 Rule: Options segment prohibits STOP_LOSS_MARKET.
        # For Options, order_type MUST be "STOP_LOSS" (Stop Loss Limit) with both trigger_price and price set.
        if is_option:
            order_type_str = "STOP_LOSS"
            # Limit price set 2 points below trigger price (min 0.05) to ensure execution without wide slippage
            limit_price = max(0.05, round(rounded_trigger - 2.0, 1))
        else:
            order_type_str = "STOP_LOSS_MARKET"
            limit_price = 0.0

        logger.info(
            f"Placing broker-side SL order ({order_type_str}): {symbol} qty={quantity} "
            f"trigger={rounded_trigger} price={limit_price} (txn={transaction})"
        )

        res = dhan_api_call(
            "order", _dhan_client.place_order,
            security_id=security_id,
            exchange_segment=exch_seg,
            transaction_type=transaction,
            quantity=int(quantity),
            order_type=order_type_str,
            product_type=dhan_product_type,
            price=limit_price,
            trigger_price=rounded_trigger
        )

        if not res or res.get("status") == "failure":
            remarks = res.get("remarks") or "Unknown failure"
            raise RuntimeError(f"Dhan SL placement failed: {remarks}")

        order_id = str(res["data"]["orderId"])
        logger.info(f"Broker-side SL order placed. Order ID: {order_id}, trigger: {rounded_trigger}, price: {limit_price}")
        return order_id
    except Exception as e:
        logger.error(f"Error placing broker-side SL order: {e}")
        raise


def modify_broker_sl(order_id: str, quantity: int, new_trigger_price: float, is_option: bool = True) -> str:
    """Modify the trigger price of a pending SL order at Dhan."""
    if not _connected or _dhan_client is None:
        raise RuntimeError("Not connected to Dhan broker")

    rounded_trigger = round(float(new_trigger_price), 1)
    if is_option:
        order_type_str = "STOP_LOSS"
        limit_price = max(0.05, round(rounded_trigger - 2.0, 1))
    else:
        order_type_str = "STOP_LOSS_MARKET"
        limit_price = 0.0

    logger.info(f"Modifying broker-side SL order {order_id} ({order_type_str}) trigger: {rounded_trigger}, price: {limit_price}")

    try:
        res = dhan_api_call(
            "order", _dhan_client.modify_order,
            order_id=order_id,
            order_type=order_type_str,
            leg_name=None,
            quantity=int(quantity),
            price=limit_price,
            trigger_price=rounded_trigger,
            disclosed_quantity=0,
            validity="DAY"
        )

        if not res or res.get("status") == "failure":
            remarks = res.get("remarks") or "Unknown failure"
            raise RuntimeError(f"Dhan SL modification failed: {remarks}")

        mod_id = str(res["data"]["orderId"])
        logger.info(f"Broker-side SL order modified. New ID: {mod_id}")
        return mod_id
    except Exception as e:
        logger.error(f"Error modifying broker-side SL order {order_id}: {e}")
        raise


def cancel_broker_sl(order_id: str):
    """Cancel a pending broker-side STOPMARKET order."""
    if not _connected or _dhan_client is None or not order_id:
        return

    logger.info(f"Cancelling broker-side SL order: {order_id}")
    try:
        res = dhan_api_call("order", _dhan_client.cancel_order, order_id)
        if not res or res.get("status") == "failure":
            remarks = res.get("remarks") or "Unknown failure"
            logger.warning(f"Failed to cancel broker SL {order_id}: {remarks}")
        else:
            logger.info(f"Broker SL {order_id} cancelled.")
    except Exception as e:
        logger.error(f"Error cancelling broker-side SL {order_id}: {e}")


# ── State Sync & Account Helpers ──────────────────────────────────────────────

def get_order_details(order_id: str) -> dict:
    try:
        res = _dhan_client.get_order_by_id(order_id)
        if isinstance(res, dict) and "data" in res and res["data"]:
            data = res["data"]
            order_data = data[0] if isinstance(data, list) else data
            avg_price = float(order_data.get("averageTradedPrice") or order_data.get("price") or 0.0)
            return {
                "order_id": order_id,
                "executed_price": avg_price,
                "status": order_data.get("orderStatus", "UNKNOWN"),
            }
        return {
            "order_id": order_id,
            "executed_price": 0.0,
            "status": "UNKNOWN",
        }
    except Exception as e:
        logger.warning(f"Could not fetch order details for {order_id}: {e}")
        return {
            "order_id": order_id,
            "executed_price": 0.0,
            "status": "ERROR",
            "error": str(e),
        }


def sync_position_from_broker(instrument: str, tracked_symbol: str = None) -> dict:
    """Check if a specific position still exists at the broker.

    Args:
        instrument: e.g. "BANKNIFTY" — used as fallback filter.
        tracked_symbol: the EXACT symbol the app is tracking (e.g.
            "BANKNIFTY 28 JUL 58300 PUT").  When provided, the function
            looks for THIS symbol only.  This prevents false
            BROKER_SL_HIT when the user has other positions on the
            same underlying.

    Returns:
        dict with has_position, direction, symbol, qty, entry_price, exchange
        or {} if position not found.
    """
    try:
        pos_df = get_positions()
        if pos_df is None or pos_df.empty:
            return {}

        filtered = pos_df.copy()
        sym_col = None
        for col in ["tradingSymbol", "trading_symbol", "symbol"]:
            if col in filtered.columns:
                sym_col = col
                break
        if sym_col is None:
            return {}

        qty_col = None
        for col in ["netQty", "net_qty", "quantity"]:
            if col in filtered.columns:
                qty_col = col
                break
        if qty_col is None:
            return {}

        filtered[qty_col] = pd.to_numeric(filtered[qty_col], errors="coerce")
        filtered = filtered[filtered[qty_col] != 0]

        if filtered.empty:
            return {}

        # Priority 1: match by exact tracked symbol
        if tracked_symbol:
            exact = filtered[filtered[sym_col].str.strip() == tracked_symbol.strip()]
            if not exact.empty:
                row = exact.iloc[0]
                net_qty = int(float(row[qty_col]))
                return {
                    "has_position": True,
                    "direction": "LONG" if net_qty > 0 else "SHORT",
                    "symbol": str(row[sym_col]).strip(),
                    "qty": abs(net_qty),
                    "entry_price": _extract_entry_price(row),
                    "exchange": _extract_exchange(row),
                }
            # Exact symbol not found — position was closed at broker
            return {}

        # Priority 2 (legacy fallback): broad instrument match
        mask = filtered[sym_col].str.contains(instrument, case=False, na=False)
        filtered = filtered[mask]
        if filtered.empty:
            return {}

        row = filtered.iloc[0]
        net_qty = int(float(row[qty_col]))
        return {
            "has_position": True,
            "direction": "LONG" if net_qty > 0 else "SHORT",
            "symbol": str(row[sym_col]).strip(),
            "qty": abs(net_qty),
            "entry_price": _extract_entry_price(row),
            "exchange": _extract_exchange(row),
        }
    except Exception as e:
        logger.error(f"sync_position_from_broker error: {e}")
        return {}


def _extract_entry_price(row) -> float:
    for col in ["buyAvg", "buy_avg", "averagePrice", "average_price"]:
        if col in row.index and row[col]:
            try:
                return float(row[col])
            except (ValueError, TypeError):
                pass
    return 0.0


def _extract_exchange(row) -> str:
    for col in ["exchangeSegment", "exchange_segment", "exchange"]:
        if col in row.index and row[col]:
            return str(row[col])
    return ""


def get_positions() -> pd.DataFrame:
    global _positions_cache, _positions_cache_time
    import pytz
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    if _positions_cache is not None and _positions_cache_time is not None:
        if (now - _positions_cache_time).total_seconds() < 5:
            return _positions_cache
    if not _connected or _dhan_client is None:
        return pd.DataFrame()
    try:
        res = dhan_api_call("user_data", _dhan_client.get_positions)
        if isinstance(res, dict) and "data" in res and res["data"]:
            df = pd.DataFrame(res["data"])
            _positions_cache = df
            _positions_cache_time = now
            return df
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"get_positions error: {e}")
        return pd.DataFrame()


def get_lot_size(instrument: str) -> int:
    global _lot_size_cache
    inst_upper = instrument.upper()
    if inst_upper in _lot_size_cache:
        return _lot_size_cache[inst_upper]

    # Prioritize official exchange lot sizes from INSTRUMENT_META
    if inst_upper in INSTRUMENT_META and "lot_size" in INSTRUMENT_META[inst_upper]:
        lot = int(INSTRUMENT_META[inst_upper]["lot_size"])
        _lot_size_cache[inst_upper] = lot
        logger.info(f"Using official exchange lot size for {inst_upper}: {lot}")
        return lot

    try:
        df = _load_instrument_df()
        cond = (
            (df["SEM_TRADING_SYMBOL"] == inst_upper) |
            df["SEM_TRADING_SYMBOL"].str.startswith(inst_upper + "-", na=False) |
            df["SEM_TRADING_SYMBOL"].str.startswith(inst_upper + " ", na=False) |
            (df["SEM_CUSTOM_SYMBOL"] == inst_upper) |
            df["SEM_CUSTOM_SYMBOL"].str.startswith(inst_upper + " ", na=False)
        )
        match = df[cond]
        deriv_types = ["OPTIDX", "FUTIDX", "OPTSTK", "FUTSTK", "OPTCOM", "FUTCOM", "OPTCUR", "FUTCUR"]
        deriv_match = match[match["SEM_INSTRUMENT_NAME"].isin(deriv_types)]
        if not deriv_match.empty:
            match = deriv_match

        if not match.empty:
            for lot in match["SEM_LOT_UNITS"].dropna().unique():
                try:
                    lot_val = int(float(lot))
                    if lot_val > 0:
                        _lot_size_cache[inst_upper] = lot_val
                        logger.info(f"Lot size for {inst_upper} resolved from CSV: {lot_val}")
                        return lot_val
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        logger.warning(f"Error fetching lot size from CSV for {inst_upper}: {e}")

    fallback = 30
    _lot_size_cache[inst_upper] = fallback
    return fallback


def get_balance() -> float:
    global _balance_cache, _balance_cache_time
    import pytz
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    if _balance_cache is not None and _balance_cache_time is not None:
        if (now - _balance_cache_time).total_seconds() < 5:
            return _balance_cache
    if not _connected or _dhan_client is None:
        return 0.0
    try:
        res = dhan_api_call("user_data", _dhan_client.get_fund_limits)
        if isinstance(res, dict) and res.get("status") != "failure" and "data" in res:
            bal = float(res["data"].get("availabelBalance", 0.0))
            _balance_cache = bal
            _balance_cache_time = now
            return bal
        return 0.0
    except Exception as e:
        logger.error(f"get_balance error: {e}")
        return 0.0


def get_live_pnl() -> float:
    try:
        pos_df = get_positions()
        if pos_df is None or pos_df.empty:
            return 0.0
        total = 0.0
        for col in ['unrealizedProfit', 'unrealized_profit', 'unrealisedProfit']:
            if col in pos_df.columns:
                total += pd.to_numeric(pos_df[col], errors='coerce').fillna(0).sum()
                break
        return round(total, 2)
    except Exception as e:
        logger.error(f"get_live_pnl error: {e}")
        return 0.0


def get_today_pnl(instrument: Optional[str] = None) -> float:
    try:
        pos_df = get_positions()
        if pos_df is None or pos_df.empty:
            return 0.0

        if instrument:
            inst_upper = instrument.upper()
            pos_df = pos_df[pos_df['tradingSymbol'].str.upper().str.contains(inst_upper, na=False)]
            if pos_df.empty:
                return 0.0

        total_pnl = 0.0
        for idx, row in pos_df.iterrows():
            realized = float(row.get('realizedProfit', 0.0) or row.get('realisedProfit', 0.0) or 0.0)
            unrealized = float(row.get('unrealizedProfit', 0.0) or row.get('unrealisedProfit', 0.0) or 0.0)
            buy_avg = float(row.get('buyAvg', 0.0) or 0.0)
            cost_price = float(row.get('costPrice', 0.0) or 0.0)
            buy_qty = int(row.get('buyQty', 0) or row.get('dayBuyQty', 0) or 0)
            net_qty = int(row.get('netQty', 0) or 0)

            if cost_price > 0 and buy_avg > 0 and buy_qty > 0:
                realized = realized - (cost_price - buy_avg) * buy_qty

            if cost_price > 0 and buy_avg > 0 and net_qty > 0:
                unrealized = unrealized - (cost_price - buy_avg) * net_qty

            total_pnl += (realized + unrealized)

        return round(total_pnl, 2)
    except Exception as e:
        logger.error(f"get_today_pnl error: {e}")
        return 0.0


def calculate_option_sl_price(
    direction: str,
    option_entry_price: float,
    index_entry_price: float,
    current_index_sl: float,
    option_symbol: str = None
) -> float:
    """
    Calculate option premium SL price based on index risk points and actual option delta.
    Falls back to a default delta of 0.6 if option symbol details cannot be resolved.
    """
    delta = 0.6  # Default fallback
    
    if option_symbol:
        try:
            df = _load_instrument_df()
            match = df[((df['SEM_CUSTOM_SYMBOL'] == option_symbol) | (df['SEM_TRADING_SYMBOL'] == option_symbol))]
            if not match.empty:
                row = match.iloc[-1]
                strike = float(row.get('SEM_STRIKE_PRICE', 0))
                opt_type = str(row.get('SEM_OPTION_TYPE', '')).upper()
                exp_date_str = str(row.get('SEM_EXPIRY_DATE', ''))
                
                if strike > 0 and opt_type in ['CE', 'PE'] and exp_date_str:
                    from datetime import datetime
                    import math
                    
                    try:
                        exp_dt = datetime.strptime(exp_date_str.split()[0], "%Y-%m-%d")
                        # Compare using timezone-aware IST date converted to naive to prevent timezone-naive comparison type errors
                        ist_tz = pytz.timezone("Asia/Kolkata")
                        today_ist = datetime.now(ist_tz).replace(tzinfo=None)
                        days = max(0.5, (exp_dt - today_ist).days + 0.5)  # min 0.5 days to avoid division by zero
                    except Exception:
                        days = 3.0
                        
                    T = days / 365.0
                    S = float(index_entry_price)
                    K = float(strike)
                    
                    # 35% volatility for commodity (CrudeOil), 16% for index options
                    sigma = 0.35 if option_symbol and "CRUDEOIL" in option_symbol.upper() else 0.16
                    r = 0.07      # 7% risk-free interest rate
                    
                    if S > 0 and K > 0:
                        d1 = (math.log(S / K) + (r + (sigma ** 2) / 2.0) * T) / (sigma * math.sqrt(T))
                        # Cumulative normal distribution function
                        cdf = (1.0 + math.erf(d1 / math.sqrt(2.0))) / 2.0
                        
                        if opt_type == "CE":
                            delta = cdf
                        else:
                            delta = abs(cdf - 1.0)
                            
                        logger.info(
                            f"Calculated actual option delta for {option_symbol}: strike={K}, "
                            f"type={opt_type}, days_to_expiry={days:.1f}, spot={S:.2f}, delta={delta:.3f}"
                        )
        except Exception as e:
            logger.warning(f"Error calculating actual option delta for {option_symbol}: {e}. Falling back to 0.6.")

    if direction == "LONG":  # Call CE (bought on index long signal)
        risk_points = index_entry_price - current_index_sl
    else:  # Put PE (bought on index short signal)
        risk_points = current_index_sl - index_entry_price

    opt_sl = option_entry_price - (risk_points * abs(delta))
    # Round to nearest 0.05 tick size (standard for options contracts on Dhan)
    tick_size = 0.05
    opt_sl_rounded = round(opt_sl / tick_size) * tick_size
    return max(0.05, round(opt_sl_rounded, 2))


def get_order_list() -> list:
    """
    Fetch all orders placed during the current day from Dhan API.
    """
    if not _connected or _dhan_client is None:
        return []
    try:
        res = _dhan_client.get_order_list()
        if isinstance(res, dict) and "data" in res:
            return res["data"] if isinstance(res["data"], list) else []
        return []
    except Exception as e:
        logger.error(f"Error fetching order list from Dhan: {e}")
        return []


def get_trade_history(from_date: str, to_date: str) -> list:
    """
    Fetch trade history for a given date range (YYYY-MM-DD) from Dhan API.
    """
    if not _connected or _dhan_client is None:
        return []
    try:
        # Fetch first page. If more pagination is needed, we loop up to 5 pages.
        all_trades = []
        for page in range(5):
            res = _dhan_client.get_trade_history(from_date=from_date, to_date=to_date, page_number=page)
            if isinstance(res, dict) and res.get("status") == "success" and "data" in res:
                data = res["data"]
                if not data or not isinstance(data, list):
                    break
                all_trades.extend(data)
                if len(data) < 100: # assuming max page size is 100
                    break
            else:
                break
        return all_trades
    except Exception as e:
        logger.error(f"Error fetching trade history from Dhan: {e}")
        return []
