"""
regime_strategy.py — Regime-Based Trend-Only Strategy.

Detects trending markets and enters on pullbacks.
Does NOT trade sideways/range markets — sits flat.
Exits when the trend regime ends (flips to SIDEWAYS/TRANSITION)
or via trailing stop loss.

Uses agents:
  1. Regime Detection — is the market trending?
  2. Trend Entry — pullback entry within the trend
  3. HTF Structure — S/R context for level awareness
  4. Momentum & Volume — quality filter
  5. Orchestrator — combines, vetoes, produces final signal

API:
  - get_current_signal(frames, position) -> dict
  - run_backtest(frames, ...) -> dict
  - add_indicators(df) -> df
"""
import logging
import threading
import numpy as np
import pandas as pd
from typing import Optional
from datetime import datetime, timedelta, timezone

import strategy as main_strategy
from config import get_settings, INSTRUMENT_META

from .regime_agents.regime_detection import RegimeDetectionAgent
from .regime_agents.trend_entry import TrendEntryAgent
from .regime_agents.range_entry import RangeEntryAgent, RangeEntryState
from .regime_agents.htf_structure import HTFStructureAgent
from .regime_agents.momentum_volume import MomentumVolumeAgent
from .regime_agents.regime_orchestrator import RegimeOrchestrator

logger = logging.getLogger(__name__)

# ── Agent singletons ─────────────────────────────────────────────────────────
_regime_agent = RegimeDetectionAgent(lookback=15, swing_order=3)
_trend_agent = TrendEntryAgent(swing_order=3, min_pullback=0.20, max_pullback=0.65)
_range_agent = RangeEntryAgent(lookback=40, proximity_pct=0.15, min_range_atr=2.0)
_htf_agent = HTFStructureAgent(swing_order=5, proximity_atr=0.5)
_momentum_agent = MomentumVolumeAgent(lookback=15)
_orchestrator = RegimeOrchestrator(min_quality=0.25, min_rr=0.0, min_regime_age=1)  # R:R check disabled — trailing SL handles exits

# Regime state persistence (used by live processor via _run_agents)
_prev_regime = "SIDEWAYS"
_prev_regime_age = 0

# Lock to prevent concurrent backtest from corrupting live regime state
_regime_lock = threading.Lock()

# HTF cache — recompute only when a new 1H bar arrives (every 12 5-min bars)
_htf_cache = None
_htf_cache_hour = -1  # hour of last HTF computation

# Re-export
add_indicators = main_strategy.add_indicators

# ── Trend-only mode: range agent always returns HOLD ─────────────────────────
_TREND_ONLY = True
_EOD_EXIT_NSE = 15 * 60 + 20   # 3:20 PM IST (equity)
_EOD_EXIT_MCX = 23 * 60 + 20   # 11:20 PM IST (commodity)

def _eod_exit_minute(cfg=None):
    if cfg is None:
        cfg = get_settings()
    exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
    return _EOD_EXIT_MCX if exch == "MCX" else _EOD_EXIT_NSE


# ── Trailing SL configuration ────────────────────────────────────────────────
# Variant A (original): TRAIL_MULT=1.0, TRAIL_ACTIVATION=0.0, BE_TRIGGER=0.3, BE_BUFFER=0.05
# Variant C (wider):    TRAIL_MULT=2.0, TRAIL_ACTIVATION=0.0, BE_TRIGGER=0.6, BE_BUFFER=0.1
TRAIL_MULT = 1.5           # trailing distance = ATR * this
TRAIL_ACTIVATION = 0.3     # min profit (in ATR) before trailing starts (0 = immediate)
BE_TRIGGER = 0.4           # profit (in ATR) to lock breakeven (0 = disabled)
BE_BUFFER = 0.3            # buffer above entry for breakeven lock (in ATR)


