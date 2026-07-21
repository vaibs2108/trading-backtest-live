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

logger = logging.getLogger(__name__)

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

    def send_telegram_alert(self, message: str, receiver_chat_id: str, bot_token: str):
        """Send telegram alert."""
        import urllib.parse
        try:
            encoded_message = urllib.parse.quote(message)
            send_text = f"https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={receiver_chat_id}&text={encoded_message}"
            response = requests.get(send_text, timeout=10)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Telegram alert send failed: {e}")


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

        match = df[(df['SEM_CUSTOM_SYMBOL'] == sym_upper) | (df['SEM_TRADING_SYMBOL'] == sym_upper)]
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
        res = _dhan_client.ticker_data(instruments)
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
            fut_sym = get_futures_symbol(instrument, expiry=0)
            ltps = _get_ltp_data([fut_sym])
            val = ltps.get(fut_sym)
            if not val:
                fut_sym_next = get_futures_symbol(instrument, expiry=1)
                logger.info(f"MCX {fut_sym} failed, trying next month: {fut_sym_next}")
                ltps = _get_ltp_data([fut_sym_next])
                val = ltps.get(fut_sym_next)
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


def get_option_ltp(symbol: str) -> float:
    """Fetch live option premium LTP directly from Dhan."""
    if not symbol:
        return 0.0
    try:
        ltps = _get_ltp_data([symbol])
        return ltps.get(symbol, 0.0)
    except Exception as e:
        logger.error(f"get_option_ltp error for {symbol}: {e}")
        return 0.0


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


def fetch_index_historical_data(
    instrument: str,
    timeframe: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None
) -> Optional[pd.DataFrame]:
    """Fallback route kept for backward compatibility; calls unified get_historical_data."""
    return get_historical_data(instrument, timeframe, from_date, to_date, use_index=True)


