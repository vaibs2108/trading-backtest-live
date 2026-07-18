"""
htf_structure.py — Higher Timeframe Structure Agent.

Analyzes 1H, 1D, and 1W data to identify:
  - Key support/resistance levels from swing points
  - Fibonacci retracement levels from the last significant swing
  - HTF trend direction (context, not signals)

Provides confluence levels that strengthen or weaken LTF signals.
This agent does NOT generate entry signals — it provides context.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class HTFLevel:
    """A single support or resistance level from a higher timeframe."""
    price: float
    timeframe: str       # "1H" | "1D" | "1W"
    level_type: str      # "SUPPORT" | "RESISTANCE" | "FIB_382" | "FIB_500" | "FIB_618"
    strength: float      # 0..1 (how many times tested / how significant)
    label: str = ""


@dataclass
class HTFStructureState:
    htf_trend: str = "NEUTRAL"           # BULLISH | BEARISH | NEUTRAL
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0
    support_distance_atr: float = 0.0    # how far price is from nearest support in ATR
    resistance_distance_atr: float = 0.0
    at_htf_support: bool = False         # within 0.5 ATR of a key support
    at_htf_resistance: bool = False
    fib_levels: List[HTFLevel] = field(default_factory=list)
    sr_levels: List[HTFLevel] = field(default_factory=list)
    all_levels: List[HTFLevel] = field(default_factory=list)
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)


class HTFStructureAgent:
    """
    Scans higher timeframes for structural levels.
    """

    def __init__(self, swing_order: int = 5, proximity_atr: float = 0.5):
        self.swing_order = swing_order
        self.proximity_atr = proximity_atr

    def _find_swing_levels(self, df: pd.DataFrame, timeframe: str) -> List[HTFLevel]:
        """
        Find swing highs and lows in the dataframe.
        Returns a list of HTFLevel objects for S/R.
        """
        if df is None or len(df) < 2 * self.swing_order + 1:
            return []

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        order = self.swing_order
        n = len(highs)
        levels = []

        for i in range(order, n - order):
            wh = highs[i - order: i + order + 1]
            if highs[i] == np.max(wh) and np.sum(wh == highs[i]) == 1:
                # Count how many bars touched this level (strength)
                touches = np.sum(np.abs(highs - highs[i]) < (highs[i] * 0.001))
                strength = min(touches / 5.0, 1.0)
                levels.append(HTFLevel(
                    price=float(highs[i]), timeframe=timeframe,
                    level_type="RESISTANCE", strength=strength,
                    label=f"{timeframe} R @ {highs[i]:.0f}"
                ))

            wl = lows[i - order: i + order + 1]
            if lows[i] == np.min(wl) and np.sum(wl == lows[i]) == 1:
                touches = np.sum(np.abs(lows - lows[i]) < (lows[i] * 0.001))
                strength = min(touches / 5.0, 1.0)
                levels.append(HTFLevel(
                    price=float(lows[i]), timeframe=timeframe,
                    level_type="SUPPORT", strength=strength,
                    label=f"{timeframe} S @ {lows[i]:.0f}"
                ))

        return levels

    def _compute_fibonacci(self, df: pd.DataFrame, timeframe: str) -> List[HTFLevel]:
        """
        Compute Fibonacci retracement from the last significant swing.
        Finds the most recent significant high-to-low or low-to-high move
        and computes 38.2%, 50%, 61.8% levels.
        """
        if df is None or len(df) < 20:
            return []

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)

        # Find the highest high and lowest low in the lookback
        # Then determine direction from their order
        hh_idx = np.argmax(highs[-50:]) if len(highs) >= 50 else np.argmax(highs)
        ll_idx = np.argmin(lows[-50:]) if len(lows) >= 50 else np.argmin(lows)

        # Adjust indices for the slice
        offset = max(0, len(highs) - 50)
        hh_idx += offset
        ll_idx += offset

        hh = float(highs[hh_idx])
        ll = float(lows[ll_idx])
        swing_range = hh - ll

        if swing_range < hh * 0.005:  # less than 0.5% — too small
            return []

        levels = []
        if hh_idx > ll_idx:
            # Upswing (low then high) — retracements are below price
            for fib, name in [(0.382, "FIB_382"), (0.500, "FIB_500"), (0.618, "FIB_618")]:
                price = hh - fib * swing_range
                levels.append(HTFLevel(
                    price=round(price, 2), timeframe=timeframe,
                    level_type=name, strength=0.7,
                    label=f"{timeframe} {name} @ {price:.0f}"
                ))
        else:
            # Downswing (high then low) — retracements are above price
            for fib, name in [(0.382, "FIB_382"), (0.500, "FIB_500"), (0.618, "FIB_618")]:
                price = ll + fib * swing_range
                levels.append(HTFLevel(
                    price=round(price, 2), timeframe=timeframe,
                    level_type=name, strength=0.7,
                    label=f"{timeframe} {name} @ {price:.0f}"
                ))

        return levels

    def _determine_htf_trend(self, df_1h: pd.DataFrame, df_1d: pd.DataFrame) -> str:
        """
        Determine overall HTF trend from 1H and daily data.
        Simple: check if recent closes are above/below 20-period mean.
        """
        votes = 0  # +1 bullish, -1 bearish

        for df, label in [(df_1h, "1H"), (df_1d, "1D")]:
            if df is not None and len(df) >= 20:
                closes = df["close"].values.astype(float)
                recent = closes[-5:]
                mean20 = np.mean(closes[-20:])
                if np.mean(recent) > mean20 * 1.002:
                    votes += 1
                elif np.mean(recent) < mean20 * 0.998:
                    votes -= 1

        if votes >= 1:
            return "BULLISH"
        elif votes <= -1:
            return "BEARISH"
        return "NEUTRAL"

    def evaluate(self, current_price: float, atr: float,
                 df_1h: pd.DataFrame = None,
                 df_1d: pd.DataFrame = None,
                 df_1w: pd.DataFrame = None) -> HTFStructureState:
        """
        Analyze HTF structure and return key levels + context.

        Parameters:
            current_price: current LTF price
            atr: current ATR (from execution TF) for proximity calculation
            df_1h, df_1d, df_1w: higher timeframe DataFrames with OHLC
        """
        reasons = []
        all_levels = []

        # Gather S/R levels from each timeframe
        for df, tf_name in [(df_1h, "1H"), (df_1d, "1D"), (df_1w, "1W")]:
            if df is not None:
                sr = self._find_swing_levels(df, tf_name)
                fib = self._compute_fibonacci(df, tf_name)
                all_levels.extend(sr)
                all_levels.extend(fib)

        # Sort by distance from current price
        for level in all_levels:
            level._dist = abs(level.price - current_price)
        all_levels.sort(key=lambda x: x._dist)

        # Find nearest support and resistance
        supports = [l for l in all_levels
                    if l.price < current_price and l.level_type in ("SUPPORT", "FIB_382", "FIB_500", "FIB_618")]
        resistances = [l for l in all_levels
                       if l.price > current_price and l.level_type in ("RESISTANCE", "FIB_382", "FIB_500", "FIB_618")]

        nearest_sup = supports[0].price if supports else current_price - atr * 5
        nearest_res = resistances[0].price if resistances else current_price + atr * 5

        sup_dist_atr = (current_price - nearest_sup) / atr if atr > 0 else 99
        res_dist_atr = (nearest_res - current_price) / atr if atr > 0 else 99

        at_support = sup_dist_atr <= self.proximity_atr
        at_resistance = res_dist_atr <= self.proximity_atr

        if at_support:
            reasons.append(f"At HTF support {nearest_sup:.0f} ({sup_dist_atr:.1f} ATR away)")
        if at_resistance:
            reasons.append(f"At HTF resistance {nearest_res:.0f} ({res_dist_atr:.1f} ATR away)")

        htf_trend = self._determine_htf_trend(df_1h, df_1d)
        reasons.append(f"HTF trend: {htf_trend}")

        # Count how many levels are nearby (within 2 ATR) — more = stronger confluence
        nearby_count = sum(1 for l in all_levels if abs(l.price - current_price) < atr * 2)
        confidence = min(nearby_count / 6.0, 1.0) if nearby_count > 0 else 0.1

        # Separate for output
        sr_levels = [l for l in all_levels if l.level_type in ("SUPPORT", "RESISTANCE")]
        fib_levels = [l for l in all_levels if l.level_type.startswith("FIB")]

        # Clean up temp attribute
        for l in all_levels:
            if hasattr(l, '_dist'):
                delattr(l, '_dist')

        return HTFStructureState(
            htf_trend=htf_trend,
            nearest_support=round(nearest_sup, 2),
            nearest_resistance=round(nearest_res, 2),
            support_distance_atr=round(sup_dist_atr, 2),
            resistance_distance_atr=round(res_dist_atr, 2),
            at_htf_support=at_support,
            at_htf_resistance=at_resistance,
            fib_levels=fib_levels[:6],
            sr_levels=sr_levels[:10],
            all_levels=all_levels[:15],
            confidence=round(confidence, 3),
            reasons=reasons,
        )
