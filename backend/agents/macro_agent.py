"""
macro_agent.py — MacroAgent: 1H Swing Trend bias + Daily level analysis.

Establishes the non-negotiable directional bias from the 1-Hour Swing timeframe.
Daily and Weekly levels are used as support/resistance overlays and exhaustion checks.
"""
import numpy as np
import pandas as pd
from .base import BaseAgent, MacroState


class MacroAgent(BaseAgent):
    """
    Evaluates the trend from the 1-Hour Swing timeframe to establish directional bias.
    Uses Daily data for support/resistance overlays and exhaustion filters.
    """

    def evaluate(self, row: pd.Series) -> MacroState:
        """
        Evaluate macro state from the latest merged row.
        Row contains h1_ (1-Hour) and d1_ (Daily) prefixed columns.
        """
        reasons = []

        # ── 1-HOUR SWING TREND BIAS ─────────────────────────────────────
        h1_st = self._safe_int(self._get_val(row, "h1_supertrend_dir", 0))
        h1_ema = self._safe_int(self._get_val(row, "h1_ema_cross", 0))
        h1_macd_hist = self._safe_float(self._get_val(row, "h1_macd_hist", 0))
        h1_rsi = self._safe_float(self._get_val(row, "h1_rsi", 50))
        h1_dmp = self._safe_float(self._get_val(row, "h1_dmp", 0))
        h1_dmn = self._safe_float(self._get_val(row, "h1_dmn", 0))
        h1_adx = self._safe_float(self._get_val(row, "h1_adx", 0))

        bull_pts = 0.0
        bear_pts = 0.0

        if h1_st == 1:
            bull_pts += 0.35
            reasons.append("1H SuperTrend Bullish")
        elif h1_st == -1:
            bear_pts += 0.35
            reasons.append("1H SuperTrend Bearish")

        if h1_ema == 1:
            bull_pts += 0.20
            reasons.append("1H EMA7 > EMA21")
        elif h1_ema == -1:
            bear_pts += 0.20
            reasons.append("1H EMA7 < EMA21")

        if h1_macd_hist > 0:
            bull_pts += 0.15
        elif h1_macd_hist < 0:
            bear_pts += 0.15

        if h1_rsi > 50:
            bull_pts += 0.10
        else:
            bear_pts += 0.10

        if h1_dmp > h1_dmn:
            bull_pts += 0.10
        else:
            bear_pts += 0.10

        if h1_adx >= 25:
            if bull_pts > bear_pts:
                bull_pts += 0.10
                reasons.append(f"1H ADX Strong ({h1_adx:.0f})")
            else:
                bear_pts += 0.10
                reasons.append(f"1H ADX Strong ({h1_adx:.0f})")

        spread = abs(bull_pts - bear_pts)
        if spread < 0.08:
            bias = "NEUTRAL"
            reasons.append("1H Swing Trend neutral")
        elif bull_pts > bear_pts:
            bias = "LONG"
        else:
            bias = "SHORT"

        confidence = min(1.0, spread * 2.5)

        # ── DAILY & WEEKLY EXHAUSTION ──────────────────────────────────
        d1_rsi = self._safe_float(self._get_val(row, "d1_rsi", 50))
        d1_st_age = self._safe_int(self._get_val(row, "d1_st_bars_since_flip", 0))
        d1_macd_slope = self._safe_float(self._get_val(row, "d1_macd_hist_slope_3bar", 0))

        exhaustion = 0.0
        # Long exhaustion: RSI is overbought daily or daily SuperTrend is mature
        if bias == "LONG":
            if d1_rsi > 70:
                exhaustion += 0.35
                reasons.append("Daily RSI Overbought (>70) — high exhaustion risk")
            if d1_st_age > 40:
                exhaustion += 0.20
                reasons.append("Daily Trend Mature (>40 bars) — exhaustion warning")
            if d1_macd_slope < 0:
                exhaustion += 0.15

        # Short exhaustion: RSI is oversold daily
        elif bias == "SHORT":
            if d1_rsi < 30:
                exhaustion += 0.35
                reasons.append("Daily RSI Oversold (<30) — exhaustion risk")
            if d1_st_age > 40:
                exhaustion += 0.20
                reasons.append("Daily Trend Mature (>40 bars) — exhaustion warning")
            if d1_macd_slope > 0:
                exhaustion += 0.15

        exhaustion = min(1.0, exhaustion)

        # ── WEEKLY DIRECTION ALIGNMENT ─────────────────────────────────
        # Weekly is the outermost layer: it can boost or trim confidence.
        # It does NOT override the 1H bias, but it modulates conviction.
        w_st       = self._safe_int(self._get_val(row, "w_supertrend_dir", 0))
        w_ema_cross = self._safe_int(self._get_val(row, "w_ema_cross", 0))
        w_macd_hist = self._safe_float(self._get_val(row, "w_macd_hist", 0))

        if bias != "NEUTRAL":
            bias_dir = 1 if bias == "LONG" else -1

            if w_st == bias_dir:
                confidence = min(1.0, confidence + 0.15)
                reasons.append(f"Weekly SuperTrend aligns with {bias} — strong tailwind")
            elif w_st == -bias_dir and w_st != 0:
                confidence = max(0.0, confidence - 0.20)
                reasons.append(f"Weekly SuperTrend OPPOSES {bias} — counter-trend trade")

            if w_ema_cross == bias_dir:
                confidence = min(1.0, confidence + 0.08)
                reasons.append("Weekly EMA cross aligns")
            elif w_ema_cross == -bias_dir and w_ema_cross != 0:
                confidence = max(0.0, confidence - 0.10)

            if (bias_dir == 1 and w_macd_hist > 0) or (bias_dir == -1 and w_macd_hist < 0):
                confidence = min(1.0, confidence + 0.05)
            elif w_macd_hist != 0:
                confidence = max(0.0, confidence - 0.05)

        # ── KEY LEVELS & ZONES ──────────────────────────────────────────
        key_levels = {}
        for lvl in ["d1_fib_618", "d1_fib_500", "d1_fib_382", "d1_sup1", "d1_res1"]:
            val = self._safe_float(self._get_val(row, lvl, 0))
            if val > 0:
                key_levels[lvl] = val

        d1_fib_zone = self._safe_int(self._get_val(row, "d1_fib_zone", 2))
        fib_zone_names = {0: "BELOW_786", 1: "BELOW_618", 2: "GOLDEN_ZONE",
                          3: "ABOVE_382", 4: "ABOVE_236"}
        daily_fib_zone = fib_zone_names.get(d1_fib_zone, "OTHER")

        return MacroState(
            bias=bias,
            confidence=round(confidence, 3),
            trend_age_days=max(1, d1_st_age),
            trend_exhaustion=round(exhaustion, 3),
            weekly_structure=str(self._get_val(row, "w_supertrend_dir", 0)),
            weekly_macd_phase="N/A",
            daily_ema_gap_atr=round(self._safe_float(self._get_val(row, "d1_ema_gap_atr", 0)), 2),
            daily_adx_regime="N/A",
            daily_adx_hook=False,
            daily_fib_zone=daily_fib_zone,
            daily_rsi_regime="ABOVE_50" if d1_rsi > 50 else "BELOW_50",
            key_levels=key_levels,
            reasons=reasons,
        )
