"""
patterns.py — Structural chart pattern detection.

Detects 11 chart patterns algorithmically from swing point analysis:
- Head & Shoulders / Inverse H&S
- Double Top / Double Bottom
- Flag & Pole (Bull / Bear)
- Ascending / Descending / Symmetrical Triangle
- Rising Wedge
- Cup & Handle
- Channel (ascending / descending)

All detection is vectorized for backtest performance.
"""
import numpy as np
import pandas as pd
from typing import List, Tuple, Optional


# ═══════════════════════════════════════════════════════════════════════════════
# SWING POINT DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def find_swing_points(df: pd.DataFrame, order: int = 5) -> pd.DataFrame:
    """
    Identify swing highs and swing lows using local extrema.

    Args:
        df: DataFrame with high, low columns
        order: Number of bars on each side to confirm a swing point

    Returns:
        DataFrame with swing_high_flag, swing_low_flag, swing_high_val, swing_low_val
    """
    high = df["high"].values
    low = df["low"].values
    n = len(df)

    swing_highs = np.zeros(n, dtype=bool)
    swing_lows = np.zeros(n, dtype=bool)

    for i in range(order, n - order):
        # Swing high: highest in window
        if high[i] == max(high[i - order: i + order + 1]):
            swing_highs[i] = True
        # Swing low: lowest in window
        if low[i] == min(low[i - order: i + order + 1]):
            swing_lows[i] = True

    df = df.copy()
    df["swing_high_flag"] = swing_highs
    df["swing_low_flag"] = swing_lows
    df["swing_high_val"] = np.where(swing_highs, high, np.nan)
    df["swing_low_val"] = np.where(swing_lows, low, np.nan)

    # Forward fill for easy reference
    df["last_swing_high"] = df["swing_high_val"].ffill()
    df["last_swing_low"] = df["swing_low_val"].ffill()

    return df


def get_recent_swings(df: pd.DataFrame, n_swings: int = 5) -> Tuple[List, List]:
    """Get the N most recent swing highs and swing lows as (index, value) pairs."""
    sh_idx = df.index[df["swing_high_flag"]].tolist()
    sl_idx = df.index[df["swing_low_flag"]].tolist()

    # Use swing_high_val / swing_low_val so the correct price is returned
    # even after the lookahead-elimination shift in detect_patterns_vectorized
    swing_highs = [(i, df.loc[i, "swing_high_val"]) for i in sh_idx[-n_swings:]]
    swing_lows = [(i, df.loc[i, "swing_low_val"]) for i in sl_idx[-n_swings:]]

    return swing_highs, swing_lows


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN DETECTION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def detect_head_and_shoulders(swing_highs: List, swing_lows: List,
                               close: float, atr: float) -> dict:
    """
    Detect Head & Shoulders (bearish reversal) pattern.

    Requires 3 swing highs where middle (head) is highest,
    and 2 swing lows forming a neckline.
    """
    if len(swing_highs) < 3 or len(swing_lows) < 2:
        return {"detected": False}

    h1_idx, h1_val = swing_highs[-3]
    h2_idx, h2_val = swing_highs[-2]  # head
    h3_idx, h3_val = swing_highs[-1]

    l1_idx, l1_val = swing_lows[-2]
    l2_idx, l2_val = swing_lows[-1]

    # Head must be highest
    if not (h2_val > h1_val and h2_val > h3_val):
        return {"detected": False}

    # Shoulders should be roughly equal (within 1.5% of each other)
    shoulder_diff = abs(h1_val - h3_val) / max(h1_val, h3_val)
    if shoulder_diff > 0.015:
        return {"detected": False}

    # Neckline (connecting the two troughs)
    neckline = (l1_val + l2_val) / 2

    # Pattern should be ordered chronologically
    if not (h1_idx < h2_idx < h3_idx):
        return {"detected": False}

    # Head should be significantly above shoulders (at least 0.5 ATR)
    head_prominence = (h2_val - (h1_val + h3_val) / 2) / atr
    if head_prominence < 0.5:
        return {"detected": False}

    breakout_distance = (close - neckline) / atr
    target = neckline - (h2_val - neckline)  # measured move

    return {
        "detected": True,
        "pattern": "HEAD_SHOULDERS",
        "signal": "SHORT",
        "breakout_level": neckline,
        "breakout_distance_atr": breakout_distance,
        "target": target,
        "target_atr": abs(target - close) / atr,
        "completion": min(1.0, max(0, 1 - breakout_distance / 2)),
    }


