"""
strategy.py — Multi-Agent Trading Engine (v3 — Scenario-Based).

Replaces v2's weighted-score consensus with scenario-based reasoning.
Agents output WHERE price is in the structure, Scenario Engine generates
actionable trade scenarios, Orchestrator picks the best one.

Entry point functions (same API as before):
  - get_current_signal(frames, position) -> dict
  - run_backtest(frames, ...) -> dict
  - add_indicators(df) -> df
  - build_merged_table(frames) -> df   (unchanged)
"""
import logging
import numpy as np
import pandas as pd
import pandas_ta as ta
from pathlib import Path
from typing import Optional

from config import get_settings, INSTRUMENT_META
from features.weekly import derive_weekly_from_daily
from features.behavioral import (
    compute_all_behavioral_features,
    compute_merged_behavioral_features,
)
from features.patterns import detect_patterns_vectorized

# v3 agents
from agents.base_v3 import OrchestratorV3Signal
from agents.level_map import LevelMapBuilder
from agents.htf_agent import HTFAgent
from agents.swing_agent import SwingAgent
from agents.flow_agent import FlowAgent
from agents.trigger_agent_v3 import TriggerAgentV3
from agents.micro_agent import MicroAgent
from agents.scenario_engine import ScenarioEngine
from agents.orchestrator_v3 import OrchestratorV3
from agents.memory_agent_v2 import PatternMemoryAgentV2

# v2 agents — kept for backward compat (regime_strategy imports these)
from agents.base import PatternMemoryState
from agents.macro_agent import MacroAgent
from agents.structure_agent import StructureAgent
from agents.momentum_agent import MomentumAgent
from agents.trigger_agent import TriggerAgent
from agents.volume_agent import VolumeAgent
from agents.memory_agent import PatternMemoryAgent
from agents.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


# ML model support has been removed.



