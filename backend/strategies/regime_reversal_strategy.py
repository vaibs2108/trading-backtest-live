"""
regime_reversal_strategy.py — Combined Regime Trend + Regression Reversal.

BACKTEST ONLY.

Logic:
  1. Regime trend strategy is the BASE — provides trend direction and entries.
     When regime says LONG/SHORT, we enter and hold with trailing SL.
  2. If a Regression Reversal signal fires in the SAME direction as the
     current regime trade → do nothing (already in the right trade).
  3. If a Regression Reversal signal fires in the OPPOSITE direction →
     temporarily exit the regime trade, enter the reversal trade with
     band-based SL. Reversal trades are short-duration counter-trend scalps.
  4. When the reversal trade closes (SL hit or opposite reversal) →
     check if regime trend signal is still valid. If yes, re-enter the
     regime direction. If not, wait for a fresh regime signal.
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional

import strategy as main_strategy
from config import get_settings, INSTRUMENT_META

# Regime agents
from .regime_agents.regime_detection import RegimeDetectionAgent
from .regime_agents.trend_entry import TrendEntryAgent
from .regime_agents.range_entry import RangeEntryAgent, RangeEntryState
from .regime_agents.htf_structure import HTFStructureAgent
from .regime_agents.momentum_volume import MomentumVolumeAgent
from .regime_agents.regime_orchestrator import RegimeOrchestrator

# Reversal indicators
from .trend_reversal_strategy import add_indicators as reversal_add_indicators

logger = logging.getLogger(__name__)

# ── Agent instances (isolated from regime_strategy.py singletons) ──────────
_regime_agent = RegimeDetectionAgent(lookback=15, swing_order=3)
_trend_agent = TrendEntryAgent(swing_order=3, min_pullback=0.20, max_pullback=0.65)
_range_agent = RangeEntryAgent(lookback=40, proximity_pct=0.15, min_range_atr=2.0)
_htf_agent = HTFStructureAgent(swing_order=5, proximity_atr=0.5)
_momentum_agent = MomentumVolumeAgent(lookback=15)
_orchestrator = RegimeOrchestrator(min_quality=0.25, min_rr=0.0, min_regime_age=1)

_EOD_EXIT_NSE = 15 * 60 + 20   # 3:20 PM IST (equity)
_EOD_EXIT_MCX = 23 * 60 + 20   # 11:20 PM IST (commodity)

def _eod_exit_minute(cfg=None):
    if cfg is None:
        cfg = get_settings()
    exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
    return _EOD_EXIT_MCX if exch == "MCX" else _EOD_EXIT_NSE

# Trailing SL config (same as regime_strategy)
TRAIL_MULT = 1.5
TRAIL_ACTIVATION = 0.3
BE_TRIGGER = 0.4
BE_BUFFER = 0.3

# ── Live state persistence ─────────────────────────────────────────────────
import threading
_live_lock = threading.Lock()
_live_regime = "SIDEWAYS"
_live_regime_age = 0
_live_htf_cache = None
_live_htf_cache_hour = -1
_live_trade_source = "NONE"         # "REGIME" | "REVERSAL" | "NONE"
_live_last_regime_direction = "NONE"  # last known regime signal for re-entry
_live_last_regime_sl = 0.0

# Re-export
add_indicators = main_strategy.add_indicators


def _session_mask(base: pd.DataFrame, cfg) -> pd.Series:
    exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
    mins = base["_hour"] * 60 + base["_minute"]
    if exch == "MCX":
        start, end_excl = 9 * 60, 23 * 60 + 30
    else:
        start, end_excl = 9 * 60 + 20, 15 * 60 + 20
    return (mins >= start) & (mins < end_excl)


def _run_agents(df_slice, row, position, cfg, df_1h, df_1d, df_1w, _state):
    """Run regime agents with isolated state. Returns orchestrator signal."""
    close = float(row.get("close", 0))
    atr = float(row.get("atr", close * 0.002))
    if pd.isna(atr) or atr < 1:
        atr = close * 0.002

    regime = _regime_agent.evaluate(df_slice, _state["regime"], _state["regime_age"])
    _state["regime"] = regime.regime
    _state["regime_age"] = regime.regime_age

    cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
    if _state["htf_cache"] is None or cur_hour != _state["htf_cache_hour"]:
        _state["htf_cache"] = _htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
        _state["htf_cache_hour"] = cur_hour
    htf = _state["htf_cache"]

    trend_entry = _trend_agent.evaluate(
        df_slice, row, regime.regime, regime.confidence, position, atr
    )

    range_entry = RangeEntryState(signal="HOLD", reasons=["Trend-only mode"])

    proposed_signal = trend_entry.signal if trend_entry.signal in ("LONG", "SHORT") else "HOLD"
    momentum = _momentum_agent.evaluate(df_slice, proposed_signal, regime.regime)

    signal = _orchestrator.evaluate(
        row, regime, trend_entry, range_entry, htf, momentum, position
    )
    return signal


def get_current_signal(frames: dict, position: str = "NONE") -> dict:
    """Compute live signal combining regime trend + regression reversal.

    Flow:
      1. Build merged table, compute reversal indicators
      2. Check if a reversal signal fires on the latest completed bar
      3. If in a regime trade and opposite reversal fires → REV_OVERRIDE exit + reversal entry
      4. If in a reversal trade and it was closed (position=NONE) → check regime re-entry
      5. Otherwise → delegate to regime agents for trend entries/exits
    """
    global _live_regime, _live_regime_age, _live_htf_cache, _live_htf_cache_hour
    global _live_trade_source, _live_last_regime_direction, _live_last_regime_sl

    cfg = get_settings()

    required = ["5", "15", "60", "1D"]
    for r in required:
        if r not in frames or frames[r] is None or len(frames[r]) < 30:
            return {"signal": "HOLD", "reason": f"Insufficient data for TF={r}",
                    "strategy": "regime_reversal"}

    try:
        base = main_strategy.build_merged_table(frames, with_patterns=False)
    except Exception as e:
        logger.error(f"[RegimeReversal] Failed to build merged table: {e}")
        return {"signal": "HOLD", "reason": f"Data error: {e}",
                "strategy": "regime_reversal"}

    if base.empty:
        return {"signal": "HOLD", "reason": "Empty merged table",
                "strategy": "regime_reversal"}

    base_filtered = base[_session_mask(base, cfg)].copy()
    if base_filtered.empty:
        return {"signal": "HOLD", "reason": "Outside trading window",
                "strategy": "regime_reversal"}

    # ── Compute reversal indicators ──
    try:
        base_filtered = reversal_add_indicators(base_filtered, method="Linear",
                                                 window=50, smoothness=30.0)
    except Exception as e:
        logger.warning(f"[RegimeReversal] Reversal indicators failed: {e}")
        # Fall through — reversal signals will be False

    # ── Find the latest completed bar ──
    from datetime import datetime, timedelta
    import pytz
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata")).replace(tzinfo=None)
    latest_idx = -1
    for offset in range(1, len(base_filtered) + 1):
        candidate = base_filtered.iloc[-offset]
        candidate_time = pd.to_datetime(candidate["timestamp"])
        if cfg.data_stale_threshold_min > 0 and candidate_time.date() != now_ist.date():
            continue
        if now_ist >= candidate_time + timedelta(minutes=5):
            latest_idx = len(base_filtered) - offset
            break

    if latest_idx == -1:
        return {"signal": "HOLD", "reason": "No completed 5m bars",
                "strategy": "regime_reversal"}

    latest = base_filtered.iloc[latest_idx]
    context_start = max(0, latest_idx - 80)
    df_slice = base_filtered.iloc[context_start:latest_idx + 1].copy()

    close_val = float(latest["close"])
    atr_val = float(latest.get("atr", close_val * 0.002))
    if pd.isna(atr_val) or atr_val < 1:
        atr_val = close_val * 0.002

    # ── Reversal signals on latest bar ──
    has_top_sig = bool(latest.get("top_sig", False))  # Bearish reversal
    has_bot_sig = bool(latest.get("bot_sig", False))  # Bullish reversal
    upper_val = float(latest.get("upper", close_val + 2 * atr_val))
    lower_val = float(latest.get("lower", close_val - 2 * atr_val))
    if pd.isna(upper_val):
        upper_val = close_val + 1.5 * atr_val
    if pd.isna(lower_val):
        lower_val = close_val - 1.5 * atr_val

    # ── HTF data ──
    df_1h = frames.get("60")
    df_1d = frames.get("1D")
    df_1w = None
    if df_1d is not None and len(df_1d) >= 30:
        try:
            from features.weekly import derive_weekly_from_daily
            df_1w = derive_weekly_from_daily(df_1d)
        except Exception:
            pass

    with _live_lock:
        # ── Build live regime state dict ──
        _live_state = {
            "regime": _live_regime,
            "regime_age": _live_regime_age,
            "htf_cache": _live_htf_cache,
            "htf_cache_hour": _live_htf_cache_hour,
        }

        # Run regime agents to get regime signal
        regime_sig = _run_agents(df_slice, latest, position, cfg,
                                 df_1h, df_1d, df_1w, _live_state)

        # Persist regime state
        _live_regime = _live_state["regime"]
        _live_regime_age = _live_state["regime_age"]
        _live_htf_cache = _live_state["htf_cache"]
        _live_htf_cache_hour = _live_state["htf_cache_hour"]

        result = regime_sig.to_dict()
        result["strategy"] = "regime_reversal"

        # ════════════════════════════════════════════════════════════
        #  CASE 1: Currently in a position
        # ════════════════════════════════════════════════════════════
        if position in ("LONG", "SHORT"):

            # Check for counter-trend reversal override
            if position == "LONG" and has_top_sig:
                # Bearish reversal while LONG → exit LONG, suggest SHORT reversal
                _live_trade_source = "REVERSAL"
                _live_last_regime_direction = "LONG"
                result["signal"] = "LONG_EXIT"
                result["reason"] = "REV_OVERRIDE: Bearish reversal while in LONG"
                result["reasons"] = ["Bearish reversal signal at upper band",
                                     "Counter-trend override — will re-enter regime if valid"]
                result["exit_reason"] = "REV_OVERRIDE"
                # Note: the polling loop will handle the exit, then on next
                # call with position=NONE we'll check for reversal entry
                logger.info("[RegimeReversal] REV_OVERRIDE: Bearish reversal → exit LONG")
                return result

            elif position == "SHORT" and has_bot_sig:
                # Bullish reversal while SHORT → exit SHORT, suggest LONG reversal
                _live_trade_source = "REVERSAL"
                _live_last_regime_direction = "SHORT"
                result["signal"] = "SHORT_EXIT"
                result["reason"] = "REV_OVERRIDE: Bullish reversal while in SHORT"
                result["reasons"] = ["Bullish reversal signal at lower band",
                                     "Counter-trend override — will re-enter regime if valid"]
                result["exit_reason"] = "REV_OVERRIDE"
                logger.info("[RegimeReversal] REV_OVERRIDE: Bullish reversal → exit SHORT")
                return result

            # Same-direction reversal → do nothing extra, let regime handle
            # Regime exit signals pass through normally
            if regime_sig.signal in ("LONG_EXIT", "SHORT_EXIT"):
                _live_trade_source = "NONE"
                _live_last_regime_direction = "NONE"

            return result

        # ════════════════════════════════════════════════════════════
        #  CASE 2: No position — check for entries
        # ════════════════════════════════════════════════════════════

        # Priority 1: If we just exited a reversal override, check regime re-entry
        if _live_trade_source == "REVERSAL" and _live_last_regime_direction != "NONE":
            # Check if regime signal still valid in the same direction
            if regime_sig.signal == _live_last_regime_direction:
                # Regime still valid → re-enter
                _live_trade_source = "REGIME"
                result["signal"] = _live_last_regime_direction
                result["reason"] = f"Regime re-entry after reversal trade closed"
                result["reasons"].append("Re-entering regime trend after reversal scalp")
                _live_last_regime_direction = regime_sig.signal
                logger.info(f"[RegimeReversal] Regime re-entry: {regime_sig.signal}")
                return result
            else:
                # Regime no longer valid — clear state, wait
                _live_trade_source = "NONE"
                _live_last_regime_direction = "NONE"

        # Priority 2: Check reversal signals for quick scalp
        if has_bot_sig:
            _live_trade_source = "REVERSAL"
            result["signal"] = "LONG"
            result["sl"] = lower_val
            result["entry"] = close_val
            result["reason"] = "Bullish reversal at lower band (reversal scalp)"
            result["reasons"] = ["Bullish reversal candlestick pattern",
                                 "Price at lower regression band",
                                 "Short-duration counter-trend trade"]
            logger.info(f"[RegimeReversal] Reversal LONG entry, SL={lower_val:.1f}")
            return result

        elif has_top_sig:
            _live_trade_source = "REVERSAL"
            result["signal"] = "SHORT"
            result["sl"] = upper_val
            result["entry"] = close_val
            result["reason"] = "Bearish reversal at upper band (reversal scalp)"
            result["reasons"] = ["Bearish reversal candlestick pattern",
                                 "Price at upper regression band",
                                 "Short-duration counter-trend trade"]
            logger.info(f"[RegimeReversal] Reversal SHORT entry, SL={upper_val:.1f}")
            return result

        # Priority 3: Regime trend entry
        if regime_sig.signal in ("LONG", "SHORT"):
            _live_trade_source = "REGIME"
            _live_last_regime_direction = regime_sig.signal
            _live_last_regime_sl = regime_sig.sl
            logger.info(f"[RegimeReversal] Regime entry: {regime_sig.signal}")

        return result


def run_backtest(frames: dict, initial_capital: float = 500_000,
                 lot_size: int = 15, lot_multiplier: int = 1,
                 start_date: Optional[str] = None,
                 end_date: Optional[str] = None) -> dict:
    """Combined regime trend + regression reversal backtest."""
    cfg = get_settings()
    qty = lot_size * lot_multiplier
    eod_minute = _eod_exit_minute(cfg)

    base = main_strategy.build_merged_table(frames, with_patterns=False)
    if base.empty:
        return {"error": "No data after merging", "trades": [], "stats": {}}

    base = base[_session_mask(base, cfg)].copy().reset_index(drop=True)
    if base.empty:
        return {"error": "No data in trading window", "trades": [], "stats": {}}

    # ── Pre-compute reversal indicators on full dataset ──
    base = reversal_add_indicators(base, method="Linear", window=50, smoothness=30.0)

    # Date range filtering (keep warm-up for regime agents)
    _trade_start_date = pd.to_datetime(start_date) if start_date else None
    _trade_end_date = pd.to_datetime(end_date).date() if end_date else None
    if end_date:
        base = base[base["timestamp"].dt.date <= _trade_end_date].copy().reset_index(drop=True)

    # HTF data
    df_1h = frames.get("60")
    df_1d = frames.get("1D")
    df_1w = None
    if df_1d is not None and len(df_1d) >= 30:
        try:
            from features.weekly import derive_weekly_from_daily
            df_1w = derive_weekly_from_daily(df_1d)
        except Exception:
            pass

    logger.info(f"[RegimeReversal] Backtest on {len(base)} bars")

    # ── Isolated regime state ──
    _bt_state = {
        "regime": "SIDEWAYS",
        "regime_age": 0,
        "htf_cache": None,
        "htf_cache_hour": -1,
    }

    # Pre-extract reversal signal arrays
    top_sig = base["top_sig"].values  # Bearish reversal
    bot_sig = base["bot_sig"].values  # Bullish reversal
    upper_band = base["upper"].values
    lower_band = base["lower"].values
    close_arr = base["close"].values.astype(float)
    high_arr = base["high"].values.astype(float)
    low_arr = base["low"].values.astype(float)

    trades = []
    position = "NONE"       # LONG or SHORT or NONE
    trade_source = "NONE"   # "REGIME" or "REVERSAL"
    entry_price = sl = 0.0
    entry_idx = 0
    highest_since_entry = 0.0
    lowest_since_entry = 99999999.0

    # Track last known regime signal direction for re-entry after reversal closes
    last_regime_direction = "NONE"  # "LONG" or "SHORT" or "NONE"
    last_regime_sl = 0.0

    regime_counts = {"TRENDING_UP": 0, "TRENDING_DOWN": 0, "SIDEWAYS": 0, "TRANSITION": 0}
    n = len(base)
    start_idx = 80

    def book(exit_px, reason, idx):
        nonlocal position, trade_source
        net = (exit_px - entry_price) * qty if position == "LONG" else (entry_price - exit_px) * qty
        pnl_pts = round(exit_px - entry_price, 2) if position == "LONG" else round(entry_price - exit_px, 2)
        trades.append({
            "entry_time": pd.to_datetime(base.iloc[entry_idx]["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": pd.to_datetime(base.iloc[idx]["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
            "direction": position,
            "entry_price": round(entry_price, 2),
            "sl": round(sl, 2),
            "exit_price": round(exit_px, 2),
            "exit_reason": reason,
            "pnl": round(net, 2),
            "pnl_pts": pnl_pts,
            "pnl_inr": round(net, 2),
            "source": trade_source,
        })
        src = trade_source
        position = "NONE"
        trade_source = "NONE"
        return src

    for i in range(start_idx, n):
        row = base.iloc[i]
        bh = high_arr[i]
        bl = low_arr[i]
        bc = close_arr[i]
        ts = row["timestamp"]
        atr_v = float(row.get("atr", bc * 0.002))
        if pd.isna(atr_v) or atr_v < 5:
            atr_v = bc * 0.002

        ts_ist = pd.to_datetime(ts)
        cur_mins = ts_ist.hour * 60 + ts_ist.minute

        context_start = max(0, i - 80)
        df_slice = base.iloc[context_start:i + 1]

        cur_ts = base.iloc[i]["timestamp"]
        df_1h_cur = df_1h[df_1h["timestamp"] <= cur_ts].tail(200) if df_1h is not None else None
        df_1d_cur = df_1d[df_1d["timestamp"] <= cur_ts].tail(200) if df_1d is not None else None
        df_1w_cur = df_1w[df_1w["timestamp"] <= cur_ts].tail(200) if df_1w is not None else None

        # Track what just closed (if anything) for re-entry logic
        just_closed_source = None

        # ════════════════════════════════════════════════════════════════
        #  MANAGE OPEN POSITION
        # ════════════════════════════════════════════════════════════════
        if position != "NONE":

            # ── 0. EOD EXIT ────────────────────────────────────────────
            if cur_mins >= eod_minute:
                book(bc, "EOD_EXIT", i)
                last_regime_direction = "NONE"
                continue

            # ── 1. SL HIT ─────────────────────────────────────────────
            # No `continue` — falls through to flat section where agents
            # run (matching regime_strategy.py's fallthrough pattern).
            if position == "LONG" and bl <= sl:
                just_closed_source = book(sl, "SL_HIT", i)
            elif position == "SHORT" and bh >= sl:
                just_closed_source = book(sl, "SL_HIT", i)

            # ── 2. RUN AGENTS + TRAILING + REGIME EXIT (if still in position) ──
            if position != "NONE":
                regime_pos = position if trade_source == "REGIME" else "NONE"
                sig = _run_agents(df_slice, row, regime_pos, cfg,
                                  df_1h_cur, df_1d_cur, df_1w_cur,
                                  _state=_bt_state)
                regime_counts[_bt_state["regime"]] = regime_counts.get(_bt_state["regime"], 0) + 1

                # Track latest regime direction for re-entry after reversal
                if trade_source == "REVERSAL":
                    if sig.signal in ("LONG", "SHORT"):
                        last_regime_direction = sig.signal
                        last_regime_sl = sig.sl

                # Trailing SL + regime exit (only for REGIME trades)
                if trade_source == "REGIME":
                    if position == "LONG":
                        if bh > highest_since_entry:
                            highest_since_entry = bh
                        profit = highest_since_entry - entry_price
                        if profit >= atr_v * TRAIL_ACTIVATION:
                            trail_sl = highest_since_entry - atr_v * TRAIL_MULT
                            if BE_TRIGGER > 0 and profit > atr_v * BE_TRIGGER:
                                trail_sl = max(trail_sl, entry_price + atr_v * BE_BUFFER)
                            if trail_sl > sl:
                                sl = trail_sl

                        if sig.signal == "LONG_EXIT":
                            book(bc, "REGIME_EXIT", i)
                            last_regime_direction = "NONE"

                    elif position == "SHORT":
                        if bl < lowest_since_entry:
                            lowest_since_entry = bl
                        profit = entry_price - lowest_since_entry
                        if profit >= atr_v * TRAIL_ACTIVATION:
                            trail_sl = lowest_since_entry + atr_v * TRAIL_MULT
                            if BE_TRIGGER > 0 and profit > atr_v * BE_TRIGGER:
                                trail_sl = min(trail_sl, entry_price - atr_v * BE_BUFFER)
                            if trail_sl < sl:
                                sl = trail_sl

                        if sig.signal == "SHORT_EXIT":
                            book(bc, "REGIME_EXIT", i)
                            last_regime_direction = "NONE"

                # ── 3. CHECK REVERSAL OVERRIDE (counter-trend) ─────────
                if position != "NONE":
                    if position == "LONG" and top_sig[i]:
                        book(bc, "REV_OVERRIDE", i)
                        position = "SHORT"
                        trade_source = "REVERSAL"
                        entry_price = bc
                        entry_idx = i
                        sl = bc + 1.5 * atr_v  # SL above entry for SHORT
                        highest_since_entry = bh
                        lowest_since_entry = bl
                        continue

                    elif position == "SHORT" and bot_sig[i]:
                        book(bc, "REV_OVERRIDE", i)
                        position = "LONG"
                        trade_source = "REVERSAL"
                        entry_price = bc
                        entry_idx = i
                        sl = bc - 1.5 * atr_v  # SL below entry for LONG
                        highest_since_entry = bh
                        lowest_since_entry = bl
                        continue

            # If position was closed above (SL/REGIME_EXIT), fall through
            # to the flat section below — agents will run there, matching
            # regime_strategy.py's behavior exactly.

        # ════════════════════════════════════════════════════════════════
        #  LOOK FOR NEW ENTRY (only when flat)
        # ════════════════════════════════════════════════════════════════
        if position == "NONE":
            if cur_mins >= eod_minute:
                continue

            # ALWAYS run regime agents when flat — keeps state in sync
            # with the standalone regime strategy. This is the key fix:
            # agents must run every bar even when a reversal entry fires.
            sig = _run_agents(df_slice, row, "NONE", cfg,
                              df_1h_cur, df_1d_cur, df_1w_cur,
                              _state=_bt_state)
            regime_counts[_bt_state["regime"]] = regime_counts.get(_bt_state["regime"], 0) + 1

            # If a reversal trade just closed by SL, check regime re-entry
            if just_closed_source == "REVERSAL" and last_regime_direction != "NONE":
                if sig.signal == last_regime_direction:
                    position = last_regime_direction
                    trade_source = "REGIME"
                    entry_price = bc
                    sl = sig.sl
                    entry_idx = i
                    highest_since_entry = bh
                    lowest_since_entry = bl
                else:
                    last_regime_direction = "NONE"
                continue

            # Priority 1: Check reversal signals (quick scalp opportunity)
            if bot_sig[i]:
                position = "LONG"
                trade_source = "REVERSAL"
                entry_price = bc
                entry_idx = i
                sl = bc - 1.5 * atr_v  # SL below entry for LONG
                highest_since_entry = bh
                lowest_since_entry = bl
                continue

            elif top_sig[i]:
                position = "SHORT"
                trade_source = "REVERSAL"
                entry_price = bc
                entry_idx = i
                sl = bc + 1.5 * atr_v  # SL above entry for SHORT
                highest_since_entry = bh
                lowest_since_entry = bl
                continue

            # Priority 2: Check regime signal (already computed above)
            if sig.signal in ("LONG", "SHORT"):
                position = sig.signal
                trade_source = "REGIME"
                entry_price = bc
                sl = sig.sl
                entry_idx = i
                highest_since_entry = bh
                lowest_since_entry = bl
                last_regime_direction = sig.signal
                last_regime_sl = sig.sl

    # ── Open position marker ──
    if position != "NONE":
        trades.append({
            "entry_time": pd.to_datetime(base.iloc[entry_idx]["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": "",
            "direction": position,
            "entry_price": round(entry_price, 2),
            "sl": round(sl, 2),
            "exit_price": None,
            "exit_reason": "OPEN",
            "pnl": 0,
            "pnl_pts": 0,
            "pnl_inr": 0,
            "source": trade_source,
        })

    # ── Filter by user date range ──
    if _trade_start_date is not None and trades:
        trades = [t for t in trades
                  if pd.to_datetime(t["entry_time"]) >= _trade_start_date]

    # ── Stats ──
    tdf = pd.DataFrame(trades)
    if tdf.empty:
        return {"error": "No trades generated", "trades": [], "stats": {}}

    closed = tdf[tdf["exit_reason"] != "OPEN"]
    total = len(closed)
    wins = int((closed["pnl"] > 0).sum()) if total > 0 else 0
    gp = float(closed[closed["pnl"] > 0]["pnl"].sum()) if wins > 0 else 0.0
    gl = float(abs(closed[closed["pnl"] <= 0]["pnl"].sum())) if (total - wins) > 0 else 0.0

    equity = initial_capital + closed["pnl"].cumsum()
    dd = float(((equity - equity.cummax()) / equity.cummax() * 100).min()) if total > 0 else 0.0

    stats = {
        "total_trades": total,
        "wins": wins,
        "losses": int(total - wins),
        "win_rate_pct": round(wins / total * 100, 1) if total > 0 else 0.0,
        "profit_factor": round(gp / gl, 2) if gl > 0 else (99.9 if gp > 0 else 1.0),
        "total_pnl": round(float(closed["pnl"].sum()), 0) if total > 0 else 0,
        "avg_win": round(float(closed[closed["pnl"] > 0]["pnl"].mean()), 0) if wins > 0 else 0,
        "avg_loss": round(float(closed[closed["pnl"] <= 0]["pnl"].mean()), 0) if (total - wins) > 0 else 0,
        "max_drawdown_pct": round(abs(dd), 2),
        "expectancy": round(float(closed["pnl"].mean()), 0) if total > 0 else 0,
        "final_capital": round(float(equity.iloc[-1]), 0) if total > 0 else initial_capital,
        "exit_distribution": closed["exit_reason"].value_counts().to_dict() if total > 0 else {},
        "source_distribution": closed["source"].value_counts().to_dict() if total > 0 else {},
        "regime_distribution": regime_counts,
    }

    tdf_closed = closed.copy()
    tdf_closed["equity"] = initial_capital + tdf_closed["pnl"].cumsum()
    equity_curve = tdf_closed[["entry_time", "equity", "pnl"]].to_dict(orient="records")

    return {
        "stats": stats,
        "trades": tdf.to_dict(orient="records"),
        "equity_curve": equity_curve,
    }
