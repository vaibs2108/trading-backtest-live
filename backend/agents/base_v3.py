"""
base_v3.py — Dataclasses for Multi-Agent v3 (Scenario-Based Architecture).

Kept separate from base.py so regime_strategy (which imports base.py) is unaffected.
strategy.py imports from both base.py (for backward-compat OrchestratorSignal.to_dict)
and base_v3.py (for the new v3 types).
"""
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL MAP
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PriceLevel:
    """A single significant price level from any timeframe."""
    price: float
    level_type: str          # EMA7 | EMA21 | SUPERTREND | FIB_382 | FIB_500 | FIB_618 | FIB_236 | SR | TRENDLINE | PIVOT
    timeframe: str           # W | D | 1H | 15m | 5m
    strength: float = 0.0   # 0-1 (weekly fib = 1.0, 5m EMA7 = 0.15)
    broken: bool = False     # Has price closed beyond this level?

    def __repr__(self):
        return f"{self.timeframe}_{self.level_type}@{self.price:.1f}(s={self.strength:.2f})"


@dataclass
class LevelMap:
    """Sorted price ladder of all significant levels relative to current price."""
    levels_above: List[PriceLevel] = field(default_factory=list)   # ascending by price (nearest first)
    levels_below: List[PriceLevel] = field(default_factory=list)   # descending by price (nearest first)
    current_price: float = 0.0
    current_zone: str = "UNKNOWN"           # e.g. "BETWEEN_EMA7_EMA21_1H"
    next_target_up: Optional[PriceLevel] = None
    next_target_down: Optional[PriceLevel] = None
    nearest_htf_resistance: Optional[PriceLevel] = None    # first W/D level above
    nearest_htf_support: Optional[PriceLevel] = None       # first W/D level below


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT OUTPUTS — v3
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class HTFScenario:
    """Output from HTFAgent (Weekly + Daily)."""
    weekly_bias: str = "RANGE"              # UPTREND | DOWNTREND | RANGE
    daily_bias: str = "RANGE"               # UPTREND | DOWNTREND | RANGE
    alignment: str = "BOTH_RANGE"           # ALIGNED_BULL | ALIGNED_BEAR | CONFLICTING | BOTH_RANGE

    # Price journey on D timeframe
    daily_price_journey: str = ""           # "Below D-EMA7, traveling to D-EMA21"
    daily_journey_target: float = 0.0
    daily_journey_invalidation: float = 0.0

    # HTF key levels as trade attractors/repellers
    htf_ceiling: Optional[PriceLevel] = None
    htf_floor: Optional[PriceLevel] = None
    htf_ceiling_dist_atr: float = 999.0
    htf_floor_dist_atr: float = 999.0

    # Exhaustion
    daily_trend_age: int = 0
    daily_exhaustion: float = 0.0

    reasons: List[str] = field(default_factory=list)


@dataclass
class SwingScenario:
    """Output from SwingAgent (1H — the primary trend engine)."""
    # EMA cascade position — the core innovation
    ema_cascade: str = "UNKNOWN"            # ABOVE_ALL | BETWEEN_7_21 | BELOW_21_ABOVE_ST | BELOW_ST
                                            # (and bearish inverses: BELOW_ALL | BETWEEN_21_7 etc.)
    ema_cascade_direction: str = "STALLING" # DESCENDING | ASCENDING | STALLING

    # Price journey
    price_journey: str = ""                 # "Broke 1H EMA7 → traveling to 1H EMA21 at 57500"
    journey_target: float = 0.0
    journey_target_type: str = ""           # EMA21 | SUPERTREND | FIB etc.
    journey_invalidation: float = 0.0
    bars_in_journey: int = 0

    # SuperTrend context
    supertrend_bias: int = 0                # 1 or -1
    bars_since_st_flip: int = 0
    price_to_st_atr: float = 0.0           # positive = above ST, negative = below

    # Key 1H levels (actual prices)
    ema7_price: float = 0.0
    ema21_price: float = 0.0
    supertrend_price: float = 0.0

    # MACD momentum (supporting evidence)
    macd_expanding: bool = False
    macd_direction: int = 0                 # 1 or -1

    # Derived trade direction
    trade_bias: str = "NEUTRAL"             # LONG | SHORT | NEUTRAL
    trade_bias_reason: str = ""

    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class FlowScenario:
    """Output from FlowAgent (15m momentum + volume)."""
    trend_state: str = "CONSOLIDATION"      # IMPULSE_DOWN | IMPULSE_UP | PULLBACK | CONSOLIDATION | REVERSAL_FORMING
    momentum_trajectory: str = "STEADY"     # ACCELERATING | STEADY | DECELERATING | REVERSING
    macd_phase: str = "NEUTRAL"             # EXPANDING_BULL | CONTRACTING_BULL | EXPANDING_BEAR | CONTRACTING_BEAR

    # Volume
    volume_confirms: bool = False
    volume_state: str = "NORMAL"            # CLIMAX | EXPANDING | DRYING | DIVERGENT | NORMAL

    # Oscillator context (trend-aware — the key v3 fix)
    oscillator_context: str = "NEUTRAL"     # OVERSOLD_IN_DOWNTREND | OVERSOLD_IN_UPTREND | OVERBOUGHT_IN_UPTREND | OVERBOUGHT_IN_DOWNTREND | NEUTRAL

    # Derived
    supports_direction: str = "NEUTRAL"     # LONG | SHORT | NEUTRAL
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class TriggerScenario:
    """Output from TriggerAgent v3 (5m execution)."""
    signal: str = "HOLD"                    # LONG | SHORT | LONG_EXIT | SHORT_EXIT | HOLD
    entry_type: str = "NONE"                # PULLBACK_TO_LEVEL | BREAKOUT | BOUNCE_AT_SUPPORT | REJECTION_AT_RESISTANCE | NONE
    entry_level: Optional[PriceLevel] = None

    # 5m structure
    ema_cascade_5m: str = "UNKNOWN"
    supertrend_5m: int = 0
    bars_since_5m_flip: int = 999

    # Candle pattern
    candle_pattern: str = "NONE"            # ENGULFING | HAMMER | SHOOTING_STAR | NONE

    # Risk from LevelMap
    entry_price: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    risk_reward: float = 0.0

    # Quality
    entry_quality: float = 0.0
    time_quality: float = 0.5
    market_phase: str = "UNKNOWN"           # OPENING | MIDDAY | CLOSING | OUTSIDE

    reasons: List[str] = field(default_factory=list)


