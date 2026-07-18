"""
kalman_vix_strategy.py — Dynamic India VIX Regime-Switching Kalman Strategy.

Uses India VIX as a macro volatility indicator to switch Kalman parameters:
  - VIX < 11.5% (Chop Regime): Slows down the filter to filter out whipsaws.
  - VIX >= 11.5% (Trend Regime): Speeds up the filter to capture explosive breakouts.
"""
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from typing import Optional
from datetime import datetime, timedelta

import strategy as main_strategy
from config import get_settings, INSTRUMENT_META

logger = logging.getLogger(__name__)

# Re-export
add_indicators = main_strategy.add_indicators

def _session_mask(base: pd.DataFrame, cfg) -> pd.Series:
    exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
    mins = base["_hour"] * 60 + base["_minute"]
    if exch == "MCX":
        start, end_excl = 9 * 60, 23 * 60 + 30
    else:
        start, end_excl = 9 * 60 + 20, 15 * 60 + 20
    return (mins >= start) & (mins < end_excl)

def regime_switching_kalman_vix(prices, vix_series, vix_threshold=11.5,
                                Q_level_chop=1e-4, Q_velocity_chop=1e-6, R_obs_chop=0.5,
                                Q_level_trend=1e-4, Q_velocity_trend=1e-5, R_obs_trend=0.05):
    prices = np.array(prices, dtype=float)
    vix_series = np.array(vix_series, dtype=float)
    n = len(prices)
    
    state = np.array([prices[0], 0.0])
    P = np.eye(2)
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([1.0, 0.0])
    
    level = np.zeros(n)
    velocity = np.zeros(n)
    e = np.zeros(n)
    S = np.zeros(n)
    
    for t in range(n):
        vix_t = vix_series[t]
        if np.isnan(vix_t) or vix_t < vix_threshold:
            Q = np.diag([Q_level_chop, Q_velocity_chop])
            R = float(R_obs_chop)
        else:
            Q = np.diag([Q_level_trend, Q_velocity_trend])
            R = float(R_obs_trend)
            
        state = F @ state
        P = F @ P @ F.T + Q
        e_t = prices[t] - float(H @ state)
        S_t = float(H @ P @ H) + R
        
        e[t] = e_t
        S[t] = S_t
        
        K = (P @ H) / S_t
        state = state + K * e_t
        P = (np.eye(2) - np.outer(K, H)) @ P
        
        level[t] = state[0]
        velocity[t] = state[1]
        
    return level, velocity, e, S

# ── Public API ────────────────────────────────────────────────────────────────

def get_current_signal(frames: dict, position: str = "NONE") -> dict:
    """Compute current signal using India VIX Regime-Switching Kalman Strategy (Backtest Only)."""
    return {"signal": "HOLD", "reason": "Kalman VIX is restricted to backtest-only"}
        
    df_5m = frames["5"]
    prices = df_5m["close"].values
    
    # Fetch real-time VIX from Dhan API (with yfinance fallback)
    current_vix = 15.0
    vix_fetched = False
    try:
        from global_markets import fetch_india_vix
        vix_res = fetch_india_vix()
        if vix_res and vix_res.get("value") is not None:
            current_vix = float(vix_res["value"])
            vix_fetched = True
            logger.info(f"[KalmanVix] Real-time VIX fetched from Dhan API: {current_vix}%")
    except Exception as ex:
        logger.warning(f"Failed to fetch VIX from Dhan API: {ex}. Falling back to yfinance.")
        
    if not vix_fetched:
        try:
            vix_data = yf.Ticker("^INDIAVIX").history(period="1d")
            if not vix_data.empty:
                current_vix = float(vix_data["Close"].iloc[-1])
        except Exception as ex:
            logger.warning(f"Could not download current VIX from yfinance: {ex}. Defaulting to 15.0")
        
    # Run Kalman Filter recursion
    vix_series = np.full(len(prices), current_vix)
    _, velocity, _, _ = regime_switching_kalman_vix(prices, vix_series)
    
    last_vel = velocity[-1]
    
    # Determine signal based on velocity sign
    if last_vel > 0:
        sig = "LONG"
    elif last_vel < 0:
        sig = "SHORT"
    else:
        sig = "HOLD"
        
    # Exit current position if direction changes
    if position == "LONG" and sig == "SHORT":
        action = "SHORT" # Reversal
    elif position == "SHORT" and sig == "LONG":
        action = "LONG" # Reversal
    else:
        action = sig
        
    return {
        "signal": action,
        "sl": 0.0,
        "tp": 0.0,
        "reason": f"VIX Kalman: VIX={current_vix:.2f}%, Velocity={last_vel:.6f}"
    }

