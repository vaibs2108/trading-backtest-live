"""
trend_reversal_strategy.py — Regression Trend Reversal Strategy.

Port of AlgoAlpha's "Regression Trend Reversal Signals & Forecasts" PineScript.
Implements 6 regression methods with adaptive bands and candlestick reversal signals.

Constructed for BACKTESTING ONLY.
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional

import strategy as main_strategy
from config import get_settings, INSTRUMENT_META

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════════
#  Regression fitting methods
# ════════════════════════════════════════════════════════════════════════════════

def _linear_regression(y: np.ndarray) -> tuple:
    """OLS linear regression. Returns (fitted_value_at_end, slope)."""
    n = len(y)
    x = np.arange(n, dtype=float)
    sx = x.sum()
    sy = y.sum()
    sxy = (x * y).sum()
    sx2 = (x * x).sum()
    denom = n * sx2 - sx * sx
    if abs(denom) < 1e-12:
        return float(y[-1]), 0.0
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    reg = slope * (n - 1) + intercept
    return float(reg), float(slope)


def _theil_sen(y: np.ndarray) -> tuple:
    """Theil-Sen robust regression (median of pairwise slopes)."""
    n = len(y)
    # Sub-sample for performance on large windows
    if n > 80:
        idx = np.linspace(0, n - 1, 80, dtype=int)
        y_sub = y[idx]
        x_sub = idx.astype(float)
    else:
        y_sub = y
        x_sub = np.arange(n, dtype=float)
    ns = len(y_sub)
    slopes = []
    for i in range(ns - 1):
        for j in range(i + 1, ns):
            dx = x_sub[j] - x_sub[i]
            if abs(dx) > 1e-12:
                slopes.append((y_sub[j] - y_sub[i]) / dx)
    if not slopes:
        return float(y[-1]), 0.0
    slope = float(np.median(slopes))
    x_full = np.arange(n, dtype=float)
    intercepts = y - slope * x_full
    intercept = float(np.median(intercepts))
    reg = slope * (n - 1) + intercept
    return float(reg), float(slope)


def _loess(y: np.ndarray, bandwidth: float) -> tuple:
    """Local linear regression (LOESS) with Gaussian kernel."""
    n = len(y)
    x = np.arange(n, dtype=float)
    # Weights centred on the last bar (i=0 in Pine = most recent)
    dist = np.arange(n - 1, -1, -1, dtype=float)  # [n-1, n-2, ..., 0]
    w = np.exp(-0.5 * (dist / max(bandwidth, 0.5)) ** 2)
    sw = w.sum()
    swx = (w * x).sum()
    swy = (w * y).sum()
    swxx = (w * x * x).sum()
    swxy = (w * x * y).sum()
    denom = sw * swxx - swx * swx
    if abs(denom) < 1e-12:
        return float(y[-1]), 0.0
    slope = (sw * swxy - swx * swy) / denom
    intercept = (swy - slope * swx) / sw
    reg = slope * (n - 1) + intercept
    return float(reg), float(slope)


def _nadaraya_watson(y: np.ndarray, bandwidth: float) -> tuple:
    """Nadaraya-Watson kernel regression (Gaussian)."""
    n = len(y)
    dist = np.arange(n - 1, -1, -1, dtype=float)
    w = np.exp(-0.5 * (dist / max(bandwidth, 0.5)) ** 2)
    den = w.sum()
    if den < 1e-12:
        return float(y[-1]), 0.0
    reg = float((w * y).sum() / den)
    return reg, 0.0  # NW has no native slope


def _polynomial(y: np.ndarray, degree: int) -> tuple:
    """Polynomial regression (degree 1-3)."""
    n = len(y)
    if n <= degree:
        return float(y[-1]), 0.0
    # Normalise x to [-1, 1] for numerical stability
    t = np.linspace(-1, 1, n)
    try:
        coeffs = np.polyfit(t, y, degree)
        poly = np.poly1d(coeffs)
        reg = float(poly(1.0))  # value at the rightmost point
        dpoly = poly.deriv()
        slope = float(dpoly(1.0)) * 2.0 / (n - 1)  # scale back
    except np.linalg.LinAlgError:
        return float(y[-1]), 0.0
    return reg, slope


class _KalmanState:
    """Persistent Kalman filter state across bars."""
    __slots__ = ("level", "trend", "P00", "P01", "P11", "initialised")

    def __init__(self):
        self.level = 0.0
        self.trend = 0.0
        self.P00 = 1.0
        self.P01 = 0.0
        self.P11 = 1.0
        self.initialised = False


def _kalman_step(state: _KalmanState, price: float,
                 q: float = 0.01, r: float = 1.0) -> tuple:
    """One Kalman filter update. Returns (level, trend)."""
    if not state.initialised:
        state.level = price
        state.trend = 0.0
        state.initialised = True
        return price, 0.0

    l_pred = state.level + state.trend
    b_pred = state.trend
    p00 = state.P00 + 2.0 * state.P01 + state.P11 + q
    p01 = state.P01 + state.P11
    p11 = state.P11 + q
    yk = price - l_pred
    S = p00 + r
    K0 = p00 / S if abs(S) > 1e-12 else 0.0
    K1 = p01 / S if abs(S) > 1e-12 else 0.0
    state.level = l_pred + K0 * yk
    state.trend = b_pred + K1 * yk
    state.P00 = (1.0 - K0) * p00
    state.P01 = (1.0 - K0) * p01
    state.P11 = p11 - K1 * p01
    return state.level, state.trend


# ════════════════════════════════════════════════════════════════════════════════
#  Indicator computation (vectorised where possible)
# ════════════════════════════════════════════════════════════════════════════════

def add_indicators(df: pd.DataFrame, method: str = "Linear",
                   window: int = 50, smoothness: float = 30.0,
                   degree: int = 2, k_process: float = 0.01,
                   k_measure: float = 1.0) -> pd.DataFrame:
    """Compute regression line, bands, and reversal signals."""
    df = df.copy()
    close = df["close"].values.astype(float)
    n = len(df)

    # Bandwidth for LOESS / NW
    sm = smoothness / 100.0
    bw = max(0.5, sm * window)

    # ── Compute regression value and slope for each bar ──
    reg_vals = np.full(n, np.nan)
    slp_vals = np.full(n, np.nan)

    if method == "Kalman":
        ks = _KalmanState()
        for i in range(n):
            lev, trend = _kalman_step(ks, close[i], k_process, k_measure)
            reg_vals[i] = lev
            slp_vals[i] = trend
    else:
        fit_fn = {
            "Linear": lambda y: _linear_regression(y),
            "Theil-Sen": lambda y: _theil_sen(y),
            "LOESS": lambda y: _loess(y, bw),
            "Polynomial": lambda y: _polynomial(y, degree),
            "Nadaraya-Watson": lambda y: _nadaraya_watson(y, bw),
        }.get(method, lambda y: _linear_regression(y))

        for i in range(window - 1, n):
            seg = close[i - window + 1: i + 1]
            val, slope = fit_fn(seg)
            reg_vals[i] = val
            slp_vals[i] = slope

    df["reg"] = reg_vals
    df["reg_slope"] = slp_vals

    # ── Bands (stdev of residuals, smoothed with WMA) ──
    band_len = 50
    band_mult = 2.0
    residual = close - reg_vals
    band_dev = pd.Series(residual).rolling(band_len, min_periods=1).std().values

    raw_upper = reg_vals + band_mult * band_dev
    raw_lower = reg_vals - band_mult * band_dev

    # WMA(7) smoothing on bands
    wma_w = np.arange(1, 8, dtype=float)
    wma_w = wma_w / wma_w.sum()

    def _wma7(arr):
        out = np.full_like(arr, np.nan)
        for i in range(6, len(arr)):
            seg = arr[i - 6: i + 1]
            if not np.any(np.isnan(seg)):
                out[i] = (seg * wma_w).sum()
        return out

    df["upper"] = _wma7(raw_upper)
    df["lower"] = _wma7(raw_lower)

    # ── Direction ──
    reg_s = pd.Series(reg_vals)
    df["reg_up"] = (reg_s > reg_s.shift(3)).values

    # ── Reversal signals ──
    o = df["open"].values.astype(float)
    c = close
    upper = df["upper"].values
    lower = df["lower"].values

    top_sig = np.zeros(n, dtype=bool)
    bot_sig = np.zeros(n, dtype=bool)
    for i in range(1, n):
        # Bearish reversal: close > upper, bearish candle, prev bullish above upper
        if (c[i] > upper[i] and c[i] < o[i]
                and c[i - 1] > o[i - 1] and c[i - 1] > upper[i - 1]):
            top_sig[i] = True
        # Bullish reversal: close < lower, bullish candle, prev bearish below lower
        if (c[i] < lower[i] and c[i] > o[i]
                and c[i - 1] < o[i - 1] and c[i - 1] < lower[i - 1]):
            bot_sig[i] = True

    df["top_sig"] = top_sig  # Bearish reversal
    df["bot_sig"] = bot_sig  # Bullish reversal

    return df


# ════════════════════════════════════════════════════════════════════════════════
#  Live signal (backtest-only placeholder)
# ════════════════════════════════════════════════════════════════════════════════

def get_current_signal(frames: dict, position: str = "NONE") -> dict:
    """Trend Reversal is backtest-only. Returns HOLD for live signals."""
    return {"signal": "HOLD", "reason": "Trend Reversal is backtest-only"}


# ════════════════════════════════════════════════════════════════════════════════
#  Backtest engine
# ════════════════════════════════════════════════════════════════════════════════

def _session_mask(base: pd.DataFrame, cfg) -> pd.Series:
    """Session mask for trading hours."""
    exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
    mins = base["_hour"] * 60 + base["_minute"]
    if exch == "MCX":
        start, end_excl = 9 * 60, 23 * 60 + 30
    else:
        start, end_excl = 9 * 60 + 20, 15 * 60 + 20
    return (mins >= start) & (mins < end_excl)


def run_backtest(frames: dict, initial_capital: float = 500_000,
                 lot_size: int = 15, lot_multiplier: int = 1,
                 start_date: Optional[str] = None,
                 end_date: Optional[str] = None,
                 method: str = "Linear",
                 window: int = 50,
                 smoothness: float = 30.0,
                 degree: int = 2) -> dict:
    """Run backtest using Regression Trend Reversal signals."""
    cfg = get_settings()
    qty = lot_size * lot_multiplier

    base = main_strategy.build_merged_table(frames, with_patterns=False)
    if base.empty:
        return {"error": "No data after merging", "trades": [], "stats": {}}

    # Add indicators
    base = add_indicators(base, method=method, window=window,
                          smoothness=smoothness, degree=degree)

    base = base[_session_mask(base, cfg)].copy().reset_index(drop=True)
    if base.empty:
        return {"error": "No data in trading window", "trades": [], "stats": {}}

    if start_date:
        base = base[base["timestamp"] >= pd.to_datetime(start_date)].copy().reset_index(drop=True)
    if end_date:
        base = base[base["timestamp"].dt.date <= pd.to_datetime(end_date).date()].copy().reset_index(drop=True)

    if base.empty:
        return {"error": "No data in selected date range", "trades": [], "stats": {}}

    # ── ATR for fallback SL ──
    high = base["high"].values.astype(float)
    low = base["low"].values.astype(float)
    close_arr = base["close"].values.astype(float)
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - np.roll(close_arr, 1)),
                               np.abs(low - np.roll(close_arr, 1))))
    tr[0] = high[0] - low[0]
    atr_arr = pd.Series(tr).rolling(14, min_periods=1).mean().values

    # ── Trade simulation ──
    trades = []
    position = "NONE"
    entry_price = sl = 0.0
    entry_idx = 0

    top_sig = base["top_sig"].values
    bot_sig = base["bot_sig"].values
    upper = base["upper"].values
    lower = base["lower"].values
    open_arr = base["open"].values.astype(float)

    n = len(base)
    for i in range(1, n):
        curr_close = close_arr[i]
        bh = high[i]
        bl = low[i]
        ts = base.iloc[i]["timestamp"]

        # ── Check exits ──
        if position != "NONE":
            exit_trade = False
            exit_price = curr_close
            reason = ""

            # SL check (band-based)
            if position == "LONG":
                if bl <= sl:
                    exit_trade = True
                    exit_price = sl
                    reason = "SL_HIT"
                elif top_sig[i]:
                    # Bearish reversal → exit LONG
                    exit_trade = True
                    exit_price = curr_close
                    reason = "BEARISH_REVERSAL"
            elif position == "SHORT":
                if bh >= sl:
                    exit_trade = True
                    exit_price = sl
                    reason = "SL_HIT"
                elif bot_sig[i]:
                    # Bullish reversal → exit SHORT
                    exit_trade = True
                    exit_price = curr_close
                    reason = "BULLISH_REVERSAL"

            if exit_trade:
                if position == "LONG":
                    net = (exit_price - entry_price) * qty
                else:
                    net = (entry_price - exit_price) * qty

                trades.append({
                    "entry_time": pd.to_datetime(base.iloc[entry_idx]["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_time": pd.to_datetime(ts).strftime("%Y-%m-%d %H:%M:%S"),
                    "direction": position,
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "pnl": round(net, 2),
                    "exit_reason": reason
                })

                # Immediate re-entry on reversal signal
                if reason == "BEARISH_REVERSAL":
                    # Bearish reversal → enter SHORT
                    if i + 1 < n:
                        entry_price = float(open_arr[i + 1])
                    else:
                        entry_price = curr_close
                    position = "SHORT"
                    entry_idx = i
                    # SL = upper band (above entry for SHORT)
                    sl = upper[i] if not np.isnan(upper[i]) else entry_price + 1.5 * atr_arr[i]
                    continue
                elif reason == "BULLISH_REVERSAL":
                    # Bullish reversal → enter LONG
                    if i + 1 < n:
                        entry_price = float(open_arr[i + 1])
                    else:
                        entry_price = curr_close
                    position = "LONG"
                    entry_idx = i
                    # SL = lower band (below entry for LONG)
                    sl = lower[i] if not np.isnan(lower[i]) else entry_price - 1.5 * atr_arr[i]
                    continue
                else:
                    position = "NONE"

        # ── Check entries ──
        if position == "NONE":
            if bot_sig[i]:
                # Bullish reversal → enter LONG
                if i + 1 < n:
                    entry_price = float(open_arr[i + 1])
                else:
                    entry_price = curr_close
                position = "LONG"
                entry_idx = i
                sl = lower[i] if not np.isnan(lower[i]) else entry_price - 1.5 * atr_arr[i]

            elif top_sig[i]:
                # Bearish reversal → enter SHORT
                if i + 1 < n:
                    entry_price = float(open_arr[i + 1])
                else:
                    entry_price = curr_close
                position = "SHORT"
                entry_idx = i
                sl = upper[i] if not np.isnan(upper[i]) else entry_price + 1.5 * atr_arr[i]

    # Include open position so chart shows live entry marker
    if position != "NONE":
        trades.append({
            "entry_time": pd.to_datetime(base.iloc[entry_idx]["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": "",
            "direction": position,
            "entry_price": round(entry_price, 2),
            "exit_price": None,
            "pnl": 0,
            "exit_reason": "OPEN",
        })

    # ── Compute stats ──
    tdf = pd.DataFrame(trades)
    if tdf.empty:
        return {"error": "No trades generated", "trades": [], "stats": {}, "equity_curve": []}

    # Filter out OPEN position for stats
    closed = tdf[tdf["exit_reason"] != "OPEN"]
    total = int(len(closed))
    wins = int((closed["pnl"] > 0).sum()) if total > 0 else 0
    losses = int(total - wins)
    win_rate = float(round((wins / total) * 100, 1)) if total else 0.0

    total_pnl = float(closed["pnl"].sum()) if total > 0 else 0.0
    gross_profits = float(closed[closed["pnl"] > 0]["pnl"].sum()) if wins > 0 else 0.0
    gross_losses = float(abs(closed[closed["pnl"] < 0]["pnl"].sum())) if losses > 0 else 0.0
    profit_factor = float(round(gross_profits / gross_losses, 2)) if gross_losses > 0 else (99.9 if gross_profits > 0 else 1.0)

    avg_win = float(round(closed[closed["pnl"] > 0]["pnl"].mean(), 2)) if wins > 0 else 0.0
    avg_loss = float(round(closed[closed["pnl"] < 0]["pnl"].mean(), 2)) if losses > 0 else 0.0

    equity = initial_capital + closed["pnl"].cumsum()
    max_dd_pct = float(round(((equity - equity.cummax()) / equity.cummax() * 100).min(), 2)) if total > 0 else 0.0

    expectancy = float(round(total_pnl / total, 2)) if total else 0.0

    stats = {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "total_pnl": float(round(total_pnl, 2)),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_drawdown_pct": float(abs(max_dd_pct)),
        "expectancy": expectancy
    }

    equity_curve = []
    current_eq = initial_capital
    for _, r in closed.iterrows():
        current_eq += float(r["pnl"])
        equity_curve.append({
            "entry_time": r["entry_time"],
            "equity": float(round(current_eq, 2))
        })

    return {
        "trades": trades,
        "stats": stats,
        "equity_curve": equity_curve
    }
