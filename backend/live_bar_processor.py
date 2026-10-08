"""
Live Bar Processor — runs each strategy's run_backtest() on live frames and
diffs consecutive results to detect new entries/exits.

Architecture:
    BacktestDiffProcessor (base) calls run_backtest(frames) — the EXACT same
    function that produces chart overlay signals.  By diffing consecutive
    backtest results it detects new entries and exits, guaranteeing that live
    signals are **identical** to the backtest / chart overlay.

    Each strategy gets a thin subclass that only specifies:
        - strategy_id   (e.g. "regime_trend_range")
        - _get_module() (returns the strategy module with run_backtest())
"""

import logging
import time as _time_mod
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import broker

logger = logging.getLogger(__name__)

# Fixed anchor matching the period the active strategy was actually
# train/validate/full-discipline validated against (see STRATEGY_REGISTRY.md
# and the 2026-08-31 backtest-matrix runs) -- deliberately NOT a sliding
# "N days back from today" window, so the warm-up context live evaluates on
# stays identical to what was validated. Cost grows slowly as trading days
# accumulate (see live_signal_vs_backtest_window_open_decision memory) --
# not urgent, revisit if a cycle ever creeps toward the 5-min ceiling.
_FULL_HISTORY_ANCHOR = "2025-07-01"


def _fetch_full_history_frames(instrument: str) -> Optional[Dict[str, pd.DataFrame]]:
    """Fetch full warm-up-buffered history, mirroring /api/backtest's exact
    methodology (100-day warm-up for 60/15/5, 1100-day for 1D) -- so the
    active strategy's live evaluation sees the same data shape it was
    validated on, instead of the 300-bar rolling window every other live
    strategy still uses. Returns None on any failure so the caller can fall
    back to the existing 300-bar path without disrupting live evaluation.
    """
    try:
        import pytz
        now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
        today = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
        from_dt = datetime.strptime(_FULL_HISTORY_ANCHOR, "%Y-%m-%d")
        warmup_from = (from_dt - timedelta(days=100)).strftime("%Y-%m-%d")
        daily_from = (from_dt - timedelta(days=1100)).strftime("%Y-%m-%d")

        frames = {}
        for tf_key, tf_dhan, query_from in [
            ("1D", "DAY", daily_from),
            ("60", "60", warmup_from),
            ("15", "15", warmup_from),
            ("5", "5", warmup_from),
        ]:
            df = broker.get_historical_data(instrument, tf_dhan, query_from, today)
            if df is None or len(df) < 30:
                logger.warning(f"[full-history] {tf_key}: only {len(df) if df is not None else 0} rows, aborting")
                return None
            frames[tf_key] = df
            _time_mod.sleep(0.3)
        return frames
    except Exception as e:
        logger.warning(f"[full-history] fetch failed: {e}")
        return None


# Found live 2026-10-06: the full history above was re-downloaded on EVERY candle (~28k 5m rows
# + 15m/60m/1D, 22.8 s measured; candle-close eval 14-34 s) and, straight from Dhan's REST, it
# ended on the still-FORMING candle (09:42:01 -> 5m last bar 09:40) -- the active strategy (Option B)
# was deciding on half-formed candles while the other strategies already saw completed ones only.
# Now the history is downloaded once a day and only its OLDER part is used; the recent part comes
# from the cleaned live frames every strategy gets (main._fetch_all_frames: completed 5m/1m candles,
# no after-hours bars, live tail) -- same data, same warm-up anchor, seconds instead of half a minute.
_full_hist_cache: Dict[str, dict] = {}   # instrument -> {"day": date, "frames": {...}}


def _active_eval_frames(instrument: str, frames: dict) -> Optional[Dict[str, pd.DataFrame]]:
    """Full-history evaluation frames for the active strategy: cached older history (downloaded
    once per day) + the cleaned recent frames. None on failure (caller falls back to 300 bars)."""
    import pytz
    today = datetime.now(pytz.timezone("Asia/Kolkata")).date()
    cached = _full_hist_cache.get(instrument)
    if not cached or cached["day"] != today:
        full = _fetch_full_history_frames(instrument)
        if not full:
            return None
        _full_hist_cache[instrument] = cached = {"day": today, "frames": full}
        logger.info(f"[full-history] {instrument}: history cached for {today} "
                    f"({', '.join(f'{k}={len(v)}' for k, v in full.items())} rows)")
    out = {}
    for tf, hist in cached["frames"].items():
        recent = frames.get(tf)
        if tf == "1D" or recent is None or len(recent) == 0:
            out[tf] = hist
            continue
        cut = pd.to_datetime(recent["timestamp"]).min()
        older = hist[pd.to_datetime(hist["timestamp"]) < cut]
        merged = pd.concat([older, recent], ignore_index=True)
        merged = merged.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
        out[tf] = merged
    return out


