"""
flow_agent.py — Flow Agent: 15m momentum trajectory + volume + oscillator context.

Key v3 fix: oscillator_context is TREND-AWARE.
"Oversold in a downtrend" = strong momentum, NOT a buy signal.
"Oversold in an uptrend" = pullback, potential bounce entry.
"""
import logging
import numpy as np
import pandas as pd
from .base import BaseAgent
from .base_v3 import FlowScenario, SwingScenario

logger = logging.getLogger(__name__)


class FlowAgent(BaseAgent):
    """15m momentum trajectory, volume, and trend-aware oscillator context."""

    def evaluate(self, row: pd.Series, swing: SwingScenario) -> FlowScenario:
        reasons = []

        # ── 15m indicators ──────────────────────────────────────────────
        m15_macd_hist = self._safe_float(self._get_val(row, "m15_macd_hist", 0))
        m15_macd_slope = self._safe_float(self._get_val(row, "m15_macd_hist_slope_3bar", 0))
        m15_srsi_phase = self._safe_int(self._get_val(row, "m15_srsi_phase", 0))
        m15_srsi_k = self._safe_float(self._get_val(row, "m15_stochrsi_k", 50))
        m15_srsi_traj = self._safe_float(self._get_val(row, "m15_srsi_trajectory_5bar", 0))
        m15_st_dir = self._safe_int(self._get_val(row, "m15_supertrend_dir", 0))

        # 5m for cross-TF confirmation
        m5_macd_hist = self._safe_float(self._get_val(row, "macd_hist", 0))
        m5_macd_slope = self._safe_float(self._get_val(row, "macd_hist_slope_3bar", 0))
        m5_srsi_phase = self._safe_int(self._get_val(row, "srsi_phase", 0))
        m5_st_dir = self._safe_int(self._get_val(row, "supertrend_dir", 0))

        # Volume
        vol_relative = self._safe_float(self._get_val(row, "vol_relative", 1.0))
        vol_trend = self._safe_float(self._get_val(row, "vol_trend_5bar", 0))
        vol_divergence = self._safe_int(self._get_val(row, "vol_price_divergence", 0))

        # ── MACD phase (15m) ────────────────────────────────────────────
        if m15_macd_hist > 0 and m15_macd_slope > 0:
            macd_phase = "EXPANDING_BULL"
        elif m15_macd_hist > 0 and m15_macd_slope <= 0:
            macd_phase = "CONTRACTING_BULL"
        elif m15_macd_hist < 0 and m15_macd_slope < 0:
            macd_phase = "EXPANDING_BEAR"
        elif m15_macd_hist < 0 and m15_macd_slope >= 0:
            macd_phase = "CONTRACTING_BEAR"
        else:
            macd_phase = "NEUTRAL"

        # ── Trend state (15m) ───────────────────────────────────────────
        # Combine 15m SuperTrend + MACD phase to determine trend state
        if m15_st_dir == -1 and macd_phase == "EXPANDING_BEAR":
            trend_state = "IMPULSE_DOWN"
            reasons.append("15m impulse DOWN (ST bearish + MACD expanding)")
        elif m15_st_dir == 1 and macd_phase == "EXPANDING_BULL":
            trend_state = "IMPULSE_UP"
            reasons.append("15m impulse UP (ST bullish + MACD expanding)")
        elif macd_phase.startswith("CONTRACTING"):
            # Momentum fading — could be pullback or reversal
            if swing.trade_bias == "LONG" and m15_macd_hist < 0:
                trend_state = "PULLBACK"
                reasons.append("15m pullback in uptrend (MACD contracting)")
            elif swing.trade_bias == "SHORT" and m15_macd_hist > 0:
                trend_state = "PULLBACK"
                reasons.append("15m pullback in downtrend (MACD contracting)")
            else:
                trend_state = "CONSOLIDATION"
                reasons.append("15m consolidation")
        else:
            trend_state = "CONSOLIDATION"

        # Check for reversal forming
        if trend_state == "CONSOLIDATION":
            # Multiple oscillators changing direction simultaneously
            srsi_turning = (m15_srsi_phase >= 2 and m15_srsi_traj > 0.5) or \
                          (m15_srsi_phase <= -2 and m15_srsi_traj < -0.5)
            macd_turning = macd_phase.startswith("CONTRACTING")
            if srsi_turning and macd_turning:
                trend_state = "REVERSAL_FORMING"
                reasons.append("15m reversal forming — oscillators + MACD turning")

        # ── Momentum trajectory ─────────────────────────────────────────
        # How is momentum changing? (not where it is)
        abs_slope = abs(m15_macd_slope)
        if macd_phase.startswith("EXPANDING") and abs_slope > 0.5:
            momentum_trajectory = "ACCELERATING"
        elif macd_phase.startswith("EXPANDING"):
            momentum_trajectory = "STEADY"
        elif macd_phase.startswith("CONTRACTING") and abs_slope > 0.5:
            momentum_trajectory = "REVERSING"
        elif macd_phase.startswith("CONTRACTING"):
            momentum_trajectory = "DECELERATING"
        else:
            momentum_trajectory = "STEADY"

        reasons.append(f"15m momentum: {momentum_trajectory}")

        # ── Volume state ────────────────────────────────────────────────
        if vol_relative > 2.5:
            volume_state = "CLIMAX"
            reasons.append(f"Volume CLIMAX ({vol_relative:.1f}x average)")
        elif vol_relative > 1.5 and vol_trend > 0.15:
            volume_state = "EXPANDING"
            reasons.append("Volume expanding")
        elif vol_relative < 0.5:
            volume_state = "DRYING"
            reasons.append("Volume drying up")
        elif vol_divergence != 0:
            volume_state = "DIVERGENT"
            if vol_divergence == -1:
                reasons.append("Volume divergent — price up but volume down")
            elif vol_divergence == 1:
                reasons.append("Volume divergent — price down but volume down")
        else:
            volume_state = "NORMAL"

        # Volume confirms direction?
        volume_confirms = False
        if swing.trade_bias == "SHORT":
            volume_confirms = (volume_state in ("EXPANDING", "CLIMAX") and vol_divergence <= 0) or \
                            (vol_divergence == -1)  # price up vol down = weak rally
        elif swing.trade_bias == "LONG":
            volume_confirms = (volume_state in ("EXPANDING", "CLIMAX") and vol_divergence >= 0) or \
                            (vol_divergence == 1)  # price down vol down = weak selling

        # ── Oscillator context (TREND-AWARE — the key v3 fix) ──────────
        # This distinguishes "oversold in downtrend" from "oversold in uptrend"
        is_oversold = m15_srsi_phase >= 2 or m15_srsi_k < 20
        is_overbought = m15_srsi_phase <= -2 or m15_srsi_k > 80

        if swing.trade_bias == "SHORT" or swing.ema_cascade in ("BETWEEN_7_21", "BELOW_21_ABOVE_ST", "BELOW_ST", "BELOW_ALL"):
            # We're in a downtrend context
            if is_oversold:
                oscillator_context = "OVERSOLD_IN_DOWNTREND"
                reasons.append("Oscillators oversold in DOWNTREND — momentum, not reversal")
            elif is_overbought:
                oscillator_context = "OVERBOUGHT_IN_DOWNTREND"
                reasons.append("Oscillators overbought in DOWNTREND — SHORT opportunity")
            else:
                oscillator_context = "NEUTRAL"
        elif swing.trade_bias == "LONG" or swing.ema_cascade in ("ABOVE_ALL", "BETWEEN_21_7", "ABOVE_21_BELOW_ST", "ABOVE_ST"):
            # We're in an uptrend context
            if is_oversold:
                oscillator_context = "OVERSOLD_IN_UPTREND"
                reasons.append("Oscillators oversold in UPTREND — pullback bounce opportunity")
            elif is_overbought:
                oscillator_context = "OVERBOUGHT_IN_UPTREND"
                reasons.append("Oscillators overbought in UPTREND — momentum, not reversal")
            else:
                oscillator_context = "NEUTRAL"
        else:
            oscillator_context = "NEUTRAL"

        # ── Derived direction support ───────────────────────────────────
        supports = "NEUTRAL"
        if swing.trade_bias == "LONG":
            if trend_state in ("IMPULSE_UP", "PULLBACK") and oscillator_context != "OVERBOUGHT_IN_DOWNTREND":
                supports = "LONG"
            elif trend_state == "REVERSAL_FORMING" and m15_srsi_traj > 0:
                supports = "LONG"
        elif swing.trade_bias == "SHORT":
            if trend_state in ("IMPULSE_DOWN", "PULLBACK") and oscillator_context != "OVERSOLD_IN_UPTREND":
                supports = "SHORT"
            elif trend_state == "REVERSAL_FORMING" and m15_srsi_traj < 0:
                supports = "SHORT"

        # ── Confidence ──────────────────────────────────────────────────
        confidence = 0.3  # base
        if supports == swing.trade_bias:
            confidence += 0.25
        if momentum_trajectory == "ACCELERATING":
            confidence += 0.15
        elif momentum_trajectory == "REVERSING":
            confidence -= 0.15
        if volume_confirms:
            confidence += 0.15
        if trend_state.startswith("IMPULSE") and supports != "NEUTRAL":
            confidence += 0.15

        confidence = max(0.0, min(1.0, confidence))

        return FlowScenario(
            trend_state=trend_state,
            momentum_trajectory=momentum_trajectory,
            macd_phase=macd_phase,
            volume_confirms=volume_confirms,
            volume_state=volume_state,
            oscillator_context=oscillator_context,
            supports_direction=supports,
            confidence=round(confidence, 3),
            reasons=reasons,
        )
