"""
level_map.py — LevelMapBuilder: builds a sorted price ladder from all TFs.

Collects EMA7/21, SuperTrend, Fib, S/R, Trendlines across W/D/1H/15m/5m
and sorts them into levels_above / levels_below relative to current price.

All prices are assumed to be in IST-aligned data (timestamps already IST).
"""
import logging
import numpy as np
import pandas as pd
from typing import List, Optional
from .base_v3 import PriceLevel, LevelMap

logger = logging.getLogger(__name__)

# Strength weights: [EMA7, EMA21, SuperTrend, Fib, SR/Pivot, Trendline]
_TF_WEIGHTS = {
    "W":   {"EMA7": 0.90, "EMA21": 1.00, "SUPERTREND": 0.95, "FIB": 1.00, "SR": 0.90, "TRENDLINE": 0.85},
    "D":   {"EMA7": 0.70, "EMA21": 0.80, "SUPERTREND": 0.75, "FIB": 0.80, "SR": 0.70, "TRENDLINE": 0.65},
    "1H":  {"EMA7": 0.50, "EMA21": 0.60, "SUPERTREND": 0.55, "FIB": 0.50, "SR": 0.50, "TRENDLINE": 0.45},
    "15m": {"EMA7": 0.30, "EMA21": 0.35, "SUPERTREND": 0.30, "FIB": 0.30, "SR": 0.30, "TRENDLINE": 0.25},
    "5m":  {"EMA7": 0.15, "EMA21": 0.20, "SUPERTREND": 0.15, "FIB": 0.15, "SR": 0.20, "TRENDLINE": 0.15},
}

# Map merged-column prefixes to TF labels
_PREFIX_TO_TF = {
    "w_": "W",
    "d1_": "D",
    "h1_": "1H",
    "m15_": "15m",
    "": "5m",  # base 5m columns have no prefix
}


