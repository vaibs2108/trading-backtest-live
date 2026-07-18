"""
supertrendy_strategy.py — SuperTrendy Adaptive SuperTrend Strategy.

Constructed for BACKTESTING ONLY. Do not use for live trading.
Calculates Kaufman Efficiency Ratio (ER), Volatility Regime, Adaptive Factor,
Confirmation filter, and simulates SuperTrend trend-following trades.
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional
from datetime import datetime, timedelta

import strategy as main_strategy
from config import get_settings, INSTRUMENT_META

logger = logging.getLogger(__name__)

# Re-export base functions if needed
add_indicators_base = main_strategy.add_indicators

def _session_mask(base: pd.DataFrame, cfg) -> pd.Series:
    """Session mask for trading hours."""
    exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
    mins = base["_hour"] * 60 + base["_minute"]
    if exch == "MCX":
        start, end_excl = 9 * 60, 23 * 60 + 30
    else:
        start, end_excl = 9 * 60 + 20, 15 * 60 + 20
    return (mins >= start) & (mins < end_excl)

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add SuperTrendy indicators to the DataFrame."""
    df = df.copy()
    
    i_len = 10
    i_factor = 4.0
    i_er_len = 10
    i_confirm = 1

    # ── ATR ──
    high = df["high"]
    low = df["low"]
    close = df["close"]
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=i_len).mean().bfill()

    # ── Kaufman Efficiency Ratio (ER) ──
    net_move = (close - close.shift(i_er_len)).abs()
    total_path = (close - close.shift(1)).abs().rolling(window=i_er_len).sum()
    df["er"] = np.where(total_path > 0, net_move / total_path, 0.0)
    df["er_smooth"] = df["er"].ewm(span=3, adjust=False).mean()

    # ── Volatility Regime ──
    atr_baseline = df["atr"].rolling(window=50).mean().bfill()
    vol_ratio = np.where(atr_baseline > 0, df["atr"] / atr_baseline, 1.0)
    df["vol_ratio"] = vol_ratio
    vol_regime = np.clip(vol_ratio, 0.75, 1.40)
    df["vol_regime"] = vol_regime

    # ── Adaptive Factor ──
    df["adaptive_factor"] = i_factor * vol_regime

    # ── ER-Driven Confirmation ──
    er_smooth = df["er_smooth"].values
    conditions = [
        er_smooth < 0.25,
        er_smooth < 0.45,
        er_smooth < 0.65
    ]
    choices = [
        max(i_confirm + 2, 4),
        i_confirm + 1,
        i_confirm,
    ]
    df["adaptive_confirm"] = np.select(conditions, choices, default=max(i_confirm - 1, 1))

    # ── Flip Quality Score ──
    price_mom_pct = ((close / close.shift(5) - 1) * 100).abs().fillna(0)
    atr_pct = np.where(close > 0, (df["atr"] / close) * 100, 0.0)
    mom_norm = np.where(atr_pct > 0, np.minimum(price_mom_pct / (atr_pct * 2), 1.0), 0.0)
    vol_score = np.maximum(1.0 - np.abs(1.0 - vol_ratio), 0.0)
    
    quality_raw = df["er_smooth"] * 0.50 + vol_score * 0.30 + mom_norm * 0.20
    df["quality_pct"] = np.round(quality_raw * 100).astype(int)
    
    # ── ATR Percentile Rank ──
    df["atr_pctrank"] = df["atr"].rolling(100).rank(pct=True) * 100
    df["atr_pctrank"] = df["atr_pctrank"].fillna(50)

    # ── Manual SuperTrend ──
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + df["adaptive_factor"] * df["atr"]
    lower_basic = hl2 - df["adaptive_factor"] * df["atr"]

    n = len(df)
    upper_band = np.zeros(n)
    lower_band = np.zeros(n)
    trend_dir = np.ones(n, dtype=int)
    st = np.zeros(n)

    close_arr = close.values
    upper_basic_arr = upper_basic.values
    lower_basic_arr = lower_basic.values

    upper_band[0] = upper_basic_arr[0]
    lower_band[0] = lower_basic_arr[0]
    trend_dir[0] = 1
    st[0] = lower_band[0]

    for i in range(1, n):
        prev_upper = upper_band[i - 1]
        prev_lower = lower_band[i - 1]
        prev_close = close_arr[i - 1]
        curr_close = close_arr[i]

        if upper_basic_arr[i] < prev_upper or prev_close > prev_upper:
            upper_band[i] = upper_basic_arr[i]
        else:
            upper_band[i] = prev_upper

        if lower_basic_arr[i] > prev_lower or prev_close < prev_lower:
            lower_band[i] = lower_basic_arr[i]
        else:
            lower_band[i] = prev_lower

        prev_dir = trend_dir[i - 1]
        if prev_dir == 1:
            trend_dir[i] = -1 if curr_close < lower_band[i] else 1
        else:
            trend_dir[i] = 1 if curr_close > upper_band[i] else -1

        st[i] = lower_band[i] if trend_dir[i] == 1 else upper_band[i]

    df["st"] = st
    df["trend_dir"] = trend_dir

    # ── Confirmation Filter ──
    adaptive_confirm_arr = df["adaptive_confirm"].values
    locked_bull = trend_dir[0] == 1
    streak = 0
    flip_up = np.zeros(n, dtype=bool)
    flip_down = np.zeros(n, dtype=bool)
    any_flip = np.zeros(n, dtype=bool)

    for i in range(1, n):
        curr_bull = trend_dir[i] == 1
        if curr_bull == locked_bull:
            streak = 0
        else:
            streak += 1

        conf_threshold = adaptive_confirm_arr[i]
        f_up = streak >= conf_threshold and curr_bull
        f_down = streak >= conf_threshold and not curr_bull

        if f_up or f_down:
            locked_bull = curr_bull
            any_flip[i] = True
            flip_up[i] = f_up
            flip_down[i] = f_down

    df["flip_up"] = flip_up
    df["flip_down"] = flip_down
    df["any_flip"] = any_flip

    return df

