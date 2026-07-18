"""
macro_agent.py — MacroAgent: Weekly + Daily timeframe analysis.

Establishes the non-negotiable directional bias.
No trade is taken against the macro bias.
"""
import numpy as np
import pandas as pd
from .base import BaseAgent, MacroState


class MacroAgent(BaseAgent):
    """
    Evaluates the macro trend from Weekly + Daily data.
    Produces: directional bias, trend strength, exhaustion risk, key levels.
    """

    def evaluate(self, row: pd.Series) -> MacroState:
        """
        Evaluate macro state from the latest merged row.
        Row contains d1_ and w_ prefixed columns.
        """
        reasons = []

        # ── DAILY ANALYSIS ──────────────────────────────────────────────
        d1_st = self._safe_int(self._get_val(row, "d1_supertrend_dir", 0))
        d1_ema = self._safe_int(self._get_val(row, "d1_ema_cross", 0))
        d1_adx = self._safe_float(self._get_val(row, "d1_adx", 0))
        d1_dmp = self._safe_float(self._get_val(row, "d1_dmp", 0))
        d1_dmn = self._safe_float(self._get_val(row, "d1_dmn", 0))
        d1_rsi = self._safe_float(self._get_val(row, "d1_rsi", 50))
        d1_macd_hist = self._safe_float(self._get_val(row, "d1_macd_hist", 0))

        # Behavioral features (from behavioral.py)
        d1_st_age = self._safe_int(self._get_val(row, "d1_st_bars_since_flip", 0))
        d1_adx_hook = self._safe_float(self._get_val(row, "d1_adx_hook", 0))
        d1_adx_slope = self._safe_float(self._get_val(row, "d1_adx_slope_5bar", 0))
        d1_ema_gap = self._safe_float(self._get_val(row, "d1_ema_gap_atr", 0))
        d1_macd_slope = self._safe_float(self._get_val(row, "d1_macd_hist_slope_3bar", 0))

        # ── WEEKLY ANALYSIS ─────────────────────────────────────────────
        w_st = self._safe_int(self._get_val(row, "w_supertrend_dir", 0))
        w_ema = self._safe_int(self._get_val(row, "w_ema_cross", 0))
        w_macd_hist = self._safe_float(self._get_val(row, "w_macd_hist", 0))
        w_adx = self._safe_float(self._get_val(row, "w_adx", 0))

        # ── DIRECTIONAL BIAS ────────────────────────────────────────────
        # Daily confirmations
        daily_bull = 0.0
        daily_bear = 0.0

        if d1_st == 1:
            daily_bull += 0.25
            reasons.append("Daily SuperTrend bullish")
        elif d1_st == -1:
            daily_bear += 0.25
            reasons.append("Daily SuperTrend bearish")

        if d1_ema == 1:
            daily_bull += 0.15
            reasons.append("Daily EMA7 > EMA21")
        elif d1_ema == -1:
            daily_bear += 0.15
            reasons.append("Daily EMA7 < EMA21")

        if d1_dmp > d1_dmn:
            daily_bull += 0.10
        else:
            daily_bear += 0.10

        if d1_adx >= 25:
            # Strong trend — amplify the direction
            if daily_bull > daily_bear:
                daily_bull += 0.10
                reasons.append(f"Daily ADX strong ({d1_adx:.0f})")
            else:
                daily_bear += 0.10
                reasons.append(f"Daily ADX strong ({d1_adx:.0f})")

        if d1_macd_hist > 0:
            daily_bull += 0.10
        else:
            daily_bear += 0.10

        if d1_rsi > 50:
            daily_bull += 0.05
        else:
            daily_bear += 0.05

        # Weekly confirmations (heavier weight)
        weekly_bull = 0.0
        weekly_bear = 0.0

        if w_st == 1:
            weekly_bull += 0.30
            reasons.append("Weekly SuperTrend bullish")
        elif w_st == -1:
            weekly_bear += 0.30
            reasons.append("Weekly SuperTrend bearish")

        if w_ema == 1:
            weekly_bull += 0.15
        elif w_ema == -1:
            weekly_bear += 0.15

        if w_macd_hist > 0:
            weekly_bull += 0.15
            if w_macd_hist > abs(self._safe_float(self._get_val(row, "w_macd_hist_slope_3bar", 0))):
                reasons.append("Weekly MACD expanding")
        else:
            weekly_bear += 0.15

        # Combined score
        total_bull = daily_bull * 0.45 + weekly_bull * 0.55
        total_bear = daily_bear * 0.45 + weekly_bear * 0.55

        # Determine bias
        spread = abs(total_bull - total_bear)
        if spread < 0.08:
            bias = "NEUTRAL"
            reasons.append("Macro bias unclear — weekly/daily conflicting")
        elif total_bull > total_bear:
            bias = "LONG"
        else:
            bias = "SHORT"

        confidence = min(1.0, spread * 2.5)

        # ── TREND AGE & EXHAUSTION ──────────────────────────────────────
        # Daily SuperTrend age in trading days
        trend_age_days = max(1, d1_st_age)

        # Exhaustion: mature trends (>30 days) with contracting MACD
        exhaustion = 0.0
        if trend_age_days > 30:
            exhaustion += 0.3
            reasons.append(f"Trend {trend_age_days} days old — watch for exhaustion")
        if trend_age_days > 50:
            exhaustion += 0.2
        if d1_macd_slope < 0 and bias == "LONG":
            exhaustion += 0.2
            reasons.append("Daily MACD histogram contracting")
        elif d1_macd_slope > 0 and bias == "SHORT":
            exhaustion += 0.2
            reasons.append("Daily MACD histogram contracting")
        exhaustion = min(1.0, exhaustion)

        # ── ADX REGIME ──────────────────────────────────────────────────
        if d1_adx >= 30:
            adx_regime = "STRONG"
        elif d1_adx >= 20:
            adx_regime = "TRENDING"
        elif d1_adx >= 15:
            adx_regime = "WEAK"
        else:
            adx_regime = "RANGING"

        adx_hook = d1_adx_hook > 0.5
        if adx_hook:
            reasons.append("Daily ADX hook — new trend forming")
            confidence = min(1.0, confidence + 0.15)

        # ── WEEKLY STRUCTURE ────────────────────────────────────────────
        if w_st == 1 and w_ema == 1:
            weekly_structure = "UPTREND"
        elif w_st == -1 and w_ema == -1:
            weekly_structure = "DOWNTREND"
        else:
            weekly_structure = "RANGE"

        # ── WEEKLY MACD PHASE ───────────────────────────────────────────
        w_macd_slope = self._safe_float(self._get_val(row, "w_macd_hist_slope_3bar", 0))
        if w_macd_hist > 0 and w_macd_slope > 0:
            weekly_macd_phase = "EXPANDING"
        elif w_macd_hist > 0 and w_macd_slope <= 0:
            weekly_macd_phase = "CONTRACTING"
            if bias == "LONG":
                reasons.append("Weekly MACD contracting — caution")
        elif w_macd_hist < 0 and w_macd_slope < 0:
            weekly_macd_phase = "EXPANDING"
        elif w_macd_hist < 0 and w_macd_slope >= 0:
            weekly_macd_phase = "CONTRACTING"
            if bias == "SHORT":
                reasons.append("Weekly MACD contracting — caution")
        else:
            weekly_macd_phase = "NEUTRAL"

        # ── FIBONACCI ZONE ──────────────────────────────────────────────
        d1_fib_zone = self._safe_int(self._get_val(row, "d1_fib_zone", 2))
        fib_zone_names = {0: "BELOW_786", 1: "BELOW_618", 2: "GOLDEN_ZONE",
                          3: "ABOVE_382", 4: "ABOVE_236"}
        daily_fib_zone = fib_zone_names.get(d1_fib_zone, "OTHER")

        # ── RSI REGIME ──────────────────────────────────────────────────
        daily_rsi_regime = "ABOVE_50" if d1_rsi > 50 else "BELOW_50"

        # ── KEY LEVELS ──────────────────────────────────────────────────
        key_levels = {}
        for lvl in ["d1_fib_618", "d1_fib_500", "d1_fib_382", "d1_sup1", "d1_res1"]:
            val = self._safe_float(self._get_val(row, lvl, 0))
            if val > 0:
                key_levels[lvl] = val

        return MacroState(
            bias=bias,
            confidence=round(confidence, 3),
            trend_age_days=trend_age_days,
            trend_exhaustion=round(exhaustion, 3),
            weekly_structure=weekly_structure,
            weekly_macd_phase=weekly_macd_phase,
            daily_ema_gap_atr=round(d1_ema_gap, 2),
            daily_adx_regime=adx_regime,
            daily_adx_hook=adx_hook,
            daily_fib_zone=daily_fib_zone,
            daily_rsi_regime=daily_rsi_regime,
            key_levels=key_levels,
            reasons=reasons,
        )
