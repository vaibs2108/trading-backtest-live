"""
swing_agent.py — Swing Agent: 1H EMA cascade + price journey.

The PRIMARY trend-direction engine for v3.  Reads the EMA cascade
(price position relative to EMA7, EMA21, SuperTrend on 1H) to build
a price-journey narrative instead of scoring indicators.

Key insight: when price breaks below 1H EMA7, SHORT opportunities
open on 5m *immediately*, even if 1H SuperTrend is still bullish.
"""
import logging
import numpy as np
import pandas as pd
from .base import BaseAgent
from .base_v3 import SwingScenario, LevelMap

logger = logging.getLogger(__name__)

# Cascade states — bullish EMA structure (ema7 > ema21)
_CASCADE_BULL = {
    "ABOVE_ALL":           "Price above 1H EMA7 > EMA21 — strong uptrend",
    "BETWEEN_7_21":        "Price broke below 1H EMA7, heading to EMA21",
    "BELOW_21_ABOVE_ST":   "Price below 1H EMA21 but above SuperTrend — deep pullback",
    "BELOW_ST":            "Price below 1H SuperTrend — potential trend change",
}

# Cascade states — bearish EMA structure (ema7 < ema21)
_CASCADE_BEAR = {
    "BELOW_ALL":           "Price below 1H EMA7 < EMA21 — strong downtrend",
    "BETWEEN_21_7":        "Price bounced above 1H EMA7, heading to EMA21",
    "ABOVE_21_BELOW_ST":   "Price above 1H EMA21 but below SuperTrend — deep bounce",
    "ABOVE_ST":            "Price above 1H SuperTrend — potential trend change",
}