def get_historical_data(
    instrument: str,
    timeframe: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    use_index: bool = True,
) -> Optional[pd.DataFrame]:
    """
    Fetch historical OHLCV data from Dhan directly using dhanhq client.
    Supports daily data (historical_daily_data) and intraday timeframes 
    (intraday_minute_data with 90-day chunk limits).
    """
    global _hist_cache, _hist_cache_time
    cache_key = (instrument, timeframe, from_date, to_date, use_index)
    import pytz
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    if cache_key in _hist_cache and cache_key in _hist_cache_time:
        if (now - _hist_cache_time[cache_key]).total_seconds() < 120:
            return _hist_cache[cache_key].copy()

    if not _connected or _dhan_client is None:
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
                    match = match.sort_values(by='SEM_EXPIRY_DATE')
                    row = match.iloc[0]
                    sec_id = str(row['SEM_SMST_SECURITY_ID'])
                    exch_seg = "MCX_COMM"
                    inst_type = "FUTCOM"
                    expiry_code = int(row['SEM_EXPIRY_CODE'])
                else:
                    logger.error(f"Could not resolve FUTCOM for MCX symbol {symbol}")
                    return None
            else:
                match = df_master[
                    ((df_master['SEM_TRADING_SYMBOL'] == symbol) | (df_master['SEM_CUSTOM_SYMBOL'] == symbol)) &
                    (df_master['SEM_EXM_EXCH_ID'] == ('NSE' if exch in ['NSE', 'NFO'] else ('BSE' if exch in ['BSE', 'BFO'] else exch)))
                ]
                if match.empty:
                    logger.error(f"Could not resolve symbol {symbol} for historical data")
                    return None
                row = match.iloc[-1]
                sec_id = str(row['SEM_SMST_SECURITY_ID'])
                exch_seg = "NSE_FNO" if exch == "NFO" else ("BSE_FNO" if exch == "BFO" else ("MCX_COMM" if exch == "MCX" else "NSE_EQ"))
                inst_type = row['SEM_INSTRUMENT_NAME']
                expiry_code = int(row['SEM_EXPIRY_CODE'])

        if not sec_id:
            logger.error(f"Could not resolve security ID for {instrument}")
            return None

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
            res = _dhan_client.historical_daily_data(
                security_id=int(sec_id),
                exchange_segment=exch_seg,
                instrument_type=inst_type,
                from_date=from_dt.strftime("%Y-%m-%d"),
                to_date=to_dt.strftime("%Y-%m-%d"),
                expiry_code=int(expiry_code),
                oi=True
            )
            if isinstance(res, dict) and res.get("status") == "success" and "data" in res:
                all_dfs.append(pd.DataFrame(res["data"]))
        else:
            interval_val = int(timeframe)
            curr_from = from_dt
            while curr_from <= to_dt:
                curr_to = min(curr_from + timedelta(days=89), to_dt)
                chunk_df = None
                for attempt in range(3):
                    res = _dhan_client.intraday_minute_data(
                        security_id=str(sec_id),
                        exchange_segment=exch_seg,
                        instrument_type=inst_type,
                        from_date=curr_from.strftime("%Y-%m-%d"),
                        to_date=curr_to.strftime("%Y-%m-%d"),
                        interval=interval_val,
                        oi=False
                    )
                    if isinstance(res, dict) and res.get("status") == "success" and "data" in res and res["data"]:
                        chunk_df = pd.DataFrame(res["data"])
                        break
                    else:
                        res_str = str(res).lower()
                        is_rate_limit = any(x in res_str for x in ["dh-904", "rate_limit", "rate limit", "too many requests", "904"])
                        if is_rate_limit:
                            wait_secs = 2 ** (attempt + 1)
                            logger.warning(f"Rate limited, retry {attempt+1}/3 in {wait_secs}s")
                            _time.sleep(wait_secs)
                        else:
                            break

                if chunk_df is not None and not chunk_df.empty:
                    all_dfs.append(chunk_df)

                curr_from = curr_to + timedelta(days=1)
                _time.sleep(0.3)

        if not all_dfs:
            logger.error(f"Failed to retrieve historical data for {instrument}")
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

        _hist_cache[cache_key] = result_df
        _hist_cache_time[cache_key] = now
        return result_df.copy()
    except Exception as e:
        logger.error(f"Error in get_historical_data: {e}")
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
            inst_type = "OPTCOM"

        df_opt = df[
            (df['SEM_EXM_EXCH_ID'] == exchange) &
            (df['SEM_INSTRUMENT_NAME'] == inst_type) &
            (df['SEM_TRADING_SYMBOL'].str.startswith(instrument, na=False))
        ]

        expiries = sorted(df_opt['SEM_EXPIRY_DATE'].unique())
        if len(expiries) == 0:
            logger.error(f"No expiries found for option {instrument}")
            return None, None

        expiry_date = expiries[min(expiry, len(expiries) - 1)]
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
    """
    start_time = _time.time()
    while _time.time() - start_time < timeout:
        try:
            res = _dhan_client.get_order_by_id(order_id)
            if isinstance(res, dict) and "data" in res and res["data"]:
                data = res["data"]
                order_data = data[0] if isinstance(data, list) else data
                status = order_data.get("orderStatus")
                if status == "FILLED":
                    logger.info(f"Order {order_id} filled successfully.")
                    return order_data
                elif status in ["REJECTED", "CANCELLED"]:
                    reason = order_data.get("rejectReason") or "Order cancelled or rejected."
                    logger.error(f"Order {order_id} failed with status {status}. Reason: {reason}")
                    raise RuntimeError(f"Order failed: {status}. Reason: {reason}")
            elif isinstance(res, dict) and res.get("status") == "failure":
                remarks = res.get("remarks") or "Unknown API failure"
                logger.warning(f"API get_order_by_id returned failure for {order_id}: {remarks}")
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"Error querying status for order {order_id}: {e}")

        _time.sleep(0.5)

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
        dhan_product_type = "MARGIN" if cfg.product_type == "NRML" else "INTRA"

        # 3. Call Place Order
        res = _dhan_client.place_order(
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
    is_option = bool(re.search(r'\d+\s*(CE|PE)', symbol, re.IGNORECASE))
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
        dhan_product_type = "MARGIN" if cfg.product_type == "NRML" else "INTRA"

        res = _dhan_client.place_order(
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
        dhan_product_type = "MARGIN" if cfg.product_type == "NRML" else "INTRA"

        # Round trigger price to 1 decimal place to align with ticks
        rounded_trigger = round(float(trigger_price), 1)

        res = _dhan_client.place_order(
            security_id=security_id,
            exchange_segment=exch_seg,
            transaction_type=transaction,
            quantity=int(quantity),
            order_type="STOPMARKET",
            product_type=dhan_product_type,
            price=0.0,
            trigger_price=rounded_trigger
        )

        if not res or res.get("status") == "failure":
            remarks = res.get("remarks") or "Unknown failure"
            raise RuntimeError(f"Dhan SL placement failed: {remarks}")

        order_id = str(res["data"]["orderId"])
        logger.info(f"Broker-side SL order placed. Order ID: {order_id}, trigger: {rounded_trigger}")
        return order_id
    except Exception as e:
        logger.error(f"Error placing broker-side SL order: {e}")
        raise


def modify_broker_sl(order_id: str, quantity: int, new_trigger_price: float) -> str:
    """Modify the trigger price of a pending STOPMARKET SL order at Dhan."""
    if not _connected or _dhan_client is None:
        raise RuntimeError("Not connected to Dhan broker")

    rounded_trigger = round(float(new_trigger_price), 1)
    logger.info(f"Modifying broker-side SL order {order_id} to new trigger: {rounded_trigger}")

    try:
        res = _dhan_client.modify_order(
            order_id=order_id,
            order_type="STOPMARKET",
            leg_name=None,
            quantity=int(quantity),
            price=0.0,
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
        res = _dhan_client.cancel_order(order_id)
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
        res = _dhan_client.get_positions()
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
    if instrument in _lot_size_cache:
        return _lot_size_cache[instrument]
    try:
        df = _load_instrument_df()
        cond = (
            (df["SEM_TRADING_SYMBOL"] == instrument) |
            df["SEM_TRADING_SYMBOL"].str.startswith(instrument + "-", na=False) |
            df["SEM_TRADING_SYMBOL"].str.startswith(instrument + " ", na=False) |
            (df["SEM_CUSTOM_SYMBOL"] == instrument) |
            df["SEM_CUSTOM_SYMBOL"].str.startswith(instrument + " ", na=False)
        )
        match = df[cond]
        deriv_match = match[match["SEM_INSTRUMENT_NAME"] != "INDEX"]
        if not deriv_match.empty:
            match = deriv_match

        if not match.empty:
            for lot in match["SEM_LOT_UNITS"].unique():
                if lot and int(lot) > 0:
                    _lot_size_cache[instrument] = int(lot)
                    logger.info(f"Lot size for {instrument} resolved from CSV: {lot}")
                    return int(lot)
    except Exception as e:
        logger.warning(f"Error fetching lot size from CSV for {instrument}: {e}")

    fallback = INSTRUMENT_META.get(instrument.upper(), {}).get("lot_size", 30)
    logger.warning(f"Using fallback lot size for {instrument}: {fallback}")
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
        res = _dhan_client.get_fund_limits()
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
