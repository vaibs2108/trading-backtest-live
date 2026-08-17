"""
custom_bn_for_live2c.py — StrategyKernel wrapper around strategy_bn_for_live2c.py
(Ichimoku + Trend Confirmation strategy) for the Backtest page.
"""
import sys, os
import pandas as pd
import numpy as np
from strategy_kernel import StrategyKernel, SignalEvent

# Add root directory to sys.path to import strategy_bn_for_live2c
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import strategy_bn_for_live2c as user_strat


class BNForLive2CIchimokuTrend(StrategyKernel):
    strategy_id = "custom_bn_for_live2c"
    display_name = "BN For Live 2C — Ichimoku + Trend (Optimized)"
    live_capable = False

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df_out, long_entries, long_exits, short_entries, short_exits = user_strat.apply_strategy_ichimoku_trend_optimized(df)
        df_out['long_entry'] = long_entries.to_numpy()
        df_out['long_exit'] = long_exits.to_numpy()
        df_out['short_entry'] = short_entries.to_numpy()
        df_out['short_exit'] = short_exits.to_numpy()
        return df_out

    def run_backtest(self, frames: dict, initial_capital=500000, lot_size=15, lot_multiplier=1,
                     start_date=None, end_date=None) -> dict:
        df = frames.get('5') if '5' in frames else (frames.get('data') if 'data' in frames else list(frames.values())[0])
        df = self.compute_indicators(df.copy())

        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if start_date:
            df = df[df['timestamp'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['timestamp'] <= pd.to_datetime(end_date)]
        df = df.reset_index(drop=True)

        trades = []
        open_trade = None

        for idx in range(len(df)):
            row = df.iloc[idx]
            bar_ts = str(row['timestamp'])
            close_px = float(row['close'])

            # Check exit first
            if open_trade is not None:
                should_exit = (
                    (open_trade['direction'] == 'LONG' and bool(row.get('long_exit', False))) or
                    (open_trade['direction'] == 'SHORT' and bool(row.get('short_exit', False)))
                )
                if should_exit:
                    pnl = (close_px - open_trade['entry_price']) if open_trade['direction'] == 'LONG' \
                        else (open_trade['entry_price'] - close_px)
                    trades.append({
                        'entry_time': open_trade['entry_time'],
                        'exit_time': bar_ts,
                        'direction': open_trade['direction'],
                        'entry_price': open_trade['entry_price'],
                        'exit_price': close_px,
                        'pnl': pnl,
                        'exit_reason': 'Signal Exit'
                    })
                    open_trade = None

            if open_trade is None:
                if bool(row.get('long_entry', False)):
                    open_trade = {'entry_time': bar_ts, 'entry_price': close_px, 'direction': 'LONG'}
                elif bool(row.get('short_entry', False)):
                    open_trade = {'entry_time': bar_ts, 'entry_price': close_px, 'direction': 'SHORT'}

        if open_trade is not None:
            trades.append({
                'entry_time': open_trade['entry_time'],
                'exit_time': None,
                'direction': open_trade['direction'],
                'entry_price': open_trade['entry_price'],
                'exit_price': None,
                'pnl': None,
                'exit_reason': 'Open Position'
            })

        return {'trades': trades}