def calculate_atr(df, period=14):
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    n = len(df)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    tr_series = pd.Series(tr)
    atr = tr_series.rolling(period).mean().bfill().values
    return atr

def run_backtest(frames: dict, initial_capital: float = 500_000,
                 lot_size: int = 15, lot_multiplier: int = 1,
                 start_date: Optional[str] = None,
                 end_date: Optional[str] = None) -> dict:
    """Run backtest utilizing the VIX dynamic switching parameter Kalman filter with ATR 2.5 Stop Loss."""
    cfg = get_settings()
    qty = lot_size * lot_multiplier
    cost_bps = 1.0 # 1 bp slippage/brokerage
    
    base = main_strategy.build_merged_table(frames, with_patterns=False)
    if base.empty:
        return {"error": "No data after merging", "trades": [], "stats": {}}
        
    base = base[_session_mask(base, cfg)].copy().reset_index(drop=True)
    if base.empty:
        return {"error": "No data in trading window", "trades": [], "stats": {}}
        
    if start_date:
        base = base[base["timestamp"] >= pd.to_datetime(start_date)].copy().reset_index(drop=True)
    if end_date:
        base = base[base["timestamp"].dt.date <= pd.to_datetime(end_date).date()].copy().reset_index(drop=True)
        
    if base.empty:
        return {"error": "No data in selected date range", "trades": [], "stats": {}}
        
    prices = base["close"].values
    timestamps = pd.to_datetime(base["timestamp"])
    
    # Calculate ATR
    atr_series = calculate_atr(base, period=14)
    
    # Fetch historical India VIX for the backtest range (Dhan API with yfinance fallback)
    vix_threshold = 11.5
    start_str = timestamps.min().strftime('%Y-%m-%d')
    end_str = (timestamps.max() + timedelta(days=1)).strftime('%Y-%m-%d')
    
    vix_series = np.full(len(prices), 15.0) # default fallback
    vix_fetched = False
    try:
        import broker
        df_vix = broker.get_historical_data("INDIA VIX", "DAY", start_str, end_str, use_index=True)
        if df_vix is not None and not df_vix.empty:
            df_vix.index = pd.to_datetime(df_vix['date'] if 'date' in df_vix.columns else df_vix.index).normalize()
            base['date_only'] = timestamps.dt.normalize()
            vix_mapping = df_vix['close'].to_dict()
            base['vix'] = base['date_only'].map(vix_mapping)
            base['vix'] = base['vix'].ffill().bfill()
            vix_series = base['vix'].values
            vix_fetched = True
            logger.info("Successfully fetched historical India VIX from Dhan API for backtest")
    except Exception as ex:
        logger.warning(f"Failed to fetch VIX from Dhan API for backtest: {ex}. Falling back to yfinance.")
        
    if not vix_fetched:
        try:
            vix_df = yf.Ticker('^INDIAVIX').history(start=start_str, end=end_str)
            if not vix_df.empty:
                vix_df.index = vix_df.index.tz_localize(None).normalize()
                base['date_only'] = timestamps.dt.normalize()
                vix_mapping = vix_df['Close'].to_dict()
                base['vix'] = base['date_only'].map(vix_mapping)
                base['vix'] = base['vix'].ffill().bfill()
                vix_series = base['vix'].values
        except Exception as ex:
            logger.warning(f"Error fetching VIX from yfinance fallback: {ex}. Using default VIX=15.0")
        
    _, velocity, _, _ = regime_switching_kalman_vix(prices, vix_series, vix_threshold)
    
    # Compute backtest performance with SL logic
    n = len(prices)
    position = 0.0
    trade_start = 0
    stop_loss = 0.0
    trades = []
    
    for i in range(1, n):
        current_price = prices[i]
        current_atr = atr_series[i]
        
        if position == 1.0:
            # Check Stop Loss
            if current_price <= stop_loss:
                points_pnl = stop_loss - prices[trade_start]
                pnl_rupees = points_pnl * qty
                trades.append({
                    "entry_time": timestamps.iloc[trade_start].strftime('%Y-%m-%d %H:%M'),
                    "exit_time": timestamps.iloc[i].strftime('%Y-%m-%d %H:%M'),
                    "direction": "LONG",
                    "entry_price": float(prices[trade_start]),
                    "exit_price": float(stop_loss),
                    "pnl": float(pnl_rupees),
                    "exit_reason": "Stop Loss (ATR 2.5x)"
                })
                position = 0.0
            # Check Kalman Crossover
            elif velocity[i] < 0:
                points_pnl = current_price - prices[trade_start]
                pnl_rupees = points_pnl * qty
                trades.append({
                    "entry_time": timestamps.iloc[trade_start].strftime('%Y-%m-%d %H:%M'),
                    "exit_time": timestamps.iloc[i].strftime('%Y-%m-%d %H:%M'),
                    "direction": "LONG",
                    "entry_price": float(prices[trade_start]),
                    "exit_price": float(current_price),
                    "pnl": float(pnl_rupees),
                    "exit_reason": "Kalman Crossover Reversal"
                })
                # Reversal to SHORT
                position = -1.0
                trade_start = i
                stop_loss = current_price + 2.5 * current_atr
                
        elif position == -1.0:
            # Check Stop Loss
            if current_price >= stop_loss:
                points_pnl = prices[trade_start] - stop_loss
                pnl_rupees = points_pnl * qty
                trades.append({
                    "entry_time": timestamps.iloc[trade_start].strftime('%Y-%m-%d %H:%M'),
                    "exit_time": timestamps.iloc[i].strftime('%Y-%m-%d %H:%M'),
                    "direction": "SHORT",
                    "entry_price": float(prices[trade_start]),
                    "exit_price": float(stop_loss),
                    "pnl": float(pnl_rupees),
                    "exit_reason": "Stop Loss (ATR 2.5x)"
                })
                position = 0.0
            # Check Kalman Crossover
            elif velocity[i] > 0:
                points_pnl = prices[trade_start] - current_price
                pnl_rupees = points_pnl * qty
                trades.append({
                    "entry_time": timestamps.iloc[trade_start].strftime('%Y-%m-%d %H:%M'),
                    "exit_time": timestamps.iloc[i].strftime('%Y-%m-%d %H:%M'),
                    "direction": "SHORT",
                    "entry_price": float(prices[trade_start]),
                    "exit_price": float(current_price),
                    "pnl": float(pnl_rupees),
                    "exit_reason": "Kalman Crossover Reversal"
                })
                # Reversal to LONG
                position = 1.0
                trade_start = i
                stop_loss = current_price - 2.5 * current_atr
                
        else: # Flat
            if velocity[i] > 0:
                position = 1.0
                trade_start = i
                stop_loss = current_price - 2.5 * current_atr
            elif velocity[i] < 0:
                position = -1.0
                trade_start = i
                stop_loss = current_price + 2.5 * current_atr
                
    tdf = pd.DataFrame(trades)
    if tdf.empty:
        return {"error": "No trades generated", "trades": [], "stats": {}}
        
    # Trade-by-trade cash equity calculation to guarantee perfect consistency
    tdf["equity"] = initial_capital + tdf["pnl"].cumsum()
    
    total = len(tdf)
    wins = (tdf["pnl"] > 0).sum()
    gp = tdf[tdf["pnl"] > 0]["pnl"].sum()
    gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
    
    # Calculate drawdown correctly on cash equity curve
    equity_series = pd.Series([initial_capital] + list(tdf["equity"]))
    dd = ((equity_series - equity_series.cummax()) / equity_series.cummax() * 100).min()
    
    # Recalculate Sharpe based on trade-by-trade returns for consistency
    trade_returns = tdf["pnl"] / initial_capital
    std_returns = trade_returns.std()
    sharpe = (trade_returns.mean() / std_returns * np.sqrt(252)) if std_returns > 0 else 0.0
    
    stats = {
        "total_trades": total,
        "wins": int(wins),
        "losses": int(total - wins),
        "win_rate_pct": round(wins / total * 100, 1),
        "profit_factor": round(gp / gl, 2) if gl > 0 else 0,
        "total_pnl": round(tdf["pnl"].sum(), 0),
        "avg_win": round(tdf[tdf["pnl"] > 0]["pnl"].mean(), 0) if wins > 0 else 0,
        "avg_loss": round(tdf[tdf["pnl"] <= 0]["pnl"].mean(), 0) if (total - wins) > 0 else 0,
        "max_drawdown_pct": round(dd, 2),
        "expectancy": round(tdf["pnl"].mean(), 0),
        "final_capital": round(float(tdf["equity"].iloc[-1]), 0),
        "exit_distribution": tdf["exit_reason"].value_counts().to_dict(),
    }
    
    # Construct exact equity_curve formatting
    equity_curve = tdf[["entry_time", "equity", "pnl"]].to_dict(orient="records")
    
    return {
        "stats": stats,
        "trades": tdf.to_dict(orient="records"),
        "equity_curve": equity_curve
    }