def _session_mask(base: pd.DataFrame, cfg) -> pd.Series:
    """Instrument-aware intraday session filter."""
    exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
    mins = base["_hour"] * 60 + base["_minute"]
    if exch == "MCX":
        start, end_excl = 9 * 60, 23 * 60 + 30
    else:
        start, end_excl = 9 * 60 + 20, 15 * 60 + 20
    return (mins >= start) & (mins < end_excl)


def _run_agents(df_slice, row, position, cfg, df_1h=None, df_1d=None, df_1w=None,
                _state=None):
    """Run agents for a single bar and return the orchestrator signal.

    Args:
        _state: optional dict with keys 'regime', 'regime_age', 'htf_cache',
                'htf_cache_hour'.  When provided the function uses (and
                mutates) this dict instead of module globals — this lets the
                backtest run with fully isolated state so it never corrupts
                the live processor's regime tracking.

    Thread-safe for the global-state path: acquires _regime_lock.
    """
    global _prev_regime, _prev_regime_age, _htf_cache, _htf_cache_hour

    close = float(row.get("close", 0))
    atr = float(row.get("atr", close * 0.002))
    if pd.isna(atr) or atr < 1:
        atr = close * 0.002

    if _state is not None:
        # ── Isolated path (backtest) — no globals, no lock ──────────
        regime = _regime_agent.evaluate(df_slice, _state["regime"], _state["regime_age"])
        _state["regime"] = regime.regime
        _state["regime_age"] = regime.regime_age

        cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
        if _state["htf_cache"] is None or cur_hour != _state["htf_cache_hour"]:
            _state["htf_cache"] = _htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
            _state["htf_cache_hour"] = cur_hour
        htf = _state["htf_cache"]
    else:
        # ── Live path — uses module globals under lock ──────────────
        with _regime_lock:
            regime = _regime_agent.evaluate(df_slice, _prev_regime, _prev_regime_age)
            _prev_regime = regime.regime
            _prev_regime_age = regime.regime_age

            cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
            if _htf_cache is None or cur_hour != _htf_cache_hour:
                _htf_cache = _htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
                _htf_cache_hour = cur_hour
            htf = _htf_cache

    # 2. Trend Entry Agent (stateless — no globals)
    trend_entry = _trend_agent.evaluate(
        df_slice, row, regime.regime, regime.confidence, position, atr
    )

    # 3. Range Entry — disabled in trend-only mode
    if _TREND_ONLY:
        range_entry = RangeEntryState(signal="HOLD", reasons=["Trend-only mode"])
    else:
        range_entry = _range_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr
        )

    # 5. Momentum/Volume — validate proposed signal
    proposed_signal = trend_entry.signal if trend_entry.signal in ("LONG", "SHORT") else "HOLD"
    momentum = _momentum_agent.evaluate(df_slice, proposed_signal, regime.regime)

    # 6. Orchestrator
    signal = _orchestrator.evaluate(
        row, regime, trend_entry, range_entry, htf, momentum, position
    )

    return signal


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def get_current_signal(frames: dict, position: str = "NONE") -> dict:
    """Compute current signal using regime-based trend strategy."""
    global _prev_regime, _prev_regime_age, _htf_cache, _htf_cache_hour
    cfg = get_settings()

    required = ["5", "15", "60", "1D"]
    for r in required:
        if r not in frames or frames[r] is None or len(frames[r]) < 30:
            return {"signal": "HOLD", "reason": f"Insufficient data for TF={r}"}

    try:
        base = main_strategy.build_merged_table(frames, with_patterns=False)
    except Exception as e:
        logger.error(f"[RegimeStrategy] Failed to build merged table: {e}")
        return {"signal": "HOLD", "reason": f"Data processing error: {e}"}

    if base.empty:
        return {"signal": "HOLD", "reason": "Empty merged table"}

    base_filtered = base[_session_mask(base, cfg)].copy()
    if base_filtered.empty:
        return {"signal": "HOLD", "reason": "Outside trading window"}

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
        return {"signal": "HOLD", "reason": "No completed 5m bars found in window"}

    latest = base_filtered.iloc[latest_idx]
    context_start = max(0, latest_idx - 80)
    df_slice = base_filtered.iloc[context_start:latest_idx + 1].copy()

    df_1h = frames.get("60")
    df_1d = frames.get("1D")
    df_1w = None
    if df_1d is not None and len(df_1d) >= 30:
        try:
            from features.weekly import derive_weekly_from_daily
            df_1w = derive_weekly_from_daily(df_1d)
        except Exception:
            pass

    signal = _run_agents(df_slice, latest, position, cfg, df_1h, df_1d, df_1w)
    return signal.to_dict()