def get_current_signal(frames: dict, position: str = "NONE") -> dict:
    """SuperTrendy is backtest-only. Returns HOLD for live signals."""
    return {"signal": "HOLD", "reason": "SuperTrendy is backtest-only"}

def run_backtest(frames: dict, initial_capital: float = 500_000,
                 lot_size: int = 15, lot_multiplier: int = 1,
                 start_date: Optional[str] = None,
                 end_date: Optional[str] = None) -> dict:
    """Run backtest utilizing the SuperTrendy confirmation indicators."""
    cfg = get_settings()
    qty = lot_size * lot_multiplier

    base = main_strategy.build_merged_table(frames, with_patterns=False)
    if base.empty:
        return {"error": "No data after merging", "trades": [], "stats": {}}

    # Add indicators
    base = add_indicators(base)

    base = base[_session_mask(base, cfg)].copy().reset_index(drop=True)
    if base.empty:
        return {"error": "No data in trading window", "trades": [], "stats": {}}

    if start_date:
        base = base[base["timestamp"] >= pd.to_datetime(start_date)].copy().reset_index(drop=True)
    if end_date:
        base = base[base["timestamp"].dt.date <= pd.to_datetime(end_date).date()].copy().reset_index(drop=True)

    if base.empty:
        return {"error": "No data in selected date range", "trades": [], "stats": {}}

    trades = []
    position = "NONE"
    entry_price = sl = 0.0
    entry_idx = 0
    entry_atr = 0.0
    
    # Rank 1 Optimal Parameters
    sl_atr_mult = 1.5
    use_eod_exit = False

    n = len(base)
    for i in range(1, n):
        row = base.iloc[i]
        curr_close = float(row["close"])
        bh = float(row.get("high", curr_close))
        bl = float(row.get("low", curr_close))
        ts = row["timestamp"]
        curr_st = float(row["st"])
        curr_atr = float(row["atr"])
        
        # Check exits
        if position != "NONE":
            exit_trade = False
            exit_price = curr_close
            reason = "SIG_EXIT"
            
            if position == "LONG":
                # Check 1.5x ATR SL
                atr_sl = entry_price - sl_atr_mult * entry_atr
                if curr_close < curr_st or bl < curr_st:
                    exit_trade = True
                    exit_price = min(curr_close, curr_st)
                    reason = "STOP_LOSS_ST"
                elif bl < atr_sl:
                    exit_trade = True
                    exit_price = atr_sl
                    reason = "STOP_LOSS_ATR"
                elif row["flip_down"]:
                    exit_trade = True
                    exit_price = curr_close
                    reason = "OPPOSITE_SIGNAL"
            elif position == "SHORT":
                # Check 1.5x ATR SL
                atr_sl = entry_price + sl_atr_mult * entry_atr
                if curr_close > curr_st or bh > curr_st:
                    exit_trade = True
                    exit_price = max(curr_close, curr_st)
                    reason = "STOP_LOSS_ST"
                elif bh > atr_sl:
                    exit_trade = True
                    exit_price = atr_sl
                    reason = "STOP_LOSS_ATR"
                elif row["flip_up"]:
                    exit_trade = True
                    exit_price = curr_close
                    reason = "OPPOSITE_SIGNAL"
            
            # Intraday EOD exit (only if enabled)
            if use_eod_exit:
                hour = int(ts.hour if hasattr(ts, "hour") else pd.to_datetime(ts).hour)
                minute = int(ts.minute if hasattr(ts, "minute") else pd.to_datetime(ts).minute)
                if hour * 60 + minute >= 15 * 60 + 10:
                    exit_trade = True
                    exit_price = curr_close
                    reason = "EOD_EXIT"
                
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
                position = "NONE"

        # Check entries
        if position == "NONE":
            hour = int(ts.hour if hasattr(ts, "hour") else pd.to_datetime(ts).hour)
            minute = int(ts.minute if hasattr(ts, "minute") else pd.to_datetime(ts).minute)
            
            can_enter = True
            if use_eod_exit and hour * 60 + minute >= 15 * 60:
                can_enter = False
                
            if can_enter:
                if row["flip_up"]:
                    if i + 1 < n:
                        entry_price = float(base.iloc[i + 1].get("open", curr_close))
                    else:
                        entry_price = curr_close
                    position = "LONG"
                    entry_idx = i
                    entry_atr = curr_atr
                elif row["flip_down"]:
                    if i + 1 < n:
                        entry_price = float(base.iloc[i + 1].get("open", curr_close))
                    else:
                        entry_price = curr_close
                    position = "SHORT"
                    entry_idx = i
                    entry_atr = curr_atr

    # Compute stats
    tdf = pd.DataFrame(trades)
    if tdf.empty:
        return {"error": "No trades generated", "trades": [], "stats": {}, "equity_curve": []}

    total = int(len(tdf))
    wins = int((tdf["pnl"] > 0).sum())
    losses = int(total - wins)
    win_rate = float(round((wins / total) * 100, 1)) if total else 0.0
    
    total_pnl = float(tdf["pnl"].sum())
    gross_profits = float(tdf[tdf["pnl"] > 0]["pnl"].sum())
    gross_losses = float(abs(tdf[tdf["pnl"] < 0]["pnl"].sum()))
    profit_factor = float(round(gross_profits / gross_losses, 2)) if gross_losses > 0 else (99.9 if gross_profits > 0 else 1.0)
    
    avg_win = float(round(tdf[tdf["pnl"] > 0]["pnl"].mean(), 2)) if wins > 0 else 0.0
    avg_loss = float(round(tdf[tdf["pnl"] < 0]["pnl"].mean(), 2)) if losses > 0 else 0.0
    
    equity = initial_capital + tdf["pnl"].cumsum()
    max_dd_pct = float(round(((equity - equity.cummax()) / equity.cummax() * 100).min(), 2))
    
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
    for idx, r in tdf.iterrows():
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