class LevelMapBuilder:
    """Builds a LevelMap from a single merged row."""

    def __init__(self, proximity_atr: float = 0.5):
        """
        Args:
            proximity_atr: levels within this many ATR of each other are merged.
        """
        self.proximity_atr = proximity_atr

    def build(self, row: pd.Series) -> LevelMap:
        """Build a LevelMap from the latest merged row."""
        close = _safe(row, "close", 0.0)
        atr = _safe(row, "atr", close * 0.002)
        if atr < 1:
            atr = close * 0.002

        levels: List[PriceLevel] = []

        # ── Collect levels from each timeframe ──────────────────────────
        for prefix, tf in _PREFIX_TO_TF.items():
            weights = _TF_WEIGHTS.get(tf, _TF_WEIGHTS["5m"])

            # EMA7
            ema7 = _safe(row, f"{prefix}ema7", 0.0)
            if ema7 > 0:
                levels.append(PriceLevel(
                    price=ema7, level_type="EMA7", timeframe=tf,
                    strength=weights["EMA7"], broken=close < ema7 if close else False
                ))

            # EMA21
            ema21 = _safe(row, f"{prefix}ema21", 0.0)
            if ema21 > 0:
                levels.append(PriceLevel(
                    price=ema21, level_type="EMA21", timeframe=tf,
                    strength=weights["EMA21"], broken=close < ema21 if close else False
                ))

            # SuperTrend
            st = _safe(row, f"{prefix}supertrend", 0.0)
            if st > 0:
                levels.append(PriceLevel(
                    price=st, level_type="SUPERTREND", timeframe=tf,
                    strength=weights["SUPERTREND"], broken=close < st if close else False
                ))

            # Fibonacci levels (only for W, D, 1H)
            if tf in ("W", "D", "1H"):
                for fib_name, fib_col in [("FIB_618", "fib_618"), ("FIB_500", "fib_500"),
                                            ("FIB_382", "fib_382"), ("FIB_236", "fib_236")]:
                    fib_val = _safe(row, f"{prefix}{fib_col}", 0.0)
                    if fib_val > 0:
                        levels.append(PriceLevel(
                            price=fib_val, level_type=fib_name, timeframe=tf,
                            strength=weights["FIB"]
                        ))

            # S/R (pivot-based)
            for sr_name, sr_col in [("SR_RES", "res1"), ("SR_SUP", "sup1")]:
                sr_val = _safe(row, f"{prefix}{sr_col}", 0.0)
                if sr_val > 0:
                    levels.append(PriceLevel(
                        price=sr_val, level_type=sr_name, timeframe=tf,
                        strength=weights["SR"]
                    ))

            # Trendlines
            tl_res = _safe(row, f"{prefix}trendline_resistance", 0.0)
            if tl_res > 0 and not np.isnan(tl_res):
                levels.append(PriceLevel(
                    price=tl_res, level_type="TRENDLINE_RES", timeframe=tf,
                    strength=weights["TRENDLINE"]
                ))
            tl_sup = _safe(row, f"{prefix}trendline_support", 0.0)
            if tl_sup > 0 and not np.isnan(tl_sup):
                levels.append(PriceLevel(
                    price=tl_sup, level_type="TRENDLINE_SUP", timeframe=tf,
                    strength=weights["TRENDLINE"]
                ))

        # ── Filter invalid levels ───────────────────────────────────────
        levels = [lv for lv in levels if lv.price > 0 and not np.isnan(lv.price)]

        # ── Merge nearby levels (within proximity_atr) ──────────────────
        levels = self._merge_nearby(levels, atr)

        # ── Split into above/below current price ───────────────────────
        above = sorted([lv for lv in levels if lv.price > close], key=lambda x: x.price)
        below = sorted([lv for lv in levels if lv.price <= close], key=lambda x: -x.price)

        # ── HTF levels ──────────────────────────────────────────────────
        htf_tfs = {"W", "D"}
        htf_res = next((lv for lv in above if lv.timeframe in htf_tfs), None)
        htf_sup = next((lv for lv in below if lv.timeframe in htf_tfs), None)

        # ── Significant next targets (strength > 0.3) ──────────────────
        next_up = next((lv for lv in above if lv.strength >= 0.3), above[0] if above else None)
        next_down = next((lv for lv in below if lv.strength >= 0.3), below[0] if below else None)

        # ── Current zone ────────────────────────────────────────────────
        zone = self._determine_zone(row, close)

        return LevelMap(
            levels_above=above,
            levels_below=below,
            current_price=close,
            current_zone=zone,
            next_target_up=next_up,
            next_target_down=next_down,
            nearest_htf_resistance=htf_res,
            nearest_htf_support=htf_sup,
        )

    def _merge_nearby(self, levels: List[PriceLevel], atr: float) -> List[PriceLevel]:
        """Merge levels within proximity_atr of each other, keeping highest strength."""
        if not levels:
            return levels
        threshold = self.proximity_atr * atr
        levels = sorted(levels, key=lambda x: x.price)
        merged = [levels[0]]
        for lv in levels[1:]:
            if abs(lv.price - merged[-1].price) < threshold:
                # Keep the higher-strength level
                if lv.strength > merged[-1].strength:
                    merged[-1] = lv
            else:
                merged.append(lv)
        return merged

    def _determine_zone(self, row: pd.Series, close: float) -> str:
        """Determine which structural zone price is in on the 1H timeframe."""
        h1_ema7 = _safe(row, "h1_ema7", 0.0)
        h1_ema21 = _safe(row, "h1_ema21", 0.0)
        h1_st = _safe(row, "h1_supertrend", 0.0)

        if h1_ema7 <= 0 or h1_ema21 <= 0 or h1_st <= 0:
            return "UNKNOWN"

        # Bullish cascade: price > ema7 > ema21 > ST
        if close > h1_ema7 and h1_ema7 > h1_ema21:
            return "ABOVE_ALL_1H"
        if close < h1_ema7 and close > h1_ema21 and h1_ema7 > h1_ema21:
            return "BETWEEN_EMA7_EMA21_1H"
        if close < h1_ema21 and close > h1_st and h1_ema7 > h1_ema21:
            return "BELOW_EMA21_ABOVE_ST_1H"
        if close < h1_st and h1_ema7 > h1_ema21:
            return "BELOW_ST_1H"

        # Bearish cascade: price < ema7 < ema21 < ST
        if close < h1_ema7 and h1_ema7 < h1_ema21:
            return "BELOW_ALL_1H"
        if close > h1_ema7 and close < h1_ema21 and h1_ema7 < h1_ema21:
            return "BETWEEN_EMA21_EMA7_1H"
        if close > h1_ema21 and close < h1_st and h1_ema7 < h1_ema21:
            return "ABOVE_EMA21_BELOW_ST_1H"
        if close > h1_st and h1_ema7 < h1_ema21:
            return "ABOVE_ST_1H"

        return "MIXED_1H"


def _safe(row: pd.Series, col: str, default: float = 0.0) -> float:
    """Safely get float from row."""
    val = row.get(col, default)
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default