@dataclass
class LiveSignal:
    """A signal generated by the live bar processor."""
    signal_type: str       # "ENTRY" or "EXIT"
    direction: str         # "LONG" or "SHORT"
    signal: str            # "LONG", "SHORT", "LONG_EXIT", "SHORT_EXIT"
    time: str              # ISO timestamp of the bar
    entry_price: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pts: float = 0.0
    strategy: str = ""
    weighted_score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    macro_bias: str = ""
    adx_1h: float = 0.0
    atr_5m: float = 0.0
    h1_trend: str = ""
    regime: str = ""
    regime_confidence: float = 0.0
    playbook: str = ""
    entry_quality: float = 0.0
    ml_prob: float = 0.0
    rr_t1: float = 0.0
    risk_pts: float = 0.0
    long_score: float = 0.0
    short_score: float = 0.0

    def to_signal_dict(self) -> dict:
        """Convert to dict format compatible with _add_signal_to_history."""
        d = {
            "signal": self.signal,
            "time": self.time,
            "entry": self.entry_price if self.signal_type == "ENTRY" else self.exit_price,
            "close": self.exit_price if self.signal_type == "EXIT" else self.entry_price,
            "sl": self.sl,
            "target1": self.target1,
            "target2": self.target2,
            "strategy": self.strategy,
            "weighted_score": self.weighted_score,
            "reasons": self.reasons,
            "macro_bias": self.macro_bias,
            "adx_1h": self.adx_1h,
            "atr_5m": self.atr_5m,
            "h1_trend": self.h1_trend,
            "ml_prob": self.ml_prob,
            "rr_t1": self.rr_t1,
            "risk_pts": self.risk_pts,
            "long_score": self.long_score,
            "short_score": self.short_score,
            "regime": self.regime,
            "regime_confidence": self.regime_confidence,
            "playbook": self.playbook,
            "entry_quality": self.entry_quality,
        }
        if self.signal_type == "EXIT":
            d["reason"] = self.exit_reason
            d["exit_price"] = self.exit_price
            d["pnl"] = self.pnl
            d["pnl_pts"] = self.pnl_pts
        return d


def _to_ist_iso(ts) -> str:
    """Convert a timestamp to IST ISO string."""
    import pytz
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("Asia/Kolkata")
    return t.isoformat()


# Root-cause fix (2026-08-25) for the "stale replay" issue: as the rolling
# 300-bar eval window slides forward, a kernel's own indicator/regime state
# near the window's older edge isn't perfectly stable -- the same historical
# bar can go from "not a trade" to "a trade" between polls purely because the
# window's warm-up context for it changed, with no change in the underlying
# market data. That made the diff below occasionally "discover" a trade from
# days ago and report it as brand new. main.py already had downstream
# age-gates (Telegram, chart) that caught this before it reached the user,
# but the diff itself had no such check, so the same stale rediscovery could
# repeat indefinitely every time the window happened to flicker again.
# STALE_ENTRY_MAX_AGE_MIN is deliberately generous (not the 10-min cutoff
# main.py uses for order execution/alerts) -- normal poll-cycle detection
# latency measured up to ~8 min in practice, and unlike the downstream gates
# (which just skip an alert for an otherwise-still-tracked signal), silently
# dropping the ENTRY here also removes it from consideration forever on the
# next poll. A wide margin protects against ever discarding a genuine signal
# while still comfortably catching rediscoveries that are actually hours to
# days old.
STALE_ENTRY_MAX_AGE_MIN = 60.0

# Per-strategy override of the non-active trailing-window size (default 300
# bars below). Added 2026-10-01 for the RAM/Range-Filter engine family
# (custom_ram_rf_box.py and its Option A/B subclasses): RAM and Range Filter
# are recursive indicators whose state is built from index 0 of whatever
# array they're given, so a too-short window can diverge from what a full-
# history run would produce. Measured cost of 1000 vs 300 bars for this
# specific engine: ~32ms vs ~1.8ms per poll -- negligible against the 1-2s
# live-feed-driven poll cadence, and strategies already evaluate concurrently
# (asyncio.gather), so this adds no perceptible signal latency. The shared
# `frames` dict already carries far more than 1000 bars of 5m data every
# cycle (see main.py's _fetch_all_frames, ~30 calendar days), so widening
# this is a pure in-memory slice, not an extra fetch. Scoped to just this
# family rather than changed globally -- the 5 already-live production
# strategies stay at their validated 300-bar window, untouched.
_WIDE_WINDOW_BARS = {
    "custom_option_a_tg_ram_rf": 1000,
    "custom_option_b_ram_rf": 1000,
    "custom_ram_rf_box": 1000,
}
DEFAULT_WINDOW_BARS = 300


def _bar_age_minutes(bar_time) -> Optional[float]:
    """Age of a bar's own timestamp in minutes, or None if unparseable."""
    try:
        import pytz
        parsed = pd.to_datetime(bar_time)
        if getattr(parsed, "tzinfo", None) is not None:
            parsed = parsed.tz_convert("Asia/Kolkata").tz_localize(None)
        now_naive = pd.Timestamp.now(tz=pytz.timezone("Asia/Kolkata")).tz_localize(None)
        return (now_naive - parsed).total_seconds() / 60.0
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# GENERIC BACKTEST-DIFF PROCESSOR (base class)
# ═══════════════════════════════════════════════════════════════════════════════

