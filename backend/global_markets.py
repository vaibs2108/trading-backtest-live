"""
Global Markets Data & Sentiment Module
──────────────────────────────────────
Fetches global index/futures data, news sentiment, and market analysis.
All data is cached to avoid rate limiting.
Uses yfinance for international markets, Dhan API for Indian data (VIX, expiry).
"""
import time
import logging
import threading
import re
from datetime import datetime, timezone, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache ────────────────────────────────────────────────────────────────────
_cache = {}
_cache_lock = threading.Lock()

def _get_cached(key: str, max_age_seconds: int = 300):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["ts"]) < max_age_seconds:
            return entry["data"]
    return None

def _set_cached(key: str, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}

def clear_all_cache():
    """Clear all cached data — called by refresh endpoint."""
    with _cache_lock:
        _cache.clear()


# ── Global Market Indices ────────────────────────────────────────────────────
GLOBAL_TICKERS = {
    # US Futures (live during Indian market hours 9:15-15:30 IST)
    "YM=F":   {"name": "Dow Jones Futures",   "region": "US",     "type": "futures", "unit": "pts"},
    "NQ=F":   {"name": "Nasdaq 100 Futures",  "region": "US",     "type": "futures", "unit": "pts"},
    "ES=F":   {"name": "S&P 500 Futures",     "region": "US",     "type": "futures", "unit": "pts"},
    # Asian
    "^N225":  {"name": "Nikkei 225",          "region": "Asia",   "type": "index",   "unit": "pts"},
    "^HSI":   {"name": "Hang Seng",           "region": "Asia",   "type": "index",   "unit": "pts"},
    # European
    "^GDAXI": {"name": "DAX (Germany)",       "region": "Europe", "type": "index",   "unit": "pts"},
    "^FTSE":  {"name": "FTSE 100 (UK)",       "region": "Europe", "type": "index",   "unit": "pts"},
    # India
    "^NSEI":  {"name": "Nifty 50",            "region": "India",  "type": "index",   "unit": "pts"},
    # Commodities — prices in USD
    "GC=F":   {"name": "Gold",                "region": "Commodities", "type": "futures", "unit": "USD"},
    "CL=F":   {"name": "Crude Oil (WTI)",     "region": "Commodities", "type": "futures", "unit": "USD"},
    "SI=F":   {"name": "Silver",              "region": "Commodities", "type": "futures", "unit": "USD"},
}

# ── Dhan Index OHLC Helper ──────────────────────────────────────────────────
# Security IDs for Indian indices on Dhan (IDX_I segment)
DHAN_INDEX_IDS = {
    "NIFTY": 13,
    "BANKNIFTY": 25,
    "GIFTNIFTY": 5024,
    "INDIA VIX": 21,
    "FINNIFTY": 27,
    "MIDCPNIFTY": 442,
}


def _fetch_dhan_index_ohlc(symbols: list) -> dict:
    """
    Fetch OHLC + LTP data from Dhan for Indian indices.
    Returns: { symbol: { ltp, open, high, low, close (prev_close) } }
    """
    try:
        import broker
        if not broker.is_connected() or broker._dhan_client is None:
            return {}

        sec_ids = []
        id_map = {}
        for sym in symbols:
            sid = DHAN_INDEX_IDS.get(sym.upper())
            if sid:
                sec_ids.append(sid)
                id_map[str(sid)] = sym

        if not sec_ids:
            return {}

        # Use ohlc_data endpoint for OHLC + LTP
        res = broker._dhan_client.ohlc_data({"IDX_I": sec_ids})

        result = {}
        if isinstance(res, dict) and res.get("status") == "success":
            inner = res.get("data", {}).get("data", {})
            for seg, sec_dict in inner.items():
                for sec_id, quotes in sec_dict.items():
                    if sec_id in id_map:
                        sym = id_map[sec_id]
                        ohlc = quotes.get("ohlc", {}) or {}
                        result[sym] = {
                            "ltp": float(quotes.get("last_price", 0.0)),
                            "open": float(ohlc.get("open", 0.0) or quotes.get("open", 0.0)),
                            "high": float(ohlc.get("high", 0.0) or quotes.get("high", 0.0)),
                            "low": float(ohlc.get("low", 0.0) or quotes.get("low", 0.0)),
                            "close": float(ohlc.get("close", 0.0) or quotes.get("close", 0.0)),  # prev day close
                        }
        return result

    except Exception as e:
        logger.warning(f"Dhan index OHLC fetch failed: {e}")
        return {}


def fetch_global_markets() -> dict:
    """Fetch global market data using yfinance. Cached for 1 minute."""
    cached = _get_cached("global_markets", 60)
    if cached:
        return cached

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed. Run: pip install yfinance")
        return {"error": "yfinance not installed", "markets": [], "timestamp": datetime.now(timezone.utc).isoformat()}

    markets = []
    try:
        tickers_str = " ".join(GLOBAL_TICKERS.keys())
        data = yf.download(tickers_str, period="5d", interval="1d", group_by="ticker", progress=False, threads=False)

        for symbol, info in GLOBAL_TICKERS.items():
            try:
                if len(GLOBAL_TICKERS) == 1:
                    ticker_data = data
                else:
                    ticker_data = data[symbol] if symbol in data.columns.get_level_values(0) else None

                if ticker_data is None or ticker_data.empty:
                    markets.append({
                        "symbol": symbol, **info,
                        "price": None, "change": None, "change_pct": None,
                        "prev_close": None, "status": "unavailable"
                    })
                    continue

                ticker_data = ticker_data.dropna(subset=["Close"])
                if ticker_data.empty:
                    markets.append({
                        "symbol": symbol, **info,
                        "price": None, "change": None, "change_pct": None,
                        "prev_close": None, "status": "unavailable"
                    })
                    continue

                current = float(ticker_data["Close"].iloc[-1])
                prev_close = float(ticker_data["Close"].iloc[-2]) if len(ticker_data) >= 2 else current
                change = round(current - prev_close, 2)
                change_pct = round((change / prev_close) * 100, 2) if prev_close else 0

                markets.append({
                    "symbol": symbol, **info,
                    "price": round(current, 2),
                    "change": change,
                    "change_pct": change_pct,
                    "prev_close": round(prev_close, 2),
                    "status": "ok"
                })
            except Exception as e:
                logger.warning(f"Failed to parse {symbol}: {e}")
                markets.append({
                    "symbol": symbol, **info,
                    "price": None, "change": None, "change_pct": None,
                    "prev_close": None, "status": "error"
                })

    except Exception as e:
        logger.error(f"yfinance download failed: {e}")
        return {"error": str(e), "markets": [], "timestamp": datetime.now(timezone.utc).isoformat()}

    # ── GIFT Nifty — from Dhan API only (no proxy) ──
    gift_data = _fetch_dhan_index_ohlc(["GIFTNIFTY"])
    gift = gift_data.get("GIFTNIFTY")
    if gift and gift["ltp"] > 0:
        prev_close = None
        try:
            import broker
            if broker.is_connected() and broker._dhan_client is not None:
                import pytz
                now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
                from_d = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")
                to_d   = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
                res = broker._dhan_client.historical_daily_data(
                    security_id=5024,
                    exchange_segment="IDX_I",
                    instrument_type="INDEX",
                    from_date=from_d,
                    to_date=to_d,
                    expiry_code=0
                )
                if isinstance(res, dict) and res.get("status") == "success" and res.get("data"):
                    closes = res["data"].get("close", [])
                    timestamps = res["data"].get("timestamp", [])
                    if closes:
                        last_ts = timestamps[-1]
                        last_date = datetime.fromtimestamp(last_ts, pytz.timezone("Asia/Kolkata")).date()
                        if last_date == now_ist.date() and len(closes) >= 2:
                            prev_close = float(closes[-2])
                        else:
                            prev_close = float(closes[-1])
        except Exception as ex:
            logger.warning(f"Failed to fetch GIFT Nifty historical prev close: {ex}")

        if not prev_close:
            prev_close = gift["close"] if gift["close"] > 0 else None

        change = round(gift["ltp"] - prev_close, 2) if prev_close else None
        change_pct = round((change / prev_close) * 100, 2) if prev_close and prev_close > 0 else None
        gift_nifty = {
            "symbol": "GIFTNIFTY",
            "name": "GIFT Nifty",
            "region": "India",
            "type": "futures",
            "unit": "pts",
            "price": round(gift["ltp"], 2),
            "change": change,
            "change_pct": change_pct,
            "prev_close": round(prev_close, 2) if prev_close else None,
            "open": round(gift["open"], 2) if gift["open"] > 0 else None,
            "high": round(gift["high"], 2) if gift["high"] > 0 else None,
            "low": round(gift["low"], 2) if gift["low"] > 0 else None,
            "status": "ok",
            "source": "Dhan API"
        }
        nifty_idx = next((i for i, m in enumerate(markets) if m["symbol"] == "^NSEI"), len(markets))
        markets.insert(nifty_idx + 1, gift_nifty)
    else:
        # Fallback: fetch last known daily close from Dhan historical data
        gift_fallback = None
        try:
            import broker
            if broker.is_connected() and broker._dhan_client is not None:
                import pytz
                now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
                from_d = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")
                to_d   = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
                res = broker._dhan_client.historical_daily_data(
                    security_id=5024,  # GIFTNIFTY index
                    exchange_segment="IDX_I",
                    instrument_type="INDEX",
                    from_date=from_d,
                    to_date=to_d,
                    expiry_code=0
                )
                if isinstance(res, dict) and res.get("status") == "success" and res.get("data"):
                    import pandas as pd
                    df = pd.DataFrame(res["data"])
                    if not df.empty:
                        last_row = df.iloc[-1]
                        price = float(last_row.get("close", 0))
                        prev_close = float(df.iloc[-2]["close"]) if len(df) >= 2 else price
                        if price > 0:
                            change = round(price - prev_close, 2) if prev_close > 0 else 0
                            change_pct = round((change / prev_close) * 100, 2) if prev_close > 0 else 0
                            gift_fallback = {
                                "symbol": "GIFTNIFTY", "name": "GIFT Nifty", "region": "India",
                                "type": "futures", "unit": "pts",
                                "price": round(price, 2),
                                "change": change, "change_pct": change_pct,
                                "prev_close": round(prev_close, 2) if prev_close > 0 else None,
                                "open": None, "high": None, "low": None,
                                "status": "ok", "source": "Dhan (Last Close)"
                            }
                            logger.info(f"GIFT Nifty from daily historical fallback: {price}")
        except Exception as ex:
            logger.warning(f"GIFT Nifty historical fallback failed: {ex}")

        nifty_idx = next((i for i, m in enumerate(markets) if m["symbol"] == "^NSEI"), len(markets))
        if gift_fallback:
            markets.insert(nifty_idx + 1, gift_fallback)
        else:
            markets.insert(nifty_idx + 1, {
                "symbol": "GIFTNIFTY", "name": "GIFT Nifty", "region": "India",
                "type": "futures", "unit": "pts", "price": None, "change": None,
                "change_pct": None, "prev_close": None, "status": "unavailable",
                "note": "Connect to Dhan for live GIFT Nifty"
            })

    result = {
        "markets": markets,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": None
    }
    _set_cached("global_markets", result)
    return result