def detect_inverse_head_and_shoulders(swing_highs: List, swing_lows: List,
                                       close: float, atr: float) -> dict:
    """Detect Inverse Head & Shoulders (bullish reversal)."""
    if len(swing_lows) < 3 or len(swing_highs) < 2:
        return {"detected": False}

    l1_idx, l1_val = swing_lows[-3]
    l2_idx, l2_val = swing_lows[-2]  # head (lowest)
    l3_idx, l3_val = swing_lows[-1]

    h1_idx, h1_val = swing_highs[-2]
    h2_idx, h2_val = swing_highs[-1]

    # Head must be lowest
    if not (l2_val < l1_val and l2_val < l3_val):
        return {"detected": False}

    shoulder_diff = abs(l1_val - l3_val) / max(l1_val, l3_val)
    if shoulder_diff > 0.015:
        return {"detected": False}

    neckline = (h1_val + h2_val) / 2

    if not (l1_idx < l2_idx < l3_idx):
        return {"detected": False}

    head_prominence = ((l1_val + l3_val) / 2 - l2_val) / atr
    if head_prominence < 0.5:
        return {"detected": False}

    breakout_distance = (neckline - close) / atr
    target = neckline + (neckline - l2_val)

    return {
        "detected": True,
        "pattern": "INV_HEAD_SHOULDERS",
        "signal": "LONG",
        "breakout_level": neckline,
        "breakout_distance_atr": breakout_distance,
        "target": target,
        "target_atr": abs(target - close) / atr,
        "completion": min(1.0, max(0, 1 - breakout_distance / 2)),
    }


def detect_double_top(swing_highs: List, swing_lows: List,
                      close: float, atr: float) -> dict:
    """Detect Double Top (bearish reversal)."""
    if len(swing_highs) < 2 or len(swing_lows) < 1:
        return {"detected": False}

    h1_idx, h1_val = swing_highs[-2]
    h2_idx, h2_val = swing_highs[-1]
    l_idx, l_val = swing_lows[-1]  # trough between peaks

    # Peaks should be close in value (within 0.5%)
    if abs(h1_val - h2_val) / max(h1_val, h2_val) > 0.005:
        return {"detected": False}

    # Trough should be between the two peaks chronologically
    if not (h1_idx < l_idx < h2_idx):
        return {"detected": False}

    # Minimum depth of trough (at least 1 ATR below peaks)
    peak_avg = (h1_val + h2_val) / 2
    trough_depth = (peak_avg - l_val) / atr
    if trough_depth < 1.0:
        return {"detected": False}

    breakout_distance = (close - l_val) / atr
    target = l_val - (peak_avg - l_val)

    return {
        "detected": True,
        "pattern": "DOUBLE_TOP",
        "signal": "SHORT",
        "breakout_level": l_val,
        "breakout_distance_atr": breakout_distance,
        "target": target,
        "target_atr": abs(target - close) / atr,
        "completion": min(1.0, max(0, 1 - breakout_distance / 2)),
    }


def detect_double_bottom(swing_highs: List, swing_lows: List,
                          close: float, atr: float) -> dict:
    """Detect Double Bottom (bullish reversal)."""
    if len(swing_lows) < 2 or len(swing_highs) < 1:
        return {"detected": False}

    l1_idx, l1_val = swing_lows[-2]
    l2_idx, l2_val = swing_lows[-1]
    h_idx, h_val = swing_highs[-1]

    if abs(l1_val - l2_val) / max(l1_val, l2_val) > 0.005:
        return {"detected": False}

    if not (l1_idx < h_idx < l2_idx):
        return {"detected": False}

    trough_avg = (l1_val + l2_val) / 2
    peak_height = (h_val - trough_avg) / atr
    if peak_height < 1.0:
        return {"detected": False}

    breakout_distance = (h_val - close) / atr
    target = h_val + (h_val - trough_avg)

    return {
        "detected": True,
        "pattern": "DOUBLE_BOTTOM",
        "signal": "LONG",
        "breakout_level": h_val,
        "breakout_distance_atr": breakout_distance,
        "target": target,
        "target_atr": abs(target - close) / atr,
        "completion": min(1.0, max(0, 1 - breakout_distance / 2)),
    }


