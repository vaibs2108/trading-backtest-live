"""
htf_agent.py — HTF Agent: Weekly + Daily big-picture bias and level identification.

HTF levels are trade DRIVERS (targets / rejection zones), not just vetoes.
"Price approaching weekly fib 61.8% resistance" = target for shorts, no longs until broken.
"""
import logging
import numpy as np
import pandas as pd
from .base import BaseAgent
from .base_v3 import HTFScenario, PriceLevel, LevelMap

logger = logging.getLogger(__name__)


class HTFAgent(BaseAgent):
    """Weekly + Daily structure: bias, journey, ceiling/floor levels."""

    def evaluate(self, row: pd.Series, level_map: LevelMap) -> HTFScenario:
        reasons = []

        # ── Weekly bias ─────────────────────────────────────────────────
        w_st = self._safe_int(self._get_val(row, "w_supertrend_dir", 0))
        w_ema = self._safe_int(self._get_val(row, "w_ema_cross", 0))
        w_macd = self._safe_float(self._get_val(row, "w_macd_hist", 0))

        w_bull = (1 if w_st == 1 else 0) + (1 if w_ema == 1 else 0) + (1 if w_macd > 0 else 0)
        w_bear = (1 if w_st == -1 else 0) + (1 if w_ema == -1 else 0) + (1 if w_macd < 0 else 0)

        if w_bull >= 2:
            weekly_bias = "UPTREND"
            reasons.append("Weekly UPTREND (ST+EMA+MACD)")
        elif w_bear >= 2:
            weekly_bias = "DOWNTREND"
            reasons.append("Weekly DOWNTREND (ST+EMA+MACD)")
        else:
            weekly_bias = "RANGE"
            reasons.append("Weekly RANGE")

        # ── Daily bias ──────────────────────────────────────────────────
        d_st = self._safe_int(self._get_val(row, "d1_supertrend_dir", 0))
        d_ema = self._safe_int(self._get_val(row, "d1_ema_cross", 0))
        d_macd = self._safe_float(self._get_val(row, "d1_macd_hist", 0))
        d_rsi = self._safe_float(self._get_val(row, "d1_rsi", 50))

        d_bull = (1 if d_st == 1 else 0) + (1 if d_ema == 1 else 0) + (1 if d_macd > 0 else 0)
        d_bear = (1 if d_st == -1 else 0) + (1 if d_ema == -1 else 0) + (1 if d_macd < 0 else 0)

        if d_bull >= 2:
            daily_bias = "UPTREND"
            reasons.append("Daily UPTREND")
        elif d_bear >= 2:
            daily_bias = "DOWNTREND"
            reasons.append("Daily DOWNTREND")
        else:
            daily_bias = "RANGE"
            reasons.append("Daily RANGE")

        # ── Alignment ───────────────────────────────────────────────────
        if weekly_bias == "UPTREND" and daily_bias == "UPTREND":
            alignment = "ALIGNED_BULL"
        elif weekly_bias == "DOWNTREND" and daily_bias == "DOWNTREND":
            alignment = "ALIGNED_BEAR"
        elif weekly_bias == "RANGE" and daily_bias == "RANGE":
            alignment = "BOTH_RANGE"
        else:
            alignment = "CONFLICTING"
            reasons.append(f"HTF conflict: W={weekly_bias}, D={daily_bias}")

        # ── Daily price journey (EMA cascade on D) ──────────────────────
        close = self._safe_float(self._get_val(row, "close", 0))
        d_ema7 = self._safe_float(self._get_val(row, "d1_ema7", 0))
        d_ema21 = self._safe_float(self._get_val(row, "d1_ema21", 0))
        d_st_val = self._safe_float(self._get_val(row, "d1_supertrend", 0))
        atr = self._safe_float(self._get_val(row, "atr", close * 0.002))

        journey = ""
        journey_target = 0.0
        journey_invalidation = 0.0

        if d_ema7 > 0 and d_ema21 > 0 and d_st_val > 0:
            if d_ema == 1:  # Daily bullish EMA structure
                if close > d_ema7:
                    journey = "Riding above D-EMA7 in daily uptrend"
                    journey_target = d_ema7  # pullback target
                    journey_invalidation = d_ema21
                elif close < d_ema7 and close > d_ema21:
                    journey = f"Broke below D-EMA7, traveling to D-EMA21 @ {d_ema21:.0f}"
                    journey_target = d_ema21
                    journey_invalidation = d_ema7
                    reasons.append(journey)
                elif close < d_ema21 and close > d_st_val:
                    journey = f"Below D-EMA21, heading to D-SuperTrend @ {d_st_val:.0f}"
                    journey_target = d_st_val
                    journey_invalidation = d_ema21
                    reasons.append(journey)
                elif close < d_st_val:
                    journey = "Below D-SuperTrend — daily trend potentially broken"
                    reasons.append(journey)
            else:  # Daily bearish EMA structure
                if close < d_ema7:
                    journey = "Riding below D-EMA7 in daily downtrend"
                    journey_target = d_ema7
                    journey_invalidation = d_ema21
                elif close > d_ema7 and close < d_ema21:
                    journey = f"Bounced above D-EMA7, traveling to D-EMA21 @ {d_ema21:.0f}"
                    journey_target = d_ema21
                    journey_invalidation = d_ema7
                    reasons.append(journey)
                elif close > d_ema21 and close < d_st_val:
                    journey = f"Above D-EMA21, heading to D-SuperTrend @ {d_st_val:.0f}"
                    journey_target = d_st_val
                    journey_invalidation = d_ema21
                    reasons.append(journey)
                elif close > d_st_val:
                    journey = "Above D-SuperTrend — daily downtrend potentially broken"
                    reasons.append(journey)

        # ── HTF ceiling/floor from LevelMap ─────────────────────────────
        htf_ceiling = level_map.nearest_htf_resistance
        htf_floor = level_map.nearest_htf_support
        ceil_dist = abs(close - htf_ceiling.price) / max(atr, 1) if htf_ceiling else 999.0
        floor_dist = abs(close - htf_floor.price) / max(atr, 1) if htf_floor else 999.0

        if htf_ceiling and ceil_dist < 3:
            reasons.append(f"HTF ceiling: {htf_ceiling} ({ceil_dist:.1f} ATR away)")
        if htf_floor and floor_dist < 3:
            reasons.append(f"HTF floor: {htf_floor} ({floor_dist:.1f} ATR away)")

        # ── Exhaustion ──────────────────────────────────────────────────
        d_st_age = self._safe_int(self._get_val(row, "d1_st_bars_since_flip", 0))
        d_macd_slope = self._safe_float(self._get_val(row, "d1_macd_hist_slope_3bar", 0))

        exhaustion = 0.0
        if daily_bias == "UPTREND":
            if d_rsi > 70:
                exhaustion += 0.35
                reasons.append("Daily RSI overbought — exhaustion risk")
            if d_st_age > 40:
                exhaustion += 0.20
            if d_macd_slope < 0:
                exhaustion += 0.15
        elif daily_bias == "DOWNTREND":
            if d_rsi < 30:
                exhaustion += 0.35
                reasons.append("Daily RSI oversold — exhaustion risk")
            if d_st_age > 40:
                exhaustion += 0.20
            if d_macd_slope > 0:
                exhaustion += 0.15
        exhaustion = min(1.0, exhaustion)

        return HTFScenario(
            weekly_bias=weekly_bias,
            daily_bias=daily_bias,
            alignment=alignment,
            daily_price_journey=journey,
            daily_journey_target=journey_target,
            daily_journey_invalidation=journey_invalidation,
            htf_ceiling=htf_ceiling,
            htf_floor=htf_floor,
            htf_ceiling_dist_atr=round(ceil_dist, 2),
            htf_floor_dist_atr=round(floor_dist, 2),
            daily_trend_age=d_st_age,
            daily_exhaustion=round(exhaustion, 3),
            reasons=reasons,
        )
