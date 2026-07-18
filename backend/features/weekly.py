"""
weekly.py — Derive weekly timeframe from daily OHLCV data.
Resamples daily bars into weekly bars and computes indicators.
"""
import pandas as pd
import numpy as np
import pandas_ta as ta


def derive_weekly_from_daily(df_daily: pd.DataFrame) -> pd.DataFrame:
    """
    Convert daily OHLCV data into weekly OHLCV + indicators.
    
    Args:
        df_daily: DataFrame with columns [timestamp, open, high, low, close, volume]
    
    Returns:
        DataFrame with weekly bars + indicators (SuperTrend, EMA, MACD, ADX, etc.)
    """
    df = df_daily.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Resample to weekly (Monday start, Friday close)
    df = df.set_index("timestamp")
    weekly = df.resample("W-FRI").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"]).reset_index()

    weekly = weekly.rename(columns={"timestamp": "timestamp"})
    weekly["tf"] = "W"

    # Add indicators
    weekly = add_weekly_indicators(weekly)

    return weekly


def add_weekly_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the same indicator set on weekly data."""
    # SuperTrend (10, 3)
    st = df.ta.supertrend(length=10, multiplier=3)
    if st is not None and f"SUPERT_10_3" in st.columns:
        df["supertrend"] = st["SUPERT_10_3"]
        df["supertrend_dir"] = st["SUPERTd_10_3"]
    else:
        df["supertrend"] = np.nan
        df["supertrend_dir"] = 0

    # EMA 7 / 21
    df["ema7"] = df.ta.ema(length=7)
    df["ema21"] = df.ta.ema(length=21)
    df["ema_cross"] = np.where(df["ema7"] > df["ema21"], 1, -1)

    # MACD (12, 26, 9)
    macd = df.ta.macd(fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_sig"] = macd["MACDs_12_26_9"]
        df["macd_hist"] = macd["MACDh_12_26_9"]
    else:
        df["macd"] = df["macd_sig"] = df["macd_hist"] = 0.0

    # Stoch RSI
    srsi = df.ta.stochrsi(length=14, rsi_length=14, k=3, d=3)
    if srsi is not None:
        df["stochrsi_k"] = srsi["STOCHRSIk_14_14_3_3"]
        df["stochrsi_d"] = srsi["STOCHRSId_14_14_3_3"]
    else:
        df["stochrsi_k"] = df["stochrsi_d"] = 50.0

    # Stoch (14, 3, 1)
    st2 = df.ta.stoch(k=14, d=3, smooth_k=1)
    if st2 is not None:
        df["stoch_k"] = st2["STOCHk_14_3_1"]
        df["stoch_d"] = st2["STOCHd_14_3_1"]
    else:
        df["stoch_k"] = df["stoch_d"] = 50.0

    # DMI / ADX
    adx = df.ta.adx(length=14)
    if adx is not None:
        df["adx"] = adx["ADX_14"]
        df["dmp"] = adx["DMP_14"]
        df["dmn"] = adx["DMN_14"]
    else:
        df["adx"] = df["dmp"] = df["dmn"] = 0.0

    # ATR
    df["atr"] = df.ta.atr(length=14)

    # RSI
    df["rsi"] = df.ta.rsi(length=14)

    # Fibonacci (rolling swing)
    lookback = 30  # 30 weeks ~ 7 months
    df["swing_high"] = df["high"].rolling(lookback, min_periods=5).max()
    df["swing_low"] = df["low"].rolling(lookback, min_periods=5).min()
    rng = df["swing_high"] - df["swing_low"]
    df["fib_786"] = df["swing_high"] - 0.786 * rng
    df["fib_618"] = df["swing_high"] - 0.618 * rng
    df["fib_500"] = df["swing_high"] - 0.500 * rng
    df["fib_382"] = df["swing_high"] - 0.382 * rng
    df["fib_236"] = df["swing_high"] - 0.236 * rng

    # Pivot S/R
    df["pivot"] = (df["high"].shift(1) + df["low"].shift(1) + df["close"].shift(1)) / 3
    df["res1"] = 2 * df["pivot"] - df["low"].shift(1)
    df["sup1"] = 2 * df["pivot"] - df["high"].shift(1)

    return df
