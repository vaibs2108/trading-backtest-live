"""
base.py — Base agent class and typed dataclass outputs for all agents.
"""
from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd
import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT OUTPUT DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MacroState:
    """Output from MacroAgent (Weekly + Daily analysis)."""
    bias: str = "NEUTRAL"                  # LONG | SHORT | NEUTRAL
    confidence: float = 0.0                # 0-1
    trend_age_days: int = 0                # Days since daily SuperTrend flip
    trend_exhaustion: float = 0.0          # 0-1 (mature trend = higher)
    weekly_structure: str = "RANGE"        # UPTREND | DOWNTREND | RANGE
    weekly_macd_phase: str = "NEUTRAL"     # EXPANDING | CONTRACTING | CROSSING
    daily_ema_gap_atr: float = 0.0         # EMA7-EMA21 gap in daily ATR
    daily_adx_regime: str = "RANGING"      # STRONG | TRENDING | WEAK | RANGING
    daily_adx_hook: bool = False           # ADX turning up = new trend
    daily_fib_zone: str = "OTHER"          # ABOVE_236 | GOLDEN_ZONE | BELOW_618
    daily_rsi_regime: str = "NEUTRAL"      # ABOVE_50 | BELOW_50
    key_levels: dict = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)


@dataclass
class StructureState:
    """Output from StructureAgent (1H S/R, Fibonacci, Chart Patterns)."""
    # Support / Resistance
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0
    support_type: str = "NONE"             # FIB_618 | EMA21 | SUPERTREND | PIVOT | STRUCTURAL
    support_strength: float = 0.0          # 0-1
    support_test_count: int = 0
    price_to_support_atr: float = 0.0
    price_to_resistance_atr: float = 0.0

    # Dynamic S/R
    ema21_as_support: bool = False
    supertrend_as_support: bool = False
    ema7_broken_traveling_to_21: bool = False
    bars_at_ema21: int = 0                  # consecutive bars price has been near 1H EMA21

    # Chart Patterns
    pattern_name: str = "NONE"
    pattern_signal: str = "NONE"           # LONG | SHORT | NEUTRAL
    pattern_completion: float = 0.0        # 0-1
    pattern_breakout_dist_atr: float = 0.0
    pattern_target_atr: float = 0.0

    # HTF (Weekly/Daily) level conflicts — key new fields
    # If True: a weekly or daily level is within the danger zone for this direction
    htf_resistance_conflict: bool = False   # at W/D resistance while going LONG
    htf_support_conflict: bool = False      # at W/D support while going SHORT
    htf_conflict_reason: str = ""           # e.g. "W_FIB_618 @ 54210 (1.2 H1 ATR)"
    htf_confirms_bias: bool = False         # W/D level is support and confirms LONG (or vice versa)
    w_nearest_res_dist_atr: float = 999.0  # distance to nearest weekly resistance in H1 ATR
    w_nearest_sup_dist_atr: float = 999.0  # distance to nearest weekly support in H1 ATR
    d1_nearest_res_dist_atr: float = 999.0
    d1_nearest_sup_dist_atr: float = 999.0

    # Trendlines
    trendline_res_dist_atr: float = 999.0  # distance: +ve = below resistance, -ve = above (broken)
    trendline_sup_dist_atr: float = 999.0  # distance: +ve = above support, -ve = below (broken)
    trendline_res_slope: float = 0.0       # < 0 = descending resistance trendline
    trendline_sup_slope: float = 0.0       # > 0 = ascending support trendline

    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class MomentumState:
    """Output from MomentumAgent (15m + 5m oscillator trajectories)."""
    # StochRSI
    srsi_15m_phase: int = 0                # -3 to +3 phase encoding
    srsi_5m_phase: int = 0
    srsi_trajectory_15m: float = 0.0       # K line slope
    srsi_trajectory_5m: float = 0.0
    srsi_kd_cross_15m: int = 0             # +1 bull, -1 bear, 0 none
    srsi_kd_cross_5m: int = 0

    # MACD
    macd_15m_hist_slope: float = 0.0
    macd_5m_hist_slope: float = 0.0
    macd_15m_phase: str = "NEUTRAL"        # EXPANDING_BULL | CONTRACTING_BULL | etc.

    # Stochastic
    stoch_5m_phase: int = 0
    stoch_srsi_agreement: int = 0          # +1 both OS, -1 both OB, 0 disagree

    # Composite
    momentum_alignment: int = 0            # -4 to +4
    divergence_detected: str = "NONE"      # BULLISH_DIV | BEARISH_DIV | NONE
    momentum_shift_imminent: bool = False

    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class TriggerState:
    """Output from TriggerAgent (5m + 1m entry timing)."""
    signal: str = "HOLD"                   # LONG | SHORT | LONG_EXIT | SHORT_EXIT | HOLD
    entry_quality: float = 0.0             # 0-1

    # 5m conditions
    supertrend_just_flipped: bool = False
    ema_cross_aligned: bool = False
    candle_pattern: str = "NONE"           # BULLISH_ENGULFING | HAMMER | etc.

    # 1m confirmation
    m1_supertrend_confirms: bool = False
    m1_srsi_confirms: bool = False

    # Risk levels
    entry_price: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    risk_atr: float = 0.0
    rr_ratio: float = 0.0

    # Time quality
    market_phase: str = "UNKNOWN"          # OPENING | MIDDAY | CLOSING
    time_quality: float = 0.5

    reasons: List[str] = field(default_factory=list)