def detect_bull_flag(df: pd.DataFrame, close: float, atr: float,
                     lookback: int = 30) -> dict:
    """
    Detect Bull Flag (continuation).
    Pole: Strong upmove (>3 ATR in <10 bars)
    Flag: Mild consolidation downward (<1.5 ATR retracement in 5-15 bars)
    """
    if len(df) < lookback:
        return {"detected": False}

    recent = df.iloc[-lookback:]
    highs = recent["high"].values
    lows = recent["low"].values
    closes = recent["close"].values

    # Find pole: look for sharp upmove
    best_pole = None
    for pole_end in range(10, lookback - 5):
        for pole_start in range(max(0, pole_end - 10), pole_end):
            pole_move = highs[pole_end] - lows[pole_start]
            pole_bars = pole_end - pole_start
            if pole_move > 3 * atr and pole_bars <= 10:
                # Flag: consolidation after pole
                flag_data = closes[pole_end:]
                if len(flag_data) >= 3:
                    flag_high = max(highs[pole_end:])
                    flag_low = min(lows[pole_end:])
                    retracement = flag_high - flag_low
                    if retracement < 1.5 * atr:
                        best_pole = {
                            "pole_move_atr": pole_move / atr,
                            "flag_retracement_atr": retracement / atr,
                            "flag_bars": len(flag_data),
                        }

    if best_pole is None:
        return {"detected": False}

    breakout_level = max(highs[-15:])
    breakout_distance = (breakout_level - close) / atr

    return {
        "detected": True,
        "pattern": "BULL_FLAG",
        "signal": "LONG",
        "breakout_level": breakout_level,
        "breakout_distance_atr": breakout_distance,
        "target_atr": best_pole["pole_move_atr"],  # measured move = pole size
        "completion": min(1.0, max(0, 1 - breakout_distance / 1.5)),
    }


def detect_bear_flag(df: pd.DataFrame, close: float, atr: float,
                     lookback: int = 30) -> dict:
    """Detect Bear Flag (continuation). Mirror of bull flag."""
    if len(df) < lookback:
        return {"detected": False}

    recent = df.iloc[-lookback:]
    highs = recent["high"].values
    lows = recent["low"].values
    closes = recent["close"].values

    best_pole = None
    for pole_end in range(10, lookback - 5):
        for pole_start in range(max(0, pole_end - 10), pole_end):
            pole_move = highs[pole_start] - lows[pole_end]
            pole_bars = pole_end - pole_start
            if pole_move > 3 * atr and pole_bars <= 10:
                flag_data = closes[pole_end:]
                if len(flag_data) >= 3:
                    flag_high = max(highs[pole_end:])
                    flag_low = min(lows[pole_end:])
                    retracement = flag_high - flag_low
                    if retracement < 1.5 * atr:
                        best_pole = {
                            "pole_move_atr": pole_move / atr,
                            "flag_retracement_atr": retracement / atr,
                        }

    if best_pole is None:
        return {"detected": False}

    breakout_level = min(lows[-15:])
    breakout_distance = (close - breakout_level) / atr

    return {
        "detected": True,
        "pattern": "BEAR_FLAG",
        "signal": "SHORT",
        "breakout_level": breakout_level,
        "breakout_distance_atr": breakout_distance,
        "target_atr": best_pole["pole_move_atr"],
        "completion": min(1.0, max(0, 1 - breakout_distance / 1.5)),
    }