@dataclass
class MicroConfirmation:
    """Output from MicroAgent (1m confirmation)."""
    confirms_entry: bool = False
    confirms_exit: bool = False

    m1_supertrend: int = 0
    m1_ema_cross: int = 0
    m1_srsi_state: str = "NEUTRAL"          # OVERSOLD_CURLING | OVERBOUGHT_TURNING | NEUTRAL

    reversal_detected: bool = False
    reversal_strength: float = 0.0

    reasons: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO ENGINE OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradingScenario:
    """A candidate trade scenario generated by the ScenarioEngine."""
    direction: str = "HOLD"                 # LONG | SHORT
    scenario_type: str = "NONE"             # EMA_CASCADE | HTF_LEVEL_BOUNCE | TREND_CONTINUATION | BREAKOUT | REVERSAL

    # Narrative
    narrative: str = ""

    # Levels
    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0
    target: float = 0.0
    target_level: Optional[PriceLevel] = None
    invalidation: float = 0.0
    invalidation_level: Optional[PriceLevel] = None

    # Confluence
    tf_agreement: int = 0                   # how many TFs agree
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)

    # Confidence
    base_confidence: float = 0.0
    faiss_boost: float = 0.0
    final_confidence: float = 0.0

    # FAISS context
    historical_analogs: int = 0
    historical_win_rate: float = 0.5
    historical_avg_move_atr: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR v3 SIGNAL (backward-compatible with OrchestratorSignal.to_dict)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class OrchestratorV3Signal:
    """Final output from Orchestrator v3 — scenario-based."""
    signal: str = "HOLD"
    time: str = ""
    entry: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    risk_pts: float = 0.0
    rr_t1: float = 0.0

    # Scenario info
    scenario_type: str = "NONE"
    narrative: str = ""
    scenario_confidence: float = 0.0

    # Confidence breakdown
    structure_confidence: float = 0.0
    faiss_confidence: float = 0.0
    volume_confirms: bool = True

    # Backward-compatible fields
    weighted_score: float = 0.0
    ml_prob: float = 0.0
    macro_bias: str = "NEUTRAL"
    macro_confidence: float = 0.0
    momentum_confidence: float = 0.0
    trigger_quality: float = 0.0
    memory_win_rate: float = 0.5
    pattern_detected: str = "NONE"
    long_score: float = 0.0
    short_score: float = 0.0
    atr_5m: float = 0.0
    adx_1h: float = 0.0
    h1_trend: str = "NEUTRAL"

    reasons: List[str] = field(default_factory=list)
    veto_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Backward-compatible dict matching old OrchestratorSignal.to_dict() API format."""
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
            "weighted_score": round(self.scenario_confidence, 3),
            "pattern_detected": self.pattern_detected,
            "reasons": self.reasons[:5],
            # Agent confidence breakdown
            "macro_confidence": round(self.macro_confidence, 3),
            "structure_confidence": round(self.structure_confidence, 3),
            "momentum_confidence": round(self.momentum_confidence, 3),
            "trigger_quality": round(self.trigger_quality, 3),
            "volume_confirms": self.volume_confirms,
            "memory_win_rate": round(self.memory_win_rate, 3),
            # v3 extras
            "scenario_type": self.scenario_type,
            "narrative": self.narrative,
        }
        if self.signal == "HOLD":
            d["reason"] = "; ".join(self.veto_reasons[:3]) if self.veto_reasons else "No scenario"
        if self.signal in ("LONG_EXIT", "SHORT_EXIT"):
            d["close"] = self.entry
            d["reason"] = "; ".join(self.reasons[:3])
        return d