# ═══════════════════════════════════════════════════════════════════════════════
# INDICATOR COMPUTATION (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_trendlines(df: pd.DataFrame, swing_order: int = 5) -> pd.DataFrame:
    """
    Project dynamic trendlines from the last 2 confirmed swing highs/lows.
    """
    highs = df["high"].values
    lows  = df["low"].values
    n     = len(df)

    roll_hi = df["high"].rolling(2 * swing_order + 1, center=True).max()
    roll_lo = df["low"].rolling(2 * swing_order + 1, center=True).min()

    is_hi = ((df["high"] == roll_hi) & roll_hi.notna()).shift(swing_order).fillna(False).values
    is_lo = ((df["low"]  == roll_lo) & roll_lo.notna()).shift(swing_order).fillna(False).values

    tl_res       = np.full(n, np.nan)
    tl_sup       = np.full(n, np.nan)
    tl_res_slope = np.zeros(n)
    tl_sup_slope = np.zeros(n)

    hi_stack: list = []
    lo_stack: list = []

    for i in range(n):
        if is_hi[i]:
            hi_stack.append((i, highs[i]))
            if len(hi_stack) > 3:
                hi_stack.pop(0)
        if is_lo[i]:
            lo_stack.append((i, lows[i]))
            if len(lo_stack) > 3:
                lo_stack.pop(0)

        if len(hi_stack) >= 2:
            (x1, y1), (x2, y2) = hi_stack[-2], hi_stack[-1]
            slope = (y2 - y1) / max(x2 - x1, 1)
            tl_res[i]       = y2 + slope * (i - x2)
            tl_res_slope[i] = slope

        if len(lo_stack) >= 2:
            (x1, y1), (x2, y2) = lo_stack[-2], lo_stack[-1]
            slope = (y2 - y1) / max(x2 - x1, 1)
            tl_sup[i]       = y2 + slope * (i - x2)
            tl_sup_slope[i] = slope

    df["trendline_resistance"] = tl_res
    df["trendline_support"]    = tl_sup
    df["trendline_res_slope"]  = tl_res_slope
    df["trendline_sup_slope"]  = tl_sup_slope
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators on a DataFrame."""
    df = df.copy()

    # SuperTrend (10, 3)
    st = df.ta.supertrend(length=10, multiplier=3)
    if st is not None and "SUPERT_10_3" in st.columns:
        df["supertrend"] = st["SUPERT_10_3"]
        df["supertrend_dir"] = st["SUPERTd_10_3"]
    else:
        df["supertrend"] = df["close"]
        df["supertrend_dir"] = 0

    # EMA 7 / 21
    df["ema7"] = df.ta.ema(length=7)
    df["ema21"] = df.ta.ema(length=21)
    df["ema_cross"] = np.where(df["ema7"] > df["ema21"], 1, -1)

    # MACD (12, 26, 9)
    macd = df.ta.macd(fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_sig"] = macd["MACDs_12_26_9"]
        df["macd_hist"] = macd["MACDh_12_26_9"]
    else:
        df["macd"] = df["macd_sig"] = df["macd_hist"] = 0.0

    # Stoch RSI (14, 14, 3, 3)
    srsi = df.ta.stochrsi(length=14, rsi_length=14, k=3, d=3)
    if srsi is not None:
        df["stochrsi_k"] = srsi["STOCHRSIk_14_14_3_3"]
        df["stochrsi_d"] = srsi["STOCHRSId_14_14_3_3"]
    else:
        df["stochrsi_k"] = df["stochrsi_d"] = 50.0

    # Stoch (14, 3, 1)
    st2 = df.ta.stoch(k=14, d=3, smooth_k=1)
    if st2 is not None:
        df["stoch_k"] = st2["STOCHk_14_3_1"]
        df["stoch_d"] = st2["STOCHd_14_3_1"]
    else:
        df["stoch_k"] = df["stoch_d"] = 50.0

    # DMI / ADX (14)
    adx = df.ta.adx(length=14)
    if adx is not None:
        df["adx"] = adx["ADX_14"]
        df["dmp"] = adx["DMP_14"]
        df["dmn"] = adx["DMN_14"]
    else:
        df["adx"] = df["dmp"] = df["dmn"] = 0.0

    # ATR
    df["atr"] = df.ta.atr(length=14)

    # RSI
    df["rsi"] = df.ta.rsi(length=14)

    # Fibonacci (rolling swing)
    lookback = 50
    df["swing_high"] = df["high"].rolling(lookback, min_periods=10).max().shift(1)
    df["swing_low"] = df["low"].rolling(lookback, min_periods=10).min().shift(1)
    rng = df["swing_high"] - df["swing_low"]
    df["fib_618"] = df["swing_high"] - 0.618 * rng
    df["fib_500"] = df["swing_high"] - 0.500 * rng
    df["fib_382"] = df["swing_high"] - 0.382 * rng
    df["fib_236"] = df["swing_high"] - 0.236 * rng

    # Pivot S/R
    df["pivot"] = (df["high"].shift(1) + df["low"].shift(1) + df["close"].shift(1)) / 3
    df["res1"] = 2 * df["pivot"] - df["low"].shift(1)
    df["sup1"] = 2 * df["pivot"] - df["high"].shift(1)

    # Trendlines
    df = compute_trendlines(df)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# MERGE HELPER (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

MERGE_COLS = [
    "close", "supertrend", "supertrend_dir", "ema7", "ema21", "ema_cross",
    "macd", "macd_sig", "macd_hist",
    "stochrsi_k", "stochrsi_d", "stoch_k", "stoch_d",
    "adx", "dmp", "dmn", "atr", "rsi",
    "fib_618", "fib_382", "fib_500", "fib_236",
    "sup1", "res1", "swing_high", "swing_low",
    "trendline_resistance", "trendline_support",
    "trendline_res_slope", "trendline_sup_slope",
]


def asof_merge(base: pd.DataFrame, other: pd.DataFrame,
               prefix: str, cols: list = None) -> pd.DataFrame:
    """Forward-fill merge higher TF data onto base TF."""
    if cols is None:
        cols = [c for c in MERGE_COLS if c in other.columns]
    sub = other[["timestamp"] + cols].copy()
    sub = sub.rename(columns={c: f"{prefix}{c}" for c in cols})

    for frame in [base, sub]:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        if frame["timestamp"].dt.tz is not None:
            frame["timestamp"] = frame["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        frame["timestamp"] = frame["timestamp"].astype("datetime64[ns]")

    return pd.merge_asof(
        base.sort_values("timestamp"),
        sub.sort_values("timestamp"),
        on="timestamp", direction="backward"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# V3 AGENT INSTANCES (module-level singletons)
# ═══════════════════════════════════════════════════════════════════════════════

_level_map_builder = LevelMapBuilder()
_htf_agent = HTFAgent()
_swing_agent = SwingAgent()
_flow_agent = FlowAgent()
_trigger_agent_v3 = TriggerAgentV3()
_micro_agent = MicroAgent()
_scenario_engine = ScenarioEngine()

# FAISS Pattern Memory v2 (lazy-loaded singleton)
_memory_agent_v2 = None
_memory_agent_v2_attempted = False


def _get_memory_agent_v2(cfg) -> PatternMemoryAgentV2:
    """Lazy-load the FAISS v2 index on first call.

    Returns the PatternMemoryAgentV2 instance (may be unloaded if the
    index files do not exist yet — in that case, query() returns a
    neutral MemoryResult and the orchestrator falls through to the
    static fallback).
    """
    global _memory_agent_v2, _memory_agent_v2_attempted
    if _memory_agent_v2 is not None:
        return _memory_agent_v2
    if _memory_agent_v2_attempted:
        return None  # Already tried and failed
    _memory_agent_v2_attempted = True
    try:
        agent = PatternMemoryAgentV2()
        loaded = agent.load_index(cfg.memory_index_dir)
        if loaded:
            _memory_agent_v2 = agent
            logger.info("PatternMemoryAgentV2 (FAISS v2) loaded successfully")
            return agent
        else:
            logger.info("FAISS v2 index not available — memory agent disabled")
            return None
    except Exception as e:
        logger.warning(f"Failed to initialise PatternMemoryAgentV2: {e}")
        return None


# v2 agent instances (kept for regime_strategy + diagnostic script imports)
_macro_agent = MacroAgent()
_structure_agent = StructureAgent()
_momentum_agent = MomentumAgent()
_trigger_agent = TriggerAgent()
_volume_agent = VolumeAgent()
_memory_agent = PatternMemoryAgent()


def _get_orchestrator_v3(cfg) -> OrchestratorV3:
    """Create Orchestrator v3 with settings."""
    return OrchestratorV3(
        min_confidence=cfg.min_orchestrator_score,
        min_tf_agreement=2,
        min_trigger_quality=cfg.min_trigger_quality,
        min_rr=cfg.min_rr,
        max_rr=getattr(cfg, 'max_rr', 0.0),
        block_short_oversold=getattr(cfg, 'block_short_oversold', False),
    )


def _get_orchestrator(cfg) -> Orchestrator:
    """Create v2 Orchestrator (kept for backward compat / diagnostic script)."""
    weights = {
        "macro": cfg.agent_weight_macro,
        "structure": cfg.agent_weight_structure,
        "momentum": cfg.agent_weight_momentum,
        "trigger": cfg.agent_weight_trigger,
        "volume": cfg.agent_weight_volume,
        "memory": cfg.agent_weight_memory,
    }
    return Orchestrator(weights=weights, min_score=cfg.min_orchestrator_score)


# ═══════════════════════════════════════════════════════════════════════════════
# BUILD MERGED TABLE (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

def build_merged_table(frames: dict, with_patterns: bool = True) -> pd.DataFrame:
    """
    Build the full merged table with all indicators, behavioral features,
    and chart patterns from multi-timeframe data.
    """
    enriched = {}
    for tf, df in frames.items():
        if df is None or len(df) < 30:
            continue
        try:
            enriched[tf] = add_indicators(df.copy())
        except Exception as e:
            logger.warning(f"Indicator calc failed for {tf}: {e}")
            enriched[tf] = df.copy()

    if "5" not in enriched:
        return pd.DataFrame()

    # Derive weekly from daily
    if "1D" in enriched and "W" not in enriched:
        try:
            enriched["W"] = derive_weekly_from_daily(enriched["1D"])
        except Exception as e:
            logger.warning(f"Weekly derivation failed: {e}")

    base = enriched["5"].copy()

    # Merge higher timeframes (shifted forward so HTF data is only available after bar closes)
    if "60" in enriched:
        h1 = enriched["60"].copy()
        h1["timestamp"] = pd.to_datetime(h1["timestamp"]) + pd.Timedelta(hours=1)
        base = asof_merge(base, h1, "h1_")
    if "15" in enriched:
        m15 = enriched["15"].copy()
        m15["timestamp"] = pd.to_datetime(m15["timestamp"]) + pd.Timedelta(minutes=15)
        base = asof_merge(base, m15, "m15_")
    if "1D" in enriched:
        d1 = enriched["1D"].copy()
        d1["timestamp"] = pd.to_datetime(d1["timestamp"]) + pd.Timedelta(days=1)
        base = asof_merge(base, d1, "d1_")
    if "W" in enriched:
        w = enriched["W"].copy()
        w["timestamp"] = pd.to_datetime(w["timestamp"]) + pd.Timedelta(weeks=1)
        base = asof_merge(base, w, "w_")

    # 1min data
    if "1" in enriched:
        df1 = enriched["1"][["timestamp", "supertrend_dir", "stochrsi_k", "stochrsi_d"]].copy()
        df1 = df1.rename(columns={
            "supertrend_dir": "m1_st",
            "stochrsi_k": "m1_srsi_k",
            "stochrsi_d": "m1_srsi_d"
        })
        base["timestamp"] = base["timestamp"].astype("datetime64[ns]")
        df1["timestamp"] = df1["timestamp"].astype("datetime64[ns]")
        base = pd.merge_asof(base.sort_values("timestamp"),
                             df1.sort_values("timestamp"),
                             on="timestamp", direction="backward")
    else:
        base["m1_st"] = base.get("supertrend_dir", 0)
        base["m1_srsi_k"] = base.get("stochrsi_k", 50)
        base["m1_srsi_d"] = base.get("stochrsi_d", 50)

    base = base.sort_values("timestamp").reset_index(drop=True)

    # Compute behavioral features
    base = compute_merged_behavioral_features(base)

    # Defragment
    base = base.copy()

    # Detect chart patterns on 1H data
    if with_patterns and "60" in enriched:
        try:
            h1_with_patterns = detect_patterns_vectorized(enriched["60"], swing_order=5)
            pattern_cols = ["pattern_name", "pattern_signal", "pattern_completion",
                           "pattern_breakout_dist_atr", "pattern_target_atr",
                           "pattern_encoded", "pattern_signal_num"]
            pattern_sub = h1_with_patterns[["timestamp"] + pattern_cols].copy()
            for frame in [base, pattern_sub]:
                frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=False)
                if hasattr(frame["timestamp"].dt, 'tz') and frame["timestamp"].dt.tz is not None:
                    frame["timestamp"] = frame["timestamp"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
                frame["timestamp"] = frame["timestamp"].astype("datetime64[ns]")
            base = pd.merge_asof(base.sort_values("timestamp"),
                                 pattern_sub.sort_values("timestamp"),
                                 on="timestamp", direction="backward")
        except Exception as e:
            logger.warning(f"Pattern detection failed: {e}")
            for col in ["pattern_name", "pattern_signal"]:
                base[col] = "NONE"
            for col in ["pattern_completion", "pattern_breakout_dist_atr",
                        "pattern_target_atr", "pattern_encoded", "pattern_signal_num"]:
                base[col] = 0

    # EMA21 proximity counter
    if "h1_ema_price_to_ema21_atr" in base.columns:
        near = (base["h1_ema_price_to_ema21_atr"].abs() < 0.8).astype(int).values
        bars_arr = np.zeros(len(near), dtype=int)
        count = 0
        for j in range(len(near)):
            count = (count + 1) if near[j] else 0
            bars_arr[j] = count
        base["h1_bars_near_ema21"] = bars_arr
    else:
        base["h1_bars_near_ema21"] = 0

    # Time columns (IST)
    ts_col = pd.to_datetime(base["timestamp"])
    base["_hour"] = ts_col.dt.hour
    base["_minute"] = ts_col.dt.minute

    return base


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION MASK (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

def _session_mask(base: pd.DataFrame, cfg) -> pd.Series:
    """Instrument-aware intraday session filter (IST minutes)."""
    exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
    mins = base["_hour"] * 60 + base["_minute"]
    if exch == "MCX":
        start, end_excl = 9 * 60, 23 * 60 + 30
    else:
        start, end_excl = 9 * 60 + 20, 15 * 60 + 20
    return (mins >= start) & (mins < end_excl)


# ═══════════════════════════════════════════════════════════════════════════════
# V3 SIGNAL PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def _run_v3_pipeline(row: pd.Series, position: str, cfg) -> dict:
    """Run full v3 agent pipeline on a single merged row. Returns signal dict."""
    # 1. Level Map
    level_map = _level_map_builder.build(row)

    # 2. HTF Agent (Weekly + Daily)
    htf = _htf_agent.evaluate(row, level_map)

    # 3. Swing Agent (1H cascade — the primary bias engine)
    swing = _swing_agent.evaluate(row, level_map)

    # 4. Flow Agent (15m momentum + volume)
    flow = _flow_agent.evaluate(row, swing)

    # 5. Trigger Agent v3 (5m execution)
    trigger = _trigger_agent_v3.evaluate(row, swing, flow, level_map, position)

    # 6. Micro Agent (1m confirmation)
    micro = _micro_agent.evaluate(row, swing, trigger.signal)

    # 7. Scenario Engine — generate candidate scenarios
    scenarios = _scenario_engine.generate(row, htf, swing, flow, trigger, level_map)

    # 8. FAISS Pattern Memory v2 — load once, query per scenario inside orchestrator
    mem_agent = _get_memory_agent_v2(cfg) if cfg.pattern_memory_enabled else None

    # 9. Orchestrator v3 — pick best scenario
    orchestrator = _get_orchestrator_v3(cfg)
    signal = orchestrator.evaluate(
        row, scenarios, htf, swing, flow, trigger, micro, level_map,
        faiss_win_rate=0.5, faiss_confidence=0.0,
        position=position,
        memory_agent=mem_agent,
    )

    return signal


def get_current_signal(frames: dict, position: str = "NONE") -> dict:
    """
    Given multi-timeframe DataFrames, compute the current signal.
    Returns a signal dict compatible with the existing API.
    """
    cfg = get_settings()

    required = ["5", "15", "60", "1D"]
    for r in required:
        if r not in frames or frames[r] is None or len(frames[r]) < 30:
            return {"signal": "HOLD", "reason": f"Insufficient data for TF={r}"}

    try:
        base = build_merged_table(frames, with_patterns=cfg.chart_patterns_enabled)
    except Exception as e:
        logger.error(f"Failed to build merged table: {e}")
        return {"signal": "HOLD", "reason": f"Data processing error: {e}"}

    if base.empty:
        return {"signal": "HOLD", "reason": "Empty merged table"}

    base_filtered = base[_session_mask(base, cfg)].copy()

    if base_filtered.empty:
        return {"signal": "HOLD", "reason": "Outside trading window"}

    # Latest completed bar
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
        return {"signal": "HOLD", "reason": "No completed 5m bars found in window"}

    latest = base_filtered.iloc[latest_idx]

    # Run v3 pipeline
    signal = _run_v3_pipeline(latest, position, cfg)

    return signal.to_dict()


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE (v3)
# ═══════════════════════════════════════════════════════════════════════════════

def run_backtest(frames: dict, initial_capital: float = 500_000,
                 lot_size: int = 30, lot_multiplier: int = 1,
                 start_date: Optional[str] = None,
                 end_date: Optional[str] = None) -> dict:
    """Run full backtest using v3 agent pipeline."""
    cfg = get_settings()
    qty = lot_size * lot_multiplier

    base = build_merged_table(frames, with_patterns=cfg.chart_patterns_enabled)

    if base.empty:
        return {"error": "No data after merging", "trades": [], "stats": {}}

    base = base[_session_mask(base, cfg)].copy().reset_index(drop=True)

    if base.empty:
        return {"error": "No data in trading window", "trades": [], "stats": {}}

    # ── Date filtering: keep warm-up in base, filter output trades ──
    # The /api/backtest endpoint fetches 100 days of warm-up data.
    # We must NOT remove those bars from `base` — agents need them for
    # context.  Instead we filter the output trades by date range.
    _trade_start_date = pd.to_datetime(start_date) if start_date else None
    _trade_end_date = pd.to_datetime(end_date).date() if end_date else None

    if end_date:
        base = base[base["timestamp"].dt.date <= _trade_end_date].copy().reset_index(drop=True)

    logger.info(f"Running v3 backtest on {len(base)} bars...")

    # Reset swing agent state for backtest
    _swing_agent._prev_cascade = "UNKNOWN"
    _swing_agent._journey_start_bar = 0
    _swing_agent._bar_count = 0

    trades = []
    position = "NONE"
    entry_price = sl = t1 = t2 = 0.0
    entry_idx = 0
    t1_hit = False

    # Trailing SL state
    entry_atr = 0.0
    peak_profit = 0.0      # max favorable excursion in points
    trail_step = 0          # 0=initial, 1=BE, 2=trailing
    is_trailing = False

    # Cooldown state (block same-direction re-entry after loss)
    block_long_until = 0
    block_short_until = 0
    cooldown_bars = getattr(cfg, 'cooldown_bars', 0)

    n = len(base)
    start_idx = 0 if start_date else 60

    for i in range(start_idx, n):
        row = base.iloc[i]
        bh = float(row.get("high", row["close"]))
        bl = float(row.get("low", row["close"]))
        bc = float(row["close"])
        atr_v = float(row.get("atr", bc * 0.002))
        ts = row["timestamp"]

        if pd.isna(atr_v) or atr_v < 5:
            atr_v = bc * 0.002

        # ── MANAGE OPEN POSITION ────────────────────────────────────────
        if position != "NONE":
            bars_held = i - entry_idx

            # Trailing SL updates
            be_trigger = getattr(cfg, 'trailing_be_trigger_atr', 0)
            trail_start = getattr(cfg, 'trailing_start_atr', 0)
            trail_offset = getattr(cfg, 'trailing_offset_atr', 0.5)

            if entry_atr > 0:
                if position == "LONG":
                    bar_max_profit = bh - entry_price
                else:
                    bar_max_profit = entry_price - bl
                profit_atr = bar_max_profit / entry_atr
                peak_profit = max(peak_profit, bar_max_profit)
                peak_profit_atr = peak_profit / entry_atr

                # Step 1: Move to breakeven
                if be_trigger > 0 and trail_step == 0 and profit_atr >= be_trigger:
                    sl = entry_price
                    trail_step = 1
                # Step 2: Start trailing
                if trail_start > 0 and trail_step >= 1 and peak_profit_atr >= trail_start:
                    is_trailing = True
                    trail_step = 2
                # Continuous trail update
                if is_trailing and trail_offset > 0:
                    if position == "LONG":
                        new_sl = entry_price + peak_profit - trail_offset * entry_atr
                        sl = max(sl, new_sl)
                    else:
                        new_sl = entry_price - peak_profit + trail_offset * entry_atr
                        sl = min(sl, new_sl)

            def book(exit_px, reason):
                nonlocal position, t1_hit, peak_profit, trail_step, is_trailing
                nonlocal block_long_until, block_short_until
                half_qty = int(qty * 0.5) if t1_hit else 0
                rem_qty = qty - half_qty

                if t1_hit:
                    avg_exit = (t1 * half_qty + exit_px * rem_qty) / qty
                else:
                    avg_exit = exit_px

                if position == "LONG":
                    net = (avg_exit - entry_price) * qty
                else:
                    net = (entry_price - avg_exit) * qty

                entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
                exit_ts_ist = pd.to_datetime(ts)

                _pnl_pts = round(avg_exit - entry_price, 2) if position == "LONG" else round(entry_price - avg_exit, 2)
                trades.append({
                    "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_time": exit_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                    "direction": position,
                    "entry_price": round(entry_price, 2),
                    "sl": round(sl, 2),
                    "target1": round(t1, 2),
                    "target2": round(t2, 2),
                    "exit_price": round(avg_exit, 2),
                    "exit_reason": reason,
                    "pnl": round(net, 2),
                    "pnl_pts": _pnl_pts,
                    "pnl_inr": round(net, 2),
                    "hit_t1": t1_hit,
                })

                # Cooldown: block same-direction re-entry after a loss
                if net <= 0 and cooldown_bars > 0:
                    if position == "LONG":
                        block_long_until = i + cooldown_bars
                    elif position == "SHORT":
                        block_short_until = i + cooldown_bars

                position = "NONE"
                t1_hit = False
                peak_profit = 0.0
                trail_step = 0
                is_trailing = False

            if position == "LONG":
                if bl <= sl:
                    book(sl, f"TRAIL_S{trail_step}" if trail_step > 0 else "SL_HIT")
                elif t1_hit and bh >= t2:
                    book(t2, "T2_HIT")
                elif not t1_hit and bh >= t1:
                    t1_hit = True
                    sl = max(sl, entry_price)  # don't move SL backward if trail already advanced it
                elif bars_held >= cfg.max_hold_bars:
                    book(bc, "TIME_EXIT")
                else:
                    sig = _run_v3_pipeline(row, "LONG", cfg)
                    if sig.signal == "LONG_EXIT":
                        book(bc, "SIG_EXIT")
                    elif sig.signal == "SHORT":
                        book(bc, "OPPOSITE_SIGNAL")

            elif position == "SHORT":
                if bh >= sl:
                    book(sl, f"TRAIL_S{trail_step}" if trail_step > 0 else "SL_HIT")
                elif t1_hit and bl <= t2:
                    book(t2, "T2_HIT")
                elif not t1_hit and bl <= t1:
                    t1_hit = True
                    sl = min(sl, entry_price)
                elif bars_held >= cfg.max_hold_bars:
                    book(bc, "TIME_EXIT")
                else:
                    sig = _run_v3_pipeline(row, "SHORT", cfg)
                    if sig.signal == "SHORT_EXIT":
                        book(bc, "SIG_EXIT")
                    elif sig.signal == "LONG":
                        book(bc, "OPPOSITE_SIGNAL")

        # ── LOOK FOR NEW ENTRY ──────────────────────────────────────────
        if position == "NONE":
            sig = _run_v3_pipeline(row, "NONE", cfg)

            if sig.signal in ("LONG", "SHORT"):
                # Cooldown: skip if same direction was just stopped out
                if sig.signal == "LONG" and i < block_long_until:
                    continue
                if sig.signal == "SHORT" and i < block_short_until:
                    continue

                # Use current close for entry — no look-ahead bias
                entry_price = bc
                sl = sig.sl
                t1 = sig.target1
                t2 = sig.target2
                position = sig.signal
                entry_idx = i
                t1_hit = False
                entry_atr = atr_v
                peak_profit = 0.0
                trail_step = 0
                is_trailing = False

    # ── INCLUDE OPEN POSITION (so chart shows today's live entry marker) ──
    if position != "NONE":
        entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
        trades.append({
            "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": "",
            "direction": position,
            "entry_price": round(entry_price, 2),
            "sl": round(sl, 2),
            "target1": round(t1, 2),
            "target2": round(t2, 2),
            "exit_price": None,
            "exit_reason": "OPEN",
            "pnl": 0,
            "pnl_pts": 0,
            "pnl_inr": 0,
            "hit_t1": t1_hit,
        })

    # ── FILTER TRADES BY USER'S REQUESTED DATE RANGE ──────────────
    if _trade_start_date is not None and trades:
        trades = [t for t in trades
                  if pd.to_datetime(t["entry_time"]) >= _trade_start_date]

    # ── COMPUTE STATS ───────────────────────────────────────────────────
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

    return {
        "stats": stats,
        "trades": tdf.to_dict(orient="records"),
        "equity_curve": equity_curve,
    }
