"""
trend_entry.py — Trend Entry Agent (v2).

Active only when the regime is TRENDING_UP or TRENDING_DOWN.
Uses rolling high/low pullback detection — NOT swing-based.

Entry logic:
  LONG (in uptrend): price pulls back from the recent N-bar high by
  a meaningful amount (measured in ATR), then prints a bullish bar.
  SL below the pullback low.

  SHORT (in downtrend): price rallies from recent N-bar low,
  then prints a bearish bar. SL above the rally high.

Targets are not used for exits (trailing SL + regime flip handle that).
We still compute them for the orchestrator's R:R check.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List


@dataclass
class TrendEntryState:
    signal: str = "HOLD"
    entry_price: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    pullback_depth: float = 0.0    # pullback in ATR units
    entry_quality: float = 0.0
    reasons: List[str] = field(default_factory=list)


class TrendEntryAgent:
    """
    Pullback entries using rolling high/low — works in real trends
    where formal swing points may not form.
    """

    def __init__(self, swing_order: int = 3, min_pullback: float = 0.20,
                 max_pullback: float = 0.65, lookback: int = 6):
        """
        min_pullback: min pullback in ATR units to consider an entry
        max_pullback: max pullback in ATR units (beyond = trend may be broken)
        lookback: bars to scan for recent high/low
        """
        self.min_pullback = min_pullback  # now in ATR units (e.g. 0.5 ATR)
        self.max_pullback = max_pullback  # e.g. 2.5 ATR
        self.lookback = lookback

        # ATR-based pullback thresholds (much more robust than % of swing)
        self.min_pullback_atr = 0.10   # at least 0.3 ATR pullback
        self.max_pullback_atr = 2.5   # more than 3 ATR = trend might be broken

    def _is_bullish_bar(self, row: pd.Series) -> bool:
        """Strong bullish bar: close > open, body > 35% of range."""
        o = float(row.get("open", 0))
        c = float(row.get("close", 0))
        h = float(row.get("high", c))
        l = float(row.get("low", c))
        bar_range = h - l
        if bar_range <= 0:
            return False
        body = c - o
        return body > 0 and body / bar_range > 0.20

    def _is_bearish_bar(self, row: pd.Series) -> bool:
        """Strong bearish bar: close < open, body > 35% of range."""
        o = float(row.get("open", 0))
        c = float(row.get("close", 0))
        h = float(row.get("high", c))
        l = float(row.get("low", c))
        bar_range = h - l
        if bar_range <= 0:
            return False
        body = o - c
        return body > 0 and body / bar_range > 0.20

    def evaluate(self, df: pd.DataFrame, row: pd.Series, regime: str,
                 regime_confidence: float, position: str = "NONE",
                 atr: float = 0.0) -> TrendEntryState:
        """
        Evaluate trend entry using rolling high/low pullback.
        """
        close = float(row.get("close", 0))
        high = float(row.get("high", close))
        low = float(row.get("low", close))

        if regime not in ("TRENDING_UP", "TRENDING_DOWN"):
            return TrendEntryState(signal="HOLD",
                                   reasons=["Regime is not trending — trend agent inactive"])

        n = len(df)
        if n < 15:
            return TrendEntryState(signal="HOLD", reasons=["Insufficient history"])

        if atr <= 0:
            atr = close * 0.002

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)

        lb = min(self.lookback, n)

        # ── EXIT LOGIC ───────────────────────────────────────────────
        if position == "LONG" and regime == "TRENDING_DOWN":
            return TrendEntryState(signal="LONG_EXIT", entry_price=close,
                                   reasons=["Regime flipped to TRENDING_DOWN — exit LONG"])

        if position == "SHORT" and regime == "TRENDING_UP":
            return TrendEntryState(signal="SHORT_EXIT", entry_price=close,
                                   reasons=["Regime flipped to TRENDING_UP — exit SHORT"])

        # Also exit if regime goes to SIDEWAYS or TRANSITION
        if position == "LONG" and regime not in ("TRENDING_UP",):
            return TrendEntryState(signal="LONG_EXIT", entry_price=close,
                                   reasons=[f"Regime is {regime} — exit LONG"])

        if position == "SHORT" and regime not in ("TRENDING_DOWN",):
            return TrendEntryState(signal="SHORT_EXIT", entry_price=close,
                                   reasons=[f"Regime is {regime} — exit SHORT"])

        if position != "NONE":
            return TrendEntryState(signal=position, reasons=[f"Holding {position} position — no exit triggered"])

        # ── LONG ENTRY (uptrend pullback) ────────────────────────────
        if regime == "TRENDING_UP":
            recent_high = float(np.max(highs[-lb:]))
            pullback_pts = recent_high - close
            pullback_atr = pullback_pts / atr

            # Price must have pulled back from the high but not too far
            if self.min_pullback_atr <= pullback_atr <= self.max_pullback_atr:
                if self._is_bullish_bar(row):
                    # SL below the recent low (last 10 bars) with buffer
                    recent_low = float(np.min(lows[-6:]))
                    sl = recent_low - atr * 0.5

                    # Targets for R:R calculation
                    # Targets are cosmetic (trailing SL handles exits)
                    pullback_pts = recent_high - close
                    t1 = recent_high
                    t2 = recent_high + pullback_pts

                    quality = self._entry_quality(pullback_atr, regime_confidence)

                    return TrendEntryState(
                        signal="LONG",
                        entry_price=close,
                        sl=round(sl, 2),
                        target1=round(t1, 2),
                        target2=round(t2, 2),
                        pullback_depth=round(pullback_atr, 3),
                        entry_quality=round(quality, 3),
                        reasons=[
                            f"Uptrend pullback: {pullback_atr:.1f} ATR from {lb}-bar high",
                            f"Recent high: {recent_high:.0f}, Close: {close:.0f}",
                            "Bullish reversal bar confirmed",
                        ]
                    )
                else:
                    return TrendEntryState(
                        signal="HOLD",
                        reasons=[f"Pullback {pullback_atr:.1f} ATR valid but no bullish bar yet"])

            elif pullback_atr < self.min_pullback_atr:
                return TrendEntryState(
                    signal="HOLD",
                    reasons=[f"Pullback too shallow: {pullback_atr:.1f} ATR (need {self.min_pullback_atr})"])
            else:
                return TrendEntryState(
                    signal="HOLD",
                    reasons=[f"Pullback too deep: {pullback_atr:.1f} ATR (max {self.max_pullback_atr})"])

        # ── SHORT ENTRY (downtrend pullback) ─────────────────────────
        if regime == "TRENDING_DOWN":
            recent_low = float(np.min(lows[-lb:]))
            rally_pts = close - recent_low
            rally_atr = rally_pts / atr

            if self.min_pullback_atr <= rally_atr <= self.max_pullback_atr:
                if self._is_bearish_bar(row):
                    recent_high = float(np.max(highs[-6:]))
                    sl = recent_high + atr * 0.5

                    # Targets are cosmetic (trailing SL handles exits)
                    rally_pts = close - recent_low
                    t1 = recent_low
                    t2 = recent_low - rally_pts

                    quality = self._entry_quality(rally_atr, regime_confidence)

                    return TrendEntryState(
                        signal="SHORT",
                        entry_price=close,
                        sl=round(sl, 2),
                        target1=round(t1, 2),
                        target2=round(t2, 2),
                        pullback_depth=round(rally_atr, 3),
                        entry_quality=round(quality, 3),
                        reasons=[
                            f"Downtrend rally: {rally_atr:.1f} ATR from {lb}-bar low",
                            f"Recent low: {recent_low:.0f}, Close: {close:.0f}",
                            "Bearish reversal bar confirmed",
                        ]
                    )
                else:
                    return TrendEntryState(
                        signal="HOLD",
                        reasons=[f"Rally {rally_atr:.1f} ATR valid but no bearish bar yet"])
            elif rally_atr < self.min_pullback_atr:
                return TrendEntryState(
                    signal="HOLD",
                    reasons=[f"Rally too shallow: {rally_atr:.1f} ATR"])
            else:
                return TrendEntryState(
                    signal="HOLD",
                    reasons=[f"Rally too deep: {rally_atr:.1f} ATR"])

        return TrendEntryState(signal="HOLD",
                               reasons=[f"No entry setup in {regime}"])

    def _entry_quality(self, pullback_atr: float, regime_conf: float) -> float:
        """
        Score entry quality (0..1).
        Best entries: moderate pullback (0.8-1.5 ATR) + high regime confidence.
        """
        # Optimal pullback: around 1.0 ATR — not too shallow, not too deep
        if 0.8 <= pullback_atr <= 1.5:
            depth_score = 0.9
        elif 0.5 <= pullback_atr < 0.8:
            depth_score = 0.6
        elif 1.5 < pullback_atr <= 2.5:
            depth_score = 0.5
        else:
            depth_score = 0.3

        return 0.5 * depth_score + 0.5 * regime_conf
