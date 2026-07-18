"""
scenario_engine.py — Scenario Engine: generates candidate trading scenarios.

Takes outputs from all agents and constructs 0-3 actionable scenarios per bar.
Each scenario has: direction, target, invalidation, confluence count, narrative.

This is the BRAIN of v3 — it replaces weighted scoring with structural reasoning.
"""
import logging
import numpy as np
import pandas as pd
from typing import List, Optional
from .base_v3 import (
    TradingScenario, HTFScenario, SwingScenario, FlowScenario,
    TriggerScenario, LevelMap, PriceLevel,
)

logger = logging.getLogger(__name__)


class ScenarioEngine:
    """Generates candidate trade scenarios from agent outputs."""

    def __init__(self, min_tf_agreement: int = 2):
        self.min_tf_agreement = min_tf_agreement

    def generate(self, row: pd.Series, htf: HTFScenario, swing: SwingScenario,
                 flow: FlowScenario, trigger: TriggerScenario,
                 level_map: LevelMap) -> List[TradingScenario]:
        """Generate 0-3 candidate scenarios from all agent outputs."""
        scenarios: List[TradingScenario] = []
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if atr < 1:
            atr = close * 0.002

        # ── Rule 1: EMA Cascade Trade ───────────────────────────────────
        cascade_scenario = self._rule_ema_cascade(htf, swing, flow, trigger, level_map, close, atr)
        if cascade_scenario:
            scenarios.append(cascade_scenario)

        # ── Rule 2: HTF Level Bounce/Rejection ──────────────────────────
        htf_scenario = self._rule_htf_level(htf, swing, flow, trigger, level_map, close, atr)
        if htf_scenario:
            scenarios.append(htf_scenario)

        # ── Rule 3: Trend Continuation After Pullback ───────────────────
        cont_scenario = self._rule_trend_continuation(htf, swing, flow, trigger, level_map, close, atr)
        if cont_scenario:
            scenarios.append(cont_scenario)

        # ── Rule 4: Breakout ────────────────────────────────────────────
        breakout_scenario = self._rule_breakout(swing, flow, trigger, level_map, close, atr)
        if breakout_scenario:
            scenarios.append(breakout_scenario)

        # ── Rule 5: Reversal at Exhaustion ──────────────────────────────
        reversal_scenario = self._rule_reversal_exhaustion(htf, swing, flow, trigger, level_map, close, atr)
        if reversal_scenario:
            scenarios.append(reversal_scenario)

        return scenarios

    def _rule_ema_cascade(self, htf, swing, flow, trigger, level_map, close, atr):
        """Rule 1: EMA cascade descent/ascent creates directional trade."""
        cascade = swing.ema_cascade
        direction = swing.trade_bias

        # Only fire for cascade states that imply a journey
        cascade_short_states = {"BETWEEN_7_21", "BELOW_21_ABOVE_ST", "BELOW_ST"}
        cascade_long_states = {"BETWEEN_21_7", "ABOVE_21_BELOW_ST", "ABOVE_ST"}

        if direction == "SHORT" and cascade in cascade_short_states:
            target = swing.journey_target
            invalidation = swing.journey_invalidation

            if target <= 0 or target >= close:
                return None

            # Count TF agreement
            tf_agree = 1  # 1H swing cascade counts as 1
            supporting = [swing.price_journey]
            contradicting = []

            # 15m flow agrees?
            if flow.supports_direction == "SHORT":
                tf_agree += 1
                supporting.append(f"15m: {flow.trend_state}")
            elif flow.supports_direction == "LONG":
                contradicting.append(f"15m supports LONG ({flow.trend_state})")

            # 5m trigger agrees?
            if trigger.signal == "SHORT":
                tf_agree += 1
                supporting.append(f"5m: ST={trigger.supertrend_5m}, EQ={trigger.entry_quality:.2f}")
            elif trigger.signal == "LONG":
                contradicting.append("5m trigger is LONG")

            # HTF alignment
            if htf.alignment == "ALIGNED_BEAR":
                tf_agree += 1
                supporting.append("W+D aligned bearish")
            elif htf.alignment == "ALIGNED_BULL":
                contradicting.append("W+D aligned bullish (counter-trend)")

            # Oscillator context bonus
            if flow.oscillator_context == "OVERSOLD_IN_DOWNTREND":
                supporting.append("Oscillators oversold = momentum, not reversal")
            elif flow.oscillator_context == "OVERBOUGHT_IN_DOWNTREND":
                supporting.append("Oscillators overbought in downtrend = SHORT entry")

            confidence = self._compute_confidence(tf_agree, len(contradicting), swing.confidence, flow.confidence)

            narrative = (f"EMA CASCADE SHORT: {swing.price_journey}. "
                        f"15m {flow.trend_state}, momentum {flow.momentum_trajectory}. "
                        f"Target: {target:.0f}, Invalidation: {invalidation:.0f}")

            return TradingScenario(
                direction="SHORT",
                scenario_type="EMA_CASCADE",
                narrative=narrative,
                entry_zone_low=close - 0.5 * atr,
                entry_zone_high=close + 0.5 * atr,
                target=target,
                invalidation=invalidation,
                tf_agreement=tf_agree,
                supporting_evidence=supporting,
                contradicting_evidence=contradicting,
                base_confidence=confidence,
                final_confidence=confidence,
            )

        elif direction == "LONG" and cascade in cascade_long_states:
            target = swing.journey_target
            invalidation = swing.journey_invalidation

            if target <= 0 or target <= close:
                return None

            tf_agree = 1
            supporting = [swing.price_journey]
            contradicting = []

            if flow.supports_direction == "LONG":
                tf_agree += 1
                supporting.append(f"15m: {flow.trend_state}")
            elif flow.supports_direction == "SHORT":
                contradicting.append(f"15m supports SHORT ({flow.trend_state})")

            if trigger.signal == "LONG":
                tf_agree += 1
                supporting.append(f"5m: ST={trigger.supertrend_5m}, EQ={trigger.entry_quality:.2f}")

            if htf.alignment == "ALIGNED_BULL":
                tf_agree += 1
                supporting.append("W+D aligned bullish")
            elif htf.alignment == "ALIGNED_BEAR":
                contradicting.append("W+D aligned bearish (counter-trend)")

            if flow.oscillator_context == "OVERSOLD_IN_UPTREND":
                supporting.append("Oversold in uptrend = bounce setup")

            confidence = self._compute_confidence(tf_agree, len(contradicting), swing.confidence, flow.confidence)

            narrative = (f"EMA CASCADE LONG: {swing.price_journey}. "
                        f"15m {flow.trend_state}, momentum {flow.momentum_trajectory}. "
                        f"Target: {target:.0f}, Invalidation: {invalidation:.0f}")

            return TradingScenario(
                direction="LONG",
                scenario_type="EMA_CASCADE",
                narrative=narrative,
                entry_zone_low=close - 0.5 * atr,
                entry_zone_high=close + 0.5 * atr,
                target=target,
                invalidation=invalidation,
                tf_agreement=tf_agree,
                supporting_evidence=supporting,
                contradicting_evidence=contradicting,
                base_confidence=confidence,
                final_confidence=confidence,
            )

        return None

    def _rule_htf_level(self, htf, swing, flow, trigger, level_map, close, atr):
        """Rule 2: Trade at HTF level (rejection or bounce)."""
        # SHORT at HTF ceiling
        if htf.htf_ceiling and htf.htf_ceiling_dist_atr < 1.5:
            if flow.momentum_trajectory in ("DECELERATING", "REVERSING"):
                tf_agree = 1
                supporting = [f"Near HTF ceiling {htf.htf_ceiling} ({htf.htf_ceiling_dist_atr:.1f} ATR)"]
                contradicting = []

                if swing.trade_bias == "SHORT":
                    tf_agree += 1
                    supporting.append("1H cascade supports SHORT")
                if flow.supports_direction == "SHORT":
                    tf_agree += 1
                if trigger.signal == "SHORT":
                    tf_agree += 1

                if htf.alignment == "ALIGNED_BULL":
                    contradicting.append("HTF aligned bull — counter-trend at resistance")

                confidence = self._compute_confidence(tf_agree, len(contradicting), 0.6, flow.confidence)

                return TradingScenario(
                    direction="SHORT",
                    scenario_type="HTF_LEVEL_BOUNCE",
                    narrative=f"HTF REJECTION: Price at {htf.htf_ceiling}, momentum {flow.momentum_trajectory}",
                    entry_zone_low=close - 0.3 * atr,
                    entry_zone_high=htf.htf_ceiling.price,
                    target=swing.journey_target if swing.journey_target > 0 and swing.journey_target < close else close - 3 * atr,
                    target_level=htf.htf_ceiling,
                    invalidation=htf.htf_ceiling.price + 0.5 * atr,
                    tf_agreement=tf_agree,
                    supporting_evidence=supporting,
                    contradicting_evidence=contradicting,
                    base_confidence=confidence,
                    final_confidence=confidence,
                )

        # LONG at HTF floor
        if htf.htf_floor and htf.htf_floor_dist_atr < 1.5:
            if flow.momentum_trajectory in ("DECELERATING", "REVERSING"):
                tf_agree = 1
                supporting = [f"Near HTF floor {htf.htf_floor} ({htf.htf_floor_dist_atr:.1f} ATR)"]
                contradicting = []

                if swing.trade_bias == "LONG":
                    tf_agree += 1
                if flow.supports_direction == "LONG":
                    tf_agree += 1
                if trigger.signal == "LONG":
                    tf_agree += 1

                if htf.alignment == "ALIGNED_BEAR":
                    contradicting.append("HTF aligned bear — counter-trend at support")

                confidence = self._compute_confidence(tf_agree, len(contradicting), 0.6, flow.confidence)

                return TradingScenario(
                    direction="LONG",
                    scenario_type="HTF_LEVEL_BOUNCE",
                    narrative=f"HTF BOUNCE: Price at {htf.htf_floor}, momentum {flow.momentum_trajectory}",
                    entry_zone_low=htf.htf_floor.price,
                    entry_zone_high=close + 0.3 * atr,
                    target=swing.journey_target if swing.journey_target > close else close + 3 * atr,
                    target_level=htf.htf_floor,
                    invalidation=htf.htf_floor.price - 0.5 * atr,
                    tf_agreement=tf_agree,
                    supporting_evidence=supporting,
                    contradicting_evidence=contradicting,
                    base_confidence=confidence,
                    final_confidence=confidence,
                )
        return None

    def _rule_trend_continuation(self, htf, swing, flow, trigger, level_map, close, atr):
        """Rule 3: Continue existing strong trend after a pullback."""
        # LONG continuation
        if swing.trade_bias == "LONG" and swing.ema_cascade == "ABOVE_ALL":
            if flow.trend_state in ("PULLBACK", "IMPULSE_UP", "CONSOLIDATION"):
                is_pullback = flow.trend_state == "PULLBACK"
                tf_agree = 2 if is_pullback else 1  # pullback = stronger confluence
                label = "15m pullback — ideal entry" if is_pullback else f"15m {flow.trend_state} — trend active"
                supporting = ["Strong uptrend (ABOVE_ALL)", label]
                contradicting = []

                if trigger.signal == "LONG":
                    tf_agree += 1
                    supporting.append("5m confirms LONG")
                if htf.alignment in ("ALIGNED_BULL", "BOTH_RANGE"):
                    tf_agree += 1

                if htf.htf_ceiling and htf.htf_ceiling_dist_atr < 2:
                    contradicting.append(f"HTF ceiling nearby ({htf.htf_ceiling_dist_atr:.1f} ATR)")

                confidence = self._compute_confidence(tf_agree, len(contradicting), swing.confidence, flow.confidence)

                target = level_map.next_target_up.price if level_map.next_target_up else close + 2 * atr

                return TradingScenario(
                    direction="LONG",
                    scenario_type="TREND_CONTINUATION",
                    narrative=f"TREND CONTINUATION LONG: 1H ABOVE_ALL, 15m pulling back",
                    entry_zone_low=close - 0.3 * atr,
                    entry_zone_high=close + 0.3 * atr,
                    target=target,
                    invalidation=swing.ema7_price - 0.3 * atr if swing.ema7_price > 0 else close - 2 * atr,
                    tf_agreement=tf_agree,
                    supporting_evidence=supporting,
                    contradicting_evidence=contradicting,
                    base_confidence=confidence,
                    final_confidence=confidence,
                )

        # SHORT continuation
        if swing.trade_bias == "SHORT" and swing.ema_cascade == "BELOW_ALL":
            if flow.trend_state in ("PULLBACK", "IMPULSE_DOWN", "CONSOLIDATION"):
                is_pullback = flow.trend_state == "PULLBACK"
                tf_agree = 2 if is_pullback else 1
                label = "15m pullback — ideal entry" if is_pullback else f"15m {flow.trend_state} — trend active"
                supporting = ["Strong downtrend (BELOW_ALL)", label]
                contradicting = []

                if trigger.signal == "SHORT":
                    tf_agree += 1
                    supporting.append("5m confirms SHORT")
                if htf.alignment in ("ALIGNED_BEAR", "BOTH_RANGE"):
                    tf_agree += 1

                if htf.htf_floor and htf.htf_floor_dist_atr < 2:
                    contradicting.append(f"HTF floor nearby ({htf.htf_floor_dist_atr:.1f} ATR)")

                confidence = self._compute_confidence(tf_agree, len(contradicting), swing.confidence, flow.confidence)
                target = level_map.next_target_down.price if level_map.next_target_down else close - 2 * atr

                return TradingScenario(
                    direction="SHORT",
                    scenario_type="TREND_CONTINUATION",
                    narrative=f"TREND CONTINUATION SHORT: 1H BELOW_ALL, 15m pulling back",
                    entry_zone_low=close - 0.3 * atr,
                    entry_zone_high=close + 0.3 * atr,
                    target=target,
                    invalidation=swing.ema7_price + 0.3 * atr if swing.ema7_price > 0 else close + 2 * atr,
                    tf_agreement=tf_agree,
                    supporting_evidence=supporting,
                    contradicting_evidence=contradicting,
                    base_confidence=confidence,
                    final_confidence=confidence,
                )
        return None

    def _rule_breakout(self, swing, flow, trigger, level_map, close, atr):
        """Rule 4: Breakout through a significant level."""
        if trigger.bars_since_5m_flip > 3:
            return None  # Breakouts need fresh ST flip

        if flow.volume_state not in ("EXPANDING", "CLIMAX"):
            return None  # Breakouts need volume

        if trigger.signal == "LONG" and trigger.entry_type == "BREAKOUT":
            target = level_map.next_target_up.price if level_map.next_target_up else close + 3 * atr
            return TradingScenario(
                direction="LONG",
                scenario_type="BREAKOUT",
                narrative=f"BREAKOUT LONG: 5m ST just flipped with volume",
                entry_zone_low=close - 0.2 * atr,
                entry_zone_high=close + 0.5 * atr,
                target=target,
                invalidation=close - 1.5 * atr,
                tf_agreement=2,
                supporting_evidence=["5m breakout", f"Volume {flow.volume_state}"],
                base_confidence=0.50,
                final_confidence=0.50,
            )

        if trigger.signal == "SHORT" and trigger.entry_type == "BREAKOUT":
            target = level_map.next_target_down.price if level_map.next_target_down else close - 3 * atr
            return TradingScenario(
                direction="SHORT",
                scenario_type="BREAKOUT",
                narrative=f"BREAKOUT SHORT: 5m ST just flipped with volume",
                entry_zone_low=close - 0.5 * atr,
                entry_zone_high=close + 0.2 * atr,
                target=target,
                invalidation=close + 1.5 * atr,
                tf_agreement=2,
                supporting_evidence=["5m breakout", f"Volume {flow.volume_state}"],
                base_confidence=0.50,
                final_confidence=0.50,
            )

        return None

    def _rule_reversal_exhaustion(self, htf, swing, flow, trigger, level_map, close, atr):
        """Rule 5: Counter-trend reversal when daily trend is exhausted.

        Conditions (all three must be met):
          1. htf.daily_exhaustion > 0.7  (trend running out of steam)
          2. 1H EMA cascade is changing direction OR already in counter-trend state
          3. 15m oscillator context matches trend exhaustion
             (OVERSOLD_IN_DOWNTREND for bearish exhaustion → LONG reversal,
              OVERBOUGHT_IN_UPTREND for bullish exhaustion → SHORT reversal)

        Generates a lower-confidence counter-trend scenario.
        """
        if htf.daily_exhaustion <= 0.7:
            return None

        # Detect which trend is exhausted and what the reversal direction is
        reversal_dir = None
        exhaustion_osc = None

        if htf.daily_bias == "UPTREND":
            # Bullish trend exhausted → potential SHORT reversal
            reversal_dir = "SHORT"
            exhaustion_osc = "OVERBOUGHT_IN_UPTREND"
        elif htf.daily_bias == "DOWNTREND":
            # Bearish trend exhausted → potential LONG reversal
            reversal_dir = "LONG"
            exhaustion_osc = "OVERSOLD_IN_DOWNTREND"
        else:
            return None  # RANGE — no exhaustion to reverse

        # Check cascade is starting to change direction
        cascade_changing = False
        if reversal_dir == "SHORT":
            # For bearish reversal: cascade should be descending or in a bearish state
            cascade_changing = (
                swing.ema_cascade_direction == "DESCENDING" or
                swing.ema_cascade in ("BETWEEN_7_21", "BELOW_21_ABOVE_ST", "BELOW_ST")
            )
        elif reversal_dir == "LONG":
            # For bullish reversal: cascade should be ascending or in a bullish state
            cascade_changing = (
                swing.ema_cascade_direction == "ASCENDING" or
                swing.ema_cascade in ("BETWEEN_21_7", "ABOVE_21_BELOW_ST", "ABOVE_ST")
            )

        if not cascade_changing:
            return None

        # Check oscillator context matches exhaustion
        if flow.oscillator_context != exhaustion_osc:
            return None

        # All three conditions met — build the reversal scenario
        tf_agree = 1  # HTF exhaustion
        supporting = [f"Daily exhaustion {htf.daily_exhaustion:.2f}"]
        contradicting = []

        # Cascade supports?
        supporting.append(f"1H cascade {swing.ema_cascade_direction}: {swing.ema_cascade}")
        tf_agree += 1

        # 15m flow supports reversal direction?
        if flow.supports_direction == reversal_dir:
            tf_agree += 1
            supporting.append(f"15m supports {reversal_dir} ({flow.trend_state})")
        elif flow.supports_direction != "NEUTRAL":
            contradicting.append(f"15m supports {flow.supports_direction}")

        # 5m trigger agrees?
        if trigger.signal == reversal_dir:
            tf_agree += 1
            supporting.append(f"5m trigger confirms {reversal_dir}")

        # Oscillator context
        supporting.append(f"Oscillator: {flow.oscillator_context}")

        # HTF alignment as contradiction (counter-trend by definition)
        if htf.alignment in ("ALIGNED_BULL", "ALIGNED_BEAR"):
            contradicting.append(f"HTF still {htf.alignment} — counter-trend risk")

        # Compute confidence (lower for counter-trend trades)
        confidence = self._compute_confidence(tf_agree, len(contradicting), swing.confidence, flow.confidence)
        confidence = round(confidence * 0.85, 3)  # 15% penalty for counter-trend

        # Target and invalidation
        if reversal_dir == "LONG":
            target = level_map.next_target_up.price if level_map.next_target_up else close + 2 * atr
            invalidation = close - 1.5 * atr
        else:
            target = level_map.next_target_down.price if level_map.next_target_down else close - 2 * atr
            invalidation = close + 1.5 * atr

        narrative = (f"REVERSAL {reversal_dir}: Daily trend exhausted "
                    f"(exhaustion={htf.daily_exhaustion:.2f}). "
                    f"1H cascade {swing.ema_cascade_direction}, "
                    f"15m {flow.oscillator_context}. "
                    f"Target: {target:.0f}, Invalidation: {invalidation:.0f}")

        return TradingScenario(
            direction=reversal_dir,
            scenario_type="REVERSAL",
            narrative=narrative,
            entry_zone_low=close - 0.5 * atr,
            entry_zone_high=close + 0.5 * atr,
            target=target,
            invalidation=invalidation,
            tf_agreement=tf_agree,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            base_confidence=confidence,
            final_confidence=confidence,
        )

    def _compute_confidence(self, tf_agree: int, contradictions: int,
                             swing_conf: float, flow_conf: float) -> float:
        """Compute scenario confidence from TF agreement and agent confidences."""
        base = 0.20 + (tf_agree * 0.15)  # Each agreeing TF adds 0.15
        base += (swing_conf + flow_conf) / 2 * 0.20  # Agent confidence contribution
        base -= contradictions * 0.10  # Each contradiction removes 0.10
        return max(0.0, min(1.0, round(base, 3)))