class SwingAgent(BaseAgent):
    """1H EMA cascade + price journey → trade bias."""

    def __init__(self):
        super().__init__()
        self._prev_cascade = "UNKNOWN"
        self._journey_start_bar = 0
        self._bar_count = 0

    def evaluate(self, row: pd.Series, level_map: LevelMap) -> SwingScenario:
        reasons = []
        self._bar_count += 1

        close = self._safe_float(self._get_val(row, "close", 0))
        atr = self._safe_float(self._get_val(row, "atr", close * 0.002))
        if atr < 1:
            atr = close * 0.002

        # ── 1H indicator values ─────────────────────────────────────────
        h1_ema7 = self._safe_float(self._get_val(row, "h1_ema7", 0))
        h1_ema21 = self._safe_float(self._get_val(row, "h1_ema21", 0))
        h1_st_val = self._safe_float(self._get_val(row, "h1_supertrend", 0))
        h1_st_dir = self._safe_int(self._get_val(row, "h1_supertrend_dir", 0))
        h1_st_flip = self._safe_int(self._get_val(row, "h1_st_bars_since_flip",
                                     self._safe_int(self._get_val(row, "st_bars_since_flip", 999))))
        h1_macd_hist = self._safe_float(self._get_val(row, "h1_macd_hist", 0))
        h1_macd_slope = self._safe_float(self._get_val(row, "h1_macd_hist_slope_3bar", 0))

        # Fallback if 1H data not available
        if h1_ema7 <= 0 or h1_ema21 <= 0:
            return SwingScenario(reasons=["1H data not available"])

        # ── Determine EMA cascade state ─────────────────────────────────
        ema_bull = h1_ema7 > h1_ema21  # Bullish EMA structure

        if ema_bull:
            if close > h1_ema7:
                cascade = "ABOVE_ALL"
            elif close > h1_ema21:
                cascade = "BETWEEN_7_21"
            elif h1_st_val > 0 and close > h1_st_val:
                cascade = "BELOW_21_ABOVE_ST"
            else:
                cascade = "BELOW_ST"
        else:
            # Bearish EMA structure (ema7 < ema21)
            if close < h1_ema7:
                cascade = "BELOW_ALL"
            elif close < h1_ema21:
                cascade = "BETWEEN_21_7"
            elif h1_st_val > 0 and close < h1_st_val:
                cascade = "ABOVE_21_BELOW_ST"
            else:
                cascade = "ABOVE_ST"

        # ── Cascade direction (are we descending or ascending through levels?) ─
        if cascade != self._prev_cascade:
            self._journey_start_bar = self._bar_count
            self._prev_cascade = cascade

        bars_in_journey = self._bar_count - self._journey_start_bar

        # Determine direction based on cascade position
        if ema_bull:
            # In a bullish structure, moving down through levels = DESCENDING
            cascade_order = ["ABOVE_ALL", "BETWEEN_7_21", "BELOW_21_ABOVE_ST", "BELOW_ST"]
        else:
            # In a bearish structure, moving up = ASCENDING
            cascade_order = ["BELOW_ALL", "BETWEEN_21_7", "ABOVE_21_BELOW_ST", "ABOVE_ST"]

        cascade_idx = cascade_order.index(cascade) if cascade in cascade_order else -1
        prev_idx = cascade_order.index(self._prev_cascade) if self._prev_cascade in cascade_order else -1

        if cascade_idx > prev_idx and prev_idx >= 0:
            cascade_direction = "DESCENDING" if ema_bull else "ASCENDING"
        elif cascade_idx < prev_idx and prev_idx >= 0:
            cascade_direction = "ASCENDING" if ema_bull else "DESCENDING"
        else:
            cascade_direction = "STALLING"

        desc = (_CASCADE_BULL if ema_bull else _CASCADE_BEAR).get(cascade, cascade)
        reasons.append(desc)

        # ── Price journey narrative ─────────────────────────────────────
        journey = ""
        journey_target = 0.0
        journey_target_type = ""
        journey_invalidation = 0.0

        if ema_bull:
            if cascade == "ABOVE_ALL":
                journey = "Strong uptrend — price riding above all 1H MAs"
                journey_target = h1_ema7
                journey_target_type = "EMA7"
                journey_invalidation = h1_ema7  # break below invalidates
            elif cascade == "BETWEEN_7_21":
                journey = f"Broke 1H EMA7 → heading to 1H EMA21 @ {h1_ema21:.0f}"
                journey_target = h1_ema21
                journey_target_type = "EMA21"
                journey_invalidation = h1_ema7  # reclaim = journey over
            elif cascade == "BELOW_21_ABOVE_ST":
                journey = f"Below 1H EMA21 → heading to 1H ST @ {h1_st_val:.0f}"
                journey_target = h1_st_val
                journey_target_type = "SUPERTREND"
                journey_invalidation = h1_ema21
            elif cascade == "BELOW_ST":
                # Below everything — use level map for next target
                next_down = level_map.next_target_down
                if next_down:
                    journey = f"Below 1H SuperTrend — trend change. Next: {next_down}"
                    journey_target = next_down.price
                    journey_target_type = f"{next_down.timeframe}_{next_down.level_type}"
                else:
                    journey = "Below 1H SuperTrend — trend change"
                journey_invalidation = h1_st_val
        else:
            if cascade == "BELOW_ALL":
                journey = "Strong downtrend — price below all 1H MAs"
                journey_target = h1_ema7
                journey_target_type = "EMA7"
                journey_invalidation = h1_ema7
            elif cascade == "BETWEEN_21_7":
                journey = f"Bounced above 1H EMA7 → heading to 1H EMA21 @ {h1_ema21:.0f}"
                journey_target = h1_ema21
                journey_target_type = "EMA21"
                journey_invalidation = h1_ema7
            elif cascade == "ABOVE_21_BELOW_ST":
                journey = f"Above 1H EMA21 → heading to 1H ST @ {h1_st_val:.0f}"
                journey_target = h1_st_val
                journey_target_type = "SUPERTREND"
                journey_invalidation = h1_ema21
            elif cascade == "ABOVE_ST":
                next_up = level_map.next_target_up
                if next_up:
                    journey = f"Above 1H SuperTrend — trend change. Next: {next_up}"
                    journey_target = next_up.price
                    journey_target_type = f"{next_up.timeframe}_{next_up.level_type}"
                else:
                    journey = "Above 1H SuperTrend — trend change"
                journey_invalidation = h1_st_val

        reasons.append(journey)

        # ── MACD momentum (supporting evidence) ────────────────────────
        macd_expanding = h1_macd_slope != 0 and np.sign(h1_macd_hist) == np.sign(h1_macd_slope)
        macd_direction = 1 if h1_macd_hist > 0 else -1 if h1_macd_hist < 0 else 0
        if macd_expanding:
            reasons.append(f"1H MACD expanding {'bullish' if macd_direction == 1 else 'bearish'}")

        # ── Derived trade bias ──────────────────────────────────────────
        # The trade bias comes from the cascade position, NOT from SuperTrend alone
        price_to_st = (close - h1_st_val) / max(atr, 1) if h1_st_val > 0 else 0

        if ema_bull:
            if cascade == "ABOVE_ALL":
                trade_bias = "LONG"
                trade_bias_reason = "1H EMA cascade bullish — price above all MAs"
                confidence = 0.75
            elif cascade == "BETWEEN_7_21":
                # KEY v3 CHANGE: this enables SHORT even with bullish EMA structure
                trade_bias = "SHORT"
                trade_bias_reason = "Broke 1H EMA7 → SHORT on 5m toward EMA21"
                confidence = 0.55
            elif cascade == "BELOW_21_ABOVE_ST":
                trade_bias = "SHORT"
                trade_bias_reason = "Below 1H EMA21 → SHORT on 5m toward SuperTrend"
                confidence = 0.65
            elif cascade == "BELOW_ST":
                trade_bias = "SHORT"
                trade_bias_reason = "Below 1H SuperTrend — trend change → SHORT"
                confidence = 0.80
            else:
                trade_bias = "NEUTRAL"
                trade_bias_reason = "Unclear cascade state"
                confidence = 0.0
        else:
            # Bearish EMA structure
            if cascade == "BELOW_ALL":
                trade_bias = "SHORT"
                trade_bias_reason = "1H EMA cascade bearish — price below all MAs"
                confidence = 0.75
            elif cascade == "BETWEEN_21_7":
                trade_bias = "LONG"
                trade_bias_reason = "Bounced above 1H EMA7 → LONG on 5m toward EMA21"
                confidence = 0.55
            elif cascade == "ABOVE_21_BELOW_ST":
                trade_bias = "LONG"
                trade_bias_reason = "Above 1H EMA21 → LONG on 5m toward SuperTrend"
                confidence = 0.65
            elif cascade == "ABOVE_ST":
                trade_bias = "LONG"
                trade_bias_reason = "Above 1H SuperTrend — trend change → LONG"
                confidence = 0.80
            else:
                trade_bias = "NEUTRAL"
                trade_bias_reason = "Unclear cascade state"
                confidence = 0.0

        # Adjust confidence based on MACD support
        if macd_direction != 0:
            bias_dir = 1 if trade_bias == "LONG" else -1 if trade_bias == "SHORT" else 0
            if macd_direction == bias_dir:
                confidence = min(1.0, confidence + 0.10)
                if macd_expanding:
                    confidence = min(1.0, confidence + 0.05)
            else:
                confidence = max(0.0, confidence - 0.10)

        reasons.append(trade_bias_reason)

        return SwingScenario(
            ema_cascade=cascade,
            ema_cascade_direction=cascade_direction,
            price_journey=journey,
            journey_target=journey_target,
            journey_target_type=journey_target_type,
            journey_invalidation=journey_invalidation,
            bars_in_journey=bars_in_journey,
            supertrend_bias=h1_st_dir,
            bars_since_st_flip=h1_st_flip,
            price_to_st_atr=round(price_to_st, 2),
            ema7_price=h1_ema7,
            ema21_price=h1_ema21,
            supertrend_price=h1_st_val,
            macd_expanding=macd_expanding,
            macd_direction=macd_direction,
            trade_bias=trade_bias,
            trade_bias_reason=trade_bias_reason,
            confidence=round(confidence, 3),
            reasons=reasons,
        )