def _fetch_gift_nifty() -> Optional[dict]:
    """Legacy — GIFT Nifty now fetched from Dhan API in fetch_global_markets()."""
    return None


# ── India VIX (via Dhan API) ────────────────────────────────────────────────
def fetch_india_vix() -> dict:
    """Fetch India VIX from Dhan API using IDX_I segment. Cached 2 min."""
    cached = _get_cached("india_vix", 120)
    if cached:
        return cached

    result = {
        "value": None,
        "interpretation": "unavailable",
        "level": "unknown",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Fetch from Dhan API (primary source for all Indian data)
    vix_val = None
    vix_data = _fetch_dhan_index_ohlc(["INDIA VIX"])
    vix_entry = vix_data.get("INDIA VIX")
    if vix_entry and vix_entry["ltp"] > 0:
        vix_val = vix_entry["ltp"]
        logger.info(f"VIX from Dhan API: {vix_val}")

    if not vix_val or vix_val <= 0:
        try:
            import broker
            import pytz
            now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
            from_d = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")
            to_d   = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
            df = broker.get_historical_data("INDIA VIX", "DAY", from_d, to_d, use_index=True)
            if df is not None and not df.empty:
                vix_val = float(df.iloc[-1]['close'])
                logger.info(f"VIX from daily historical fallback: {vix_val}")
        except Exception as ex:
            logger.warning(f"VIX fallback failed: {ex}")


    try:
        if vix_val and vix_val > 0:
            vix_val = round(vix_val, 2)
            # Interpretation based on VIX levels
            if vix_val < 12:
                level = "very_low"
                interpretation = "Very low volatility — markets calm, complacency zone. Trend trades work well."
            elif vix_val < 15:
                level = "low"
                interpretation = "Low volatility — stable conditions, good for directional trades."
            elif vix_val < 20:
                level = "moderate"
                interpretation = "Moderate volatility — normal market conditions. Standard risk management."
            elif vix_val < 25:
                level = "elevated"
                interpretation = "Elevated volatility — trade cautiously, tighten stop losses, reduce position size."
            elif vix_val < 30:
                level = "high"
                interpretation = "High volatility — significant uncertainty. Use wider stops, smaller lots. Option premiums are rich."
            else:
                level = "extreme"
                interpretation = "Extreme volatility — panic/crisis zone. Very wide swings expected. Trade small or sit out."

            result = {
                "value": vix_val,
                "interpretation": interpretation,
                "level": level,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        else:
            result["error"] = "Could not fetch VIX value"

    except Exception as e:
        logger.warning(f"India VIX fetch failed: {e}")
        result["error"] = str(e)

    _set_cached("india_vix", result)
    return result


# ── Expiry Today (via Dhan API) ─────────────────────────────────────────────
# Dhan security IDs and exchange segments for expiry lookups
EXPIRY_INSTRUMENTS = {
    "NIFTY": {
        "security_id": 13,
        "exchange": "NSE",
        "exchange_segment": "NSE_FNO",
        "instrument_type": "OPTIDX",
    },
    "BANKNIFTY": {
        "security_id": 25,
        "exchange": "NSE",
        "exchange_segment": "NSE_FNO",
        "instrument_type": "OPTIDX",
    },
    "FINNIFTY": {
        "security_id": 27,
        "exchange": "NSE",
        "exchange_segment": "NSE_FNO",
        "instrument_type": "OPTIDX",
    },
    "MIDCPNIFTY": {
        "security_id": 442,
        "exchange": "NSE",
        "exchange_segment": "NSE_FNO",
        "instrument_type": "OPTIDX",
    },
    "SENSEX": {
        "security_id": 51,
        "exchange": "BSE",
        "exchange_segment": "BSE_FNO",
        "instrument_type": "OPTIDX",
    },
    "BANKEX": {
        "security_id": 69,
        "exchange": "BSE",
        "exchange_segment": "BSE_FNO",
        "instrument_type": "OPTIDX",
    },
}


def fetch_expiry_today() -> dict:
    """Check which instruments have expiry today using Dhan API. Cached 30 min."""
    cached = _get_cached("expiry_today", 1800)
    if cached:
        return cached

    today_str = date.today().strftime("%Y-%m-%d")
    result = {
        "today": today_str,
        "expiries": [],
        "has_expiry": False,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    try:
        import broker
        if not broker.is_connected():
            result["error"] = "Dhan not connected"
            _set_cached("expiry_today", result)
            return result

        client = broker._dhan_client

        for instrument, meta in EXPIRY_INSTRUMENTS.items():
            try:
                resp = client.expiry_list(
                    under_security_id=meta["security_id"],
                    under_exchange_segment=meta["exchange_segment"]
                )

                if not isinstance(resp, dict) or resp.get("status") == "failure":
                    continue

                expiry_dates = resp.get("data", [])
                if not expiry_dates:
                    continue

                # Check if today matches any expiry
                for exp_date in expiry_dates:
                    # Dhan returns dates in various formats, normalize
                    exp_str = str(exp_date).strip()
                    # Try matching YYYY-MM-DD
                    if today_str in exp_str:
                        # Determine if weekly or monthly
                        # Monthly = last Thursday of month (roughly)
                        # We'll classify: if it's the last expiry in the month, call it monthly
                        same_month_expiries = [d for d in expiry_dates if str(d).startswith(today_str[:7])]
                        is_monthly = (exp_str == str(sorted(same_month_expiries)[-1])) if same_month_expiries else False

                        expiry_type = "Monthly" if is_monthly else "Weekly"
                        result["expiries"].append({
                            "instrument": instrument,
                            "type": expiry_type,
                            "date": today_str,
                            "label": f"{instrument} {expiry_type} Expiry"
                        })
                        result["has_expiry"] = True
                        break

            except Exception as e:
                logger.warning(f"Expiry check failed for {instrument}: {e}")
                continue

    except Exception as e:
        logger.warning(f"Expiry today fetch failed: {e}")
        result["error"] = str(e)

    _set_cached("expiry_today", result)
    return result


# ── Global Market Analysis ───────────────────────────────────────────────────
def analyze_global_bias(markets_data: Optional[dict] = None) -> dict:
    """Rules-based global market bias analysis."""
    cached = _get_cached("global_bias", 300)
    if cached:
        return cached

    if not markets_data:
        markets_data = fetch_global_markets()

    markets = markets_data.get("markets", [])
    if not markets:
        return {"bias": "neutral", "score": 0, "rationale": ["No market data available"], "details": {}}

    up_count = 0
    down_count = 0
    total_valid = 0
    us_bias = 0
    asia_bias = 0
    europe_bias = 0
    rationale = []

    for m in markets:
        if m.get("status") != "ok" or m.get("change_pct") is None:
            continue
        # Skip commodities from bias calculation (they have their own dynamics)
        if m.get("region") == "Commodities":
            continue
        total_valid += 1
        pct = m["change_pct"]

        if pct > 0:
            up_count += 1
        elif pct < 0:
            down_count += 1

        region = m.get("region", "")
        if region == "US":
            us_bias += pct
        elif region == "Asia":
            asia_bias += pct
        elif region == "Europe":
            europe_bias += pct

    if total_valid == 0:
        return {"bias": "neutral", "score": 0, "rationale": ["No valid market data"], "details": {}}

    us_count = sum(1 for m in markets if m.get("region") == "US" and m.get("status") == "ok")
    asia_count = sum(1 for m in markets if m.get("region") == "Asia" and m.get("status") == "ok")
    europe_count = sum(1 for m in markets if m.get("region") == "Europe" and m.get("status") == "ok")

    us_avg = round(us_bias / us_count, 2) if us_count else 0
    asia_avg = round(asia_bias / asia_count, 2) if asia_count else 0
    europe_avg = round(europe_bias / europe_count, 2) if europe_count else 0

    # US gets 50% weight, Asia 25%, Europe 25%
    score = (us_avg * 50 + asia_avg * 25 + europe_avg * 25) / 100
    normalized_score = max(-100, min(100, round(score / 3 * 100)))

    if normalized_score >= 30:
        bias = "bullish"
    elif normalized_score >= 10:
        bias = "mildly_bullish"
    elif normalized_score <= -30:
        bias = "bearish"
    elif normalized_score <= -10:
        bias = "mildly_bearish"
    else:
        bias = "neutral"

    if us_avg > 0.3:
        rationale.append(f"US futures positive ({us_avg:+.2f}%), supporting bullish sentiment")
    elif us_avg < -0.3:
        rationale.append(f"US futures negative ({us_avg:+.2f}%), adding bearish pressure")
    else:
        rationale.append(f"US futures flat ({us_avg:+.2f}%), neutral signal")

    if asia_avg > 0.3:
        rationale.append(f"Asian markets trending up ({asia_avg:+.2f}%), positive cue for India")
    elif asia_avg < -0.3:
        rationale.append(f"Asian markets down ({asia_avg:+.2f}%), cautious signal")

    if europe_avg > 0.3:
        rationale.append(f"European markets green ({europe_avg:+.2f}%)")
    elif europe_avg < -0.3:
        rationale.append(f"European markets red ({europe_avg:+.2f}%)")

    rationale.append(f"{up_count}/{total_valid} global indices positive")

    details = {
        "us_avg": us_avg, "asia_avg": asia_avg, "europe_avg": europe_avg,
        "up_count": up_count, "down_count": down_count, "total": total_valid
    }

    result = {
        "bias": bias,
        "score": normalized_score,
        "rationale": rationale,
        "details": details,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    _set_cached("global_bias", result)
    return result


# ── Weighted Keyword Sentiment ──────────────────────────────────────────────
# Each word has a weight: high-impact words score more
BULLISH_WEIGHTED = {
    # Weight 3 — strong signals
    "rally": 3, "surge": 3, "soar": 3, "breakout": 3, "boom": 3,
    # Weight 2 — moderate signals
    "gain": 2, "rise": 2, "bullish": 2, "recovery": 2, "outperform": 2,
    "jump": 2, "advance": 2, "climb": 2, "record": 2, "beat": 2,
    # Weight 1 — mild signals
    "up": 1, "high": 1, "positive": 1, "growth": 1, "strong": 1,
    "buy": 1, "upgrade": 1, "momentum": 1, "profit": 1, "exceed": 1,
    "optimism": 1, "bull": 1,
}

BEARISH_WEIGHTED = {
    # Weight 3 — strong signals
    "crash": 3, "plunge": 3, "collapse": 3, "meltdown": 3, "panic": 3,
    "crisis": 3, "recession": 3,
    # Weight 2 — moderate signals
    "drop": 2, "fall": 2, "bearish": 2, "tumble": 2, "sink": 2,
    "slump": 2, "plummet": 2, "sell-off": 2, "selloff": 2, "decline": 2,
    # Weight 1 — mild signals
    "down": 1, "low": 1, "negative": 1, "weak": 1, "fear": 1,
    "sell": 1, "downgrade": 1, "risk": 1, "loss": 1, "miss": 1,
    "concern": 1, "warning": 1, "threat": 1, "volatile": 1,
    "uncertainty": 1, "inflation": 1, "tariff": 1, "bear": 1,
}

# Negation words that flip sentiment of the next keyword
NEGATION_WORDS = {"not", "no", "never", "neither", "nor", "don't", "doesn't",
                  "didn't", "won't", "wouldn't", "shouldn't", "couldn't",
                  "hardly", "barely", "despite", "without", "fails"}

# Context amplifiers / dampeners
AMPLIFIERS = {"very", "extremely", "sharply", "massive", "huge", "major", "significantly"}
DAMPENERS = {"slightly", "marginally", "somewhat", "modest", "minor", "little"}

RSS_FEEDS = [
    # Indian market news
    ("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "Economic Times"),
    ("https://www.moneycontrol.com/rss/marketreports.xml", "MoneyControl"),
    ("https://news.google.com/rss/search?q=indian+stock+market+nifty+banknifty&hl=en-IN&gl=IN&ceid=IN:en", "Google News India"),
    # Global macro / geopolitics
    ("https://news.google.com/rss/search?q=global+markets+stocks+economy&hl=en&gl=US&ceid=US:en", "Google Global Markets"),
    ("https://news.google.com/rss/search?q=war+geopolitics+trade+tariff+sanctions&hl=en&gl=US&ceid=US:en", "Google Geopolitics"),
    # Commodities (oil, gold, silver)
    ("https://news.google.com/rss/search?q=crude+oil+price+gold+silver+commodity&hl=en&gl=US&ceid=US:en", "Google Commodities"),
]


def _score_headline(title: str) -> dict:
    """Score a single headline using weighted keywords, negation detection, and context scoring."""
    words = re.findall(r"[a-z'-]+", title.lower())
    bull_score = 0.0
    bear_score = 0.0
    bull_hits = []
    bear_hits = []

    i = 0
    while i < len(words):
        word = words[i]

        # Check for negation window (next 2 words)
        is_negated = False
        if i > 0 and words[i - 1] in NEGATION_WORDS:
            is_negated = True
        if i > 1 and words[i - 2] in NEGATION_WORDS:
            is_negated = True

        # Check for amplifier/dampener (preceding word)
        multiplier = 1.0
        if i > 0:
            prev = words[i - 1]
            if prev in AMPLIFIERS:
                multiplier = 1.5
            elif prev in DAMPENERS:
                multiplier = 0.5

        if word in BULLISH_WEIGHTED:
            weight = BULLISH_WEIGHTED[word] * multiplier
            if is_negated:
                bear_score += weight
                bear_hits.append(f"not-{word}")
            else:
                bull_score += weight
                bull_hits.append(word)

        elif word in BEARISH_WEIGHTED:
            weight = BEARISH_WEIGHTED[word] * multiplier
            if is_negated:
                bull_score += weight
                bull_hits.append(f"not-{word}")
            else:
                bear_score += weight
                bear_hits.append(word)

        i += 1

    net = bull_score - bear_score
    if net > 0:
        sentiment = "bullish"
    elif net < 0:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    return {
        "bull_score": bull_score,
        "bear_score": bear_score,
        "net_score": net,
        "sentiment": sentiment,
        "bull_hits": bull_hits,
        "bear_hits": bear_hits,
    }


def fetch_news_sentiment() -> dict:
    """Fetch and analyze news headlines with weighted keyword scoring."""
    cached = _get_cached("news_sentiment", 600)
    if cached:
        return cached

    import xml.etree.ElementTree as ET
    import requests

    headlines = []
    total_bull = 0.0
    total_bear = 0.0
    total_scored = 0

    for feed_url, source in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, timeout=8, headers={
                "User-Agent": "Mozilla/5.0 (compatible; AlgoTrader/1.0)"
            })
            if resp.status_code != 200:
                continue

            root = ET.fromstring(resp.content)
            items = root.findall(".//item")[:10]

            for item in items:
                title_el = item.find("title")
                pub_el = item.find("pubDate")
                if title_el is None:
                    continue

                title = title_el.text or ""
                title_clean = re.sub(r"<[^>]+>", "", title).strip()
                if not title_clean:
                    continue

                scored = _score_headline(title_clean)
                bull_s = scored["bull_score"]
                bear_s = scored["bear_score"]

                if bull_s > 0 or bear_s > 0:
                    total_scored += 1
                    total_bull += bull_s
                    total_bear += bear_s

                headlines.append({
                    "title": title_clean[:120],
                    "source": source,
                    "sentiment": scored["sentiment"],
                    "score": round(scored["net_score"], 1),
                    "pub_date": pub_el.text if pub_el is not None else None
                })

        except Exception as e:
            logger.warning(f"RSS feed {source} failed: {e}")
            continue

    # Overall news sentiment score: -100 to +100
    total_keywords = total_bull + total_bear
    if total_keywords == 0:
        overall = "neutral"
        news_score = 0
    else:
        net = total_bull - total_bear
        news_score = max(-100, min(100, round(net / total_keywords * 100)))
        if news_score >= 25:
            overall = "bullish"
        elif news_score <= -25:
            overall = "bearish"
        else:
            overall = "neutral"

    # Categorize headlines by topic
    headline_sources = {}
    for h in headlines:
        src = h.get("source", "Unknown")
        headline_sources[src] = headline_sources.get(src, 0) + 1

    result = {
        "overall": overall,
        "score": news_score,
        "bull_count": round(total_bull, 1),
        "bear_count": round(total_bear, 1),
        "total_headlines": len(headlines),
        "scored_headlines": total_scored,
        "calculation": f"Weighted: ({total_bull:.1f} bull - {total_bear:.1f} bear) / ({total_bull:.1f} + {total_bear:.1f}) × 100 = {news_score}",
        "method": "Weighted keywords (crash=3, rally=3, drop=2, gain=2, up=1) with negation detection & context scoring",
        "refresh_seconds": 600,
        "headlines": sorted(headlines, key=lambda h: abs(h.get("score", 0)), reverse=True)[:25],
        "sources": headline_sources,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    _set_cached("news_sentiment", result)
    return result


# ── Fear & Greed (CNN) ──────────────────────────────────────────────────────
def _parse_fg_score(score_val):
    """Convert raw Fear & Greed score to label."""
    score = round(float(score_val))
    if score >= 75:
        label = "Extreme Greed"
    elif score >= 55:
        label = "Greed"
    elif score >= 45:
        label = "Neutral"
    elif score >= 25:
        label = "Fear"
    else:
        label = "Extreme Fear"
    return score, label


def fetch_fear_greed() -> dict:
    """Fetch CNN Fear & Greed Index with multiple fallback sources."""
    cached = _get_cached("fear_greed", 900)
    if cached:
        return cached

    import requests

    result = {"score": None, "label": "unavailable", "refresh_seconds": 900, "timestamp": datetime.now(timezone.utc).isoformat()}

    # Source 1: CNN production API (primary)
    try:
        resp = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://edition.cnn.com/markets/fear-and-greed",
            },
            verify=True,
        )
        logger.info(f"Fear & Greed CNN API response: status={resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            fg = data.get("fear_and_greed", {})
            raw_score = fg.get("score")
            if raw_score is not None:
                score, label = _parse_fg_score(raw_score)
                result = {
                    "score": score,
                    "label": label,
                    "previous_close": round(float(fg.get("previous_close", score))),
                    "source": "CNN",
                    "refresh_seconds": 900,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                _set_cached("fear_greed", result)
                return result
        else:
            logger.warning(f"Fear & Greed CNN API returned status {resp.status_code}: {resp.text[:200]}")
    except requests.exceptions.SSLError as e:
        logger.warning(f"Fear & Greed SSL error (try verify=False): {e}")
        # Retry without SSL verification (some corporate proxies intercept)
        try:
            resp = requests.get(
                "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                verify=False,
            )
            if resp.status_code == 200:
                data = resp.json()
                fg = data.get("fear_and_greed", {})
                raw_score = fg.get("score")
                if raw_score is not None:
                    score, label = _parse_fg_score(raw_score)
                    result = {
                        "score": score, "label": label,
                        "previous_close": round(float(fg.get("previous_close", score))),
                        "source": "CNN (no-verify)",
                        "refresh_seconds": 900,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                    _set_cached("fear_greed", result)
                    return result
        except Exception as e2:
            logger.warning(f"Fear & Greed no-verify fallback also failed: {e2}")
    except Exception as e:
        logger.warning(f"Fear & Greed primary fetch failed: {e}")

    # Source 2: Alternative API (api.alternative.me — crypto-focused but widely available)
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", [])
            if items:
                raw = int(items[0].get("value", 0))
                score, label = _parse_fg_score(raw)
                result = {
                    "score": score, "label": label,
                    "previous_close": None,
                    "source": "alternative.me (crypto F&G)",
                    "refresh_seconds": 900,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                logger.info(f"Fear & Greed from alternative.me: {score}")
    except Exception as e:
        logger.warning(f"Fear & Greed alternative source also failed: {e}")

    _set_cached("fear_greed", result)
    return result


# ── Composite Master Score ──────────────────────────────────────────────────
def compute_composite_score(news_sentiment: dict, fear_greed: dict,
                            global_bias: dict, india_vix: dict) -> dict:
    """
    Multi-factor composite sentiment score blending:
      - News Sentiment (30% weight)
      - CNN Fear & Greed (25% weight)
      - Global Market Bias (25% weight)
      - India VIX inverse (20% weight) — high VIX = bearish pressure

    Output: -100 (extremely bearish) to +100 (extremely bullish)
    """
    components = {}
    weights = {}
    total_weight = 0

    # 1. News Sentiment Score (-100 to +100)
    news_score = news_sentiment.get("score")
    if news_score is not None:
        components["news"] = {"score": news_score, "weight": 30, "label": "News Sentiment"}
        weights["news"] = 30
        total_weight += 30

    # 2. Fear & Greed (0–100, map to -100 to +100)
    fg_score = fear_greed.get("score")
    if fg_score is not None:
        # 0→-100, 50→0, 100→+100
        fg_mapped = (fg_score - 50) * 2
        components["fear_greed"] = {"score": fg_mapped, "raw": fg_score, "weight": 25, "label": "Fear & Greed"}
        weights["fear_greed"] = 25
        total_weight += 25

    # 3. Global Bias Score (-100 to +100)
    bias_score = global_bias.get("score")
    if bias_score is not None:
        components["global_bias"] = {"score": bias_score, "weight": 25, "label": "Global Bias"}
        weights["global_bias"] = 25
        total_weight += 25

    # 4. India VIX inverse — high VIX = bearish pressure
    vix_val = india_vix.get("value")
    if vix_val is not None and vix_val > 0:
        # VIX 10→+80, 15→+40, 20→0, 25→-40, 30→-80, 35+→-100
        vix_score = max(-100, min(100, round((20 - vix_val) * 8)))
        components["vix"] = {"score": vix_score, "raw": vix_val, "weight": 20, "label": "VIX Pressure"}
        weights["vix"] = 20
        total_weight += 20

    if total_weight == 0:
        return {
            "composite_score": 0,
            "label": "unavailable",
            "components": {},
            "explanation": "No data available for composite calculation"
        }

    # Weighted average
    weighted_sum = sum(components[k]["score"] * weights[k] for k in components)
    composite = round(weighted_sum / total_weight)
    composite = max(-100, min(100, composite))

    # Label
    if composite >= 50:
        label = "Strong Bullish"
    elif composite >= 20:
        label = "Bullish"
    elif composite >= 5:
        label = "Mildly Bullish"
    elif composite >= -5:
        label = "Neutral"
    elif composite >= -20:
        label = "Mildly Bearish"
    elif composite >= -50:
        label = "Bearish"
    else:
        label = "Strong Bearish"

    # Build explanation
    parts = []
    for k, c in components.items():
        parts.append(f"{c['label']}: {c['score']:+d} × {c['weight']}%")
    explanation = " + ".join(parts) + f" = {composite}"

    return {
        "composite_score": composite,
        "label": label,
        "components": components,
        "explanation": explanation,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ── Nifty / BankNifty Correlation & Analysis ─────────────────────────────────
def fetch_nifty_banknifty_correlation(markets_data: Optional[dict] = None) -> dict:
    """
    Analyze Nifty vs BankNifty correlation and relative strength.
    Uses Dhan API for all Indian index data (OHLC + LTP).
    """
    cached = _get_cached("nifty_bn_corr", 120)
    if cached:
        return cached

    result = {
        "nifty": {"price": None, "change": None, "change_pct": None},
        "banknifty": {"price": None, "change": None, "change_pct": None},
        "direction": "unknown",
        "alignment": "unknown",
        "relative_strength": None,
        "beta": None,
        "analysis": [],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Fetch OHLC + LTP from Dhan for both indices in one call
    dhan_data = _fetch_dhan_index_ohlc(["NIFTY", "BANKNIFTY"])

    nifty_d = dhan_data.get("NIFTY")
    bn_d = dhan_data.get("BANKNIFTY")

    # Fallback to historical daily candles if live data is not coming (e.g. on weekends/holidays)
    if not nifty_d or not bn_d or nifty_d.get("ltp", 0) <= 0 or bn_d.get("ltp", 0) <= 0:
        try:
            import broker
            import pytz
            now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
            from_d = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")
            to_d   = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
            
            nifty_df = broker.get_historical_data("NIFTY", "DAY", from_d, to_d, use_index=True)
            bn_df = broker.get_historical_data("BANKNIFTY", "DAY", from_d, to_d, use_index=True)
            
            if nifty_df is not None and not nifty_df.empty and len(nifty_df) >= 2:
                nifty_d = {
                    "ltp": float(nifty_df.iloc[-1]["close"]),
                    "close": float(nifty_df.iloc[-2]["close"]),
                    "open": float(nifty_df.iloc[-1]["open"]),
                    "high": float(nifty_df.iloc[-1]["high"]),
                    "low": float(nifty_df.iloc[-1]["low"]),
                }
            if bn_df is not None and not bn_df.empty and len(bn_df) >= 2:
                bn_d = {
                    "ltp": float(bn_df.iloc[-1]["close"]),
                    "close": float(bn_df.iloc[-2]["close"]),
                    "open": float(bn_df.iloc[-1]["open"]),
                    "high": float(bn_df.iloc[-1]["high"]),
                    "low": float(bn_df.iloc[-1]["low"]),
                }
        except Exception as ex:
            logger.warning(f"Correlation historical fallback failed: {ex}")

    if not nifty_d or not bn_d or nifty_d.get("ltp", 0) <= 0 or bn_d.get("ltp", 0) <= 0:
        result["analysis"] = ["Connect to Dhan for live Nifty/BankNifty correlation"]
        _set_cached("nifty_bn_corr", result)
        return result

    # Fetch actual previous close from historical daily candles
    nifty_prev = None
    bn_prev = None
    try:
        import broker
        import pytz
        import pandas as pd
        if broker.is_connected() and broker._dhan_client is not None:
            now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
            from_d = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")
            to_d   = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
            
            nifty_df = broker.get_historical_data("NIFTY", "DAY", from_d, to_d, use_index=True)
            if nifty_df is not None and not nifty_df.empty:
                last_date = pd.to_datetime(nifty_df.iloc[-1]["timestamp"]).date()
                if last_date == now_ist.date() and len(nifty_df) >= 2:
                    nifty_prev = float(nifty_df.iloc[-2]["close"])
                else:
                    nifty_prev = float(nifty_df.iloc[-1]["close"])
                    
            bn_df = broker.get_historical_data("BANKNIFTY", "DAY", from_d, to_d, use_index=True)
            if bn_df is not None and not bn_df.empty:
                last_date = pd.to_datetime(bn_df.iloc[-1]["timestamp"]).date()
                if last_date == now_ist.date() and len(bn_df) >= 2:
                    bn_prev = float(bn_df.iloc[-2]["close"])
                else:
                    bn_prev = float(bn_df.iloc[-1]["close"])
    except Exception as ex:
        logger.warning(f"Correlation historical fallback failed to get prev close: {ex}")

    # Fallback to current close if daily fetch failed
    if not nifty_prev and nifty_d.get("close", 0) > 0:
        nifty_prev = nifty_d["close"]
    if not bn_prev and bn_d.get("close", 0) > 0:
        bn_prev = bn_d["close"]

    nifty_price = nifty_d["ltp"]
    bn_price = bn_d["ltp"]

    # Calculate changes
    nifty_change = round(nifty_price - nifty_prev, 2) if nifty_prev else None
    nifty_pct = round((nifty_change / nifty_prev) * 100, 2) if nifty_prev and nifty_prev > 0 else None
    bn_change = round(bn_price - bn_prev, 2) if bn_prev else None
    bn_pct = round((bn_change / bn_prev) * 100, 2) if bn_prev and bn_prev > 0 else None

    result["nifty"] = {
        "price": round(nifty_price, 2),
        "change": nifty_change,
        "change_pct": nifty_pct,
        "prev_close": round(nifty_prev, 2) if nifty_prev else None,
        "open": round(nifty_d["open"], 2) if nifty_d["open"] > 0 else None,
        "high": round(nifty_d["high"], 2) if nifty_d["high"] > 0 else None,
        "low": round(nifty_d["low"], 2) if nifty_d["low"] > 0 else None,
    }
    result["banknifty"] = {
        "price": round(bn_price, 2),
        "change": bn_change,
        "change_pct": bn_pct,
        "prev_close": round(bn_prev, 2) if bn_prev else None,
        "open": round(bn_d["open"], 2) if bn_d["open"] > 0 else None,
        "high": round(bn_d["high"], 2) if bn_d["high"] > 0 else None,
        "low": round(bn_d["low"], 2) if bn_d["low"] > 0 else None,
    }

    # Direction & alignment analysis
    analysis = []

    if nifty_pct is not None and bn_pct is not None:
        nifty_dir = "up" if nifty_pct > 0.05 else ("down" if nifty_pct < -0.05 else "flat")
        bn_dir = "up" if bn_pct > 0.05 else ("down" if bn_pct < -0.05 else "flat")

        # Same direction?
        if nifty_dir == bn_dir:
            result["direction"] = "aligned"
            if nifty_dir == "up":
                result["alignment"] = "Both indices moving UP — bullish alignment"
            elif nifty_dir == "down":
                result["alignment"] = "Both indices moving DOWN — bearish alignment"
            else:
                result["alignment"] = "Both indices FLAT — no clear direction"
        else:
            result["direction"] = "divergent"
            result["alignment"] = f"Nifty {nifty_dir.upper()} vs BankNifty {bn_dir.upper()} — divergence signal"

        # Beta / relative strength
        if abs(nifty_pct) > 0.01:
            beta = round(bn_pct / nifty_pct, 2)
            result["beta"] = beta
            result["relative_strength"] = round(abs(bn_pct) - abs(nifty_pct), 2)

            if beta > 1.5:
                analysis.append(f"BankNifty showing {beta:.1f}x leverage over Nifty — banking sector leading")
            elif beta > 1:
                analysis.append(f"BankNifty moving {beta:.1f}x relative to Nifty — moderate bank strength")
            elif beta > 0:
                analysis.append(f"BankNifty lagging Nifty ({beta:.1f}x) — broader market leading")
            elif beta < 0:
                analysis.append(f"Indices moving in opposite directions (β={beta:.1f}) — divergence, exercise caution")

        # Strength analysis
        if abs(bn_pct) > abs(nifty_pct) * 1.5:
            analysis.append(f"Strong BankNifty ({bn_pct:+.2f}%) vs Nifty ({nifty_pct:+.2f}%) — bank-heavy move")
        elif abs(nifty_pct) > abs(bn_pct) * 1.5:
            analysis.append(f"Nifty ({nifty_pct:+.2f}%) outpacing BankNifty ({bn_pct:+.2f}%) — broad market driven")
        else:
            analysis.append(f"Nifty {nifty_pct:+.2f}% | BankNifty {bn_pct:+.2f}% — balanced move")

        # Trading implications
        if result["direction"] == "divergent":
            analysis.append("⚠ Divergence detected — BankNifty signals may have lower confidence")
        elif abs(bn_pct) > 1.0:
            analysis.append("Strong BankNifty momentum — favorable for directional trades")
        elif abs(bn_pct) < 0.1 and abs(nifty_pct) < 0.1:
            analysis.append("Both indices near flat — range-bound conditions likely")

    result["analysis"] = analysis
    _set_cached("nifty_bn_corr", result)
    return result


def build_oi_cross_analysis(nifty_oi: dict, bn_oi: dict) -> dict:
    """Compare NIFTY vs BANKNIFTY OI sentiment — called from get_market_context()."""
    oi_cross = {"status": "unavailable"}
    try:
        if nifty_oi.get("status") == "ok" and bn_oi.get("status") == "ok":
            oi_cross["nifty_pcr"] = nifty_oi.get("pcr")
            oi_cross["banknifty_pcr"] = bn_oi.get("pcr")
            oi_cross["nifty_verdict"] = nifty_oi.get("verdict_text", "N/A")
            oi_cross["banknifty_verdict"] = bn_oi.get("verdict_text", "N/A")
            oi_cross["nifty_max_pain"] = nifty_oi.get("max_pain")
            oi_cross["banknifty_max_pain"] = bn_oi.get("max_pain")
            oi_cross["nifty_oi_range"] = nifty_oi.get("oi_range", "—")
            oi_cross["banknifty_oi_range"] = bn_oi.get("oi_range", "—")
            oi_cross["nifty_iv"] = nifty_oi.get("atm_iv")
            oi_cross["banknifty_iv"] = bn_oi.get("atm_iv")

            n_v = nifty_oi.get("oi_verdict", "")
            b_v = bn_oi.get("oi_verdict", "")
            n_bull = "bullish" in n_v
            b_bull = "bullish" in b_v
            n_bear = "bearish" in n_v
            b_bear = "bearish" in b_v

            if (n_bull and b_bull) or (n_bear and b_bear):
                oi_cross["oi_alignment"] = "aligned"
                oi_cross["oi_alignment_text"] = f"OI sentiment aligned — both {oi_cross['nifty_verdict']}/{oi_cross['banknifty_verdict']}. Stronger conviction for directional trades."
            elif (n_bull and b_bear) or (n_bear and b_bull):
                oi_cross["oi_alignment"] = "divergent"
                oi_cross["oi_alignment_text"] = f"OI sentiment divergent — Nifty {oi_cross['nifty_verdict']} vs BankNifty {oi_cross['banknifty_verdict']}. Proceed with caution on directional bets."
            else:
                oi_cross["oi_alignment"] = "mixed"
                oi_cross["oi_alignment_text"] = f"Mixed OI signals — Nifty {oi_cross['nifty_verdict']}, BankNifty {oi_cross['banknifty_verdict']}. No clear cross-index OI confirmation."

            oi_cross["status"] = "ok"
    except Exception as e:
        logger.warning(f"OI cross-analysis failed: {e}")
    return oi_cross


# ── Open Interest Analysis (Dhan Option Chain) ──────────────────────────────
# Strike step sizes for index/commodity options
STRIKE_STEPS = {
    "BANKNIFTY": 100, "NIFTY": 50, "FINNIFTY": 50,
    "MIDCPNIFTY": 25, "SENSEX": 100, "BANKEX": 100,
    "CRUDEOIL": 50, "GOLD": 100, "SILVER": 500,
    "NIFTYNXT50": 50,
}

# Exchange segment for option chain (IDX_I for indices, MCX for commodities, BSE_FNO for BSE)
OI_EXCHANGE_SEGMENTS = {
    "BANKNIFTY": "IDX_I", "NIFTY": "IDX_I", "FINNIFTY": "IDX_I",
    "MIDCPNIFTY": "IDX_I", "NIFTYNXT50": "IDX_I",
    "SENSEX": "IDX_I", "BANKEX": "IDX_I",
    "CRUDEOIL": "MCX", "GOLD": "MCX", "SILVER": "MCX",
}

# Security IDs for option chain underlyings
OI_SECURITY_IDS = {
    "BANKNIFTY": 25, "NIFTY": 13, "FINNIFTY": 27,
    "MIDCPNIFTY": 442, "SENSEX": 51, "BANKEX": 69,
    "NIFTYNXT50": 26138,
}

# Available instruments for the UI selector
OI_INSTRUMENTS = [
    {"value": "BANKNIFTY", "label": "BANKNIFTY"},
    {"value": "NIFTY", "label": "NIFTY 50"},
    {"value": "FINNIFTY", "label": "FINNIFTY"},
    {"value": "MIDCPNIFTY", "label": "MIDCPNIFTY"},
    {"value": "SENSEX", "label": "SENSEX"},
    {"value": "BANKEX", "label": "BANKEX"},
]


def fetch_oi_expiry_list(instrument: str = "BANKNIFTY") -> dict:
    """Return available expiries for the given instrument from Dhan."""
    cache_key = f"oi_expiry_list_{instrument}"
    cached = _get_cached(cache_key, 300)  # 5 min cache
    if cached:
        return cached

    result = {"instrument": instrument, "expiries": [], "status": "unavailable"}
    try:
        import broker
        if not broker.is_connected() or broker._dhan_client is None:
            return result
        client = broker._dhan_client
        sec_id = OI_SECURITY_IDS.get(instrument.upper(), DHAN_INDEX_IDS.get(instrument.upper()))
        exch_seg = OI_EXCHANGE_SEGMENTS.get(instrument.upper(), "IDX_I")
        if not sec_id:
            return result
        resp = client.expiry_list(under_security_id=sec_id, under_exchange_segment=exch_seg)
        if isinstance(resp, dict) and resp.get("status") == "success":
            dates = resp.get("data", [])
            if isinstance(dates, dict):
                dates = dates.get("data", [])
            result["expiries"] = sorted(dates) if isinstance(dates, list) else []
            result["status"] = "ok"
    except Exception as e:
        logger.error(f"Expiry list fetch failed for {instrument}: {e}")
        result["error"] = str(e)
    _set_cached(cache_key, result)
    return result


def fetch_oi_analysis(instrument: str = "BANKNIFTY", expiry: str = None) -> dict:
    """
    Comprehensive Open Interest analysis from Dhan option chain.
    Produces: PCR, Max Pain, Support/Resistance levels from OI,
    OI change analysis, ATM IV & straddle, expected range, trading signals.
    Cached for 3 minutes.
    """
    cache_key = f"oi_analysis_{instrument}_{expiry or 'nearest'}"
    cached = _get_cached(cache_key, 180)
    if cached:
        return cached

    result = {
        "instrument": instrument,
        "status": "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    try:
        import broker
        if not broker.is_connected() or broker._dhan_client is None:
            result["error"] = "Dhan not connected"
            _set_cached(cache_key, result)
            return result

        client = broker._dhan_client
        sec_id = OI_SECURITY_IDS.get(instrument.upper(), DHAN_INDEX_IDS.get(instrument.upper()))
        exch_seg = OI_EXCHANGE_SEGMENTS.get(instrument.upper(), "IDX_I")
        if not sec_id:
            result["error"] = f"Unknown instrument: {instrument}"
            _set_cached(cache_key, result)
            return result

        # 1. Determine expiry to use
        if expiry:
            nearest_expiry = expiry
        else:
            # Fetch expiry list
            expiry_data = fetch_oi_expiry_list(instrument)
            if expiry_data.get("status") != "ok" or not expiry_data.get("expiries"):
                result["error"] = f"Cannot fetch expiry list for {instrument}"
                _set_cached(cache_key, result)
                return result
            nearest_expiry = expiry_data["expiries"][0]

        # 2. Fetch option chain
        # Dhan API: POST /optionchain → {"data": {"last_price": ..., "oc": {...}}, "status": "success"}
        oc_resp = client.option_chain(
            under_security_id=sec_id,
            under_exchange_segment=exch_seg,
            expiry=nearest_expiry
        )

        if not isinstance(oc_resp, dict) or oc_resp.get("status") != "success":
            result["error"] = f"Option chain fetch failed: {oc_resp.get('remarks', oc_resp.get('errorMessage', 'unknown'))}"
            _set_cached(cache_key, result)
            return result

        # Parse response — Dhan docs: {"data": {"last_price": ..., "oc": {...}}}
        # dhanhq client may also return {"data": {"data": {"last_price":..., "oc":{...}}}}
        resp_data = oc_resp.get("data", {})
        if isinstance(resp_data, dict):
            if "oc" in resp_data:
                oc_data = resp_data["oc"]
                spot_price = resp_data.get("last_price", 0)
            elif isinstance(resp_data.get("data"), dict) and "oc" in resp_data["data"]:
                oc_data = resp_data["data"]["oc"]
                spot_price = resp_data["data"].get("last_price", 0)
            else:
                oc_data = None
                spot_price = 0
        else:
            oc_data = None
            spot_price = 0

        # Fallback: get spot from OHLC if not in option chain response
        if spot_price <= 0:
            spot_data = _fetch_dhan_index_ohlc([instrument])
            spot_price = spot_data.get(instrument, {}).get("ltp", 0)

        if not oc_data:
            result["error"] = "Empty option chain data"
            _set_cached(cache_key, result)
            return result

        if spot_price <= 0:
            result["error"] = "Cannot determine spot price"
            _set_cached(cache_key, result)
            return result

        # 3. Parse option chain into structured data
        strike_step = STRIKE_STEPS.get(instrument, 100)
        atm_strike = round(spot_price / strike_step) * strike_step

        total_call_oi = 0
        total_put_oi = 0
        total_call_volume = 0
        total_put_volume = 0
        strikes_data = []

        for strike_str, details in oc_data.items():
            try:
                strike = float(strike_str)
            except (ValueError, TypeError):
                continue

            ce = details.get("ce", {})
            pe = details.get("pe", {})

            ce_oi = ce.get("oi", 0) or 0
            pe_oi = pe.get("oi", 0) or 0
            ce_prev_oi = ce.get("previous_oi", 0) or 0
            pe_prev_oi = pe.get("previous_oi", 0) or 0
            ce_vol = ce.get("volume", 0) or 0
            pe_vol = pe.get("volume", 0) or 0
            ce_iv = ce.get("implied_volatility", 0) or 0
            pe_iv = pe.get("implied_volatility", 0) or 0
            ce_ltp = ce.get("last_price", 0) or 0
            pe_ltp = pe.get("last_price", 0) or 0

            ce_greeks = ce.get("greeks", {})
            pe_greeks = pe.get("greeks", {})

            total_call_oi += ce_oi
            total_put_oi += pe_oi
            total_call_volume += ce_vol
            total_put_volume += pe_vol

            strikes_data.append({
                "strike": strike,
                "ce_oi": ce_oi, "pe_oi": pe_oi,
                "ce_oi_change": ce_oi - ce_prev_oi,
                "pe_oi_change": pe_oi - pe_prev_oi,
                "ce_volume": ce_vol, "pe_volume": pe_vol,
                "ce_iv": round(ce_iv, 2), "pe_iv": round(pe_iv, 2),
                "ce_ltp": round(ce_ltp, 2), "pe_ltp": round(pe_ltp, 2),
                "ce_delta": round(ce_greeks.get("delta", 0) or 0, 3),
                "pe_delta": round(pe_greeks.get("delta", 0) or 0, 3),
                "ce_theta": round(ce_greeks.get("theta", 0) or 0, 2),
                "pe_theta": round(pe_greeks.get("theta", 0) or 0, 2),
                "ce_gamma": round(ce_greeks.get("gamma", 0) or 0, 5),
                "pe_gamma": round(pe_greeks.get("gamma", 0) or 0, 5),
                "ce_vega": round(ce_greeks.get("vega", 0) or 0, 2),
                "pe_vega": round(pe_greeks.get("vega", 0) or 0, 2),
            })

        if not strikes_data:
            result["error"] = "No valid strikes parsed"
            _set_cached(cache_key, result)
            return result

        strikes_data.sort(key=lambda x: x["strike"])

        # ══════════════════════════════════════════════════════════════════
        # 5. ANALYSIS COMPUTATIONS
        # ══════════════════════════════════════════════════════════════════

        # ── A. Put-Call Ratio (PCR) ──
        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 0
        pcr_volume = round(total_put_volume / total_call_volume, 2) if total_call_volume > 0 else 0

        if pcr >= 1.3:
            pcr_signal = "strongly_bullish"
            pcr_interpretation = "PCR > 1.3 — Heavy put writing indicates strong support. Market makers expect upside."
        elif pcr >= 1.0:
            pcr_signal = "bullish"
            pcr_interpretation = "PCR > 1.0 — More puts than calls, indicating bullish undercurrent. Puts acting as support."
        elif pcr >= 0.7:
            pcr_signal = "neutral"
            pcr_interpretation = "PCR in 0.7-1.0 range — balanced market. No strong directional OI bias."
        elif pcr >= 0.5:
            pcr_signal = "bearish"
            pcr_interpretation = "PCR < 0.7 — Call-heavy market. Resistance likely to cap upside. Bearish undertone."
        else:
            pcr_signal = "strongly_bearish"
            pcr_interpretation = "PCR < 0.5 — Extreme call writing. Strong resistance overhead. Potential for sharp correction."

        # ── B. Max Pain ──
        # Max Pain = strike where sum of (call ITM losses + put ITM losses) for ALL buyers is maximum
        # i.e., the strike where option writers profit the most
        max_pain_strike = atm_strike
        min_total_loss = float('inf')

        for candidate in strikes_data:
            price_at = candidate["strike"]
            total_loss = 0
            for s in strikes_data:
                # Call buyer loss: call OI * max(0, price_at - strike)
                call_itm = max(0, price_at - s["strike"])
                total_loss += s["ce_oi"] * call_itm
                # Put buyer loss: put OI * max(0, strike - price_at)
                put_itm = max(0, s["strike"] - price_at)
                total_loss += s["pe_oi"] * put_itm

            if total_loss < min_total_loss:
                min_total_loss = total_loss
                max_pain_strike = candidate["strike"]

        max_pain_distance = round(spot_price - max_pain_strike, 2)
        max_pain_pct = round((max_pain_distance / spot_price) * 100, 2) if spot_price > 0 else 0

        # ── C. Support & Resistance from OI ──
        # Highest Put OI = Support (put sellers defend these levels)
        # Highest Call OI = Resistance (call sellers defend these levels)
        sorted_by_put_oi = sorted(strikes_data, key=lambda x: x["pe_oi"], reverse=True)
        sorted_by_call_oi = sorted(strikes_data, key=lambda x: x["ce_oi"], reverse=True)

        # Top 3 support levels (highest put OI)
        supports = []
        for s in sorted_by_put_oi[:5]:
            if s["pe_oi"] > 0 and s["strike"] <= spot_price + strike_step:
                supports.append({
                    "strike": s["strike"],
                    "put_oi": s["pe_oi"],
                    "put_oi_change": s["pe_oi_change"],
                    "strength": "strong" if s["pe_oi"] > sorted_by_put_oi[0]["pe_oi"] * 0.7 else "moderate"
                })
            if len(supports) >= 3:
                break

        # Top 3 resistance levels (highest call OI)
        resistances = []
        for s in sorted_by_call_oi[:5]:
            if s["ce_oi"] > 0 and s["strike"] >= spot_price - strike_step:
                resistances.append({
                    "strike": s["strike"],
                    "call_oi": s["ce_oi"],
                    "call_oi_change": s["ce_oi_change"],
                    "strength": "strong" if s["ce_oi"] > sorted_by_call_oi[0]["ce_oi"] * 0.7 else "moderate"
                })
            if len(resistances) >= 3:
                break

        immediate_support = supports[0]["strike"] if supports else atm_strike - strike_step * 2
        immediate_resistance = resistances[0]["strike"] if resistances else atm_strike + strike_step * 2

        # ── D. OI Change Analysis (Fresh positions) ──
        # Top strikes where OI is being BUILT today
        call_oi_buildup = sorted(strikes_data, key=lambda x: x["ce_oi_change"], reverse=True)[:3]
        put_oi_buildup = sorted(strikes_data, key=lambda x: x["pe_oi_change"], reverse=True)[:3]

        # Net OI change interpretation
        total_call_oi_change = sum(s["ce_oi_change"] for s in strikes_data)
        total_put_oi_change = sum(s["pe_oi_change"] for s in strikes_data)

        if total_put_oi_change > total_call_oi_change * 1.5:
            oi_change_signal = "bullish"
            oi_change_text = "Fresh put writing dominant — support being built. Bullish OI change."
        elif total_call_oi_change > total_put_oi_change * 1.5:
            oi_change_signal = "bearish"
            oi_change_text = "Fresh call writing dominant — resistance being added. Bearish OI change."
        else:
            oi_change_signal = "neutral"
            oi_change_text = "Balanced OI additions on both sides — no strong directional buildup."

        # ── E. ATM Straddle & Expected Range ──
        atm_data = next((s for s in strikes_data if s["strike"] == atm_strike), None)
        atm_straddle = 0
        atm_iv_avg = 0
        data_quality = "good"
        if atm_data:
            raw_straddle = atm_data["ce_ltp"] + atm_data["pe_ltp"]
            atm_iv_avg = round((atm_data["ce_iv"] + atm_data["pe_iv"]) / 2, 2) if (atm_data["ce_iv"] + atm_data["pe_iv"]) > 0 else 0

            # Sanity check: straddle should be < 20% of spot for indices
            # If straddle > 50% of spot, data is clearly wrong (BSE indices often return bad data)
            if raw_straddle > spot_price * 0.5:
                logger.warning(f"{instrument} ATM straddle ({raw_straddle}) > 50% of spot ({spot_price}) — bad data")
                atm_straddle = 0
                data_quality = "poor"
            else:
                atm_straddle = round(raw_straddle, 2)

            if atm_iv_avg == 0:
                data_quality = "poor" if data_quality == "poor" else "partial"

        # Expected range = spot ± straddle premium (1 SD move)
        if atm_straddle > 0:
            expected_low = round(spot_price - atm_straddle, 2)
            expected_high = round(spot_price + atm_straddle, 2)
        else:
            expected_low = 0
            expected_high = 0

        # ── F. IV Skew Analysis ──
        # Compare OTM put IV vs OTM call IV (fear gauge)
        otm_puts = [s for s in strikes_data if s["strike"] < atm_strike and s["pe_iv"] > 0]
        otm_calls = [s for s in strikes_data if s["strike"] > atm_strike and s["ce_iv"] > 0]

        avg_put_iv = round(sum(s["pe_iv"] for s in otm_puts[-3:]) / max(len(otm_puts[-3:]), 1), 2) if otm_puts else 0
        avg_call_iv = round(sum(s["ce_iv"] for s in otm_calls[:3]) / max(len(otm_calls[:3]), 1), 2) if otm_calls else 0
        iv_skew = round(avg_put_iv - avg_call_iv, 2)

        if iv_skew > 3:
            iv_skew_text = f"Put IV premium +{iv_skew}% — market pricing downside risk higher (fear premium)"
        elif iv_skew < -3:
            iv_skew_text = f"Call IV premium {iv_skew}% — unusual call demand, possible breakout expectation"
        else:
            iv_skew_text = f"IV skew balanced ({iv_skew:+.1f}%) — no significant directional fear"

        # ── G. Composite OI Signal ──
        signals = []
        bullish_points = 0
        bearish_points = 0

        # PCR signal
        if pcr_signal in ("strongly_bullish", "bullish"):
            bullish_points += 2 if pcr_signal == "strongly_bullish" else 1
        elif pcr_signal in ("strongly_bearish", "bearish"):
            bearish_points += 2 if pcr_signal == "strongly_bearish" else 1

        # Max Pain vs Spot
        if max_pain_distance > 0:
            signals.append(f"Spot above Max Pain by {max_pain_distance:.0f} pts — price may revert down toward {max_pain_strike:.0f}")
            bearish_points += 1
        elif max_pain_distance < -strike_step:
            signals.append(f"Spot below Max Pain by {abs(max_pain_distance):.0f} pts — gravitational pull upward to {max_pain_strike:.0f}")
            bullish_points += 1
        else:
            signals.append(f"Spot near Max Pain ({max_pain_strike:.0f}) — price likely to stay range-bound near this level")

        # OI change
        if oi_change_signal == "bullish":
            bullish_points += 1
        elif oi_change_signal == "bearish":
            bearish_points += 1

        # Support/Resistance proximity
        dist_to_support = spot_price - immediate_support
        dist_to_resistance = immediate_resistance - spot_price
        if dist_to_support < dist_to_resistance * 0.5:
            signals.append(f"Spot near strong put support at {immediate_support:.0f} — limited downside risk")
            bullish_points += 1
        elif dist_to_resistance < dist_to_support * 0.5:
            signals.append(f"Spot near strong call resistance at {immediate_resistance:.0f} — upside capped")
            bearish_points += 1

        # IV skew
        if iv_skew > 3:
            bearish_points += 1
        elif iv_skew < -3:
            bullish_points += 1

        # Overall OI verdict
        net = bullish_points - bearish_points
        if net >= 3:
            oi_verdict = "strongly_bullish"
            verdict_text = "Strong Bullish"
        elif net >= 1:
            oi_verdict = "bullish"
            verdict_text = "Mildly Bullish"
        elif net <= -3:
            oi_verdict = "strongly_bearish"
            verdict_text = "Strong Bearish"
        elif net <= -1:
            oi_verdict = "bearish"
            verdict_text = "Mildly Bearish"
        else:
            oi_verdict = "neutral"
            verdict_text = "Neutral / Range-bound"

        # ── H. Near-ATM Strike Table (±5 strikes for display) ──
        near_atm = [s for s in strikes_data
                     if atm_strike - strike_step * 5 <= s["strike"] <= atm_strike + strike_step * 5]

        display_strikes = []
        for s in near_atm:
            display_strikes.append({
                "strike": s["strike"],
                "is_atm": s["strike"] == atm_strike,
                "ce_oi": s["ce_oi"], "pe_oi": s["pe_oi"],
                "ce_oi_chg": s["ce_oi_change"], "pe_oi_chg": s["pe_oi_change"],
                "ce_ltp": s["ce_ltp"], "pe_ltp": s["pe_ltp"],
                "ce_iv": s["ce_iv"], "pe_iv": s["pe_iv"],
                "ce_vol": s["ce_volume"], "pe_vol": s["pe_volume"],
                "ce_delta": s["ce_delta"], "pe_delta": s["pe_delta"],
                "ce_theta": s["ce_theta"], "pe_theta": s["pe_theta"],
                "ce_gamma": s["ce_gamma"], "pe_gamma": s["pe_gamma"],
                "ce_vega": s["ce_vega"], "pe_vega": s["pe_vega"],
            })

        # ══════════════════════════════════════════════════════════════════
        # BUILD RESULT
        # ══════════════════════════════════════════════════════════════════
        result = {
            "instrument": instrument,
            "status": "ok",
            "spot_price": round(spot_price, 2),
            "atm_strike": atm_strike,
            "expiry": str(nearest_expiry),
            "strike_step": strike_step,

            # PCR
            "pcr": pcr,
            "pcr_volume": pcr_volume,
            "pcr_signal": pcr_signal,
            "pcr_interpretation": pcr_interpretation,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,

            # Max Pain
            "max_pain": max_pain_strike,
            "max_pain_distance": max_pain_distance,
            "max_pain_pct": max_pain_pct,

            # Support / Resistance
            "supports": supports,
            "resistances": resistances,
            "immediate_support": immediate_support,
            "immediate_resistance": immediate_resistance,
            "oi_range": f"{immediate_support:.0f} - {immediate_resistance:.0f}",

            # OI Change
            "oi_change_signal": oi_change_signal,
            "oi_change_text": oi_change_text,
            "total_call_oi_change": total_call_oi_change,
            "total_put_oi_change": total_put_oi_change,
            "call_oi_buildup": [{"strike": s["strike"], "change": s["ce_oi_change"]} for s in call_oi_buildup if s["ce_oi_change"] > 0],
            "put_oi_buildup": [{"strike": s["strike"], "change": s["pe_oi_change"]} for s in put_oi_buildup if s["pe_oi_change"] > 0],

            # ATM & Straddle
            "atm_straddle": atm_straddle,
            "atm_iv": atm_iv_avg,
            "expected_range": {"low": expected_low, "high": expected_high},

            # IV Skew
            "avg_put_iv": avg_put_iv,
            "avg_call_iv": avg_call_iv,
            "iv_skew": iv_skew,
            "iv_skew_text": iv_skew_text,

            # Verdict
            "oi_verdict": oi_verdict,
            "verdict_text": verdict_text,
            "signals": signals,
            "bullish_points": bullish_points,
            "bearish_points": bearish_points,

            # Strike table
            "strikes_table": display_strikes,

            # Data quality indicator
            "data_quality": data_quality,

            # Selectors for UI
            "available_instruments": OI_INSTRUMENTS,

            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Attach expiry list for the current instrument
        try:
            exp_data = fetch_oi_expiry_list(instrument)
            result["available_expiries"] = exp_data.get("expiries", [])
        except Exception:
            result["available_expiries"] = [str(nearest_expiry)]

    except Exception as e:
        logger.error(f"OI analysis failed for {instrument}: {e}", exc_info=True)
        result["error"] = str(e)

    _set_cached(cache_key, result)
    return result


# ── Options Context for Live Trading ─────────────────────────────────────────

def fetch_options_context(instrument: str = "BANKNIFTY", expiry: str = None, direction: str = None) -> dict:
    """
    Options awareness data for Live Trading screen.
    Reuses cached OI analysis and enriches with:
    - Strike recommendations (ATM, ITM, OTM) with Greeks
    - IV context (percentile estimate, high/low label)
    - Theta decay per hour
    - Expected move from straddle
    - Days to expiry with urgency
    - Position Greeks helpers
    """
    from datetime import datetime, timezone, date

    oi = fetch_oi_analysis(instrument, expiry)

    result = {
        "instrument": instrument,
        "status": "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    if oi.get("status") != "ok":
        result["error"] = oi.get("error", "OI analysis unavailable")
        return result

    spot = oi.get("spot_price", 0)
    atm_strike = oi.get("atm_strike", 0)
    strike_step = oi.get("strike_step", 100)
    expiry_date_str = oi.get("expiry", "")
    strikes_table = oi.get("strikes_table", [])
    atm_straddle = oi.get("atm_straddle", 0)
    atm_iv = oi.get("atm_iv", 0)
    data_quality = oi.get("data_quality", "good")

    # ── Days to Expiry ──
    dte = None
    expiry_urgency = "normal"
    try:
        exp_d = date.fromisoformat(expiry_date_str)
        today_d = date.today()
        dte = (exp_d - today_d).days
        if dte <= 0:
            expiry_urgency = "expiry_day"
        elif dte == 1:
            expiry_urgency = "critical"
        elif dte <= 3:
            expiry_urgency = "high"
        elif dte <= 7:
            expiry_urgency = "moderate"
    except Exception:
        pass

    # ── Strike Recommendations ──
    # Build from strikes_table which now includes Greeks for both CE and PE
    recommended_strikes = []
    dir_label = (direction or "LONG").upper()
    is_call = dir_label in ("LONG", "BUY", "CALL", "CE")

    for s in strikes_table:
        strike = s["strike"]
        # Generate both CE and PE options
        for is_c in (True, False):
            prefix = "ce_" if is_c else "pe_"
            opt_ltp = s.get(f"{prefix}ltp", 0)
            opt_delta = abs(s.get(f"{prefix}delta", 0))
            opt_theta = s.get(f"{prefix}theta", 0)
            opt_iv = s.get(f"{prefix}iv", 0)
            opt_gamma = s.get(f"{prefix}gamma", 0)
            opt_vega = s.get(f"{prefix}vega", 0)
            opt_oi = s.get(f"{prefix.replace('_', '')}oi", s.get(f"{prefix}oi", 0))

            # Classify: ITM / ATM / OTM
            if is_c:
                moneyness = "ATM" if strike == atm_strike else ("ITM" if strike < atm_strike else "OTM")
                intrinsic = max(0, spot - strike)
                breakeven = strike + opt_ltp if opt_ltp > 0 else 0
            else:
                moneyness = "ATM" if strike == atm_strike else ("ITM" if strike > atm_strike else "OTM")
                intrinsic = max(0, strike - spot)
                breakeven = strike - opt_ltp if opt_ltp > 0 else 0

            time_value = max(0, opt_ltp - intrinsic)
            theta_per_hour = round(abs(opt_theta) / 6.25, 2) if opt_theta else 0

            recommended_strikes.append({
                "strike": strike,
                "type": "CE" if is_c else "PE",
                "moneyness": moneyness,
                "is_atm": s.get("is_atm", False),
                "ltp": opt_ltp,
                "delta": round(opt_delta, 3),
                "theta": opt_theta,
                "theta_per_hour": theta_per_hour,
                "gamma": opt_gamma,
                "vega": opt_vega,
                "iv": opt_iv,
                "oi": opt_oi,
                "intrinsic": round(intrinsic, 2),
                "time_value": round(time_value, 2),
                "breakeven": round(breakeven, 2),
            })

    # Sort by proximity to ATM
    recommended_strikes.sort(key=lambda x: abs(x["strike"] - atm_strike))

    # Tag best picks
    if recommended_strikes:
        for s in recommended_strikes:
            tags = []
            d = s["delta"]
            if 0.45 <= d <= 0.55:
                tags.append("ATM pick")
            elif 0.55 < d <= 0.70:
                tags.append("Safe ITM")
            elif 0.30 <= d < 0.45:
                tags.append("Value OTM")
            elif d > 0.70:
                tags.append("Deep ITM")
            elif 0.15 <= d < 0.30:
                tags.append("Aggressive OTM")
            s["tags"] = tags

    # ── ATM Greeks for quick reference (based on requested direction) ──
    atm_data = next((s for s in recommended_strikes if s["is_atm"] and s["type"] == ("CE" if is_call else "PE")), None)
    # Fallback to any ATM strike if not found
    if not atm_data:
        atm_data = next((s for s in recommended_strikes if s["is_atm"]), None)
    atm_greeks = None
    if atm_data:
        atm_greeks = {
            "delta": atm_data["delta"],
            "theta": atm_data["theta"],
            "theta_per_hour": atm_data["theta_per_hour"],
            "gamma": atm_data["gamma"],
            "vega": atm_data["vega"],
            "iv": atm_data["iv"],
            "premium": atm_data["ltp"],
        }

    # ── IV Context ──
    # Simple IV level classification based on typical ranges
    iv_level = "unknown"
    iv_label = ""
    if atm_iv > 0:
        if instrument in ("BANKNIFTY", "FINNIFTY"):
            if atm_iv > 25:
                iv_level = "high"
                iv_label = "IV elevated — options expensive, favor selling or tight strikes"
            elif atm_iv > 18:
                iv_level = "moderate"
                iv_label = "IV normal range — balanced pricing"
            else:
                iv_level = "low"
                iv_label = "IV low — options cheap, good for buying"
        elif instrument in ("NIFTY", "MIDCPNIFTY", "NIFTYNXT50"):
            if atm_iv > 20:
                iv_level = "high"
                iv_label = "IV elevated — options expensive, favor selling or tight strikes"
            elif atm_iv > 14:
                iv_level = "moderate"
                iv_label = "IV normal range — balanced pricing"
            else:
                iv_level = "low"
                iv_label = "IV low — options cheap, good for buying"
        elif instrument in ("SENSEX", "BANKEX"):
            if atm_iv > 22:
                iv_level = "high"
                iv_label = "IV elevated — options expensive"
            elif atm_iv > 15:
                iv_level = "moderate"
                iv_label = "IV normal range"
            else:
                iv_level = "low"
                iv_label = "IV low — options cheap"
        else:
            # Commodities / others
            if atm_iv > 30:
                iv_level = "high"
                iv_label = "IV elevated"
            elif atm_iv > 20:
                iv_level = "moderate"
                iv_label = "IV normal"
            else:
                iv_level = "low"
                iv_label = "IV low"

    # ── Expected Move ──
    expected_move = {}
    if atm_straddle > 0 and spot > 0:
        expected_move = {
            "points": round(atm_straddle, 0),
            "pct": round((atm_straddle / spot) * 100, 2),
            "range_low": round(spot - atm_straddle, 0),
            "range_high": round(spot + atm_straddle, 0),
        }

    # ── Theta Decay Context ──
    theta_context = {}
    if atm_greeks and atm_greeks["theta"] != 0:
        daily_decay = abs(atm_greeks["theta"])
        theta_context = {
            "atm_daily_decay": daily_decay,
            "atm_hourly_decay": atm_greeks["theta_per_hour"],
            "decay_warning": "Rapid decay — avoid holding overnight" if dte is not None and dte <= 1 else
                            "High decay zone — prefer intraday" if dte is not None and dte <= 3 else
                            "Normal decay — swing-friendly" if dte is not None and dte > 5 else "",
            "pct_of_premium": round((daily_decay / atm_greeks["premium"]) * 100, 1) if atm_greeks["premium"] > 0 else 0,
        }

    # ── OI Context (from parent OI analysis) ──
    oi_context = {
        "pcr": oi.get("pcr", 0),
        "pcr_signal": oi.get("pcr_signal", ""),
        "max_pain": oi.get("max_pain", 0),
        "max_pain_distance": oi.get("max_pain_distance", 0),
        "oi_verdict": oi.get("oi_verdict", ""),
        "verdict_text": oi.get("verdict_text", ""),
        "supports": oi.get("supports", []),
        "resistances": oi.get("resistances", []),
        "immediate_support": oi.get("immediate_support", 0),
        "immediate_resistance": oi.get("immediate_resistance", 0),
        "iv_skew": oi.get("iv_skew", 0),
        "iv_skew_text": oi.get("iv_skew_text", ""),
        "signals": oi.get("signals", []),
    }

    result.update({
        "status": "ok",
        "spot_price": spot,
        "atm_strike": atm_strike,
        "strike_step": strike_step,
        "expiry": expiry_date_str,
        "dte": dte,
        "expiry_urgency": expiry_urgency,
        "direction": dir_label,
        "data_quality": data_quality,

        "recommended_strikes": recommended_strikes,
        "atm_greeks": atm_greeks,

        "iv_context": {
            "atm_iv": atm_iv,
            "iv_level": iv_level,
            "iv_label": iv_label,
            "avg_call_iv": oi.get("avg_call_iv", 0),
            "avg_put_iv": oi.get("avg_put_iv", 0),
        },

        "expected_move": expected_move,
        "theta_context": theta_context,
        "oi_context": oi_context,

        "available_instruments": OI_INSTRUMENTS,
    })

    # Attach expiry list
    try:
        exp_data = fetch_oi_expiry_list(instrument)
        result["available_expiries"] = exp_data.get("expiries", [])
    except Exception:
        result["available_expiries"] = [expiry_date_str] if expiry_date_str else []

    return result


# ── Combined Endpoint Data ───────────────────────────────────────────────────
def get_market_context() -> dict:
    """Get all market context data in one call."""
    markets = fetch_global_markets()
    bias = analyze_global_bias(markets)
    sentiment = fetch_news_sentiment()
    fear_greed = fetch_fear_greed()
    vix = fetch_india_vix()
    expiry = fetch_expiry_today()
    correlation = fetch_nifty_banknifty_correlation(markets)

    # Compute composite master score
    composite = compute_composite_score(sentiment, fear_greed, bias, vix)

    # OI Analysis (non-blocking — don't break context if it fails)
    bn_oi = {"status": "unavailable"}
    nifty_oi = {"status": "unavailable"}
    try:
        bn_oi = fetch_oi_analysis("BANKNIFTY")
    except Exception as e:
        logger.error(f"BN OI analysis in context failed: {e}")
    try:
        nifty_oi = fetch_oi_analysis("NIFTY")
    except Exception as e:
        logger.error(f"NIFTY OI analysis in context failed: {e}")

    # Build OI cross-analysis and attach to correlation
    try:
        correlation["oi_cross"] = build_oi_cross_analysis(nifty_oi, bn_oi)
    except Exception as e:
        logger.warning(f"OI cross-analysis failed: {e}")
        correlation["oi_cross"] = {"status": "unavailable"}

    return {
        "global_markets": markets,
        "global_bias": bias,
        "news_sentiment": sentiment,
        "fear_greed": fear_greed,
        "india_vix": vix,
        "expiry_today": expiry,
        "composite_score": composite,
        "nifty_bn_correlation": correlation,
        "oi_analysis": bn_oi,
    }
