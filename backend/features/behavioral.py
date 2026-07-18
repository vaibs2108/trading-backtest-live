"""
behavioral.py — Behavioral feature engine.

Computes ~120 features that capture HOW indicators are behaving over time,
not just their point-in-time values. These features let ML learn the dynamics
that a human trader reads from charts.

All functions are vectorized (operate on full DataFrames) for backtest speed.
"""
import numpy as np
import pandas as pd
from typing import Optional
import warnings

# Suppress pandas PerformanceWarning about highly fragmented DataFrames
try:
    from pandas.errors import PerformanceWarning
    warnings.filterwarnings("ignore", category=PerformanceWarning)
except ImportError:
    pass



# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _slope(series: pd.Series, window: int) -> pd.Series:
    """Linear regression slope over a rolling window."""
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    if x_var == 0:
        return pd.Series(0.0, index=series.index)

    def _calc_slope(vals):
        if len(vals) < window or np.isnan(vals).any():
            return np.nan
        y_mean = vals.mean()
        return ((x * (vals - y_mean)).sum()) / x_var

    return series.rolling(window, min_periods=window).apply(_calc_slope, raw=True)


def _bars_since_condition(condition: pd.Series) -> pd.Series:
    """Count bars since the last True value in a boolean series."""
    groups = (~condition).cumsum()
    result = condition.groupby(groups).cumcount()
    # Where condition was never True, return a large number
    never_true = ~condition.cummax()
    result[never_true] = 999
    return result