def run_backtest(frames: dict, initial_capital: float = 500_000,
                 lot_size: int = 30, lot_multiplier: int = 1,
                 start_date: Optional[str] = None,
                 end_date: Optional[str] = None) -> dict:
    """Run backtest — trend-only, ride until regime flips.

    Uses fully isolated regime state (via _state dict) so that chart-overlay
    backtests (called from /api/chart_signals every 90s) never touch the
    live processor's module-level globals.
    """
    cfg = get_settings()
    eod_minute = _eod_exit_minute(cfg)
    trail_mult = getattr(cfg, "regime_trail_mult", 1.5)
    trail_activation = getattr(cfg, "regime_trail_activation", 0.3)
    be_trigger = getattr(cfg, "regime_be_trigger", 0.4)
    be_buffer = getattr(cfg, "regime_be_buffer", 0.3)
    qty = lot_size * lot_multiplier

    base = main_strategy.build_merged_table(frames, with_patterns=False)
    if base.empty:
        return {"error": "No data after merging", "trades": [], "stats": {}}

    base = base[_session_mask(base, cfg)].copy().reset_index(drop=True)
    if base.empty:
        return {"error": "No data in trading window", "trades": [], "stats": {}}

    # ── Date filtering: keep warm-up in base, filter output trades ──
    # The backtest page fetches 100 days of warm-up data before the
    # user's start_date.  We must NOT remove that from `base` — the
    # regime/HTF agents need it for context.  Instead we record the
    # user's date range and filter the output trades at the end.
    _trade_start_date = pd.to_datetime(start_date) if start_date else None
    _trade_end_date = pd.to_datetime(end_date).date() if end_date else None

    if end_date:
        base = base[base["timestamp"].dt.date <= _trade_end_date].copy().reset_index(drop=True)

    df_1h = frames.get("60")
    df_1d = frames.get("1D")
    df_1w = None
    if df_1d is not None and len(df_1d) >= 30:
        try:
            from features.weekly import derive_weekly_from_daily
            df_1w = derive_weekly_from_daily(df_1d)
        except Exception:
            pass

    logger.info(f"[RegimeStrategy] Backtest on {len(base)} bars, range: {base['timestamp'].min()} -> {base['timestamp'].max()} (trend-only mode)")

    # ── Isolated regime state for this backtest run ─────────────────
    # This dict is passed to _run_agents(_state=...) so the backtest
    # NEVER reads or writes the module-level globals used by the live
    # processor.  Eliminates cross-contamination completely.
    _bt_state = {
        "regime": "SIDEWAYS",
        "regime_age": 0,
        "htf_cache": None,
        "htf_cache_hour": -1,
    }

    # Diagnostics
    regime_counts = {"TRENDING_UP": 0, "TRENDING_DOWN": 0, "SIDEWAYS": 0, "TRANSITION": 0}
    entry_signals = 0
    vetoed = 0

    trades = []
    position = "NONE"
    entry_price = sl = 0.0
    entry_idx = 0
    highest_since_entry = 0.0   # track highest high since entry (for LONG trailing)
    lowest_since_entry = 99999999.0  # track lowest low since entry (for SHORT trailing)
    n = len(base)
    start_idx = 80

    for i in range(start_idx, n):
        row = base.iloc[i]
        bh = float(row.get("high", row["close"]))
        bl = float(row.get("low", row["close"]))
        bc = float(row["close"])
        ts = row["timestamp"]
        atr_v = float(row.get("atr", bc * 0.002))
        if pd.isna(atr_v) or atr_v < 5:
            atr_v = bc * 0.002

        # IST time for EOD exit check
        ts_ist = pd.to_datetime(ts)
        cur_mins = ts_ist.hour * 60 + ts_ist.minute
        cur_date = ts_ist.date()

        context_start = max(0, i - 80)
        df_slice = base.iloc[context_start:i + 1]

        # Slice HTF data to prevent lookahead — only bars at or before current timestamp
        # Limit to last 200 bars for performance (HTF agent only uses recent data)
        cur_ts = base.iloc[i]["timestamp"]
        df_1h_cur = df_1h[df_1h["timestamp"] <= cur_ts].tail(200) if df_1h is not None else None
        df_1d_cur = df_1d[df_1d["timestamp"] <= cur_ts].tail(200) if df_1d is not None else None
        df_1w_cur = df_1w[df_1w["timestamp"] <= cur_ts].tail(200) if df_1w is not None else None

        # ── MANAGE OPEN POSITION ────────────────────────────────────
        if position != "NONE":

            def book(exit_px, reason):
                nonlocal position
                net = (exit_px - entry_price) * qty if position == "LONG" else (entry_price - exit_px) * qty
                _pnl_pts = round(exit_px - entry_price, 2) if position == "LONG" else round(entry_price - exit_px, 2)
                entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
                exit_ts_ist = pd.to_datetime(ts)
                trades.append({
                    "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_time": exit_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                    "direction": position,
                    "entry_price": round(entry_price, 2),
                    "sl": round(sl, 2),
                    "exit_price": round(exit_px, 2),
                    "exit_reason": reason,
                    "pnl": round(net, 2),
                    "pnl_pts": _pnl_pts,
                    "pnl_inr": round(net, 2),
                })
                position = "NONE"

            # ── 0. END OF DAY EXIT ──────────────────────────────────
            if cur_mins >= eod_minute:
                book(bc, "EOD_EXIT")
                continue

            # ── 1. STOP LOSS HIT ────────────────────────────────────
            if position == "LONG" and bl <= sl:
                book(sl, "SL_HIT")
            elif position == "SHORT" and bh >= sl:
                book(sl, "SL_HIT")

            # ── 2. TRAILING STOP (continuous) ───────────────────────
            elif position == "LONG":
                if bh > highest_since_entry:
                    highest_since_entry = bh
                profit = highest_since_entry - entry_price
                if profit >= atr_v * trail_activation:
                    trail_sl = highest_since_entry - atr_v * trail_mult
                    if be_trigger > 0 and profit > atr_v * be_trigger:
                        trail_sl = max(trail_sl, entry_price + atr_v * be_buffer)
                    if trail_sl > sl:
                        sl = trail_sl

                # ── 3. REGIME FLIP EXIT ─────────────────────────────
                sig = _run_agents(df_slice, row, "LONG", cfg, df_1h_cur, df_1d_cur, df_1w_cur, _state=_bt_state)
                if sig.signal == "LONG_EXIT":
                    book(bc, "REGIME_EXIT")
                elif position == "LONG":
                    # Check for opposite entry signal (exit only, no auto-flip)
                    opp_sig = _run_agents(df_slice, row, "NONE", cfg, df_1h_cur, df_1d_cur, df_1w_cur, _state=_bt_state)
                    if opp_sig.signal == "SHORT":
                        book(bc, "OPPOSITE_SIGNAL")

            elif position == "SHORT":
                if bl < lowest_since_entry:
                    lowest_since_entry = bl
                profit = entry_price - lowest_since_entry
                if profit >= atr_v * trail_activation:
                    trail_sl = lowest_since_entry + atr_v * trail_mult
                    if be_trigger > 0 and profit > atr_v * be_trigger:
                        trail_sl = min(trail_sl, entry_price - atr_v * be_buffer)
                    if trail_sl < sl:
                        sl = trail_sl

                sig = _run_agents(df_slice, row, "SHORT", cfg, df_1h_cur, df_1d_cur, df_1w_cur, _state=_bt_state)
                if sig.signal == "SHORT_EXIT":
                    book(bc, "REGIME_EXIT")
                elif position == "SHORT":
                    # Check for opposite entry signal (exit only, no auto-flip)
                    opp_sig = _run_agents(df_slice, row, "NONE", cfg, df_1h_cur, df_1d_cur, df_1w_cur, _state=_bt_state)
                    if opp_sig.signal == "LONG":
                        book(bc, "OPPOSITE_SIGNAL")

        # ── LOOK FOR NEW ENTRY ──────────────────────────────────────
        if position == "NONE":

            # No new entries in last 10 min
            if cur_mins >= eod_minute:
                continue

            sig = _run_agents(df_slice, row, "NONE", cfg, df_1h_cur, df_1d_cur, df_1w_cur, _state=_bt_state)
            regime_counts[_bt_state["regime"]] = regime_counts.get(_bt_state["regime"], 0) + 1
            if sig.veto_reasons:
                vetoed += 1

            if sig.signal in ("LONG", "SHORT"):
                entry_signals += 1
                # Use current close for entry — no look-ahead bias
                entry_price = bc
                sl = sig.sl
                position = sig.signal
                entry_idx = i
                highest_since_entry = bh
                lowest_since_entry = bl

    # ── INCLUDE OPEN POSITION (so chart shows today's live entry marker) ──
    if position != "NONE":
        entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
        trades.append({
            "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": "",
            "direction": position,
            "entry_price": round(entry_price, 2),
            "sl": round(sl, 2),
            "exit_price": None,
            "exit_reason": "OPEN",
            "pnl": 0,
            "pnl_pts": 0,
            "pnl_inr": 0,
        })

    # ── FILTER TRADES BY USER'S REQUESTED DATE RANGE ──────────────
    # Warm-up bars were kept in `base` so the regime agent could build
    # state.  Now discard trades that fall outside the requested range.
    if _trade_start_date is not None and trades:
        trades = [t for t in trades
                  if pd.to_datetime(t["entry_time"]) >= _trade_start_date]

    # ── COMPUTE STATS ───────────────────────────────────────────────
    tdf = pd.DataFrame(trades)
    if tdf.empty:
        return {"error": "No trades generated", "trades": [], "stats": {}}

    total = len(tdf)
    wins = (tdf["pnl"] > 0).sum()
    gp = tdf[tdf["pnl"] > 0]["pnl"].sum()
    gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
    equity = initial_capital + tdf["pnl"].cumsum()
    dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

    stats = {
        "total_trades": total,
        "wins": int(wins),
        "losses": int(total - wins),
        "win_rate_pct": round(wins / total * 100, 1),
        "profit_factor": round(gp / gl, 2) if gl > 0 else 0,
        "total_pnl": round(tdf["pnl"].sum(), 0),
        "avg_win": round(tdf[tdf["pnl"] > 0]["pnl"].mean(), 0) if wins > 0 else 0,
        "avg_loss": round(tdf[tdf["pnl"] <= 0]["pnl"].mean(), 0) if (total - wins) > 0 else 0,
        "max_drawdown_pct": round(dd, 2),
        "expectancy": round(tdf["pnl"].mean(), 0),
        "final_capital": round(float(equity.iloc[-1]), 0),
        "exit_distribution": tdf["exit_reason"].value_counts().to_dict(),
    }

    tdf["equity"] = initial_capital + tdf["pnl"].cumsum()
    equity_curve = tdf[["entry_time", "equity", "pnl"]].to_dict(orient="records")

    stats["regime_distribution"] = regime_counts
    stats["entry_signals"] = entry_signals
    stats["vetoed_signals"] = vetoed

    return {
        "stats": stats,
        "trades": tdf.to_dict(orient="records"),
        "equity_curve": equity_curve,
    }
