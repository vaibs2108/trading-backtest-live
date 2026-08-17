import pandas as pd
import numpy as np
from strategy_kernel import StrategyKernel, SignalEvent

class BankNiftyStrategy(StrategyKernel):
    strategy_id = 'banknifty_strategy'
    display_name = 'Bank Nifty 5-Minute EMA and RSI Strategy'
    live_capable = False

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # Ensure timestamp is in datetime format
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Calculate indicators
        df.ta.ema(length=9, append=True)  # EMA_9
        df.ta.ema(length=21, append=True)  # EMA_21
        df.ta.rsi(length=14, append=True)  # RSI_14
        df.ta.atr(length=14, append=True)  # ATRr_14

        # Fill NaNs
        df = df.bfill().fillna(0.0)

        # Define conditions for long and short
        df['long_cond'] = (df['EMA_9'] > df['EMA_21']) & (df['RSI_14'] > 50)
        df['short_cond'] = (df['EMA_9'] < df['EMA_21']) & (df['RSI_14'] < 50)

        return df

    def run_backtest(self, frames: dict, initial_capital=500000, lot_size=15, lot_multiplier=1, start_date=None, end_date=None) -> dict:
        df = frames.get('5') if '5' in frames else (frames.get('data') if 'data' in frames else list(frames.values())[0])
        df = self.compute_indicators(df.copy())

        # Respect start_date and end_date
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
            bar_ts = pd.to_datetime(row['timestamp'])

            # Check for long entry
            if open_trade is None and bool(row['long_cond']):
                entry_price = float(row['close'])
                stop_loss = entry_price - (1.5 * float(row['ATRr_14']))
                target_price = entry_price + (3 * float(row['ATRr_14']))
                open_trade = {
                    'entry_time': str(bar_ts),
                    'entry_price': entry_price,
                    'direction': 'LONG',
                    'stop_loss': stop_loss,
                    'target_price': target_price
                }

            # Check for short entry
            elif open_trade is None and bool(row['short_cond']):
                entry_price = float(row['close'])
                stop_loss = entry_price + (1.5 * float(row['ATRr_14']))
                target_price = entry_price - (3 * float(row['ATRr_14']))
                open_trade = {
                    'entry_time': str(bar_ts),
                    'entry_price': entry_price,
                    'direction': 'SHORT',
                    'stop_loss': stop_loss,
                    'target_price': target_price
                }

            # Check for exit conditions
            if open_trade is not None:
                if (open_trade['direction'] == 'LONG' and (float(row['close']) <= open_trade['stop_loss'] or float(row['close']) >= open_trade['target_price'])):
                    exit_price = float(row['close'])
                    pnl = exit_price - open_trade['entry_price']
                    trades.append({
                        'entry_time': open_trade['entry_time'],
                        'exit_time': str(bar_ts),
                        'direction': 'LONG',
                        'entry_price': open_trade['entry_price'],
                        'exit_price': exit_price,
                        'pnl': pnl,
                        'exit_reason': 'Stop Loss' if exit_price <= open_trade['stop_loss'] else 'Target Hit'
                    })
                    open_trade = None

                elif (open_trade['direction'] == 'SHORT' and (float(row['close']) >= open_trade['stop_loss'] or float(row['close']) <= open_trade['target_price'])):
                    exit_price = float(row['close'])
                    pnl = open_trade['entry_price'] - exit_price
                    trades.append({
                        'entry_time': open_trade['entry_time'],
                        'exit_time': str(bar_ts),
                        'direction': 'SHORT',
                        'entry_price': open_trade['entry_price'],
                        'exit_price': exit_price,
                        'pnl': pnl,
                        'exit_reason': 'Stop Loss' if exit_price >= open_trade['stop_loss'] else 'Target Hit'
                    })
                    open_trade = None

        # If there's an open trade at the end of the data
        if open_trade is not None:
            trades.append({
                'entry_time': open_trade['entry_time'],
                'exit_time': None,
                'direction': open_trade['direction'],
                'entry_price': open_trade['entry_price'],
                'exit_price': None,
                'pnl': None,
                'exit_reason': 'Open Trade'
            })

        return {'trades': trades}