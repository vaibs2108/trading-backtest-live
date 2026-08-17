from strategy_kernel import StrategyKernel, SignalEvent
import pandas as pd
import numpy as np

class NeuralKernelBands(StrategyKernel):
    strategy_id = "neural_kernel_bands"
    display_name = "Neural Kernel Bands [ATR-Trailing v6]"
    live_capable = False

    def __init__(self):
        self.lookback = 20
        self.base_h = 6.0
        self.atr_factor = 2.0
        self.sigma_mult = 2.0
        self.max_trades = 10
        self.atr_period = 14
        self.trail_activation_atr = 1.5
        self.trail_offset_atr = 2.0
        self.closed_trades = 0  # Initialize closed trades count

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Calculate ATR
        df.ta.atr(length=self.atr_period, append=True)  # Adds ATRr_14
        df['norm_atr'] = (df['ATRr_14'] / df['close']) * self.atr_factor
        df['effective_h'] = self.base_h * (1.0 + df['norm_atr'])

        # Initialize state variables
        sum_weights = []
        sum_prices = []
        last_closed_count = 0
        trades_today = 0
        last_state = "Neutral"
        trend_state = "Neutral"

        for idx in range(len(df)):
            # Reset sums for each bar
            sum_w = 0.0
            sum_p = 0.0

            # Calculate kernel weights and sums
            for i in range(self.lookback):
                if idx - i >= 0:
                    u = float(i)
                    kernel_weight = np.exp(-(np.power(u, 2)) / (2 * np.power(df['effective_h'].iloc[idx], 2)))
                    sum_w += kernel_weight
                    sum_p += (df['close'].iloc[idx - i] * kernel_weight)

            sum_weights.append(sum_w)
            sum_prices.append(sum_p)

            # Calculate kernel moving average
            kernel_ma = sum_p / sum_w if sum_w > 0 else df['close'].iloc[idx]

            # Calculate residual and standard deviation
            residual = df['close'].iloc[idx] - kernel_ma
            if idx >= self.lookback - 1:
                df['sigma_dev'] = pd.Series([np.std(residual) for _ in range(len(df))], index=df.index)
            else:
                df['sigma_dev'] = 0  # Default value for the first few rows

            # Calculate upper and lower bands
            df['upper_band'] = kernel_ma + (df['sigma_dev'].iloc[idx] * self.sigma_mult)
            df['lower_band'] = kernel_ma - (df['sigma_dev'].iloc[idx] * self.sigma_mult)

            # Daily trades reset
            if idx > 0 and df['timestamp'].iloc[idx].date() != df['timestamp'].iloc[idx - 1].date():
                trades_today = 0

            # Update trades today count
            if self.closed_trades > last_closed_count:
                trades_today += (self.closed_trades - last_closed_count)
                last_closed_count = self.closed_trades

            # Determine trend state
            if df['close'].iloc[idx] > df['upper_band']:
                trend_state = "Bullish"
            elif df['close'].iloc[idx] < df['lower_band']:
                trend_state = "Bearish"

            # Check for state flips
            state_flipped_bull = (trend_state == "Bullish" and last_state != "Bullish")
            state_flipped_bear = (trend_state == "Bearish" and last_state != "Bearish")

            if state_flipped_bull or state_flipped_bear:
                last_state = trend_state

            # Entry conditions
            if trades_today < self.max_trades:
                if state_flipped_bull:
                    self.closed_trades += 1  # Simulate trade entry
                elif state_flipped_bear:
                    self.closed_trades += 1  # Simulate trade entry

        # Assign calculated columns to DataFrame
        df['kernel_ma'] = pd.Series(sum_weights, index=df.index)
        df['sum_prices'] = pd.Series(sum_prices, index=df.index)
        df['trades_today'] = trades_today
        df['trend_state'] = trend_state

        return df.bfill().fillna(0.0)

    def run_backtest(self, frames: dict, initial_capital=500000, lot_size=15, lot_multiplier=1, start_date=None, end_date=None) -> dict:
        df = frames.get('5') if '5' in frames else (frames.get('data') if 'data' in frames else list(frames.values())[0])
        df = self.compute_indicators(df.copy())

        # Filter by date range
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

            # Check for open trades and manage exits
            if open_trade:
                if open_trade['direction'] == 'LONG':
                    # Manage trailing stop for LONG
                    if row['close'] >= (open_trade['entry_price'] + (self.trail_activation_atr * row['ATRr_14'])):
                        open_trade['trailing_stop'] = max(open_trade.get('trailing_stop', 0), row['close'] - (self.trail_offset_atr * row['ATRr_14']))
                    if row['close'] < open_trade['trailing_stop']:
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(bar_ts),
                            'direction': 'LONG',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': row['close'],
                            'pnl': row['close'] - open_trade['entry_price'],
                            'exit_reason': 'Trailing Stop'
                        })
                        open_trade = None

                elif open_trade['direction'] == 'SHORT':
                    # Manage trailing stop for SHORT
                    if row['close'] <= (open_trade['entry_price'] - (self.trail_activation_atr * row['ATRr_14'])):
                        open_trade['trailing_stop'] = min(open_trade.get('trailing_stop', float('inf')), row['close'] + (self.trail_offset_atr * row['ATRr_14']))
                    if row['close'] > open_trade['trailing_stop']:
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(bar_ts),
                            'direction': 'SHORT',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': row['close'],
                            'pnl': open_trade['entry_price'] - row['close'],
                            'exit_reason': 'Trailing Stop'
                        })
                        open_trade = None

            # Entry logic
            if row['trades_today'] < self.max_trades:
                if row['trend_state'] == "Bullish" and (row['close'] > row['upper_band']):
                    open_trade = {
                        'entry_time': str(bar_ts),
                        'entry_price': row['close'],
                        'direction': 'LONG',
                        'trailing_stop': None
                    }
                elif row['trend_state'] == "Bearish" and (row['close'] < row['lower_band']):
                    open_trade = {
                        'entry_time': str(bar_ts),
                        'entry_price': row['close'],
                        'direction': 'SHORT',
                        'trailing_stop': None
                    }

        # Handle any open trade at the end of the data
        if open_trade:
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