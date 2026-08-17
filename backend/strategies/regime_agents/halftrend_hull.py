"""
halftrend_hull.py — Port of the confluence entry from
scratch/research_v1/pine scripts/All bank atm.txt: HalfTrend (Everget)
flip, confirmed by a Hull-based momentum flip within N bars.

HalfTrend is ported faithfully (a 2-bar swing/ATR-channel trend-reversal
system -- structurally similar to Chandelier Exit / SuperTrend, but with
its own distinct swing-detection logic).

Hull Butterfly Oscillator (LuxAlgo) is NOT ported literally. Its `cmean`
term is `ta.cum(abs(hso)) / bar_index * mult` -- an EXPANDING average of
|hso| since the very first bar of the chart. That is not a well-defined
quantity to port into a rolling-window backtest (its value depends on
however much history precedes whatever start date the original TradingView
chart happened to load, which isn't a stable, reproducible reference).
Instead this module computes a standard Hull Moving Average (HMA) and
detects a MOMENTUM FLIP (HMA slope changing sign) as an honest substitute
for "the Butterfly oscillator's os state flipped" -- same role (a fast
momentum-direction confirmation), different, more portable math. This
substitution is a deliberate adaptation, not a literal port -- flagged
here and to the user for transparency.
"""
import numpy as np
import pandas as pd


def _wma(series: np.ndarray, length: int) -> np.ndarray:
    weights = np.arange(1, length + 1, dtype=float)
    out = np.full(len(series), np.nan)
    for i in range(length - 1, len(series)):
        window = series[i - length + 1:i + 1]
        out[i] = np.dot(window, weights) / weights.sum()
    return out


def compute_hma(closes: np.ndarray, length: int = 11) -> np.ndarray:
    half_len = max(1, int(length / 2))
    sqrt_len = max(1, int(round(np.sqrt(length))))
    wma_half = _wma(closes, half_len)
    wma_full = _wma(closes, length)
    raw = 2 * wma_half - wma_full
    raw = np.nan_to_num(raw, nan=closes)
    hma = _wma(raw, sqrt_len)
    return hma


def compute_halftrend(df_slice: pd.DataFrame, amplitude: int = 2, channel_deviation: int = 2,
                       atr_length: int = 100):
    """Recomputes HalfTrend recursively over the window. Returns per-bar
    arrays aligned to df_slice, or None if the window is too short."""
    n = len(df_slice)
    if n < max(atr_length, 20) + 5:
        return None

    highs = df_slice["high"].values.astype(float)
    lows = df_slice["low"].values.astype(float)
    closes = df_slice["close"].values.astype(float)

    prev_close = np.empty(n)
    prev_close[0] = closes[0]
    prev_close[1:] = closes[:-1]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))
    atr2 = pd.Series(tr).ewm(alpha=1.0 / atr_length, adjust=False).mean().values / 2.0
    dev = channel_deviation * atr2

    high_s = pd.Series(highs)
    low_s = pd.Series(lows)
    high_amp = high_s.rolling(amplitude).max().values
    low_amp = low_s.rolling(amplitude).min().values
    highma = high_s.rolling(amplitude).mean().values
    lowma = low_s.rolling(amplitude).mean().values

    trend = np.zeros(n, dtype=int)
    next_trend = np.zeros(n, dtype=int)
    max_low_price = np.full(n, np.nan)
    min_high_price = np.full(n, np.nan)
    up = np.zeros(n)
    down = np.zeros(n)
    ht = np.zeros(n)

    start = amplitude
    max_low_price[start - 1] = lows[start - 1] if start > 0 else lows[0]
    min_high_price[start - 1] = highs[start - 1] if start > 0 else highs[0]

    for i in range(start, n):
        prev_trend = trend[i - 1]
        prev_next_trend = next_trend[i - 1]
        prev_max_low = max_low_price[i - 1] if not np.isnan(max_low_price[i - 1]) else lows[i - 1]
        prev_min_high = min_high_price[i - 1] if not np.isnan(min_high_price[i - 1]) else highs[i - 1]

        cur_trend = prev_trend
        cur_next_trend = prev_next_trend
        cur_max_low = prev_max_low
        cur_min_high = prev_min_high

        if np.isnan(high_amp[i]) or np.isnan(highma[i]):
            trend[i] = prev_trend
            next_trend[i] = prev_next_trend
            max_low_price[i] = prev_max_low
            min_high_price[i] = prev_min_high
            up[i] = up[i - 1]
            down[i] = down[i - 1]
            ht[i] = up[i] if trend[i] == 0 else down[i]
            continue

        if prev_next_trend == 1:
            cur_max_low = max(low_amp[i], prev_max_low)
            if highma[i] < cur_max_low and closes[i] < lows[i - 1]:
                cur_trend = 1
                cur_next_trend = 0
                cur_min_high = high_amp[i]
        else:
            cur_min_high = min(high_amp[i], prev_min_high)
            if lowma[i] > cur_min_high and closes[i] > highs[i - 1]:
                cur_trend = 0
                cur_next_trend = 1
                cur_max_low = low_amp[i]

        trend[i] = cur_trend
        next_trend[i] = cur_next_trend
        max_low_price[i] = cur_max_low
        min_high_price[i] = cur_min_high

        if cur_trend == 0:
            if trend[i - 1] != 0:
                up[i] = down[i - 1]
            else:
                up[i] = max(cur_max_low, up[i - 1]) if i > start else cur_max_low
            down[i] = down[i - 1]
            ht[i] = up[i]
        else:
            if trend[i - 1] != 1:
                down[i] = up[i - 1]
            else:
                down[i] = min(cur_min_high, down[i - 1]) if i > start else cur_min_high
            up[i] = up[i - 1]
            ht[i] = down[i]

    buy_signal = np.zeros(n, dtype=bool)
    sell_signal = np.zeros(n, dtype=bool)
    buy_signal[1:] = (trend[1:] == 0) & (trend[:-1] == 1)
    sell_signal[1:] = (trend[1:] == 1) & (trend[:-1] == 0)

    return {
        "trend": trend, "ht": ht, "buy_signal": buy_signal, "sell_signal": sell_signal,
        "dev": dev,
    }


def compute_hull_momentum_flip(df_slice: pd.DataFrame, length: int = 11):
    """Momentum-flip proxy for the Hull Butterfly Oscillator's `os` state
    flip (see module docstring for why the literal indicator isn't
    ported). Returns (hull_up_flip, hull_down_flip) bool arrays: True on
    the bar the HMA's slope changes from falling to rising (up_flip) or
    rising to falling (down_flip)."""
    closes = df_slice["close"].values.astype(float)
    n = len(closes)
    if n < length + 5:
        return None
    hma = compute_hma(closes, length)
    slope = np.diff(hma, prepend=hma[0])
    rising = slope > 0
    up_flip = np.zeros(n, dtype=bool)
    down_flip = np.zeros(n, dtype=bool)
    up_flip[1:] = rising[1:] & ~rising[:-1]
    down_flip[1:] = ~rising[1:] & rising[:-1]
    return up_flip, down_flip
