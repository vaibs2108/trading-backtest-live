"""
live_feed.py — Real-time candle builder via Dhan WebSocket MarketFeed.

Subscribes to tick data for the configured instrument's INDEX,
builds 1m and 5m candles in real-time, and fires an asyncio Event
the instant a 5m candle closes — allowing the signal loop to react
within 1-2 seconds of candle close instead of polling every 15s.

Architecture:
  1. A background thread runs MarketFeed.run_forever() + get_data() loop
  2. Each tick updates the current 1m and 5m candle OHLCV
  3. When the clock crosses a 5m boundary, the completed candle is
     appended to the candle history and the asyncio Event is set
  4. The signal polling loop awaits this event (with a 15s timeout
     as fallback) and processes signals immediately

Higher timeframes (15m, 60m, daily) are still fetched via historical
API since they change slowly and don't need tick-level precision.
"""

import asyncio
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable

import pandas as pd

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


class CandleBuilder:
    """Builds OHLCV candles from tick data for a single timeframe."""

    def __init__(self, tf_minutes: int, max_candles: int = 2000):
        self.tf_minutes = tf_minutes
        self.max_candles = max_candles
        self.candles: deque = deque(maxlen=max_candles)
        self._current: Optional[dict] = None
        self._current_slot: Optional[datetime] = None

    def _get_slot(self, dt: datetime) -> datetime:
        """Round down to the start of the current candle slot (timezone-naive IST)."""
        minute = (dt.minute // self.tf_minutes) * self.tf_minutes
        slot = dt.replace(minute=minute, second=0, microsecond=0)
        if slot.tzinfo is not None:
            slot = slot.replace(tzinfo=None)
        return slot

    def on_tick(self, price: float, volume: int, tick_time: datetime) -> Optional[dict]:
        """Process a tick. Returns the completed candle dict if a new slot started."""
        slot = self._get_slot(tick_time)
        completed = None

        if self._current_slot is None or slot > self._current_slot:
            # New candle slot — finalize the old one
            if self._current is not None:
                completed = self._current.copy()
                self.candles.append(completed)
            # Start new candle
            self._current_slot = slot
            self._current = {
                "timestamp": slot,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0,
            }
        else:
            # Same slot — update OHLCV
            self._current["high"] = max(self._current["high"], price)
            self._current["low"] = min(self._current["low"], price)
            self._current["close"] = price

        # Volume is cumulative from exchange; we track it per candle
        # by storing last known volume and computing delta
        self._current["volume"] += volume if volume > 0 else 0

        return completed

    def get_dataframe(self, include_current: bool = False) -> pd.DataFrame:
        """Return candle history as DataFrame matching the app's expected format."""
        rows = list(self.candles)
        if include_current and self._current is not None:
            rows.append(self._current.copy())
        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        
        cleaned_rows = []
        for r in rows:
            rc = r.copy()
            ts = rc.get("timestamp")
            if isinstance(ts, datetime) and ts.tzinfo is not None:
                rc["timestamp"] = ts.replace(tzinfo=None)
            elif isinstance(ts, pd.Timestamp) and ts.tzinfo is not None:
                rc["timestamp"] = ts.tz_localize(None)
            cleaned_rows.append(rc)

        df = pd.DataFrame(cleaned_rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df


class LiveFeedManager:
    """
    Manages the Dhan WebSocket connection, builds candles, and signals
    the async polling loop when a 5m candle completes.
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._feed = None  # MarketFeed instance
        self._instrument: Optional[str] = None
        self._security_id: Optional[str] = None
        self._exchange_seg = None  # MarketFeed.NSE / MarketFeed.BSE / etc.

        # Candle builders
        self.candle_1m = CandleBuilder(1, max_candles=500)
        self.candle_5m = CandleBuilder(5, max_candles=2000)

        # Asyncio event set when a 5m candle completes
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._candle_event: Optional[asyncio.Event] = None

        # Latest LTP for quick access
        self.last_price: float = 0.0
        self.last_tick_time: Optional[datetime] = None
        self._tick_count: int = 0

        # Callbacks for candle completion
        self._on_5m_close: Optional[Callable] = None

    def configure(self, instrument: str, security_id: str, exchange_segment: str,
                  loop: asyncio.AbstractEventLoop):
        """Configure the feed for an instrument. Call before start()."""
        self._instrument = instrument
        self._security_id = security_id
        self._exchange_seg = exchange_segment
        self._loop = loop
        self._candle_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def candle_event(self) -> Optional[asyncio.Event]:
        return self._candle_event

    def start(self, client_id: str, access_token: str):
        """Start the WebSocket feed in a background thread."""
        if self._running:
            logger.warning("LiveFeed already running")
            return

        if not self._security_id:
            logger.error("LiveFeed not configured — call configure() first")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_feed,
            args=(client_id, access_token),
            daemon=True,
            name="LiveFeed"
        )
        self._thread.start()
        logger.info(f"LiveFeed started for {self._instrument} (security_id={self._security_id})")

    def stop(self):
        """Stop the WebSocket feed."""
        self._running = False
        if self._feed is not None:
            try:
                self._feed.close_connection()
            except Exception as e:
                logger.debug(f"Error closing feed: {e}")
        self._feed = None
        logger.info("LiveFeed stopped")

    def _run_feed(self, client_id: str, access_token: str):
        """Background thread: connect to MarketFeed and process ticks."""
        while self._running:
            try:
                from dhanhq import DhanContext, MarketFeed

                dhan_context = DhanContext(client_id, access_token)

                # Map exchange segment string to MarketFeed constant
                exch_map = {
                    "IDX_I": MarketFeed.IDX,
                    "NSE_EQ": MarketFeed.NSE,
                    "NSE_FNO": MarketFeed.NSE_FNO,
                    "BSE_EQ": MarketFeed.BSE,
                    "BSE_FNO": MarketFeed.BSE_FNO,
                    "MCX_COMM": MarketFeed.MCX,
                }
                exch = exch_map.get(self._exchange_seg, MarketFeed.IDX)

                instruments = [
                    (exch, str(self._security_id), MarketFeed.Quote),
                ]

                logger.info(f"LiveFeed connecting: {instruments}")
                self._feed = MarketFeed(dhan_context, instruments, "v2")
                self._feed.run_forever()

                # Tick processing loop
                while self._running:
                    try:
                        data = self._feed.get_data()
                        if data:
                            self._process_tick(data)
                    except Exception as tick_err:
                        if "closed" in str(tick_err).lower():
                            logger.warning("LiveFeed WebSocket closed, reconnecting...")
                            break
                        logger.debug(f"Tick processing error: {tick_err}")
                    time.sleep(0.05)  # 50ms — fast enough for tick processing

            except Exception as e:
                logger.error(f"LiveFeed connection error: {e}")

            if self._running:
                logger.info("LiveFeed reconnecting in 5s...")
                time.sleep(5)

    def _process_tick(self, data: dict):
        """Process a single tick from MarketFeed."""
        if not data:
            return

        # MarketFeed Quote data contains: LTP, LTT, open, high, low, close, volume
        ltp = data.get("LTP") or data.get("ltp") or data.get("last_price")
        if ltp is None:
            return

        ltp = float(ltp)
        self.last_price = ltp
        self._tick_count += 1

        # Get tick time from data or use current time
        ltt = data.get("LTT") or data.get("ltt") or data.get("last_trade_time")
        if isinstance(ltt, (int, float)):
            # EPOCH timestamp
            tick_time = datetime.fromtimestamp(ltt, tz=_IST)
        else:
            tick_time = datetime.now(_IST)

        self.last_tick_time = tick_time

        # Volume from tick (use 0 if not available — we'll use LTQ or delta)
        vol = int(data.get("volume", 0) or 0)
        ltq = int(data.get("LTQ") or data.get("ltq") or data.get("last_traded_qty") or 0)

        # Feed ticks into candle builders
        self.candle_1m.on_tick(ltp, ltq, tick_time)
        completed_5m = self.candle_5m.on_tick(ltp, ltq, tick_time)

        # If a 5m candle just completed, signal the async loop
        if completed_5m:
            logger.info(
                f"5m candle closed: {completed_5m['timestamp']} "
                f"O={completed_5m['open']:.2f} H={completed_5m['high']:.2f} "
                f"L={completed_5m['low']:.2f} C={completed_5m['close']:.2f}"
            )
            # Set the asyncio event from the background thread
            if self._loop and self._candle_event:
                self._loop.call_soon_threadsafe(self._candle_event.set)

        # Log periodically
        if self._tick_count % 100 == 0:
            logger.debug(f"LiveFeed: {self._tick_count} ticks, LTP={ltp:.2f}")

    def get_live_candles(self, timeframe: str = "5") -> pd.DataFrame:
        """Get candle DataFrame for a timeframe. Used by the signal loop."""
        if timeframe == "1":
            return self.candle_1m.get_dataframe(include_current=False)
        elif timeframe == "5":
            return self.candle_5m.get_dataframe(include_current=False)
        return pd.DataFrame()

    def seed_candles(self, df_5m: pd.DataFrame, df_1m: pd.DataFrame = None):
        """
        Seed candle builders with historical data on startup.
        This ensures indicators have enough bars to calculate from the first tick.
        """
        if df_5m is not None and not df_5m.empty:
            for _, row in df_5m.iterrows():
                candle = {
                    "timestamp": row["timestamp"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row.get("volume", 0)),
                }
                self.candle_5m.candles.append(candle)
            # Set the current slot to the last candle's slot so we don't
            # re-trigger completion for stale data
            last_ts = df_5m["timestamp"].iloc[-1]
            if isinstance(last_ts, str):
                last_ts = pd.to_datetime(last_ts)
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=_IST)
            self.candle_5m._current_slot = self.candle_5m._get_slot(last_ts)
            logger.info(f"LiveFeed seeded with {len(df_5m)} 5m candles (last: {last_ts})")

        if df_1m is not None and not df_1m.empty:
            for _, row in df_1m.iterrows():
                candle = {
                    "timestamp": row["timestamp"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row.get("volume", 0)),
                }
                self.candle_1m.candles.append(candle)
            last_ts_1m = df_1m["timestamp"].iloc[-1]
            if isinstance(last_ts_1m, str):
                last_ts_1m = pd.to_datetime(last_ts_1m)
            if last_ts_1m.tzinfo is None:
                last_ts_1m = last_ts_1m.replace(tzinfo=_IST)
            self.candle_1m._current_slot = self.candle_1m._get_slot(last_ts_1m)
            logger.info(f"LiveFeed seeded with {len(df_1m)} 1m candles")

    def get_status(self) -> dict:
        """Return feed status for the dashboard/API."""
        return {
            "connected": self.is_running,
            "instrument": self._instrument,
            "security_id": self._security_id,
            "last_price": self.last_price,
            "last_tick_time": self.last_tick_time.isoformat() if self.last_tick_time else None,
            "tick_count": self._tick_count,
            "candles_5m": len(self.candle_5m.candles),
            "candles_1m": len(self.candle_1m.candles),
        }


# ── Singleton ──────────────────────────────────────────────────────
_feed_manager = LiveFeedManager()


def get_live_feed() -> LiveFeedManager:
    return _feed_manager
