"""
orchestrator_v3.py — Orchestrator v3: Scenario-based trade evaluator.

No weighted scores. Instead: "Is there a valid scenario with multi-TF confluence?"

Backward-compatible with OrchestratorSignal.to_dict() API format.
"""
import logging
import numpy as np
import pandas as pd
from typing import List, Optional
from .base import BaseAgent
from .base_v3 import (
    OrchestratorV3Signal, TradingScenario, HTFScenario, SwingScenario,
    FlowScenario, TriggerScenario, MicroConfirmation, LevelMap,
)

logger = logging.getLogger(__name__)


class OrchestratorV3(BaseAgent):
    """Scenario-based trade evaluator — replaces weighted consensus."""

    def __init__(self, min_confidence: float = 0.40, min_tf_agreement: int = 2, min_trigger_quality: float = 0.0, min_rr: float = 0.0, max_rr: float = 0.0, block_short_oversold: bool = False):
        super().__init__()
        self.min_confidence = min_confidence
        self.min_tf_agreement = min_tf_agreement
        self.min_trigger_quality = min_trigger_quality
        self.min_rr = min_rr
        self.max_rr = max_rr
        self.block_short_oversold = block_short_oversold

    def evaluate(self, row: pd.Series,
                 scenarios: List[TradingScenario],
                 htf: HTFScenario,
                 swing: SwingScenario,
                 flow: FlowScenario,
                 trigger: TriggerScenario,
                 micro: MicroConfirmation,
                 level_map: LevelMap,
                 faiss_win_rate: float = 0.5,
                 faiss_confidence: float = 0.0,
                 position: str = "NONE",
                 memory_agent=None) -> OrchestratorV3Signal:
        """Evaluate scenarios and produce final trade signal.

        Args:
            memory_agent: Optional PatternMemoryAgentV2 instance. When provided,
                         each matching scenario is dynamically queried against the
                         FAISS index rather than using the static faiss_win_rate /
                         faiss_confidence fallback values.
        """

        close = self._safe_float(self._get_val(row, "close", 0))
        atr = self._safe_float(self._get_val(row, "atr", close * 0.002))
        if atr < 1:
            atr = close * 0.002
        ts_val = self._get_val(row, "timestamp", "")
        try:
            ts = pd.Timestamp(ts_val).tz_localize("Asia/Kolkata").isoformat()
        except Exception:
            ts = str(ts_val)

        h1_adx = self._safe_float(self._get_val(row, "h1_adx", 0))
        h1_st = self._safe_int(self._get_val(row, "h1_supertrend_dir", 0))
        h1_trend = "LONG" if h1_st == 1 else "SHORT" if h1_st == -1 else "NEUTRAL"

        # ── EXIT SIGNALS (priority) ─────────────────────────────────────
        if trigger.signal in ("LONG_EXIT", "SHORT_EXIT"):
            return OrchestratorV3Signal(
                signal=trigger.signal,
                time=ts,
                entry=close,
                macro_bias=swing.trade_bias,
                atr_5m=round(atr, 2),
                adx_1h=round(h1_adx, 1),
                h1_trend=h1_trend,
                reasons=trigger.reasons,
            )

        # ── NO TRIGGER SIGNAL → HOLD ──────────────────────────────────
        if trigger.signal == "HOLD":
            return OrchestratorV3Signal(
                signal="HOLD",
                time=ts,
                entry=close,
                macro_bias=swing.trade_bias,
                atr_5m=round(atr, 2),
                adx_1h=round(h1_adx, 1),
                h1_trend=h1_trend,
                veto_reasons=["No 5m entry trigger"] + trigger.reasons[:2],
            )

        # ── FILTER SCENARIOS ────────────────────────────────────────────
        veto_reasons = []

        # Block SHORT when StochRSI is oversold (counter-trend into exhausted move)
        if self.block_short_oversold and trigger.signal == "SHORT":
            stoch_k = self._safe_float(self._get_val(row, "stoch_k", 50))
            if stoch_k < 20:
                veto_reasons.append(f"SHORT blocked: StochRSI K={stoch_k:.1f} < 20 (oversold)")
                return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons)

        # Time quality
        if trigger.time_quality < 0.2:
            veto_reasons.append("Market phase not suitable")
            return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons)

        # Trigger quality
        if trigger.entry_quality < self.min_trigger_quality:
            veto_reasons.append(f"Trigger entry quality {trigger.entry_quality:.2f} < {self.min_trigger_quality}")
            return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons)

        # Filter: only scenarios matching trigger direction
        matching = [s for s in scenarios if s.direction == trigger.signal]

        if not matching:
            veto_reasons.append(f"No scenario supports {trigger.signal}")
            return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons)

        # ── Filter out scenarios where target is already hit ────────────
        matching = [s for s in matching if not self._target_already_hit(s, close)]
        if not matching:
            veto_reasons.append("All scenario targets already hit by current price")
            return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons)

        # ── Apply FAISS boost (dynamic or static) ──────────────────────
        if memory_agent is not None:
            self._apply_dynamic_faiss(matching, row, swing, flow, htf, level_map, memory_agent)
        else:
            # Static fallback
            for s in matching:
                s.faiss_boost = faiss_confidence * 0.15
                s.final_confidence = min(1.0, s.base_confidence + s.faiss_boost)
                s.historical_win_rate = faiss_win_rate

        # Rank by final confidence
        ranked = sorted(matching, key=lambda s: s.final_confidence, reverse=True)
        best = ranked[0]

        # ── RISK TO REWARD VETO ──────────────────────────────────────────
        risk_pts = abs(trigger.entry_price - trigger.sl)
        rr_t1 = round(abs(trigger.target1 - trigger.entry_price) / max(risk_pts, 0.01), 2)
        if self.min_rr > 0 and rr_t1 < self.min_rr:
            veto_reasons.append(f"R:R too low: {rr_t1:.2f}x (need {self.min_rr:.2f}x)")
            return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons,
                            scenario_confidence=best.final_confidence)

        # ── MAX R:R VETO (filter ultra-tight SL entries) ─────────────────
        if self.max_rr > 0 and rr_t1 > self.max_rr:
            veto_reasons.append(f"R:R too high: {rr_t1:.2f}x (max {self.max_rr:.2f}x) — SL too tight for noise")
            return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons,
                            scenario_confidence=best.final_confidence)

        # ── MINIMUM CHECKS ──────────────────────────────────────────────
        if best.tf_agreement < self.min_tf_agreement:
            veto_reasons.append(f"Only {best.tf_agreement} TF agreement (need {self.min_tf_agreement})")
            return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons,
                            scenario_confidence=best.final_confidence)

        if best.final_confidence < self.min_confidence:
            veto_reasons.append(f"Scenario confidence {best.final_confidence:.3f} < {self.min_confidence}")
            return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons,
                            scenario_confidence=best.final_confidence)

        # ── HTF LEVEL OVERRIDE ──────────────────────────────────────────
        # Skip for EMA_CASCADE scenarios — cascade trades break through levels by design
        if best.scenario_type != "EMA_CASCADE":
            if trigger.signal == "LONG" and htf.htf_ceiling and htf.htf_ceiling_dist_atr < 1.0:
                veto_reasons.append(f"HTF ceiling {htf.htf_ceiling} only {htf.htf_ceiling_dist_atr:.1f} ATR away")
                return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons,
                                scenario_confidence=best.final_confidence)

            if trigger.signal == "SHORT" and htf.htf_floor and htf.htf_floor_dist_atr < 1.0:
                veto_reasons.append(f"HTF floor {htf.htf_floor} only {htf.htf_floor_dist_atr:.1f} ATR away")
                return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons,
                                scenario_confidence=best.final_confidence)

        # ── FAISS LOW WIN RATE VETO ─────────────────────────────────────
        if best.historical_analogs >= 5 and best.historical_win_rate < 0.30:
            veto_reasons.append(f"FAISS: only {best.historical_win_rate:.0%} win rate on similar setups")
            return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons,
                            scenario_confidence=best.final_confidence)

        # ── Chart pattern contradiction veto ────────────────────────────
        pattern_signal = str(self._get_val(row, "pattern_signal", "NONE"))
        pattern_completion = self._safe_float(self._get_val(row, "pattern_completion", 0))
        if pattern_signal not in ("NONE", "") and pattern_completion > 0.7:
            if pattern_signal != trigger.signal:
                pattern_name = str(self._get_val(row, "pattern_name", "UNKNOWN"))
                veto_reasons.append(
                    f"Chart pattern {pattern_name} signals {pattern_signal} "
                    f"vs trade {trigger.signal} (completion {pattern_completion:.0%})"
                )
                return self._hold(ts, close, atr, h1_adx, h1_trend, swing, veto_reasons,
                                scenario_confidence=best.final_confidence)

        # ── Micro confirmation (optional boost) ─────────────────────────
        if micro.confirms_entry:
            best.final_confidence = min(1.0, best.final_confidence + 0.05)

        # ── CONFIRMED SIGNAL ────────────────────────────────────────────

        reasons = [best.narrative] + best.supporting_evidence[:3]

        return OrchestratorV3Signal(
            signal=trigger.signal,
            time=ts,
            entry=trigger.entry_price,
            sl=trigger.sl,
            target1=trigger.target1,
            target2=trigger.target2,
            risk_pts=round(risk_pts, 2),
            rr_t1=rr_t1,
            scenario_type=best.scenario_type,
            narrative=best.narrative,
            scenario_confidence=round(best.final_confidence, 3),
            structure_confidence=round(swing.confidence, 3),
            faiss_confidence=round(best.faiss_boost, 3),
            volume_confirms=flow.volume_confirms,
            weighted_score=round(best.final_confidence, 3),
            macro_bias=swing.trade_bias,
            macro_confidence=round(swing.confidence, 3),
            momentum_confidence=round(flow.confidence, 3),
            trigger_quality=round(trigger.entry_quality, 3),
            memory_win_rate=round(best.historical_win_rate, 3),
            long_score=round(best.final_confidence if trigger.signal == "LONG" else 0, 3),
            short_score=round(best.final_confidence if trigger.signal == "SHORT" else 0, 3),
            atr_5m=round(atr, 2),
            adx_1h=round(h1_adx, 1),
            h1_trend=h1_trend,
            reasons=reasons,
            veto_reasons=[],
        )

    # ── HELPERS ──────────────────────────────────────────────────────────

    @staticmethod
    def _target_already_hit(scenario: TradingScenario, close: float) -> bool:
        """Return True if the scenario's target has already been reached by current price."""
        if scenario.target <= 0:
            return False
        if scenario.direction == "LONG" and close >= scenario.target:
            return True
        if scenario.direction == "SHORT" and close <= scenario.target:
            return True
        return False

    def _apply_dynamic_faiss(self, scenarios: List[TradingScenario],
                              row: pd.Series, swing, flow, htf, level_map,
                              memory_agent) -> None:
        """Query FAISS for each scenario and update its confidence fields in-place."""
        for s in scenarios:
            try:
                vector = memory_agent.encode_setup(
                    row, swing=swing, flow=flow, htf=htf,
                    level_map=level_map, scenario_type=s.scenario_type,
                )
                result = memory_agent.query(vector, scenario_type=s.scenario_type)

                s.historical_analogs = result.similar_setups_found
                s.historical_win_rate = result.historical_win_rate
                s.historical_avg_move_atr = result.historical_avg_pnl_atr
                s.faiss_boost = result.confidence * 0.15
                s.final_confidence = min(1.0, s.base_confidence + s.faiss_boost)
            except Exception as e:
                logger.debug(f"FAISS query failed for scenario {s.scenario_type}: {e}")
                s.faiss_boost = 0.0
                s.final_confidence = s.base_confidence
                s.historical_win_rate = 0.5

    def _hold(self, ts, close, atr, h1_adx, h1_trend, swing, veto_reasons,
              scenario_confidence=0.0):
        """Return a HOLD signal with veto reasons."""
        return OrchestratorV3Signal(
            signal="HOLD",
            time=ts,
            entry=close,
            macro_bias=swing.trade_bias,
            macro_confidence=round(swing.confidence, 3),
            scenario_confidence=round(scenario_confidence, 3),
            atr_5m=round(atr, 2),
            adx_1h=round(h1_adx, 1),
            h1_trend=h1_trend,
            veto_reasons=veto_reasons,
        )
