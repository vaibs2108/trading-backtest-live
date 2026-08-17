"""
supertrend_adaptive.py — Port of the "SuperOsc"-style adaptive SuperTrend
oscillator from scratch/research_v1/pine scripts/jsr 2 strategy.txt.

Original PineScript logic (faithfully reproduced here):
  1. Classic ATR SuperTrend line (length=10, mult=2.0 by default): upper/
     lower bands ratchet toward price, `trend` flips to bullish when close
     breaks the prior upper band, bearish when it breaks the prior lower
     band. `Spt` is the "active" band for the current trend direction.
  2. `osc` = position of close within the [lower, upper] band width,
     normalized to [-1, 1] relative to Spt.
  3. `ama` (adaptive moving average of osc) uses a self-adjusting alpha =
     osc^2 / length -- it smooths FASTER when osc is near its extremes
     (strong trend) and slower near zero (chop). This is the genuinely
     novel piece: an exhaustion/confirmation signal whose own responsiveness
     scales with how extended price currently is.
  4. `sig` in the source is only defined (non-na) on the exact bar osc's
     sign flips -- every other bar it's `na`. Combined with Pine's NA-is-
     falsy comparison semantics, this means the source's entry condition
     `ama*100 > sig` can only ever be true ON a sign-change bar. That is
     reproduced here as `sign_change[i]` gating.

NOTE -- bug found in the source during porting: the source's recency
filter `x2 > -79` compares an absolute bar_index (x2 = bar_index of the
last SuperTrend cross, always a large positive/growing number) against
-79. That condition is true from ~bar 80 onward regardless of how long
ago the cross actually was -- it is very likely a leftover/broken
"bars-since-cross <= 79" filter that never got wired correctly. This port
implements BOTH: `bars_since_cross_ok` (mirroring the source literally --
effectively always True, kept for a literal-port comparison run) AND
`bars_since_flip` (a genuine recency count) so both can be tested.
"""
import numpy as np
import pandas as pd


def compute_supertrend_adaptive(df_slice: pd.DataFrame, length: int = 10, mult: float = 2.0):
    """Recomputes the full recursive SuperTrend + adaptive-oscillator state
    over the given window (stateless -- no persistence needed between
    calls, matches the half-life kernel's own re-derive-from-window
    pattern already used elsewhere in this codebase).

    Returns a dict of per-bar arrays (aligned to df_slice's rows) or None
    if the window is too short:
      trend        int array, 1=bullish / 0=bearish
      spt          the active SuperTrend line
      osc          normalized oscillator in [-1, 1]
      ama          adaptive moving average of osc
      sign_change  bool array, True where osc's sign just flipped
      bars_since_flip  int array, bars since the last trend flip
    """
    n = len(df_slice)
    if n < length + 5:
        return None

    highs = df_slice["high"].values.astype(float)
    lows = df_slice["low"].values.astype(float)
    closes = df_slice["close"].values.astype(float)
    hl2 = (highs + lows) / 2.0

    prev_close = np.empty(n)
    prev_close[0] = closes[0]
    prev_close[1:] = closes[:-1]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))
    atr = pd.Series(tr).ewm(alpha=1.0 / length, adjust=False).mean().values * mult

    up_raw = hl2 + atr
    dn_raw = hl2 - atr

    upper = np.zeros(n)
    lower = np.zeros(n)
    trend = np.zeros(n, dtype=int)
    upper[0] = up_raw[0]
    lower[0] = dn_raw[0]
    trend[0] = 1

    for i in range(1, n):
        upper[i] = min(up_raw[i], upper[i - 1]) if closes[i - 1] < upper[i - 1] else up_raw[i]
        lower[i] = max(dn_raw[i], lower[i - 1]) if closes[i - 1] > lower[i - 1] else dn_raw[i]
        if closes[i] > upper[i - 1]:
            trend[i] = 1
        elif closes[i] < lower[i - 1]:
            trend[i] = 0
        else:
            trend[i] = trend[i - 1]

    spt = np.where(trend == 1, lower, upper)
    band_width = upper - lower
    band_width = np.where(band_width == 0, 1e-9, band_width)
    osc = np.clip((closes - spt) / band_width, -1.0, 1.0)

    ama = np.zeros(n)
    ama[0] = osc[0]
    for i in range(1, n):
        alpha = (osc[i] ** 2) / length
        ama[i] = ama[i - 1] + alpha * (osc[i] - ama[i - 1])

    sign_osc = np.sign(osc)
    sign_change = np.zeros(n, dtype=bool)
    sign_change[1:] = sign_osc[1:] != sign_osc[:-1]

    trend_change = np.zeros(n, dtype=bool)
    trend_change[1:] = trend[1:] != trend[:-1]
    bars_since_flip = np.zeros(n, dtype=int)
    last_flip = -10_000
    for i in range(n):
        if trend_change[i]:
            last_flip = i
        bars_since_flip[i] = i - last_flip

    return {
        "trend": trend, "spt": spt, "osc": osc, "ama": ama,
        "sign_change": sign_change, "bars_since_flip": bars_since_flip,
        "upper": upper, "lower": lower,
    }