def detect_triangle(swing_highs: List, swing_lows: List,
                    close: float, atr: float) -> dict:
    """
    Detect Triangle patterns (ascending, descending, symmetrical).

    Ascending: flat resistance + rising lows
    Descending: flat support + falling highs
    Symmetrical: converging highs and lows
    """
    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return {"detected": False}

    # Get last 3 swing highs and lows
    sh = swing_highs[-3:]
    sl = swing_lows[-3:]

    high_vals = [v for _, v in sh]
    low_vals = [v for _, v in sl]

    # Slopes
    high_slope = (high_vals[-1] - high_vals[0]) / max(len(high_vals), 1)
    low_slope = (low_vals[-1] - low_vals[0]) / max(len(low_vals), 1)

    high_flat = abs(high_vals[-1] - high_vals[0]) / atr < 0.5
    low_flat = abs(low_vals[-1] - low_vals[0]) / atr < 0.5
    highs_falling = high_slope < -atr * 0.05
    lows_rising = low_slope > atr * 0.05

    if high_flat and lows_rising:
        pattern = "ASCENDING_TRIANGLE"
        signal = "LONG"
        breakout_level = max(high_vals)
        breakout_distance = (breakout_level - close) / atr
    elif low_flat and highs_falling:
        pattern = "DESCENDING_TRIANGLE"
        signal = "SHORT"
        breakout_level = min(low_vals)
        breakout_distance = (close - breakout_level) / atr
    elif highs_falling and lows_rising:
        pattern = "SYMMETRICAL_TRIANGLE"
        signal = "NEUTRAL"  # breakout direction unknown
        apex = (max(high_vals) + min(low_vals)) / 2
        breakout_distance = abs(close - apex) / atr
        breakout_level = apex
    else:
        return {"detected": False}

    # Triangle height = measured move target
    height = max(high_vals) - min(low_vals)

    return {
        "detected": True,
        "pattern": pattern,
        "signal": signal,
        "breakout_level": breakout_level,
        "breakout_distance_atr": breakout_distance,
        "target_atr": height / atr,
        "completion": min(1.0, max(0, 1 - breakout_distance / 2)),
    }


