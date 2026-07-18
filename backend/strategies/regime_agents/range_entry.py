"""
range_entry.py — Range / Sideways Entry Agent.

Active only when the regime is SIDEWAYS.

Identifies range boundaries from recent swing highs/lows (cluster-based),
then enters LONG near the bottom and SHORT near the top.

Also detects range compression (narrowing range) which often precedes
breakouts — in that case it abstains.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List


@dataclass
class RangeEntryState:
    signal: str = "HOLD"
    entry_price: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    range_high: float = 0.0
    range_low: float = 0.0
    range_width_atr: float = 0.0   # range width in ATR units
    position_in_range: float = 0.5  # 0 = at bottom, 1 = at top
    is_compressing: bool = False     # range narrowing -> breakout likely
    entry_quality: float = 0.0
    reasons: List[str] = field(default_factory=list)


class RangeEntryAgent:
    """
    Mean-reversion entries within a detected sideways range.
    """

    def __init__(self, lookback: int = 30, proximity_pct: float = 0.15,
                 min_range_atr: float = 2.0):
        """
        lookback: bars to determine range boundaries
        proximity_pct: how close to range boundary to trigger entry (20% = within 20% of boundary)
        min_range_atr: minimum range width in ATR units to consider tradeable
        """
        self.lookback = lookback
        self.proximity_pct = proximity_pct
        self.min_range_atr = min_range_atr

    def _detect_range(self, highs: np.ndarray, lows: np.ndarray,
                      closes: np.ndarray, atr: float):
        """
        Detect range boundaries using high/low clusters.

        Instead of simple max/min (which can be outlier spikes), use the
        75th percentile of highs and 25th percentile of lows to define
        the range. This captures the "typical" trading range.
        """
        # Use percentile-based boundaries (more robust than max/min)
        range_high = float(np.percentile(highs, 85))
        range_low = float(np.percentile(lows, 15))

        # Also compute strict boundaries for SL placement
        abs_high = float(np.max(highs))
        abs_low = float(np.min(lows))

        range_width = range_high - range_low
        range_width_atr = range_width / atr if atr > 0 else 0

        # Detect compression: is the range in the last 10 bars narrower
        # than the range in the first half of the lookback?
        split = len(highs) // 2
        if split > 5:
            first_half_range = float(np.max(highs[:split]) - np.min(lows[:split]))
            second_half_range = float(np.max(highs[split:]) - np.min(lows[split:]))
            is_compressing = second_half_range < first_half_range * 0.65
        else:
            is_compressing = False

        return range_high, range_low, abs_high, abs_low, range_width_atr, is_compressing

    def evaluate(self, df: pd.DataFrame, row: pd.Series, regime: str,
                 regime_confidence: float, position: str = "NONE",
                 atr: float = 0.0) -> RangeEntryState:
        """
        Evaluate range entry/exit.

        Only active when regime == SIDEWAYS.
        """
        close = float(row.get("close", 0))

        if regime != "SIDEWAYS":
            return RangeEntryState(signal="HOLD",
                                   reasons=["Regime is not SIDEWAYS — range agent inactive"])

        n = len(df)
        lb = min(self.lookback, n)
        if lb < 15:
            return RangeEntryState(signal="HOLD", reasons=["Insufficient history for range"])

        highs = df["high"].values[-lb:].astype(float)
        lows = df["low"].values[-lb:].astype(float)
        closes = df["close"].values[-lb:].astype(float)

        if atr <= 0:
            atr = float(np.mean(highs - lows)) or close * 0.002

        rh, rl, abs_h, abs_l, rw_atr, compressing = self._detect_range(
            highs, lows, closes, atr
        )

        range_width = rh - rl
        if range_width <= 0:
            return RangeEntryState(signal="HOLD", reasons=["Zero range width"])

        pos_in_range = (close - rl) / range_width  # 0 = at bottom, 1 = at top

        base_state = RangeEntryState(
            range_high=round(rh, 2),
            range_low=round(rl, 2),
            range_width_atr=round(rw_atr, 2),
            position_in_range=round(pos_in_range, 3),
            is_compressing=compressing,
        )

        # ── EXIT LOGIC ───────────────────────────────────────────────
        if position == "LONG" and pos_in_range > 0.85:
            base_state.signal = "LONG_EXIT"
            base_state.entry_price = close
            base_state.reasons = [f"Price near range top ({pos_in_range:.0%}) — exit LONG"]
            return base_state

        if position == "SHORT" and pos_in_range < 0.15:
            base_state.signal = "SHORT_EXIT"
            base_state.entry_price = close
            base_state.reasons = [f"Price near range bottom ({pos_in_range:.0%}) — exit SHORT"]
            return base_state

        if position != "NONE":
            base_state.signal = "HOLD"
            base_state.reasons = ["In position, no new entry"]
            return base_state

        # ── COMPRESSION -> ABSTAIN ────────────────────────────────────
        if compressing:
            base_state.signal = "HOLD"
            base_state.reasons = [
                f"Range compressing (breakout likely) — no range trade",
                f"Range: {rl:.0f}–{rh:.0f} ({rw_atr:.1f} ATR)"
            ]
            return base_state

        # ── Range too narrow ─────────────────────────────────────────
        if rw_atr < self.min_range_atr:
            base_state.signal = "HOLD"
            base_state.reasons = [f"Range too narrow: {rw_atr:.1f} ATR < {self.min_range_atr}"]
            return base_state

        # ── LONG near range bottom ───────────────────────────────────
        if pos_in_range <= self.proximity_pct:
            # Confirmation: bullish bar at range bottom
            o = float(row.get("open", 0))
            h = float(row.get("high", close))
            l = float(row.get("low", close))
            bar_range = h - l
            body = close - o
            if body > 0 and bar_range > 0 and body / bar_range > 0.40:  # strong bullish bar
                sl = abs_l - atr * 0.5
                t1 = rl + range_width * 0.5    # midpoint
                t2 = rh                         # range top

                quality = self._entry_quality(pos_in_range, regime_confidence, rw_atr, "LONG")

                base_state.signal = "LONG"
                base_state.entry_price = close
                base_state.sl = round(sl, 2)
                base_state.target1 = round(t1, 2)
                base_state.target2 = round(t2, 2)
                base_state.entry_quality = round(quality, 3)
                base_state.reasons = [
                    f"Price at range bottom ({pos_in_range:.0%})",
                    f"Range: {rl:.0f}–{rh:.0f} ({rw_atr:.1f} ATR)",
                    "Bullish bar confirmation at support",
                ]
                return base_state

        # ── SHORT near range top ─────────────────────────────────────
        if pos_in_range >= (1.0 - self.proximity_pct):
            o = float(row.get("open", 0))
            h = float(row.get("high", close))
            l = float(row.get("low", close))
            bar_range = h - l
            body = o - close
            if body > 0 and bar_range > 0 and body / bar_range > 0.40:  # strong bearish bar
                sl = abs_h + atr * 0.5
                t1 = rh - range_width * 0.5    # midpoint
                t2 = rl                         # range bottom

                quality = self._entry_quality(pos_in_range, regime_confidence, rw_atr, "SHORT")

                base_state.signal = "SHORT"
                base_state.entry_price = close
                base_state.sl = round(sl, 2)
                base_state.target1 = round(t1, 2)
                base_state.target2 = round(t2, 2)
                base_state.entry_quality = round(quality, 3)
                base_state.reasons = [
                    f"Price at range top ({pos_in_range:.0%})",
                    f"Range: {rl:.0f}–{rh:.0f} ({rw_atr:.1f} ATR)",
                    "Bearish bar confirmation at resistance",
                ]
                return base_state

        base_state.signal = "HOLD"
        base_state.reasons = [
            f"Price mid-range ({pos_in_range:.0%}) — waiting for boundary",
            f"Range: {rl:.0f}–{rh:.0f}"
        ]
        return base_state

    def _entry_quality(self, pos_in_range: float, regime_conf: float,
                       range_width_atr: float, direction: str) -> float:
        """Score range entry quality (0..1)."""
        # Closer to boundary = better
        if direction == "LONG":
            proximity_score = max(0, 1.0 - pos_in_range * 5)  # 0% -> 1.0, 20% -> 0.0
        else:
            proximity_score = max(0, (pos_in_range - 0.8) * 5)  # 100% -> 1.0, 80% -> 0.0

        # Wider range = more room = better
        width_score = min(range_width_atr / 4.0, 1.0)

        return 0.4 * proximity_score + 0.3 * regime_conf + 0.3 * width_score
