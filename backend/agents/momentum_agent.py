"""
momentum_agent.py — MomentumAgent: 15m + 5m oscillator trajectories.

Reads oscillator TRAJECTORIES, not just current values.
Detects momentum shifts before they happen.
"""
import numpy as np
import pandas as pd
from .base import BaseAgent, MomentumState, MacroState


class MomentumAgent(BaseAgent):
    """
    Evaluates momentum from 15m + 5m StochRSI, MACD, Stochastic.
    Produces: oscillator phases, trajectories, divergences, alignments.
    """

    def evaluate(self, row: pd.Series, macro: MacroState) -> MomentumState:
        """Evaluate momentum from the latest merged row."""
        reasons = []
        target_dir = 1 if macro.bias == "LONG" else -1

        # ── 15m StochRSI ────────────────────────────────────────────────
        srsi_15m_phase = self._safe_int(self._get_val(row, "m15_srsi_phase", 0))
        srsi_15m_traj = self._safe_float(self._get_val(row, "m15_srsi_trajectory_5bar", 0))
        srsi_15m_kd = self._safe_int(self._get_val(row, "m15_srsi_kd_cross_dir", 0))
        srsi_15m_k = self._safe_float(self._get_val(row, "m15_stochrsi_k", 50))
        srsi_15m_div = self._safe_int(self._get_val(row, "m15_srsi_divergence", 0))

        if macro.bias == "LONG":
            if srsi_15m_phase == 3:
                reasons.append("15m StochRSI oversold + curling up (ideal for long)")
            elif srsi_15m_phase == 2:
                reasons.append("15m StochRSI oversold (waiting for curl)")
            if srsi_15m_kd == 1:
                reasons.append("15m StochRSI bullish K/D cross")
        else:
            if srsi_15m_phase == -3:
                reasons.append("15m StochRSI overbought + turning down (ideal for short)")
            elif srsi_15m_phase == -2:
                reasons.append("15m StochRSI overbought (waiting for turn)")
            if srsi_15m_kd == -1:
                reasons.append("15m StochRSI bearish K/D cross")

        # ── 5m StochRSI ─────────────────────────────────────────────────
        srsi_5m_phase = self._safe_int(self._get_val(row, "srsi_phase", 0))
        srsi_5m_traj = self._safe_float(self._get_val(row, "srsi_trajectory_5bar", 0))
        srsi_5m_kd = self._safe_int(self._get_val(row, "srsi_kd_cross_dir", 0))
        srsi_5m_k = self._safe_float(self._get_val(row, "stochrsi_k", 50))

        if macro.bias == "LONG" and srsi_5m_phase == 3:
            reasons.append("5m StochRSI oversold + curling up")
        elif macro.bias == "SHORT" and srsi_5m_phase == -3:
            reasons.append("5m StochRSI overbought + turning down")

        # ── 15m MACD ────────────────────────────────────────────────────
        macd_15m_hist_slope = self._safe_float(self._get_val(row, "m15_macd_hist_slope_3bar", 0))
        macd_15m_hist = self._safe_float(self._get_val(row, "m15_macd_hist", 0))
        macd_15m_div = self._safe_int(self._get_val(row, "m15_macd_divergence", 0))

        # Phase: is MACD expanding or contracting?
        if macd_15m_hist > 0 and macd_15m_hist_slope > 0:
            macd_15m_phase = "EXPANDING_BULL"
            if macro.bias == "LONG":
                reasons.append("15m MACD expanding bullish")
        elif macd_15m_hist > 0 and macd_15m_hist_slope <= 0:
            macd_15m_phase = "CONTRACTING_BULL"
            if macro.bias == "LONG":
                reasons.append("15m MACD contracting — momentum fading")
        elif macd_15m_hist < 0 and macd_15m_hist_slope < 0:
            macd_15m_phase = "EXPANDING_BEAR"
            if macro.bias == "SHORT":
                reasons.append("15m MACD expanding bearish")
        elif macd_15m_hist < 0 and macd_15m_hist_slope >= 0:
            macd_15m_phase = "CONTRACTING_BEAR"
            if macro.bias == "SHORT":
                reasons.append("15m MACD contracting — momentum fading")
        else:
            macd_15m_phase = "NEUTRAL"

        # ── 5m MACD ─────────────────────────────────────────────────────
        macd_5m_hist_slope = self._safe_float(self._get_val(row, "macd_hist_slope_3bar", 0))

        # ── 5m Stochastic ───────────────────────────────────────────────
        stoch_5m_phase = self._safe_int(self._get_val(row, "stoch_phase", 0))
        stoch_srsi_agree = self._safe_int(self._get_val(row, "stoch_srsi_agreement", 0))

        if macro.bias == "LONG" and stoch_srsi_agree == 1:
            reasons.append("5m Stoch + StochRSI both oversold (double confirmation)")
        elif macro.bias == "SHORT" and stoch_srsi_agree == -1:
            reasons.append("5m Stoch + StochRSI both overbought (double confirmation)")

        # ── MOMENTUM ALIGNMENT (across timeframes) ──────────────────────
        tf_mom = self._safe_int(self._get_val(row, "tf_momentum_alignment", 0))
        # tf_momentum_alignment: positive = more TFs bullish, negative = bearish
        if macro.bias == "LONG" and tf_mom >= 2:
            reasons.append(f"Momentum aligned across {tf_mom} timeframes (bullish)")
        elif macro.bias == "SHORT" and tf_mom <= -2:
            reasons.append(f"Momentum aligned across {abs(tf_mom)} timeframes (bearish)")

        # ── DIVERGENCE ──────────────────────────────────────────────────
        divergence = "NONE"
        if srsi_15m_div == 1 or macd_15m_div == 1:
            divergence = "BULLISH_DIV"
            reasons.append("Bullish divergence detected on 15m")
        elif srsi_15m_div == -1 or macd_15m_div == -1:
            divergence = "BEARISH_DIV"
            reasons.append("Bearish divergence detected on 15m")

        # ── MOMENTUM SHIFT IMMINENT ─────────────────────────────────────
        # Multiple oscillators about to cross simultaneously
        shift_imminent = False
        if macro.bias == "LONG":
            # All oscillators in oversold and starting to curl up
            os_count = sum([
                1 if srsi_15m_phase >= 2 else 0,
                1 if srsi_5m_phase >= 2 else 0,
                1 if stoch_5m_phase >= 2 else 0,
            ])
            curl_count = sum([
                1 if srsi_15m_traj > 0.5 else 0,
                1 if srsi_5m_traj > 0.5 else 0,
            ])
            if os_count >= 2 and curl_count >= 1:
                shift_imminent = True
                reasons.append("Momentum shift imminent — multiple oscillators curling from oversold")
        else:
            ob_count = sum([
                1 if srsi_15m_phase <= -2 else 0,
                1 if srsi_5m_phase <= -2 else 0,
                1 if stoch_5m_phase <= -2 else 0,
            ])
            curl_count = sum([
                1 if srsi_15m_traj < -0.5 else 0,
                1 if srsi_5m_traj < -0.5 else 0,
            ])
            if ob_count >= 2 and curl_count >= 1:
                shift_imminent = True
                reasons.append("Momentum shift imminent — multiple oscillators turning from overbought")

        # ── CONFIDENCE ──────────────────────────────────────────────────
        confidence = 0.0

        # StochRSI phase contribution
        if macro.bias == "LONG":
            if srsi_15m_phase == 3: confidence += 0.25
            elif srsi_15m_phase == 2: confidence += 0.15
            if srsi_5m_phase == 3: confidence += 0.15
            elif srsi_5m_phase == 2: confidence += 0.08
        else:
            if srsi_15m_phase == -3: confidence += 0.25
            elif srsi_15m_phase == -2: confidence += 0.15
            if srsi_5m_phase == -3: confidence += 0.15
            elif srsi_5m_phase == -2: confidence += 0.08

        # MACD contribution
        if macro.bias == "LONG" and macd_15m_phase == "EXPANDING_BULL":
            confidence += 0.15
        elif macro.bias == "SHORT" and macd_15m_phase == "EXPANDING_BEAR":
            confidence += 0.15

        # Stoch agreement
        if (macro.bias == "LONG" and stoch_srsi_agree == 1) or \
           (macro.bias == "SHORT" and stoch_srsi_agree == -1):
            confidence += 0.10

        # Divergence bonus
        if (macro.bias == "LONG" and divergence == "BULLISH_DIV") or \
           (macro.bias == "SHORT" and divergence == "BEARISH_DIV"):
            confidence += 0.15

        # Alignment bonus
        if abs(tf_mom) >= 3:
            confidence += 0.10

        # Shift imminent
        if shift_imminent:
            confidence += 0.10

        confidence = min(1.0, confidence)

        return MomentumState(
            srsi_15m_phase=srsi_15m_phase,
            srsi_5m_phase=srsi_5m_phase,
            srsi_trajectory_15m=round(srsi_15m_traj, 3),
            srsi_trajectory_5m=round(srsi_5m_traj, 3),
            srsi_kd_cross_15m=srsi_15m_kd,
            srsi_kd_cross_5m=srsi_5m_kd,
            macd_15m_hist_slope=round(macd_15m_hist_slope, 3),
            macd_5m_hist_slope=round(macd_5m_hist_slope, 3),
            macd_15m_phase=macd_15m_phase,
            stoch_5m_phase=stoch_5m_phase,
            stoch_srsi_agreement=stoch_srsi_agree,
            momentum_alignment=tf_mom,
            divergence_detected=divergence,
            momentum_shift_imminent=shift_imminent,
            confidence=round(confidence, 3),
            reasons=reasons,
        )