class BacktestDiffProcessor:
    """Runs run_backtest(frames) and diffs consecutive results.

    Subclasses must set:
        strategy_id  – e.g. "regime_trend_range"
    and implement:
        _get_module() – return the strategy module with run_backtest()
    """

    strategy_id: str = ""

    def __init__(self):
        self._prev_trade_keys: set = set()
        self._prev_open_key: Optional[tuple] = None
        self._initialised = False
        self.position = "NONE"
        self.trade_source = "NONE"
        self.entry_price = 0.0
        self.sl = 0.0
        self.target1 = 0.0
        self.target2 = 0.0
        self._last_atr = 0.0
        self._last_close = 0.0
        self._entry_signal_detail: Optional[dict] = None
        self._last_exit_signal: Optional[str] = None
        self._last_exit_reason: Optional[str] = None
        self._regime = "SIDEWAYS"
        self.last_processed_ts = None
        self.adopted_open: Optional[dict] = None   # open trade found at init, to log once (W1)

    def _get_module(self):
        """Return the strategy module that has run_backtest(). Override in subclass."""
        raise NotImplementedError

    def reset(self):
        """Reset all state — call on daily boundary or restart."""
        self._prev_trade_keys = set()
        self._prev_open_key = None
        self._initialised = False
        self.position = "NONE"
        self.trade_source = "NONE"
        self.entry_price = 0.0
        self.sl = 0.0
        self.target1 = 0.0
        self.target2 = 0.0
        self._last_atr = 0.0
        self._last_close = 0.0
        self._entry_signal_detail = None
        self._last_exit_signal = None
        self._last_exit_reason = None
        self._regime = "SIDEWAYS"
        self.last_processed_ts = None
        logger.info(f"[{self.strategy_id}] Processor state reset")

    # ──────────────────────────────────────────────────────────────────
    #  CORE: run backtest, diff trades, emit LiveSignal objects
    # ──────────────────────────────────────────────────────────────────
    def _get_kernel(self):
        """Return registered StrategyKernel for this strategy."""
        try:
            from strategy_kernel import get_kernel
            return get_kernel(self.strategy_id)
        except Exception:
            return None

    def process_frames(self, frames: dict, cfg, lot_size: int = 30) -> List[LiveSignal]:
        # Validate minimum data
        required = ["5", "15", "60", "1D"]
        for r in required:
            if r not in frames or frames[r] is None or len(frames[r]) < 30:
                return []

        # Check if 5m candle timestamp has changed — skip redundant evaluation if unchanged
        if "5" in frames and not frames["5"].empty:
            current_5m_ts = frames["5"].iloc[-1].get("timestamp")
            if self._initialised and self.last_processed_ts == current_5m_ts:
                return []

        # Active strategy (the one whose signals drive Telegram + auto-trade)
        # gets full warm-up-buffered history instead of the 300-bar window —
        # validated 2026-08-31 offline: the 300-bar window never missed a
        # real backtest signal but fired ~1.7x extra phantom trades from
        # insufficient indicator warm-up. Falls back to the 300-bar path
        # below on any fetch failure, and for every other live strategy.
        eval_frames = None
        is_active = self.strategy_id == getattr(cfg, "strategy", None)
        if is_active and getattr(cfg, "live_active_strategy_full_history", False):
            full_frames = _active_eval_frames(getattr(cfg, "instrument", "BANKNIFTY"), frames)
            if full_frames:
                eval_frames = full_frames
            else:
                logger.warning(f"[{self.strategy_id}] full-history fetch failed, falling back to 300-bar window this cycle")

        if eval_frames is None:
            # Slice frames to a trailing window for fast live bar evaluation --
            # N=300 by default, wider for strategies in _WIDE_WINDOW_BARS.
            window = _WIDE_WINDOW_BARS.get(self.strategy_id, DEFAULT_WINDOW_BARS)
            eval_frames = {}
            for tf, df in frames.items():
                if df is not None and not df.empty:
                    eval_frames[tf] = df.tail(window).copy() if len(df) > window else df
                else:
                    eval_frames[tf] = df

        # Run kernel backtest
        kernel = self._get_kernel()
        if kernel is None:
            logger.error(f"CRITICAL: StrategyKernel for '{self.strategy_id}' not registered in strategy registry!")
            return []
        try:
            result = kernel.safe_run_backtest(eval_frames)
        except Exception as e:
            logger.error(f"[{self.strategy_id}] Strategy evaluation failed: {e}", exc_info=True)
            return []

        if not result or "error" in result:
            logger.debug(f"[{self.strategy_id}] Backtest returned: {result}")
            return []

        bt_trades = result.get("trades", [])
        stats = result.get("stats", {})
        qty = lot_size

        # Update regime from stats
        regime_dist = stats.get("regime_distribution", {})
        if regime_dist:
            self._regime = max(regime_dist, key=regime_dist.get)

        # Update last close / ATR from 5m frame
        if "5" in frames and not frames["5"].empty:
            last_bar = frames["5"].iloc[-1]
            self._last_close = float(last_bar.get("close", 0))
            atr_v = float(last_bar.get("atr", 0))
            self._last_atr = atr_v if atr_v > 5 else self._last_close * 0.002
            self.last_processed_ts = last_bar.get("timestamp")

        # ── First call: snapshot state, suppress signals ──
        # 2026-10-05 (your rule: a carry-forward position is one continuous trade, as the chart
        # shows it): the strategy's open trade is its CURRENT position from the first call --
        # no entry signal is emitted for it. The old check `not t.get("exit_time")` never matched
        # (an open trade's exit_time is "-", see the note in the diff loop below), so init always
        # logged open_key=no, the tile said HOLD after every restart, and the position appeared
        # silently on the 2nd evaluation (or never, after hours).
        if not self._initialised:
            self._initialised = True
            self._prev_trade_keys = {(t["direction"], t["entry_time"]) for t in bt_trades}
            self._prev_open_key = None
            for t in reversed(bt_trades):
                if t.get("exit_reason") == "OPEN":
                    self._prev_open_key = (t["direction"], t["entry_time"])
                    self.adopted_open = dict(t)      # main.py logs it in the Signals Log once (W1)
                    self.position = t["direction"]
                    self.entry_price = t.get("entry_price", 0)
                    self.sl = t.get("sl", 0)
                    self.target1 = t.get("target1", 0)
                    self.target2 = t.get("target2", 0)
                    self.trade_source = t.get("source", "REGIME")
                    break
            logger.info(
                f"[{self.strategy_id}] Initialised from backtest: "
                f"{len(bt_trades)} trades, open_key={'yes' if self._prev_open_key else 'no'}, regime={self._regime}"
            )
            return []

        # ── Diff: detect new entries and exits ──
        signals_out: List[LiveSignal] = []
        current_keys = set()
        current_open_key = None

        # ── Vanished open trade (rolling-window instability) ──
        # Found live 2026-09-01 and again 2026-10-05 (CUSUM15 SHORT 12:00 -> LONG 12:20, no exit):
        # as the trailing window slides, the re-evaluation can stop reproducing the trade we were
        # holding -- not closed, just ABSENT -- and the diff below only emits an exit for a trade
        # it can still see. Decide here, before the entry loop:
        #   * still open in the SAME direction under a different entry bar -> it's the same
        #     position whose entry bar moved (05 Oct 12:20 -> 12:25): no new entry is emitted;
        #   * flat now, or open the other way -> emit an explicit exit (WINDOW_ROLLOFF) at the
        #     last close first, so the position, journal and Telegram are closed properly.
        _new_open = None
        for _t in bt_trades:
            if _t.get("exit_reason") == "OPEN":
                _new_open = (_t["direction"], _t["entry_time"])
        _moved_entry_key = None
        if self._prev_open_key and self._prev_open_key not in {(x["direction"], x["entry_time"]) for x in bt_trades}:
            _pdir, _pentry = self._prev_open_key
            if _new_open and _new_open[0] == _pdir:
                _moved_entry_key = _new_open
                logger.warning(f"[{self.strategy_id}] Open {_pdir} from {_pentry} vanished from the re-evaluation; "
                               f"same direction is open from {_new_open[1]} -- treated as the SAME position "
                               f"(entry bar moved), no new entry emitted")
            else:
                _ts = _to_ist_iso(self.last_processed_ts) if self.last_processed_ts is not None else ""
                _px = float(self._last_close or 0.0)
                _ep = float(self.entry_price or 0.0)
                _pts = (_px - _ep) if _pdir == "LONG" else (_ep - _px)
                signals_out.append(self._exit_from_trade({
                    "direction": _pdir, "entry_time": _pentry, "entry_price": _ep,
                    "exit_time": _ts, "exit_price": _px, "exit_reason": "WINDOW_ROLLOFF",
                    "pnl_pts": _pts if _ep else 0.0, "pnl": (_pts * qty) if _ep else 0.0, "sl": self.sl,
                }, qty))
                logger.warning(f"[{self.strategy_id}] Open {_pdir} from {_pentry} vanished from the re-evaluation "
                               f"with no exit -- emitting WINDOW_ROLLOFF exit @ {_px}")

        for t in bt_trades:
            key = (t["direction"], t["entry_time"])
            current_keys.add(key)

            # NOTE: deliberately NOT also checking `not t.get("exit_time")` here.
            # safe_run_backtest() pipes every trade through
            # format_and_enrich_backtest_result(), which coerces a still-open
            # trade's empty exit_time ("") into the literal string "-" (truthy) —
            # combining that with exit_reason=="OPEN" made is_open permanently
            # False for every genuinely-open live position, which fired a
            # spurious same-batch EXIT right behind every ENTRY and then made
            # the position invisible to _prev_open_key tracking forever after.
            # exit_reason survives enrichment unchanged, so it alone is a
            # reliable signal of "still open".
            is_open = t.get("exit_reason") == "OPEN"
            if is_open:
                current_open_key = key

            # Was the previous open trade now closed?
            if key == self._prev_open_key and not is_open:
                signals_out.append(self._exit_from_trade(t, qty))
                logger.info(
                    f"[{self.strategy_id}] EXIT {t['direction']} "
                    f"reason={t.get('exit_reason')} @ {t.get('exit_price', 0)}"
                )

            # New trade not seen before? Only trust it as a genuine live
            # signal if its own bar is recent -- a "new" trade whose entry
            # is actually old is a rediscovered rolling-window artifact, not
            # real news (see STALE_ENTRY_MAX_AGE_MIN above). current_keys
            # already includes it either way, so it's folded into known
            # state and never reconsidered again regardless.
            if key not in self._prev_trade_keys and key == _moved_entry_key:
                pass   # same position as the vanished one (see above) -- not a new entry
            elif key not in self._prev_trade_keys:
                _age = _bar_age_minutes(t["entry_time"])
                if _age is not None and _age > STALE_ENTRY_MAX_AGE_MIN:
                    logger.info(
                        f"[{self.strategy_id}] Suppressed stale rediscovered "
                        f"{t['direction']} entry_time={t['entry_time']} "
                        f"age={_age:.1f}m (not emitted as live signal)"
                    )
                else:
                    signals_out.append(self._entry_from_trade(t))
                    logger.info(
                        f"[{self.strategy_id}] ENTRY {t['direction']} "
                        f"@ {t.get('entry_price', 0)} SL={t.get('sl', 0)} "
                        f"source={t.get('source', '?')}"
                    )
                    # If already closed on the same bar, also emit exit
                    if not is_open and t.get("exit_time"):
                        signals_out.append(self._exit_from_trade(t, qty))
                        logger.info(
                            f"[{self.strategy_id}] Immediate EXIT {t['direction']} "
                            f"reason={t.get('exit_reason')} (same bar)"
                        )

        # ── Update internal state ──
        self._prev_trade_keys = current_keys
        self._prev_open_key = current_open_key

        if current_open_key:
            for t in reversed(bt_trades):
                if (t["direction"], t["entry_time"]) == current_open_key:
                    self.position = t["direction"]
                    self.entry_price = t.get("entry_price", 0)
                    self.sl = t.get("sl", 0)
                    self.target1 = t.get("target1", 0)
                    self.target2 = t.get("target2", 0)
                    self.trade_source = t.get("source", "REGIME")
                    self._last_exit_signal = None
                    self._last_exit_reason = None
                    break
        else:
            if self.position != "NONE":
                last_closed = bt_trades[-1] if bt_trades else None
                if last_closed and last_closed.get("exit_time"):
                    self._last_exit_signal = (
                        "LONG_EXIT" if last_closed["direction"] == "LONG" else "SHORT_EXIT"
                    )
                    self._last_exit_reason = last_closed.get("exit_reason", "")
            self.position = "NONE"
            self.entry_price = 0.0
            self.sl = 0.0
            self.target1 = 0.0
            self.target2 = 0.0
            self.trade_source = "NONE"

        if signals_out:
            logger.info(
                f"[{self.strategy_id}] Generated {len(signals_out)} signals "
                f"from backtest diff ({len(bt_trades)} trades total)"
            )
        return signals_out

    # ──────────────────────────────────────────────────────────────────
    #  UI signal (for the frontend signal panel)
    # ──────────────────────────────────────────────────────────────────
    def get_ui_signal(self, cfg) -> dict:
        if self._last_exit_signal is not None:
            sig_value = self._last_exit_signal
            exit_reason = self._last_exit_reason or ""
            self._last_exit_signal = None
            self._last_exit_reason = None
        elif self.position != "NONE":
            sig_value = self.position
            exit_reason = ""
        else:
            sig_value = "HOLD"
            exit_reason = ""

        return {
            "signal": sig_value,
            "strategy": self.strategy_id,
            "time": _to_ist_iso(self.last_processed_ts) if self.last_processed_ts else "",
            "entry": round(self.entry_price, 2) if self.position != "NONE" else 0.0,
            # bar the strategy's open trade was entered on ("time" above is the latest bar)
            "position_entry_time": (_to_ist_iso(self._prev_open_key[1])
                                    if self.position != "NONE" and self._prev_open_key else ""),
            "sl": round(self.sl, 2),
            "target1": round(self.target1, 2),
            "target2": round(self.target2, 2),
            "regime": self._regime,
            "regime_confidence": 0.0,
            "playbook": "",
            "weighted_score": 0.0,
            "atr_5m": round(self._last_atr, 2),
            "close": round(self._last_close, 2),
            "h1_trend": "",
            "macro_bias": "",
            "adx_1h": 0.0,
            "ml_prob": 0.0,
            "rr_t1": 0.0,
            "risk_pts": 0.0,
            "long_score": 0.0,
            "short_score": 0.0,
            "reasons": [exit_reason] if exit_reason else [],
            "trade_source": self.trade_source,
        }

    # ──────────────────────────────────────────────────────────────────
    #  Helpers: convert backtest trade dicts → LiveSignal objects
    # ──────────────────────────────────────────────────────────────────
    def _entry_from_trade(self, t: dict) -> LiveSignal:
        direction = t["direction"]
        return LiveSignal(
            signal_type="ENTRY",
            direction=direction,
            signal=direction,
            time=t.get("entry_time", ""),
            entry_price=round(t.get("entry_price", 0), 2),
            sl=round(t.get("sl", 0), 2),
            target1=round(t.get("target1", 0), 2),
            target2=round(t.get("target2", 0), 2),
            strategy=self.strategy_id,
            reasons=[f"Source: {t.get('source', 'STRATEGY')}"],
            regime=self._regime,
            atr_5m=round(self._last_atr, 2),
        )

    def _exit_from_trade(self, t: dict, qty: int) -> LiveSignal:
        direction = t["direction"]
        exit_signal = "LONG_EXIT" if direction == "LONG" else "SHORT_EXIT"
        return LiveSignal(
            signal_type="EXIT",
            direction=direction,
            signal=exit_signal,
            time=t.get("exit_time", ""),
            entry_price=round(t.get("entry_price", 0), 2),
            exit_price=round(t.get("exit_price", 0), 2),
            exit_reason=t.get("exit_reason", ""),
            pnl=round(t.get("pnl", 0), 2),
            pnl_pts=round(t.get("pnl_pts", 0), 2),
            sl=round(t.get("sl", 0), 2),
            strategy=self.strategy_id,
            reasons=[f"Exit: {t.get('exit_reason', '')} @ {t.get('exit_price', 0)}"],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY-SPECIFIC PROCESSORS (thin wrappers)
# ═══════════════════════════════════════════════════════════════════════════════

# ── W7(a): strategies that need real volume decide on Dhan's official candle ──────────────────
import pytz as _pytz_w7
_IST_W7 = _pytz_w7.timezone("Asia/Kolkata")
_official_5m_last = None    # last 5m bar timestamp with Dhan's official volume (main.py sets it)


def set_official_5m_last(ts):
    global _official_5m_last
    _official_5m_last = ts


class OfficialVolumeMixin:
    """Found live 07/08 Oct (W7/H3): Dhan's index feed carries no volume, so the live-built newest
    candle has volume 0. Alpha Combo, Time-Gated, CUSUM15 and Regime V1 use VWAP (checked: random
    volume changes their trades; Option B's do not), so at the close they saw volume 0, found no
    signal, and the signal appeared a candle later -- backdated -- once Dhan's official candle
    replaced it. Your choice (a): evaluate them on official candles only, as soon as Dhan publishes
    them (main.py refreshes every poll until it does), and record the signal at the live price and
    time of detection, not backdated."""

    def process_frames(self, frames: dict, cfg, lot_size: int = 30) -> List[LiveSignal]:
        f = frames
        last_off = _official_5m_last
        try:
            if last_off is not None and frames.get("5") is not None and len(frames["5"]):
                df5 = frames["5"]
                cut = df5[pd.to_datetime(df5["timestamp"]) <= pd.Timestamp(last_off)]
                if 30 <= len(cut) < len(df5):
                    f = dict(frames)
                    f["5"] = cut
        except Exception as e:
            logger.debug(f"[{self.strategy_id}] official-candle cut skipped: {e}")
        sigs = super().process_frames(f, cfg, lot_size)
        if sigs:
            live = None
            try:
                live = broker.get_ltp(getattr(cfg, "instrument", "BANKNIFTY"))
            except Exception:
                live = None
            now_iso = datetime.now(_IST_W7).isoformat()
            for sg in sigs:
                bar = sg.time
                if live:
                    if sg.signal_type == "ENTRY":
                        sg.entry_price = round(float(live), 2)
                    else:
                        sg.exit_price = round(float(live), 2)
                sg.time = now_iso
                sg.reasons = list(sg.reasons or []) + [
                    f"Decided on Dhan's official {str(bar)[11:16]} candle (live candles carry no volume); "
                    f"price and time at detection"]
        return sigs


class RegimeLiveProcessor(BacktestDiffProcessor):
    """Regime Trend/Range Optimized strategy."""
    strategy_id = "regime_trend_range"


class MultiAgentLiveProcessor(BacktestDiffProcessor):
    """Multi-Agent V3 Kernel strategy."""
    strategy_id = "multi_agent"


class RegimeTrendV2LiveProcessor(BacktestDiffProcessor):
    """Regime Trend V2 — Selective (optimized winner)."""
    strategy_id = "regime_trend_v2"


class RegimeTrendV2BLiveProcessor(BacktestDiffProcessor):
    """Regime Trend V2-B — Balanced (runner-up)."""
    strategy_id = "regime_trend_v2b"


class DonchianSwingLiveProcessor(BacktestDiffProcessor):
    """Donchian 5m Swing (overnight-capable)."""
    strategy_id = "donchian_5m_swing"


class DonchianIntradayLiveProcessor(BacktestDiffProcessor):
    """Donchian 5m Intraday (flat by 15:15)."""
    strategy_id = "donchian_5m_intraday"


class RegimeV1FinalLiveProcessor(OfficialVolumeMixin, BacktestDiffProcessor):
    """Regime T/R V1 Final (Research) -- Donchian+CUSUM(2.0)+HTF-hold+
    SuperTrend-adaptive on the trend side, VWAP+half-life on the range
    side. Promoted to live after the scratch/research_v1/ session."""
    strategy_id = "custom_regime_v1_trend_range_final"


class OptionBRamRFLiveProcessor(BacktestDiffProcessor):
    """Option B: Ram > Range Filter (hold-only + no-progress) -- replaces
    HalfTrend + Hull Standalone in the live loop, 2026-10-01 (see
    scratch/research_14L/FINDINGS.md rounds 12-21 and the live-wiring audit
    the same day: SL/target fix, 1000-bar window via _WIDE_WINDOW_BARS
    above, target2 informational-only via main.py's _INFO_ONLY_TARGET2).
    No trailing SL (not in main.py's _TRAIL_PARAMS) -- its own RAM/RF/
    no-progress signals are the only exit path, by design.

    W12 (07 Oct): the kernel enters at the NEXT bar's open and exits Ram trades through a limit
    at the UT bar's close. The plain diff only sees both after the fill bar has completed -- a
    bar late (today: SHORT seen 13:05 instead of 13:00; exit seen 14:30 after a +243-pt bar).
    Over 5 years that lag cost ~17,300 of 62,200 pts and nearly doubled the drawdown. So:
      * at each candle close the kernel also runs with a provisional next bar at the current
        price; a Ram entry / reversal on that bar is decided by the bar that just closed, so it
        is acted on now (other events on the provisional bar are ignored);
      * the Ram limit still armed after the last close is published as pending_limit_exit;
        main.py exits when the index touches it (forming bar high/low or last price), unless Range
        Filter (as of the last close) points the same way -- then it keeps holding, as in the kernel."""
    strategy_id = "custom_option_b_ram_rf"
    LIMIT_PEN = 0.0      # exit on a touch, exactly like the kernel's limit fill

    def __init__(self):
        super().__init__()
        self.pending_limit_exit: Optional[dict] = None
        self._prov_for_ts = None

    def reset(self):
        super().reset()
        self.pending_limit_exit = None
        self._prov_for_ts = None

    def _eval_5m(self, frames: dict):
        window = _WIDE_WINDOW_BARS.get(self.strategy_id, DEFAULT_WINDOW_BARS)
        df = frames["5"]
        return df.tail(window).copy() if len(df) > window else df.copy()

    def _update_limit_state(self, b5):
        """Ram's armed limit after the last completed bar (same tracks as the kernel)."""
        import strategies.custom.custom_ram_rf_box as rk
        b = b5.copy()
        b["timestamp"] = pd.to_datetime(b["timestamp"])
        t = b.timestamp.dt.time
        b = b[(t >= pd.Timestamp("09:15").time()) & (t <= pd.Timestamp("15:25").time())].reset_index(drop=True)
        self.pending_limit_exit = None
        if len(b) < 300:
            return
        o, h, l, c = (b[k].values.astype(float) for k in ("open", "high", "low", "close"))
        ram, rf = rk._ram_track(o, h, l, c), rk._rf_track(c)
        kern = self._get_kernel()
        bars, pts = getattr(kern, "NP_BARS", 4), getattr(kern, "NP_PTS", 10.0)
        rk._no_progress(ram, h, l, c, bars, pts)
        rk._no_progress(rf, h, l, c, bars, pts)
        ram_dir = int(ram.dir_end[-1])
        lim = getattr(ram, "lim_last", None)
        if ram_dir == 0 or lim is None:
            return
        direction = "LONG" if ram_dir > 0 else "SHORT"
        if self.position != direction:
            return
        self.pending_limit_exit = {
            "direction": direction, "level": round(float(lim), 2),
            "rf_holds": int(rf.dir_end[-1]) == ram_dir,
            "armed_at": str(b.timestamp.iloc[-1]),
        }
        logger.info(f"[{self.strategy_id}] Ram limit exit armed: {direction} level {lim:.2f} "
                    f"(exit {'held by Range Filter -- no intrabar exit' if self.pending_limit_exit['rf_holds'] else 'when the index touches it'})")

    def _provisional_signals(self, frames: dict, last_ts, qty: int) -> List[LiveSignal]:
        """Run the kernel with a provisional next bar at the current price; act now on a Ram
        entry/reversal there (it depends only on the bar that just closed)."""
        last_ts = pd.Timestamp(last_ts)
        if last_ts.time() >= pd.Timestamp("15:25").time():
            return []                      # next bar is tomorrow's open: the normal diff handles it
        if last_ts.time() <= pd.Timestamp("09:15").time():
            # Found live 08 Oct: the feed-built 09:15 candle missed the opening auction print (its
            # open/high came out 54,989.50 / 55,002.85 vs Dhan's 55,042.90 / 55,043.00), which made
            # Ram SELL at 09:20; the candle was corrected from Dhan's data ~09:23 and the SHORT
            # vanished at 09:25. live_feed now sets that candle from the exchange's day open/high/low;
            # only if that didn't happen today (e.g. feed connected late) does a first-candle signal
            # wait for the correction (the normal diff emits it at 09:25).
            official = False
            try:
                from live_feed import get_live_feed
                official = get_live_feed().first_candle_official(last_ts.date())
            except Exception:
                official = False
            if not official:
                logger.info(f"[{self.strategy_id}] no early entry on the 09:15 candle: it was not set from the "
                            f"exchange's day open/high/low today (Dhan's data corrects it ~09:23)")
                return []
        next_ts = last_ts + pd.Timedelta(minutes=5)
        px = 0.0
        try:
            import broker as _bk
            px = float(_bk.get_ltp(frames.get("_instrument", "BANKNIFTY")) or 0.0)
        except Exception:
            px = 0.0
        b5 = self._eval_5m(frames)
        if not px:
            px = float(b5.iloc[-1]["close"])
        row = {col: np.nan for col in b5.columns}
        row.update({"timestamp": next_ts if not hasattr(b5["timestamp"].iloc[-1], "tzinfo") or b5["timestamp"].iloc[-1].tzinfo is None
                    else next_ts.tz_localize(b5["timestamp"].iloc[-1].tzinfo) if next_ts.tzinfo is None else next_ts,
                    "open": px, "high": px, "low": px, "close": px, "volume": 0})
        prov = {k: v for k, v in frames.items() if k != "_instrument"}
        prov["5"] = pd.concat([b5, pd.DataFrame([row])], ignore_index=True)
        kernel = self._get_kernel()
        if kernel is None:
            return []
        try:
            res = kernel.safe_run_backtest(prov)
        except Exception as e:
            logger.warning(f"[{self.strategy_id}] provisional evaluation failed: {e}")
            return []
        trades = (res or {}).get("trades", []) if not (res or {}).get("error") else []
        new = [t for t in trades
               if pd.Timestamp(t["entry_time"]).tz_localize(None) == next_ts.tz_localize(None)
               and (t["direction"], t["entry_time"]) not in self._prev_trade_keys]
        if not new:
            return []
        t_new = new[-1]
        out: List[LiveSignal] = []
        if self._prev_open_key and self._prev_open_key[0] != t_new["direction"]:
            old = next((t for t in trades if (t["direction"], t["entry_time"]) == self._prev_open_key), None)
            if old is not None and old.get("exit_reason") != "OPEN":
                out.append(self._exit_from_trade(old, qty))
                logger.info(f"[{self.strategy_id}] EXIT (at the signal-bar close) {old['direction']} "
                            f"reason={old.get('exit_reason')} @ {old.get('exit_price')}")
        out.append(self._entry_from_trade(t_new))
        logger.info(f"[{self.strategy_id}] ENTRY (at the signal-bar close, fill bar {t_new['entry_time']}) "
                    f"{t_new['direction']} @ {t_new.get('entry_price')} SL={t_new.get('sl')}")
        key = (t_new["direction"], t_new["entry_time"])
        self._prev_trade_keys.add(key)
        self._prev_open_key = key
        self.position = t_new["direction"]
        self.entry_price = t_new.get("entry_price", 0)
        self.sl = t_new.get("sl", 0)
        self.target1 = t_new.get("target1", 0)
        self.target2 = t_new.get("target2", 0)
        self.trade_source = t_new.get("source", "STRATEGY")
        self._last_exit_signal = None
        self._last_exit_reason = None
        self.pending_limit_exit = None     # a fresh Ram position has no limit armed yet
        return out

    def process_frames(self, frames: dict, cfg, lot_size: int = 30) -> List[LiveSignal]:
        signals = super().process_frames(frames, cfg, lot_size)
        try:
            if not self._initialised or self.last_processed_ts is None or self._prov_for_ts == self.last_processed_ts:
                return signals
            self._prov_for_ts = self.last_processed_ts
            if any(s.signal_type == "ENTRY" for s in signals):
                self.pending_limit_exit = None
                return signals             # the bar itself produced an entry: nothing more this close
            self._update_limit_state(self._eval_5m(frames))
            prov = self._provisional_signals({**frames, "_instrument": getattr(cfg, "instrument", "BANKNIFTY")},
                                             self.last_processed_ts, lot_size)
            return signals + prov
        except Exception as e:
            logger.warning(f"[{self.strategy_id}] signal-bar-close handling failed: {e}", exc_info=True)
            return signals


class Cusum15LiveProcessor(OfficialVolumeMixin, BacktestDiffProcessor):
    """CUSUM 1.5 + No Donchian + CD8 (Research) -- best points/PF/net of
    the whole research session; maxDD runs ~0.9pp over the user's 7% cap,
    accepted deliberately. Promoted to live after the scratch/research_v1/
    session."""
    strategy_id = "custom_cusum15_nodonchian_cd8"


class AlphaComboLiveProcessor(OfficialVolumeMixin, BacktestDiffProcessor):
    """Alpha Combo (CUSUM 1.25 Tuned) -- CUSUM 1.5/No-Donchian/CD8 with the
    CUSUM threshold loosened to 1.25; beat the CUSUM 1.5 baseline on every
    metric in every train/validate/full window. Default live strategy,
    promoted 2026-08-22 after that strategy also beat every dual-engine
    pyramid variant tried in the same research session."""
    strategy_id = "custom_alpha_combo_cusum125"


class TimeGatedAlphaComboLiveProcessor(OfficialVolumeMixin, BacktestDiffProcessor):
    """Time-Gated Alpha Combo -- Alpha Combo with new entries blocked during
    two intraday "trap" windows (10:00-10:45 AM, 1:00-1:45 PM); existing
    positions still managed normally through those windows. Promoted to
    live 2026-08-23 after train/validate/full discipline plus a deep
    trade-level audit (chronological/overlap check, zero trap-window
    entries confirmed empirically, independent stats recompute, and a
    signal-reversal falsification test). BankNifty-only validation -- see
    STRATEGY_REGISTRY.md at the repo root."""
    strategy_id = "custom_time_gated_alpha_combo"


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCES — created lazily, on first use.
# Only strategies actually driven by the live loop get a processor instance;
# exploration strategies cost nothing until they are promoted to live.
# ═══════════════════════════════════════════════════════════════════════════════

_processors: Dict[str, BacktestDiffProcessor] = {}


def _get_processor(cls) -> BacktestDiffProcessor:
    p = _processors.get(cls.strategy_id)
    if p is None:
        p = cls()
        _processors[cls.strategy_id] = p
    return p


def get_regime_processor() -> RegimeLiveProcessor:
    return _get_processor(RegimeLiveProcessor)

def get_multi_agent_processor() -> MultiAgentLiveProcessor:
    return _get_processor(MultiAgentLiveProcessor)

def get_regime_v2_processor() -> RegimeTrendV2LiveProcessor:
    return _get_processor(RegimeTrendV2LiveProcessor)

def get_regime_v2b_processor() -> RegimeTrendV2BLiveProcessor:
    return _get_processor(RegimeTrendV2BLiveProcessor)

def get_donchian_swing_processor() -> DonchianSwingLiveProcessor:
    return _get_processor(DonchianSwingLiveProcessor)

def get_donchian_intraday_processor() -> DonchianIntradayLiveProcessor:
    return _get_processor(DonchianIntradayLiveProcessor)

def get_regime_v1_final_processor() -> RegimeV1FinalLiveProcessor:
    return _get_processor(RegimeV1FinalLiveProcessor)

def get_option_b_processor() -> OptionBRamRFLiveProcessor:
    return _get_processor(OptionBRamRFLiveProcessor)

def get_cusum15_processor() -> Cusum15LiveProcessor:
    return _get_processor(Cusum15LiveProcessor)

def get_alpha_combo_processor() -> AlphaComboLiveProcessor:
    return _get_processor(AlphaComboLiveProcessor)

def get_time_gated_alpha_combo_processor() -> TimeGatedAlphaComboLiveProcessor:
    return _get_processor(TimeGatedAlphaComboLiveProcessor)

def reset_all_processors():
    """Reset the processors that are actually in use — call at daily boundary."""
    for p in _processors.values():
        p.reset()