def _bars_since_cross(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """Count bars since series_a last crossed above or below series_b."""
    above = series_a > series_b
    cross = above != above.shift(1)
    cross.iloc[0] = False
    return _bars_since_condition(cross)


def _count_touches(price: pd.Series, level: pd.Series,
                   tolerance_pct: float, window: int) -> pd.Series:
    """Count times price touched a level (within tolerance %) in last N bars."""
    near = (((price - level).abs() / level.clip(lower=1)) < tolerance_pct)
    return near.astype(float).rolling(window, min_periods=1).sum()


def _consecutive_count(condition: pd.Series) -> pd.Series:
    """Count consecutive True values ending at current bar."""
    groups = (~condition).cumsum()
    return condition.groupby(groups).cumcount() + condition.astype(int)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SUPERTREND BEHAVIORAL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def supertrend_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Behavioral features for SuperTrend indicator.

    Features:
    - st_bars_since_flip: Candles since last SuperTrend direction change
    - st_price_dist_atr: Price distance from SuperTrend in ATR units
    - st_touch_count_10: Times price touched SuperTrend in last 10 bars
    - st_touch_count_20: Times price touched SuperTrend in last 20 bars
    - st_slope_5bar: Rate of change of SuperTrend value
    - st_bounce_avg: Average bounce size after SuperTrend touches (in ATR)
    - st_squeeze: Distance between SuperTrend and EMA21 narrowing
    """
    p = prefix
    st_dir = df[f"{p}supertrend_dir"].fillna(0)
    st_val = df[f"{p}supertrend"].fillna(df[f"{p}close"])
    close = df[f"{p}close"]
    atr = df[f"{p}atr"].fillna(close * 0.002).clip(lower=1)

    # Bars since last flip
    flip = st_dir != st_dir.shift(1)
    flip.iloc[0] = False
    df[f"{p}st_bars_since_flip"] = _bars_since_condition(flip)

    # Price distance from SuperTrend in ATR units
    df[f"{p}st_price_dist_atr"] = (close - st_val) / atr

    # Touch count (within 0.3% of SuperTrend)
    df[f"{p}st_touch_count_10"] = _count_touches(close, st_val, 0.003, 10)
    df[f"{p}st_touch_count_20"] = _count_touches(close, st_val, 0.003, 20)

    # SuperTrend slope (rate of change)
    df[f"{p}st_slope_5bar"] = _slope(st_val, 5) / atr

    # Squeeze: SuperTrend-EMA21 distance narrowing
    ema21 = df.get(f"{p}ema21", close)
    st_ema_gap = (st_val - ema21).abs() / atr
    df[f"{p}st_ema21_squeeze"] = st_ema_gap
    df[f"{p}st_squeeze_slope"] = _slope(st_ema_gap, 5)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 2. EMA CROSS BEHAVIORAL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def ema_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Behavioral features for EMA 7/21 cross.

    Features:
    - ema_bars_since_cross: Candles since last EMA7/EMA21 crossover
    - ema_gap_atr: Distance between EMA7 and EMA21 in ATR units
    - ema_gap_slope_5bar: Rate of change of EMA gap (diverging/converging)
    - ema_price_zone: Price position relative to both EMAs (0=below both, 1=between, 2=above)
    - ema_price_to_ema7_atr: Price distance to EMA7 in ATR
    - ema_price_to_ema21_atr: Price distance to EMA21 in ATR
    - ema7_slope: Slope of EMA7 (turning direction)
    - ema_rejection_count: Times price failed to reclaim EMA7 in last 10 bars
    """
    p = prefix
    ema7 = df[f"{p}ema7"].fillna(df[f"{p}close"])
    ema21 = df[f"{p}ema21"].fillna(df[f"{p}close"])
    close = df[f"{p}close"]
    atr = df[f"{p}atr"].fillna(close * 0.002).clip(lower=1)

    # Bars since cross
    df[f"{p}ema_bars_since_cross"] = _bars_since_cross(ema7, ema21)

    # EMA gap in ATR
    gap = (ema7 - ema21) / atr
    df[f"{p}ema_gap_atr"] = gap
    df[f"{p}ema_gap_slope_5bar"] = _slope(gap, 5)

    # Price zone: 0=below both, 1=between, 2=above both
    above_ema7 = (close > ema7).astype(int)
    above_ema21 = (close > ema21).astype(int)
    df[f"{p}ema_price_zone"] = above_ema7 + above_ema21

    # Distance to each EMA
    df[f"{p}ema_price_to_ema7_atr"] = (close - ema7) / atr
    df[f"{p}ema_price_to_ema21_atr"] = (close - ema21) / atr

    # EMA7 slope
    df[f"{p}ema7_slope"] = _slope(ema7, 5) / atr

    # Rejection count: price tried to go above EMA7 but failed
    crossed_above = (close > ema7) & (close.shift(1) <= ema7.shift(1))
    fell_back = (close < ema7) & (close.shift(1) >= ema7.shift(1))
    rejections = crossed_above.shift(1).fillna(False) & fell_back
    df[f"{p}ema_rejection_count_10"] = rejections.astype(float).rolling(10, min_periods=1).sum()

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 3. STOCHRSI BEHAVIORAL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def stochrsi_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Behavioral features for StochRSI.

    Features:
    - srsi_phase: Encoded phase (oversold curling up, overbought turning, etc.)
    - srsi_trajectory_5bar: Slope of K line
    - srsi_kd_cross_dir: +1 bullish cross, -1 bearish cross, 0 none
    - srsi_bars_in_oversold: Duration below 20
    - srsi_bars_in_overbought: Duration above 80
    - srsi_divergence: Price vs StochRSI divergence flag
    - srsi_speed_of_entry: How fast it entered extreme zone
    """
    p = prefix
    k = df[f"{p}stochrsi_k"].fillna(50)
    d = df[f"{p}stochrsi_d"].fillna(50)
    close = df[f"{p}close"]

    # Trajectory (slope of K over 5 bars)
    df[f"{p}srsi_trajectory_5bar"] = _slope(k, 5)

    # K-D cross direction
    k_above_d = k > d
    bullish_cross = k_above_d & (~k_above_d.shift(1).fillna(False))
    bearish_cross = (~k_above_d) & k_above_d.shift(1).fillna(True)
    cross_dir = pd.Series(0, index=df.index)
    cross_dir[bullish_cross] = 1
    cross_dir[bearish_cross] = -1
    df[f"{p}srsi_kd_cross_dir"] = cross_dir

    # Duration in extreme zones
    in_oversold = k < 20
    in_overbought = k > 80
    df[f"{p}srsi_bars_in_oversold"] = _consecutive_count(in_oversold)
    df[f"{p}srsi_bars_in_overbought"] = _consecutive_count(in_overbought)

    # Phase encoding (numerical for ML):
    # -3=overbought_falling, -2=overbought_flat, -1=mid_falling
    #  0=neutral, 1=mid_rising, 2=oversold_flat, 3=oversold_curling_up
    traj = df[f"{p}srsi_trajectory_5bar"].fillna(0)
    phase = pd.Series(0, index=df.index, dtype=float)
    phase[in_oversold & (traj > 0.5)] = 3    # oversold + curling up = best long signal
    phase[in_oversold & (traj <= 0.5)] = 2   # oversold but not curling yet
    phase[(~in_oversold) & (~in_overbought) & (traj > 0)] = 1  # mid rising
    phase[(~in_oversold) & (~in_overbought) & (traj < 0)] = -1  # mid falling
    phase[in_overbought & (traj >= -0.5)] = -2  # overbought flat
    phase[in_overbought & (traj < -0.5)] = -3   # overbought + turning down = best short signal
    df[f"{p}srsi_phase"] = phase

    # Divergence: price making new high but StochRSI lower high (bearish)
    # or price making new low but StochRSI higher low (bullish)
    price_high_20 = close.rolling(20, min_periods=5).max()
    price_low_20 = close.rolling(20, min_periods=5).min()
    srsi_high_20 = k.rolling(20, min_periods=5).max()
    srsi_low_20 = k.rolling(20, min_periods=5).min()

    new_price_high = close >= price_high_20 * 0.998
    lower_srsi_high = k < srsi_high_20 * 0.95
    new_price_low = close <= price_low_20 * 1.002
    higher_srsi_low = k > srsi_low_20 * 1.05

    div = pd.Series(0, index=df.index)
    div[new_price_high & lower_srsi_high] = -1  # bearish divergence
    div[new_price_low & higher_srsi_low] = 1     # bullish divergence
    df[f"{p}srsi_divergence"] = div

    # Speed of entry into extreme zone
    was_normal = (~in_oversold.shift(5).fillna(True)) & in_oversold
    df[f"{p}srsi_speed_entry_oversold"] = was_normal.astype(float)
    was_normal_ob = (~in_overbought.shift(5).fillna(True)) & in_overbought
    df[f"{p}srsi_speed_entry_overbought"] = was_normal_ob.astype(float)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DMI / ADX BEHAVIORAL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def adx_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Behavioral features for ADX/DMI.

    Features:
    - adx_slope_5bar: ADX trending up (new trend) or down (weakening)
    - adx_regime: Encoded regime (0=ranging, 1=weak, 2=trending, 3=strong)
    - adx_regime_duration: Bars in current regime
    - di_spread: DMP - DMN (directional conviction magnitude)
    - di_spread_slope: Is spread widening or narrowing?
    - di_cross_bars: Bars since last DI crossover
    - adx_hook: ADX was falling and now turning up (new trend birth)
    """
    p = prefix
    adx = df[f"{p}adx"].fillna(0)
    dmp = df[f"{p}dmp"].fillna(0)
    dmn = df[f"{p}dmn"].fillna(0)
    atr = df[f"{p}atr"].fillna(1).clip(lower=1)

    # ADX slope
    df[f"{p}adx_slope_5bar"] = _slope(adx, 5)

    # ADX regime (numerical)
    regime = pd.Series(0, index=df.index)
    regime[adx >= 30] = 3   # strong
    regime[(adx >= 20) & (adx < 30)] = 2  # trending
    regime[(adx >= 15) & (adx < 20)] = 1  # weak
    # 0 = ranging (adx < 15)
    df[f"{p}adx_regime"] = regime

    # Regime duration
    regime_change = regime != regime.shift(1)
    regime_change.iloc[0] = True
    df[f"{p}adx_regime_duration"] = _bars_since_condition(regime_change)

    # DI spread
    di_spread = dmp - dmn
    df[f"{p}di_spread"] = di_spread
    df[f"{p}di_spread_slope"] = _slope(di_spread, 5)

    # Bars since DI crossover
    df[f"{p}di_cross_bars"] = _bars_since_cross(dmp, dmn)

    # ADX hook: was falling (slope < 0) for 3+ bars, now rising
    adx_slope = df[f"{p}adx_slope_5bar"]
    was_falling = (adx_slope.shift(1) < 0) & (adx_slope.shift(2) < 0)
    now_rising = adx_slope > 0.3
    df[f"{p}adx_hook"] = (was_falling & now_rising & (adx < 25)).astype(float)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 5. MACD BEHAVIORAL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def macd_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Behavioral features for MACD.

    Features:
    - macd_hist_slope_3bar: Histogram momentum (expanding/contracting)
    - macd_hist_accel: Second derivative (acceleration)
    - macd_zero_cross_bars: Bars since MACD line crossed zero
    - macd_signal_cross_bars: Bars since MACD crossed signal
    - macd_hist_peak_ratio: Current hist as % of recent peak
    - macd_hist_streak: Consecutive positive/negative histogram bars
    - macd_divergence: Price vs MACD divergence
    - macd_squeeze: MACD and Signal converging toward zero
    """
    p = prefix
    macd_line = df[f"{p}macd"].fillna(0)
    macd_sig = df[f"{p}macd_sig"].fillna(0)
    macd_hist = df[f"{p}macd_hist"].fillna(0)
    close = df[f"{p}close"]
    atr = df[f"{p}atr"].fillna(close * 0.002).clip(lower=1)

    # Histogram slope
    df[f"{p}macd_hist_slope_3bar"] = _slope(macd_hist, 3)

    # Histogram acceleration (second derivative)
    hist_slope = df[f"{p}macd_hist_slope_3bar"]
    df[f"{p}macd_hist_accel"] = hist_slope - hist_slope.shift(1)

    # Bars since MACD zero cross
    macd_positive = macd_line > 0
    macd_zero_cross = macd_positive != macd_positive.shift(1)
    macd_zero_cross.iloc[0] = False
    df[f"{p}macd_zero_cross_bars"] = _bars_since_condition(macd_zero_cross)

    # Bars since MACD-Signal cross
    df[f"{p}macd_signal_cross_bars"] = _bars_since_cross(macd_line, macd_sig)

    # Histogram peak ratio: current / recent peak
    hist_abs = macd_hist.abs()
    recent_peak = hist_abs.rolling(20, min_periods=3).max().clip(lower=0.01)
    df[f"{p}macd_hist_peak_ratio"] = macd_hist / recent_peak

    # Histogram positive/negative streak
    hist_positive = macd_hist > 0
    df[f"{p}macd_hist_pos_streak"] = _consecutive_count(hist_positive)
    df[f"{p}macd_hist_neg_streak"] = _consecutive_count(~hist_positive)

    # MACD divergence with price
    price_high_20 = close.rolling(20, min_periods=5).max()
    macd_high_20 = macd_line.rolling(20, min_periods=5).max()
    price_low_20 = close.rolling(20, min_periods=5).min()
    macd_low_20 = macd_line.rolling(20, min_periods=5).min()

    div = pd.Series(0, index=df.index)
    new_high = close >= price_high_20 * 0.998
    lower_macd = macd_line < macd_high_20 * 0.90
    new_low = close <= price_low_20 * 1.002
    higher_macd = macd_line > macd_low_20 * 0.90
    div[new_high & lower_macd] = -1  # bearish
    div[new_low & higher_macd] = 1   # bullish
    df[f"{p}macd_divergence"] = div

    # Squeeze: MACD and Signal both near zero and converging
    macd_abs = macd_line.abs() / atr
    sig_abs = macd_sig.abs() / atr
    df[f"{p}macd_squeeze"] = (macd_abs + sig_abs).clip(upper=5)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 6. STOCHASTIC BEHAVIORAL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def stoch_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Behavioral features for Stochastic oscillator.

    Features:
    - stoch_phase: Phase encoding (similar to StochRSI)
    - stoch_trajectory_5bar: K line slope
    - stoch_kd_cross_quality: Where did the cross happen (extreme=high quality)
    - stoch_bars_in_oversold/overbought: Duration
    - stoch_srsi_agreement: Both Stoch and StochRSI agree on direction
    """
    p = prefix
    k_col = f"{p}stoch_k"
    d_col = f"{p}stoch_d"

    if k_col not in df.columns:
        return df

    k = df[k_col].fillna(50)
    d = df[d_col].fillna(50)

    # Trajectory
    df[f"{p}stoch_trajectory_5bar"] = _slope(k, 5)

    # Phase encoding
    traj = df[f"{p}stoch_trajectory_5bar"].fillna(0)
    in_os = k < 20
    in_ob = k > 80
    phase = pd.Series(0, index=df.index, dtype=float)
    phase[in_os & (traj > 0.5)] = 3
    phase[in_os & (traj <= 0.5)] = 2
    phase[(~in_os) & (~in_ob) & (traj > 0)] = 1
    phase[(~in_os) & (~in_ob) & (traj < 0)] = -1
    phase[in_ob & (traj >= -0.5)] = -2
    phase[in_ob & (traj < -0.5)] = -3
    df[f"{p}stoch_phase"] = phase

    # Duration in zones
    df[f"{p}stoch_bars_in_oversold"] = _consecutive_count(in_os)
    df[f"{p}stoch_bars_in_overbought"] = _consecutive_count(in_ob)

    # K-D cross quality (cross in extreme zone = higher quality)
    k_above_d = k > d
    bullish_x = k_above_d & (~k_above_d.shift(1).fillna(False))
    bearish_x = (~k_above_d) & k_above_d.shift(1).fillna(True)
    quality = pd.Series(0.0, index=df.index)
    quality[bullish_x & in_os] = 1.0   # bullish cross in oversold = best
    quality[bullish_x & (~in_os)] = 0.5
    quality[bearish_x & in_ob] = -1.0  # bearish cross in overbought = best
    quality[bearish_x & (~in_ob)] = -0.5
    df[f"{p}stoch_kd_cross_quality"] = quality

    # Agreement with StochRSI
    srsi_k_col = f"{p}stochrsi_k"
    if srsi_k_col in df.columns:
        srsi_k = df[srsi_k_col].fillna(50)
        both_oversold = in_os & (srsi_k < 20)
        both_overbought = in_ob & (srsi_k > 80)
        agree = pd.Series(0, index=df.index)
        agree[both_oversold] = 1
        agree[both_overbought] = -1
        df[f"{p}stoch_srsi_agreement"] = agree

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 7. RSI BEHAVIORAL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def rsi_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Behavioral features for RSI.

    Features:
    - rsi_zone: 0=oversold(<30), 1=neutral(30-70), 2=overbought(>70)
    - rsi_slope_5bar: Momentum direction
    - rsi_50_position: Above/below 50 centerline (regime indicator)
    - rsi_divergence: Classic RSI divergence
    - rsi_range_width: RSI oscillation range over last 20 bars
    """
    p = prefix
    rsi_col = f"{p}rsi"
    if rsi_col not in df.columns:
        return df

    rsi = df[rsi_col].fillna(50)
    close = df[f"{p}close"]

    # RSI zone
    zone = pd.Series(1, index=df.index)
    zone[rsi < 30] = 0
    zone[rsi > 70] = 2
    df[f"{p}rsi_zone"] = zone

    # RSI slope
    df[f"{p}rsi_slope_5bar"] = _slope(rsi, 5)

    # Above/below 50
    df[f"{p}rsi_above_50"] = (rsi > 50).astype(float)

    # RSI divergence
    price_high_20 = close.rolling(20, min_periods=5).max()
    rsi_high_20 = rsi.rolling(20, min_periods=5).max()
    price_low_20 = close.rolling(20, min_periods=5).min()
    rsi_low_20 = rsi.rolling(20, min_periods=5).min()

    div = pd.Series(0, index=df.index)
    div[(close >= price_high_20 * 0.998) & (rsi < rsi_high_20 * 0.95)] = -1
    div[(close <= price_low_20 * 1.002) & (rsi > rsi_low_20 * 1.05)] = 1
    df[f"{p}rsi_divergence"] = div

    # Oscillation range (volatility of RSI itself)
    df[f"{p}rsi_range_20bar"] = rsi.rolling(20, min_periods=5).max() - rsi.rolling(20, min_periods=5).min()

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 8. FIBONACCI BEHAVIORAL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def fibonacci_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Behavioral features for Fibonacci levels.

    Features:
    - fib_zone: Which Fib zone is price in (above 23.6, golden zone, below 61.8)
    - fib_nearest_level_atr: Distance to nearest Fib level in ATR
    - fib_level_respect: How well are Fib levels being respected?
    - fib_retracement_depth: Current retracement %
    - fib_bounce_at_618: Count of bounces at 61.8% level
    """
    p = prefix
    close = df[f"{p}close"]
    atr = df[f"{p}atr"].fillna(close * 0.002).clip(lower=1)

    fib_618 = df.get(f"{p}fib_618", close)
    fib_500 = df.get(f"{p}fib_500", close)
    fib_382 = df.get(f"{p}fib_382", close)
    swing_high = df.get(f"{p}swing_high", close)
    swing_low = df.get(f"{p}swing_low", close)

    # Fib zone encoding
    # 4=above 23.6 (strong uptrend), 3=23.6-38.2, 2=38.2-61.8 (golden zone), 1=below 61.8, 0=below swing low
    fib_236 = df.get(f"{p}fib_236", swing_high)
    rng = (swing_high - swing_low).clip(lower=1)
    retracement = (swing_high - close) / rng

    zone = pd.Series(2, index=df.index)
    zone[retracement <= 0.236] = 4
    zone[(retracement > 0.236) & (retracement <= 0.382)] = 3
    zone[(retracement > 0.382) & (retracement <= 0.618)] = 2  # golden zone
    zone[(retracement > 0.618) & (retracement <= 0.786)] = 1
    zone[retracement > 0.786] = 0
    df[f"{p}fib_zone"] = zone

    # Distance to nearest Fib level
    dist_618 = (close - fib_618).abs()
    dist_500 = (close - fib_500).abs()
    dist_382 = (close - fib_382).abs()
    nearest = pd.concat([dist_618, dist_500, dist_382], axis=1).min(axis=1)
    df[f"{p}fib_nearest_dist_atr"] = nearest / atr

    # Fib level respect: count bounces at 61.8 in last 20 bars
    df[f"{p}fib_618_touches"] = _count_touches(close, fib_618, 0.003, 20)
    df[f"{p}fib_500_touches"] = _count_touches(close, fib_500, 0.003, 20)

    # Retracement depth (continuous 0-1)
    df[f"{p}fib_retracement_depth"] = retracement.clip(0, 1)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 9. VOLUME BEHAVIORAL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def volume_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Behavioral features for Volume.

    Features:
    - vol_relative: Volume / 20-bar average
    - vol_trend_5bar: Volume slope (increasing/decreasing)
    - vol_price_divergence: Price up + volume down (or vice versa)
    - vol_spike_bars_ago: Bars since last volume spike (>2x avg)
    - vol_at_support: Volume behavior at support levels
    """
    p = prefix
    vol_col = f"{p}volume" if f"{p}volume" in df.columns else "volume"
    if vol_col not in df.columns:
        # No volume data — fill with neutral values
        df[f"{p}vol_relative"] = 1.0
        df[f"{p}vol_trend_5bar"] = 0.0
        df[f"{p}vol_price_divergence"] = 0
        df[f"{p}vol_spike_bars_ago"] = 999
        return df

    vol = df[vol_col].fillna(0).astype(float)
    close = df[f"{p}close"]

    # Relative volume
    vol_avg = vol.rolling(20, min_periods=3).mean().clip(lower=1)
    df[f"{p}vol_relative"] = vol / vol_avg

    # Volume trend
    df[f"{p}vol_trend_5bar"] = _slope(vol / vol_avg, 5)

    # Price-volume divergence
    price_change = close.pct_change(5).fillna(0)
    vol_change = (vol / vol_avg).pct_change(5).fillna(0)
    div = pd.Series(0, index=df.index)
    div[(price_change > 0.005) & (vol_change < -0.1)] = -1   # price up, vol down = weak rally
    div[(price_change < -0.005) & (vol_change < -0.1)] = 1   # price down, vol down = weak selloff (bullish)
    div[(price_change > 0.005) & (vol_change > 0.1)] = 2     # price up, vol up = strong rally
    div[(price_change < -0.005) & (vol_change > 0.1)] = -2   # price down, vol up = strong selloff
    df[f"{p}vol_price_divergence"] = div

    # Bars since volume spike (>2x average)
    spike = vol > (vol_avg * 2)
    df[f"{p}vol_spike_bars_ago"] = _bars_since_condition(spike).clip(upper=50)

    # Accumulation/Distribution simple proxy
    body = close - df.get(f"{p}open", close)
    candle_range = df.get(f"{p}high", close) - df.get(f"{p}low", close)
    candle_range = candle_range.clip(lower=0.01)
    clv = body / candle_range  # close location value: +1 = close at high, -1 = close at low
    df[f"{p}vol_accum_dist_5bar"] = (clv * vol).rolling(5, min_periods=1).sum() / vol_avg.clip(lower=1)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 10. SUPPORT / RESISTANCE INTERACTION FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def sr_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Features for how price interacts with support/resistance levels.

    Features:
    - sr_nearest_support_atr: Distance to nearest support (pivot/fib)
    - sr_nearest_resistance_atr: Distance to nearest resistance
    - sr_support_touches: Times price tested support
    - sr_in_sr_zone: Is price within 0.3% of any S/R level?
    """
    p = prefix
    close = df[f"{p}close"]
    atr = df[f"{p}atr"].fillna(close * 0.002).clip(lower=1)

    sup1 = df.get(f"{p}sup1", close * 0.99)
    res1 = df.get(f"{p}res1", close * 1.01)
    fib_618 = df.get(f"{p}fib_618", close * 0.99)
    fib_382 = df.get(f"{p}fib_382", close * 1.01)

    # Distance to nearest support (min of sup1, fib_618 if below price)
    sup_candidates = pd.DataFrame({
        "sup1": sup1, "fib618": fib_618
    })
    # Filter only levels below current price
    for c in sup_candidates.columns:
        sup_candidates[c] = sup_candidates[c].where(sup_candidates[c] < close, np.nan)
    nearest_sup = sup_candidates.max(axis=1).fillna(close * 0.99)
    df[f"{p}sr_nearest_support_atr"] = (close - nearest_sup) / atr

    # Distance to nearest resistance
    res_candidates = pd.DataFrame({
        "res1": res1, "fib382": fib_382
    })
    for c in res_candidates.columns:
        res_candidates[c] = res_candidates[c].where(res_candidates[c] > close, np.nan)
    nearest_res = res_candidates.min(axis=1).fillna(close * 1.01)
    df[f"{p}sr_nearest_resistance_atr"] = (nearest_res - close) / atr

    # Support touches
    df[f"{p}sr_support_touches_20"] = _count_touches(close, nearest_sup, 0.003, 20)

    # In S/R zone (near any significant level)
    near_any = pd.Series(False, index=df.index)
    for lvl in [sup1, res1, fib_618, fib_382]:
        near_any = near_any | (((close - lvl).abs() / close.clip(lower=1)) < 0.003)
    df[f"{p}sr_in_zone"] = near_any.astype(float)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 11. CROSS-TIMEFRAME ALIGNMENT FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

def alignment_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Features that measure alignment across multiple timeframes.
    These are computed on the MERGED dataframe (after asof_merge).

    Features:
    - tf_supertrend_alignment: How many TFs have SuperTrend in same direction (0-5)
    - tf_ema_alignment: How many TFs have EMA cross in same direction
    - tf_momentum_alignment: StochRSI direction agreement
    - tf_macro_micro_agreement: Do macro and micro trends agree?
    - osc_convergence_score: All oscillators pointing same way?
    """
    # SuperTrend alignment (higher = more timeframes agree)
    st_cols = []
    for p in ["w_", "d1_", "h1_", "m15_", ""]:
        col = f"{p}supertrend_dir"
        if col in df.columns:
            st_cols.append(col)

    if st_cols:
        st_sum_bull = sum((df[c] == 1).astype(float) for c in st_cols)
        st_sum_bear = sum((df[c] == -1).astype(float) for c in st_cols)
        df["tf_st_alignment_bull"] = st_sum_bull
        df["tf_st_alignment_bear"] = st_sum_bear
        df["tf_st_alignment_net"] = st_sum_bull - st_sum_bear

    # EMA alignment
    ema_cols = []
    for p in ["w_", "d1_", "h1_", "m15_", ""]:
        col = f"{p}ema_cross"
        if col in df.columns:
            ema_cols.append(col)

    if ema_cols:
        ema_bull = sum((df[c] == 1).astype(float) for c in ema_cols)
        ema_bear = sum((df[c] == -1).astype(float) for c in ema_cols)
        df["tf_ema_alignment_net"] = ema_bull - ema_bear

    # Momentum alignment (StochRSI > 50 = bullish, < 50 = bearish)
    srsi_cols = []
    for p in ["d1_", "h1_", "m15_", ""]:
        col = f"{p}stochrsi_k"
        if col in df.columns:
            srsi_cols.append(col)

    if srsi_cols:
        mom_bull = sum((df[c] > 50).astype(float) for c in srsi_cols)
        mom_bear = sum((df[c] < 50).astype(float) for c in srsi_cols)
        df["tf_momentum_alignment"] = mom_bull - mom_bear

    # Macro-micro agreement
    if "d1_supertrend_dir" in df.columns and "supertrend_dir" in df.columns:
        df["macro_micro_agree"] = (
            df["d1_supertrend_dir"] == df["supertrend_dir"]
        ).astype(float)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 12. CANDLE PATTERN FEATURES (for Trigger Agent)
# ═══════════════════════════════════════════════════════════════════════════════

def candle_pattern_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Basic candle pattern detection for entry timing.

    Features:
    - candle_body_ratio: Body / total range (high body = conviction)
    - candle_direction: +1 green, -1 red
    - candle_upper_wick_ratio: Upper wick / range (high = selling pressure)
    - candle_lower_wick_ratio: Lower wick / range (high = buying pressure)
    - engulfing_signal: Bullish/bearish engulfing pattern
    - hammer_signal: Hammer / inverted hammer
    """
    p = prefix
    o = df.get(f"{p}open", df[f"{p}close"])
    h = df.get(f"{p}high", df[f"{p}close"])
    l = df.get(f"{p}low", df[f"{p}close"])
    c = df[f"{p}close"]

    body = (c - o).abs()
    total_range = (h - l).clip(lower=0.01)
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l

    df[f"{p}candle_body_ratio"] = body / total_range
    df[f"{p}candle_direction"] = np.sign(c - o)
    df[f"{p}candle_upper_wick_pct"] = upper_wick / total_range
    df[f"{p}candle_lower_wick_pct"] = lower_wick / total_range

    # Engulfing
    prev_body = body.shift(1).fillna(0)
    prev_dir = np.sign(c.shift(1) - o.shift(1))
    curr_dir = np.sign(c - o)
    bull_engulf = (prev_dir < 0) & (curr_dir > 0) & (body > prev_body * 1.2)
    bear_engulf = (prev_dir > 0) & (curr_dir < 0) & (body > prev_body * 1.2)
    eng = pd.Series(0, index=df.index)
    eng[bull_engulf] = 1
    eng[bear_engulf] = -1
    df[f"{p}engulfing_signal"] = eng

    # Hammer: small body at top, long lower wick
    hammer = (lower_wick > body * 2) & (upper_wick < body * 0.5) & (body > 0)
    inv_hammer = (upper_wick > body * 2) & (lower_wick < body * 0.5) & (body > 0)
    ham = pd.Series(0, index=df.index)
    ham[hammer] = 1   # bullish hammer
    ham[inv_hammer] = -1  # bearish shooting star
    df[f"{p}hammer_signal"] = ham

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER FUNCTION: Compute ALL behavioral features for a single timeframe
# ═══════════════════════════════════════════════════════════════════════════════

def compute_all_behavioral_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Compute all behavioral features for a single timeframe DataFrame.

    Args:
        df: DataFrame with indicator columns (from add_indicators)
        prefix: Column prefix for this timeframe (e.g., "h1_", "d1_", "" for base TF)

    Returns:
        DataFrame with ~15-20 new behavioral feature columns per indicator
    """
    df = supertrend_features(df, prefix)
    df = ema_features(df, prefix)
    df = stochrsi_features(df, prefix)
    df = adx_features(df, prefix)
    df = macd_features(df, prefix)
    df = stoch_features(df, prefix)
    df = rsi_features(df, prefix)
    df = fibonacci_features(df, prefix)
    df = volume_features(df, prefix)
    df = sr_features(df, prefix)
    df = candle_pattern_features(df, prefix)
    return df


def compute_merged_behavioral_features(merged_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute behavioral features on the merged multi-timeframe DataFrame.
    This is called after asof_merge has joined all timeframes onto the 5min base.

    For each timeframe prefix (w_, d1_, h1_, m15_, "" for 5min),
    compute behavioral features on the available columns.
    """
    # Base timeframe (5min) — has unprefixed columns
    merged_df = compute_all_behavioral_features(merged_df, prefix="")

    # Higher timeframe columns are prefixed (d1_, h1_, m15_)
    # They don't have OHLCV (only merged indicator columns), so we compute
    # only the indicator-based features
    for tf_prefix in ["d1_", "h1_", "m15_"]:
        # Check if this TF's columns exist
        if f"{tf_prefix}close" in merged_df.columns:
            # SuperTrend features (if available)
            if f"{tf_prefix}supertrend_dir" in merged_df.columns:
                merged_df = supertrend_features(merged_df, tf_prefix)
            # EMA features
            if f"{tf_prefix}ema7" in merged_df.columns or f"{tf_prefix}ema_cross" in merged_df.columns:
                if f"{tf_prefix}ema7" not in merged_df.columns:
                    # Some TFs only have ema_cross, not raw ema values
                    # Skip full EMA features, just record the cross
                    pass
                else:
                    merged_df = ema_features(merged_df, tf_prefix)
            # StochRSI features
            if f"{tf_prefix}stochrsi_k" in merged_df.columns:
                merged_df = stochrsi_features(merged_df, tf_prefix)
            # ADX features
            if f"{tf_prefix}adx" in merged_df.columns:
                merged_df = adx_features(merged_df, tf_prefix)
            # MACD features
            if f"{tf_prefix}macd" in merged_df.columns:
                merged_df = macd_features(merged_df, tf_prefix)

    # Weekly features (if available)
    if "w_close" in merged_df.columns:
        for feat_fn in [supertrend_features, ema_features, adx_features, macd_features]:
            if f"w_supertrend_dir" in merged_df.columns or f"w_adx" in merged_df.columns:
                try:
                    merged_df = feat_fn(merged_df, "w_")
                except (KeyError, ValueError):
                    pass

    # Cross-timeframe alignment features
    merged_df = alignment_features(merged_df)

    return merged_df.copy()