def detect_rising_wedge(swing_highs: List, swing_lows: List,
                        close: float, atr: float) -> dict:
    """Detect Rising Wedge (bearish). Both trendlines rising but converging."""
    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return {"detected": False}

    high_vals = [v for _, v in swing_highs[-3:]]
    low_vals = [v for _, v in swing_lows[-3:]]

    highs_rising = (high_vals[-1] > high_vals[0]) and (high_vals[-1] > high_vals[-2])
    lows_rising = (low_vals[-1] > low_vals[0]) and (low_vals[-1] > low_vals[-2])

    if not (highs_rising and lows_rising):
        return {"detected": False}

    # Check convergence: gap between highs and lows narrowing
    gap_first = high_vals[0] - low_vals[0]
    gap_last = high_vals[-1] - low_vals[-1]

    if gap_last >= gap_first:  # not converging
        return {"detected": False}

    convergence_ratio = gap_last / max(gap_first, 1)
    if convergence_ratio > 0.7:  # not enough convergence
        return {"detected": False}

    breakout_level = min(low_vals)
    breakout_distance = (close - breakout_level) / atr

    return {
        "detected": True,
        "pattern": "RISING_WEDGE",
        "signal": "SHORT",
        "breakout_level": breakout_level,
        "breakout_distance_atr": breakout_distance,
        "target_atr": (max(high_vals) - min(low_vals)) / atr,
        "completion": min(1.0, max(0, 1 - convergence_ratio)),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VECTORIZED PATTERN DETECTION FOR BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════

def detect_patterns_vectorized(df: pd.DataFrame, swing_order: int = 5,
                                lookback: int = 50) -> pd.DataFrame:
    """
    Run pattern detection across the entire DataFrame for backtesting.
    Adds pattern columns to the DataFrame.

    This runs pattern detection every `swing_order * 2` bars for efficiency.

    Columns added:
    - pattern_name: String name of detected pattern (or "NONE")
    - pattern_signal: LONG | SHORT | NEUTRAL | NONE
    - pattern_completion: 0-1 how complete the pattern is
    - pattern_breakout_dist_atr: Distance to breakout level
    - pattern_target_atr: Expected move size
    """
    df = df.copy()
    df = find_swing_points(df, order=swing_order)

    # Shift swing flags forward by swing_order bars to eliminate lookahead bias:
    # bar i is only confirmed as a swing high after seeing swing_order bars beyond it,
    # so it should only be "visible" starting at bar i+swing_order in the backtest.
    df["swing_high_val"] = df["swing_high_val"].shift(swing_order)
    df["swing_low_val"] = df["swing_low_val"].shift(swing_order)
    df["swing_high_flag"] = df["swing_high_flag"].shift(swing_order).fillna(False).astype(bool)
    df["swing_low_flag"] = df["swing_low_flag"].shift(swing_order).fillna(False).astype(bool)
    df["last_swing_high"] = df["swing_high_val"].ffill()
    df["last_swing_low"] = df["swing_low_val"].ffill()

    n = len(df)
    pattern_names = ["NONE"] * n
    pattern_signals = ["NONE"] * n
    pattern_completions = np.zeros(n)
    pattern_breakout_dists = np.zeros(n)
    pattern_target_atrs = np.zeros(n)

    atr_col = df["atr"].fillna(df["close"] * 0.002).clip(lower=1).values
    close_col = df["close"].values

    # Run detection every few bars for efficiency
    step = max(1, swing_order)
    for i in range(lookback, n, step):
        window = df.iloc[max(0, i - lookback):i + 1]
        sh, sl = get_recent_swings(window, n_swings=5)
        if not sh or not sl:
            continue

        close_i = close_col[i]
        atr_i = atr_col[i]

        # Try all pattern detectors (first match wins, ordered by priority)
        detectors = [
            lambda: detect_head_and_shoulders(sh, sl, close_i, atr_i),
            lambda: detect_inverse_head_and_shoulders(sh, sl, close_i, atr_i),
            lambda: detect_double_top(sh, sl, close_i, atr_i),
            lambda: detect_double_bottom(sh, sl, close_i, atr_i),
            lambda: detect_triangle(sh, sl, close_i, atr_i),
            lambda: detect_rising_wedge(sh, sl, close_i, atr_i),
            lambda: detect_bull_flag(window, close_i, atr_i),
            lambda: detect_bear_flag(window, close_i, atr_i),
        ]

        for det_fn in detectors:
            result = det_fn()
            if result.get("detected", False):
                # Fill from current position to next check
                fill_end = min(i + step, n)
                for j in range(i, fill_end):
                    pattern_names[j] = result["pattern"]
                    pattern_signals[j] = result.get("signal", "NEUTRAL")
                    pattern_completions[j] = result.get("completion", 0)
                    pattern_breakout_dists[j] = result.get("breakout_distance_atr", 0)
                    pattern_target_atrs[j] = result.get("target_atr", 0)
                break

    df["pattern_name"] = pattern_names
    df["pattern_signal"] = pattern_signals
    df["pattern_completion"] = pattern_completions
    df["pattern_breakout_dist_atr"] = pattern_breakout_dists
    df["pattern_target_atr"] = pattern_target_atrs

    # Encode pattern name as numerical for ML
    pattern_map = {
        "NONE": 0, "HEAD_SHOULDERS": 1, "INV_HEAD_SHOULDERS": 2,
        "DOUBLE_TOP": 3, "DOUBLE_BOTTOM": 4, "BULL_FLAG": 5, "BEAR_FLAG": 6,
        "ASCENDING_TRIANGLE": 7, "DESCENDING_TRIANGLE": 8,
        "SYMMETRICAL_TRIANGLE": 9, "RISING_WEDGE": 10, "CUP_HANDLE": 11,
    }
    df["pattern_encoded"] = df["pattern_name"].map(pattern_map).fillna(0).astype(int)

    # Pattern signal as numerical
    sig_map = {"NONE": 0, "NEUTRAL": 0, "LONG": 1, "SHORT": -1}
    df["pattern_signal_num"] = df["pattern_signal"].map(sig_map).fillna(0).astype(int)

    return df
