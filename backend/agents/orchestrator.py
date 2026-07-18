"""
orchestrator.py — Orchestrator: Weighted consensus + veto logic.

Combines all 6 agent outputs into a final trading decision.
Uses configurable weights and hard veto rules.
"""
import logging
import numpy as np
import pandas as pd
from .base import (BaseAgent, OrchestratorSignal,
                   MacroState, StructureState, MomentumState,
                   TriggerState, VolumeState, PatternMemoryState)

logger = logging.getLogger(__name__)


class Orchestrator(BaseAgent):
    """
    Final decision maker. Combines all agents with weighted consensus.
    Applies veto rules as hard overrides.
    """

    def __init__(self, weights: dict = None, min_score: float = 0.55):
        super().__init__()
        self.weights = weights or {
            "macro": 0.25,
            "structure": 0.20,
            "momentum": 0.20,
            "trigger": 0.15,
            "volume": 0.10,
            "memory": 0.10,
        }
        self.min_score = min_score

    def evaluate(self, row: pd.Series,
                 macro: MacroState,
                 structure: StructureState,
                 momentum: MomentumState,
                 trigger: TriggerState,
                 volume: VolumeState,
                 memory: PatternMemoryState,
                 ml_prob: float = 0.0,
                 ml_threshold: float = 0.55,
                 position: str = "NONE") -> OrchestratorSignal:
        """
        Combine all agent outputs into final signal.

        Returns OrchestratorSignal with the same API format as the old strategy.
        """
        close = self._safe_float(self._get_val(row, "close", 0))
        atr = self._safe_float(self._get_val(row, "atr", close * 0.002))
        ts_val = self._get_val(row, "timestamp", "")
        try:
            ts = pd.Timestamp(ts_val).tz_localize("Asia/Kolkata").isoformat()
        except Exception:
            ts = str(ts_val)
        h1_adx = self._safe_float(self._get_val(row, "h1_adx", 0))
        h1_st = self._safe_int(self._get_val(row, "h1_supertrend_dir", 0))

        all_reasons = []
        veto_reasons = []

        # ── EXIT SIGNALS (priority) ─────────────────────────────────────
        if trigger.signal in ("LONG_EXIT", "SHORT_EXIT"):
            return OrchestratorSignal(
                signal=trigger.signal,
                time=ts,
                entry=close,
                reasons=trigger.reasons,
                macro_bias=macro.bias,
                atr_5m=round(atr, 2),
                adx_1h=round(h1_adx, 1),
                h1_trend="LONG" if h1_st == 1 else "SHORT" if h1_st == -1 else "NEUTRAL",
            )

        # ── VETO CHECKS ────────────────────────────────────────────────
        # 1. Macro must have direction, unless overridden by trigger signal
        if macro.bias == "NEUTRAL" and trigger.signal not in ("LONG", "SHORT"):
            veto_reasons.append("Macro bias NEUTRAL — no trade direction")

        # 2. Volume veto
        if volume.veto_signal:
            veto_reasons.append(f"Volume VETO: {volume.veto_reason}")

        # 3. Trigger has no signal
        if trigger.signal == "HOLD":
            veto_reasons.append("No 5m entry trigger")

        # 4. Time quality too low
        if trigger.time_quality < 0.2:
            veto_reasons.append("Market phase not suitable for entries")

        # 5. Pattern contradicts trade direction
        if trigger.signal in ("LONG", "SHORT") and structure.pattern_signal != "NONE" and structure.pattern_signal != trigger.signal:
            if structure.pattern_completion > 0.7:
                veto_reasons.append(
                    f"Chart pattern {structure.pattern_name} signals "
                    f"{structure.pattern_signal} vs trade direction {trigger.signal}"
                )

        # 6. High trend exhaustion + no momentum
        if trigger.signal == macro.bias and macro.trend_exhaustion > 0.7 and momentum.confidence < 0.3:
            veto_reasons.append("Trend exhausted + weak momentum")

        # 7. Pattern memory says this setup fails (if enough samples)
        if (memory.similar_setups_found >= 10 and
                memory.historical_win_rate < 0.30):
            veto_reasons.append(
                f"Pattern memory: only {memory.historical_win_rate:.0%} win rate "
                f"across {memory.similar_setups_found} similar setups"
            )

        # 8. At weekly/daily resistance going LONG — structural headwind
        if trigger.signal == "LONG" and structure.htf_resistance_conflict:
            veto_reasons.append(
                f"HTF VETO: {structure.htf_conflict_reason} — avoid LONG at W/D resistance"
            )

        # 9. At weekly/daily support going SHORT — structural tailwind against trade
        if trigger.signal == "SHORT" and structure.htf_support_conflict:
            veto_reasons.append(
                f"HTF VETO: {structure.htf_conflict_reason} — avoid SHORT at W/D support"
            )

        # If any veto triggered, return HOLD
        if veto_reasons:
            return OrchestratorSignal(
                signal="HOLD",
                time=ts,
                entry=close,
                long_score=round(macro.confidence if trigger.signal == "LONG" else 0, 3),
                short_score=round(macro.confidence if trigger.signal == "SHORT" else 0, 3),
                macro_bias=macro.bias,
                atr_5m=round(atr, 2),
                adx_1h=round(h1_adx, 1),
                h1_trend="LONG" if h1_st == 1 else "SHORT" if h1_st == -1 else "NEUTRAL",
                veto_reasons=veto_reasons,
                reasons=trigger.reasons,
            )

        # ── WEIGHTED SCORE ──────────────────────────────────────────────
        weighted_score = (
            self.weights["macro"] * macro.confidence +
            self.weights["structure"] * structure.confidence +
            self.weights["momentum"] * momentum.confidence +
            self.weights["trigger"] * trigger.entry_quality +
            self.weights["volume"] * volume.confidence +
            self.weights["memory"] * memory.confidence
        )

        # Collect all reasons
        all_reasons.extend(macro.reasons[:2])
        all_reasons.extend(structure.reasons[:2])
        all_reasons.extend(momentum.reasons[:2])
        all_reasons.extend(trigger.reasons[:2])
        all_reasons.extend(volume.reasons[:1])
        all_reasons.extend(memory.reasons[:1])

        # ── ML FILTER ───────────────────────────────────────────────────
        if ml_prob > 0 and ml_prob < ml_threshold:
            veto_reasons.append(
                f"ML rejected (prob={ml_prob:.3f} < {ml_threshold:.3f})"
            )
            return OrchestratorSignal(
                signal="HOLD",
                time=ts,
                entry=close,
                weighted_score=round(weighted_score, 3),
                ml_prob=round(ml_prob, 3),
                long_score=round(weighted_score if trigger.signal == "LONG" else 0, 3),
                short_score=round(weighted_score if trigger.signal == "SHORT" else 0, 3),
                macro_bias=macro.bias,
                atr_5m=round(atr, 2),
                adx_1h=round(h1_adx, 1),
                h1_trend="LONG" if h1_st == 1 else "SHORT" if h1_st == -1 else "NEUTRAL",
                veto_reasons=veto_reasons,
                reasons=all_reasons,
            )

        # ── MINIMUM SCORE CHECK ─────────────────────────────────────────
        if weighted_score < self.min_score:
            return OrchestratorSignal(
                signal="HOLD",
                time=ts,
                entry=close,
                weighted_score=round(weighted_score, 3),
                ml_prob=round(ml_prob, 3),
                long_score=round(weighted_score if trigger.signal == "LONG" else 0, 3),
                short_score=round(weighted_score if trigger.signal == "SHORT" else 0, 3),
                macro_bias=macro.bias,
                atr_5m=round(atr, 2),
                adx_1h=round(h1_adx, 1),
                h1_trend="LONG" if h1_st == 1 else "SHORT" if h1_st == -1 else "NEUTRAL",
                veto_reasons=[f"Score {weighted_score:.3f} < min {self.min_score}"],
                reasons=all_reasons,
            )

        # ── CONFIRMED SIGNAL ────────────────────────────────────────────
        signal = trigger.signal  # Use the overridden trigger direction (LONG or SHORT)

        # Risk-reward
        risk_pts = abs(trigger.entry_price - trigger.sl)
        rr_t1 = round(abs(trigger.target1 - trigger.entry_price) /
                       max(risk_pts, 0.01), 2)

        # Long/short score for backward compatibility
        long_score = weighted_score if signal == "LONG" else 0.0
        short_score = weighted_score if signal == "SHORT" else 0.0

        return OrchestratorSignal(
            signal=signal,
            time=ts,
            entry=trigger.entry_price,
            sl=trigger.sl,
            target1=trigger.target1,
            target2=trigger.target2,
            risk_pts=round(risk_pts, 2),
            rr_t1=rr_t1,
            weighted_score=round(weighted_score, 3),
            ml_prob=round(ml_prob, 3),
            macro_bias=macro.bias,
            macro_confidence=round(macro.confidence, 3),
            structure_confidence=round(structure.confidence, 3),
            momentum_confidence=round(momentum.confidence, 3),
            trigger_quality=round(trigger.entry_quality, 3),
            volume_confirms=not volume.veto_signal,
            memory_win_rate=round(memory.historical_win_rate, 3),
            pattern_detected=structure.pattern_name,
            long_score=round(long_score, 3),
            short_score=round(short_score, 3),
            atr_5m=round(atr, 2),
            adx_1h=round(h1_adx, 1),
            h1_trend="LONG" if h1_st == 1 else "SHORT" if h1_st == -1 else "NEUTRAL",
            reasons=all_reasons,
            veto_reasons=[],
        )