@dataclass
class VolumeState:
    """Output from VolumeAgent."""
    volume_at_support: str = "NEUTRAL"     # HIGH_SELLING | LOW_SELLING | NEUTRAL
    volume_at_resistance: str = "NEUTRAL"
    volume_trend_1h: str = "FLAT"          # INCREASING | DECREASING | FLAT
    volume_trend_5m: str = "FLAT"
    relative_volume_5m: float = 1.0
    breakout_volume: bool = False
    volume_divergence: str = "NONE"        # PRICE_UP_VOL_DOWN | etc.
    accumulation: bool = False
    distribution: bool = False
    veto_signal: bool = False
    veto_reason: str = ""
    confidence: float = 0.5
    reasons: List[str] = field(default_factory=list)


@dataclass
class PatternMemoryState:
    """Output from PatternMemoryAgent (FAISS vector search)."""
    similar_setups_found: int = 0
    historical_win_rate: float = 0.5
    historical_avg_pnl_atr: float = 0.0
    historical_avg_hold_bars: int = 0
    best_analog_date: str = ""
    sample_consistency: float = 0.0        # 0-1
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class OrchestratorSignal:
    """Final output from the Orchestrator."""
    signal: str = "HOLD"                   # LONG | SHORT | LONG_EXIT | SHORT_EXIT | HOLD
    time: str = ""
    entry: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    risk_pts: float = 0.0
    rr_t1: float = 0.0
    weighted_score: float = 0.0
    ml_prob: float = 0.0

    # Agent breakdown
    macro_bias: str = "NEUTRAL"
    macro_confidence: float = 0.0
    structure_confidence: float = 0.0
    momentum_confidence: float = 0.0
    trigger_quality: float = 0.0
    volume_confirms: bool = True
    memory_win_rate: float = 0.5
    pattern_detected: str = "NONE"

    # Score components
    long_score: float = 0.0
    short_score: float = 0.0
    atr_5m: float = 0.0
    adx_1h: float = 0.0
    h1_trend: str = "NEUTRAL"

    reasons: List[str] = field(default_factory=list)
    veto_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dict for API response (matches existing API format)."""
        d = {
            "signal": self.signal,
            "time": self.time,
            "entry": self.entry,
            "sl": self.sl,
            "target1": self.target1,
            "target2": self.target2,
            "risk_pts": round(self.risk_pts, 2),
            "rr_t1": round(self.rr_t1, 2),
            "long_score": round(self.long_score, 3),
            "short_score": round(self.short_score, 3),
            "ml_prob": round(self.ml_prob, 3),
            "adx_1h": round(self.adx_1h, 1),
            "atr_5m": round(self.atr_5m, 2),
            "macro_bias": self.macro_bias,
            "h1_trend": self.h1_trend,
            "weighted_score": round(self.weighted_score, 3),
            "pattern_detected": self.pattern_detected,
            "reasons": self.reasons[:5],  # Top 5 reasons
            # Agent confidence breakdown (for Telegram alerts & UI)
            "macro_confidence": round(self.macro_confidence, 3),
            "structure_confidence": round(self.structure_confidence, 3),
            "momentum_confidence": round(self.momentum_confidence, 3),
            "trigger_quality": round(self.trigger_quality, 3),
            "volume_confirms": self.volume_confirms,
            "memory_win_rate": round(self.memory_win_rate, 3),
        }
        # Add optional fields
        if self.signal == "HOLD":
            d["reason"] = "; ".join(self.veto_reasons[:3]) if self.veto_reasons else "No signal"
        if self.signal in ("LONG_EXIT", "SHORT_EXIT"):
            d["close"] = self.entry
            d["reason"] = "; ".join(self.reasons[:3])
        return d


# ═══════════════════════════════════════════════════════════════════════════════
# BASE AGENT CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class BaseAgent:
    """Base class for all trading agents."""

    def __init__(self):
        self.name = self.__class__.__name__

    def _safe_float(self, val, default: float = 0.0) -> float:
        """Safely convert a value to float."""
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _safe_int(self, val, default: int = 0) -> int:
        """Safely convert a value to int."""
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return default
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    def _get_val(self, row: pd.Series, col: str, default=0.0):
        """Get a value from a row, returning default if missing or NaN."""
        val = row.get(col, default)
        if isinstance(val, float) and np.isnan(val):
            return default
        return val
