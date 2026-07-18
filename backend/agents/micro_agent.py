"""
micro_agent.py — Micro Agent: 1m confirmation for entry/exit.

Fine-tunes entry timing and detects quick reversals.
"""
import logging
import numpy as np
import pandas as pd
from .base import BaseAgent
from .base_v3 import MicroConfirmation, SwingScenario

logger = logging.getLogger(__name__)


class MicroAgent(BaseAgent):
    """1m confirmation: checks if 1m structure supports the trade."""

    def evaluate(self, row: pd.Series, swing: SwingScenario,
                 direction: str = "HOLD") -> MicroConfirmation:
        reasons = []

        m1_st = self._safe_int(self._get_val(row, "m1_st", 0))
        m1_srsi_k = self._safe_float(self._get_val(row, "m1_srsi_k", 50))
        m1_srsi_d = self._safe_float(self._get_val(row, "m1_srsi_d", 50))

        # 1m EMA cross (if available, otherwise derive from supertrend)
        m1_ema_cross = m1_st  # rough proxy

        # ── Entry confirmation ──────────────────────────────────────────
        confirms_entry = False
        if direction == "LONG":
            if m1_st == 1:
                confirms_entry = True
                reasons.append("1m SuperTrend bullish — confirms LONG")
            if m1_srsi_k > m1_srsi_d and m1_srsi_k < 50:
                confirms_entry = True
                reasons.append("1m StochRSI curling up from low")
        elif direction == "SHORT":
            if m1_st == -1:
                confirms_entry = True
                reasons.append("1m SuperTrend bearish — confirms SHORT")
            if m1_srsi_k < m1_srsi_d and m1_srsi_k > 50:
                confirms_entry = True
                reasons.append("1m StochRSI turning down from high")

        # ── Exit / reversal detection ───────────────────────────────────
        confirms_exit = False
        reversal_detected = False
        reversal_strength = 0.0

        if direction == "LONG" and m1_st == -1:
            confirms_exit = True
            reversal_strength += 0.5
            reasons.append("1m SuperTrend flipped bearish — exit signal")
        elif direction == "SHORT" and m1_st == 1:
            confirms_exit = True
            reversal_strength += 0.5
            reasons.append("1m SuperTrend flipped bullish — exit signal")

        # StochRSI extreme + turning
        if direction == "LONG" and m1_srsi_k > 85 and m1_srsi_k < m1_srsi_d:
            reversal_strength += 0.3
            reasons.append("1m StochRSI overbought and turning")
        elif direction == "SHORT" and m1_srsi_k < 15 and m1_srsi_k > m1_srsi_d:
            reversal_strength += 0.3
            reasons.append("1m StochRSI oversold and curling")

        reversal_detected = reversal_strength >= 0.5

        # 1m SRSI state
        if m1_srsi_k < 20 and m1_srsi_k > m1_srsi_d:
            m1_srsi_state = "OVERSOLD_CURLING"
        elif m1_srsi_k > 80 and m1_srsi_k < m1_srsi_d:
            m1_srsi_state = "OVERBOUGHT_TURNING"
        else:
            m1_srsi_state = "NEUTRAL"

        return MicroConfirmation(
            confirms_entry=confirms_entry,
            confirms_exit=confirms_exit,
            m1_supertrend=m1_st,
            m1_ema_cross=m1_ema_cross,
            m1_srsi_state=m1_srsi_state,
            reversal_detected=reversal_detected,
            reversal_strength=round(min(1.0, reversal_strength), 3),
            reasons=reasons,
        )
