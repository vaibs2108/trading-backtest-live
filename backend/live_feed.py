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

_LTT_MAX_AGE_SEC = 180


def _ltt_is_current(ltt: str, now_ist: datetime) -> bool:
    """True if a "HH:MM:SS" last-trade-time belongs to a trade happening now.
    dhanhq formats Dhan's epoch with utcfromtimestamp, and Dhan's epoch base differs
    by segment (some are IST-shifted), so accept a match against either IST or UTC."""
    try:
        h, m, s = (int(x) for x in ltt.split(":"))
    except ValueError:
        return True  # unknown format: don't block ticks on a parse problem
    ltt_sec = h * 3600 + m * 60 + s
    for ref in (now_ist, now_ist - timedelta(hours=5, minutes=30)):
        ref_sec = ref.hour * 3600 + ref.minute * 60 + ref.second
        diff = abs(ltt_sec - ref_sec)
        if min(diff, 86400 - diff) <= _LTT_MAX_AGE_SEC:
            return True
    return False


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
        elif slot == self._current_slot:
            # Same slot — update OHLCV
            if self._current is None:
                # slot was seeded/time-closed without an active candle yet — start one
                # (the legitimate case: the first live tick continuing the last-seeded slot)
                self._current = {
                    "timestamp": slot,
                    "open": price, "high": price, "low": price, "close": price,
                    "volume": 0,
                }
            else:
                self._current["high"] = max(self._current["high"], price)
                self._current["low"] = min(self._current["low"], price)
                self._current["close"] = price
        else:
            # slot < self._current_slot: a late/out-of-order tick for a slot that
            # was ALREADY finalized — most often close_on_time()'s boundary check
            # (polled every ~50ms, independent of ticks) racing ahead of a tick
            # still in flight for the slot it just closed. self._current is None
            # here (close_on_time cleared it), so there is no in-progress candle
            # to update; building a fresh one would silently duplicate the
            # already-appended candle for this same timestamp — confirmed live
            # 2026-09-29: this produced two entries for the same 5m candle,
            # corrupting bar-count-based indicators (lookback windows) downstream.
            # Drop it; periodic REST reconciliation (see LiveFeedManager
            # .reconcile_recent) covers any resulting minor incompleteness.
            logger.debug(f"LiveFeed: dropped late tick for already-closed slot {slot} (current={self._current_slot})")
            return None

        # Volume is cumulative from exchange; we track it per candle
        # by storing last known volume and computing delta
        self._current["volume"] += volume if volume > 0 else 0

        return completed

    def close_on_time(self, now_dt: datetime):
        """Complete the current candle the moment the clock passes its slot end,
        WITHOUT waiting for the first tick of the next slot. Returns the
        completed candle dict, or None."""
        if self._current is None or self._current_slot is None:
            return None
        slot_now = self._get_slot(now_dt)
        if slot_now > self._current_slot:
            completed = self._current.copy()
            self.candles.append(completed)
            self._current = None
            self._current_slot = slot_now
            return completed
        return None

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
        self._reset_trade_day()

        # Callbacks for candle completion
        self._on_5m_close: Optional[Callable] = None

    def _reset_trade_day(self):
        # Real (current) trades seen today -- see traded_today()
        self._trade_day = None
        self._trade_ticks = 0
        self._trade_first: Optional[datetime] = None
        self._trade_last: Optional[datetime] = None

    def traded_today(self, now_ist: datetime) -> bool:
        """True once the feed has seen real trading today: 20+ current ticks spread over
        at least a minute. A holiday's re-sent snapshot tick never satisfies this."""
        return (self._trade_day == now_ist.date() and self._trade_ticks >= 20
                and (self._trade_last - self._trade_first).total_seconds() >= 60)

    def configure(self, instrument: str, security_id: str, exchange_segment: str,
                  loop: asyncio.AbstractEventLoop):
        """Configure the feed for an instrument. Call before start()."""
        if instrument != self._instrument:
            self._reset_trade_day()
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
        """Stop the WebSocket feed.

        dhanhq's MarketFeed.close_connection() ends in
        asyncio.run_coroutine_threadsafe(...).result() with no timeout. stop() sets
        _running=False first, so the feed thread's loop exits within ~2s and that future
        can then never complete: the caller hangs forever. On the event-loop thread this
        froze the whole server until the watchdog's 120s heartbeat kill ("Failed to fetch"
        on the Backtest page, 2026-10-02). So close in a daemon thread and give up after
        a bounded wait instead of blocking the caller.
        """
        self._running = False
        feed = self._feed
        if feed is not None:
            def _close():
                try:
                    feed.close_connection()
                except Exception as e:
                    logger.debug(f"Error closing feed: {e}")

            closer = threading.Thread(target=_close, daemon=True, name="LiveFeedClose")
            closer.start()
            closer.join(timeout=5.0)
            if closer.is_alive():
                logger.warning("LiveFeed close_connection() did not return within 5s -- abandoning it")
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
                    # Time-based rollover: close candles at the boundary even if
                    # the next tick hasn't arrived yet (millisecond-level closes)
                    try:
                        _now_ist = datetime.now(_IST)
                        self.candle_1m.close_on_time(_now_ist)
                        _c5 = self.candle_5m.close_on_time(_now_ist)
                        if _c5:
                            logger.info(
                                f"5m candle closed (time rollover): {_c5['timestamp']} "
                                f"C={_c5['close']:.2f}")
                            if self._loop and self._candle_event:
                                self._loop.call_soon_threadsafe(self._candle_event.set)
                    except Exception as _ro_err:
                        logger.debug(f"Rollover check error: {_ro_err}")
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

        # Sanity guard: reject ticks wildly away from the last known price.
        # Protects candles from wrong-instrument subscriptions / corrupt packets.
        _anchor = self.last_price or 0.0
        if _anchor <= 0:
            try:
                if self.candle_5m.candles:
                    _anchor = float(self.candle_5m.candles[-1]["close"])
            except Exception:
                _anchor = 0.0
        if _anchor > 0 and abs(ltp - _anchor) / _anchor > 0.10:
            self._rejected_ticks = getattr(self, "_rejected_ticks", 0) + 1
            if self._rejected_ticks % 200 == 1:
                logger.warning(
                    f"LiveFeed: REJECTED tick {ltp} vs anchor {_anchor:.1f} "
                    f"(wrong instrument or corrupt feed? {self._rejected_ticks} rejected)")
            return

        self.last_price = ltp
        self._tick_count += 1

        # Get tick time from data or use current time
        ltt = data.get("LTT") or data.get("ltt") or data.get("last_trade_time")
        if isinstance(ltt, str) and not _ltt_is_current(ltt, datetime.now(_IST)):
            # dhanhq hands LTT over as a time-of-day string ("HH:MM:SS"). A real trade is
            # stamped within seconds of now; Dhan's snapshot of an EARLIER trade (e.g. the
            # previous session's last tick, re-sent on holidays and after the close) is not.
            # Candles used to be built from those snapshots at wall-clock time -- on
            # 2026-10-02 (holiday) that produced flat fake 5m candles that a strategy
            # traded on. Keep the price, but never build a candle from a stale trade.
            self._stale_ticks = getattr(self, "_stale_ticks", 0) + 1
            if self._stale_ticks % 500 == 1:
                logger.info(f"LiveFeed: ignoring stale tick (last trade {ltt}) for candles "
                            f"-- no trading right now ({self._stale_ticks} ignored)")
            return
        _now_trade = datetime.now(_IST)
        if self._trade_day != _now_trade.date():
            self._trade_day, self._trade_ticks, self._trade_first = _now_trade.date(), 0, _now_trade
        self._trade_ticks += 1
        self._trade_last = _now_trade
        if isinstance(ltt, (int, float)):
            # EPOCH timestamp — but Dhan segments are inconsistent about the
            # epoch base (MCX quote packets send IST-shifted epochs, which
            # would land candles 5h30m in the future). If the derived time
            # disagrees with the wall clock by more than 3 minutes, trust
            # the wall clock instead.
            tick_time = datetime.fromtimestamp(ltt, tz=_IST)
            _now_chk = datetime.now(_IST)
            if abs((tick_time - _now_chk).total_seconds()) > 180:
                if getattr(self, "_ltt_warned", 0) < 3:
                    self._ltt_warned = getattr(self, "_ltt_warned", 0) + 1
                    logger.warning(
                        f"LiveFeed: LTT {tick_time} deviates from wall clock "
                        f"{_now_chk} — using wall clock for candle slots")
                tick_time = _now_chk
        else:
            tick_time = datetime.now(_IST)

        self.last_tick_time = tick_time

        # Volume from tick (use 0 if not available — we'll use LTQ or delta)
        try:
            vol = int(float(data.get("volume", 0) or 0))
        except Exception:
            vol = 0
        try:
            ltq = int(float(data.get("LTQ") or data.get("ltq") or data.get("last_traded_qty") or 0))
        except Exception:
            ltq = 0
        if ltq <= 0 and vol > 0:
            # Index feeds carry no LTQ — use cumulative day-volume delta so
            # live candle volume matches the historical API (backtest parity)
            _delta = vol - getattr(self, "_last_cum_vol", 0)
            if _delta < 0:
                _delta = 0
            self._last_cum_vol = vol
            ltq = _delta

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

    def reconcile_recent(self, df_5m: pd.DataFrame = None, df_1m: pd.DataFrame = None,
                          max_age_minutes: int = 90, tolerance_pct: float = 0.0005) -> tuple:
        """Patch recently-completed candles against fresh REST historical data.

        After seed_candles() runs once at startup/reconnect, every candle from
        then on is built purely from local WebSocket ticks (see CandleBuilder
        .on_tick) with nothing cross-checking it against Dhan's own settled
        OHLC. Confirmed live on 2026-09-29: the very first candles of a session
        can differ from the REST-reported candle by 100+ points on a sharp
        opening move — exactly which ticks got captured first, not a data
        outage. This never self-heals on its own; only later candles (built
        from a fuller, calmer tick stream) happen to end up close to the
        truth. Call this periodically (see main.py's _feed_reconcile_loop) with
        a fresh REST pull to close that gap for candles old enough to be
        settled — the in-progress candle is intentionally never touched, since
        REST data for a still-forming bar isn't final either.

        Returns (corrected_5m_count, corrected_1m_count).
        """
        cutoff = datetime.now(_IST).replace(tzinfo=None) - timedelta(minutes=max_age_minutes)

        def _patch(builder: CandleBuilder, df: pd.DataFrame) -> int:
            if df is None or df.empty:
                return 0
            rest = {}
            for _, row in df.iterrows():
                ts = row["timestamp"]
                if isinstance(ts, str):
                    ts = pd.to_datetime(ts)
                if getattr(ts, "tzinfo", None) is not None:
                    ts = ts.tz_localize(None)
                ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                if ts < cutoff:
                    continue
                rest[ts] = (float(row["open"]), float(row["high"]), float(row["low"]),
                            float(row["close"]), int(row.get("volume", 0) or 0))

            if not rest:
                return 0

            corrected = 0
            new_candles = deque(maxlen=builder.max_candles)
            for c in builder.candles:
                ts = c["timestamp"]
                r = rest.get(ts)
                if r is not None:
                    o, h, l, cl, v = r
                    # relative tolerance on every field, not just close — exact `!=` on floats
                    # false-positives on harmless rounding noise (confirmed live 2026-09-29:
                    # logged "corrections" that replaced a candle with itself)
                    def _differs(a, b):
                        return abs(a - b) / b > tolerance_pct if b else a != b
                    drift = abs(c["close"] - cl) / cl if cl else 0.0
                    if _differs(c["open"], o) or _differs(c["high"], h) or _differs(c["low"], l) or _differs(c["close"], cl):
                        logger.info(
                            f"LiveFeed reconcile: {builder.tf_minutes}m candle {ts} corrected "
                            f"C={c['close']:.2f}->{cl:.2f} (drift {drift * 100:.2f}%)")
                        c = {"timestamp": ts, "open": o, "high": h, "low": l, "close": cl, "volume": v}
                        corrected += 1
                new_candles.append(c)
            if corrected:
                builder.candles = new_candles  # atomic reference swap — safe against the tick thread
            return corrected

        n5 = _patch(self.candle_5m, df_5m)
        n1 = _patch(self.candle_1m, df_1m)
        return n5, n1

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
