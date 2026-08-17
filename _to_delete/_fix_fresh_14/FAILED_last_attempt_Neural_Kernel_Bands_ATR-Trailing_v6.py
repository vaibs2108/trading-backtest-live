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
        self.mintick = 0.01  # Example value, adjust as necessary
        self.closed_trades = 0  # Initialize closed trades count

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Calculate ATR
        df.ta.atr(length=14, append=True)
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
            # Calculate kernel weights and prices
            current_sum_weights = 0.0
            current_sum_prices = 0.0
            
            for i in range(self.lookback):
                if idx - i >= 0:
                    u = float(i)
                    kernel_weight = np.exp(-(np.power(u, 2)) / (2 * np.power(df['effective_h'].iloc[idx], 2)))
                    current_sum_weights += kernel_weight
                    current_sum_prices += (df['close'].iloc[idx - i] * kernel_weight)

            sum_weights.append(current_sum_weights)
            sum_prices.append(current_sum_prices)

            # Calculate kernel moving average
            kernel_ma = current_sum_weights > 0 and (current_sum_prices / current_sum_weights) or df['close'].iloc[idx]

            # Calculate residual and sigma deviation
            residual = df['close'].iloc[idx] - kernel_ma
            df['sigma_dev'] = pd.Series(df['close'].rolling(window=self.lookback).std(), index=df.index)
            upper_band = kernel_ma + (df['sigma_dev'].iloc[idx] * self.sigma_mult)
            lower_band = kernel_ma - (df['sigma_dev'].iloc[idx] * self.sigma_mult)

            # Update trades_today
            if idx > 0 and df['timestamp'].iloc[idx].date() != df['timestamp'].iloc[idx - 1].date():
                trades_today = 0

            if self.closed_trades > last_closed_count:
                trades_today += (self.closed_trades - last_closed_count)
                last_closed_count = self.closed_trades

            # Determine trend state
            if df['close'].iloc[idx] > upper_band:
                trend_state = "Bullish"
            elif df['close'].iloc[idx] < lower_band:
                trend_state = "Bearish"

            state_flipped_bull = (trend_state == "Bullish" and last_state != "Bullish")
            state_flipped_bear = (trend_state == "Bearish" and last_state != "Bearish")

            if state_flipped_bull or state_flipped_bear:
                last_state = trend_state

            # Entry conditions
            if trades_today < self.max_trades:
                if state_flipped_bull:
                    self.signal_event(SignalEvent('NK Long', df['timestamp'].iloc[idx], df['close'].iloc[idx]))
                elif state_flipped_bear:
                    self.signal_event(SignalEvent('NK Short', df['timestamp'].iloc[idx], df['close'].iloc[idx]))

            # Store the calculated values in the DataFrame
            df.at[idx, 'kernel_ma'] = kernel_ma
            df.at[idx, 'upper_band'] = upper_band
            df.at[idx, 'lower_band'] = lower_band
            df.at[idx, 'trades_today'] = trades_today
            df.at[idx, 'trend_state'] = trend_state

        # Fill NaNs
        df.fillna(method='bfill', inplace=True)
        return df

    def run_backtest(self, frames: dict, initial_capital=500000, lot_size=15, lot_multiplier=1, start_date=None, end_date=None) -> dict:
        df = frames.get('5') if '5' in frames else (frames.get('data') if 'data' in frames else list(frames.values())[0])
        df = self.compute_indicators(df.copy())

        # Filter by date
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if start_date:
            df = df[df['timestamp'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['timestamp'] <= pd.to_datetime(end_date)]
        df = df.reset_index(drop=True)

        trades = []
        trailing_atr = df.ta.atr(length=self.atr_period)

        for idx in range(len(df)):
            bar_ts = pd.to_datetime(df['timestamp'].iloc[idx])
            ticks_activation = round((trailing_atr.iloc[idx] * self.trail_activation_atr) / self.mintick)
            ticks_offset = round((trailing_atr.iloc[idx] * self.trail_offset_atr) / self.mintick)

            if self.position_size > 0:
                self.exit("ATR Trail Long", from_entry="NK Long", trail_points=ticks_activation, trail_offset=ticks_offset)
            elif self.position_size < 0:
                self.exit("ATR Trail Short", from_entry="NK Short", trail_points=ticks_activation, trail_offset=ticks_offset)

            # Record trades
            if self.position_size != 0:
                trades.append({
                    'entry_time': str(bar_ts),
                    'exit_time': None,
                    'direction': 'LONG' if self.position_size > 0 else 'SHORT',
                    'entry_price': float(df['close'].iloc[idx]),
                    'exit_price': None,
                    'pnl': None,
                    'exit_reason': None
                })

        # Handle open trades
        for trade in trades:
            if trade['exit_time'] is None:
                trade['exit_time'] = str(bar_ts)
                trade['exit_price'] = float(df['close'].iloc[-1])
                trade['pnl'] = trade['exit_price'] - trade['entry_price']

        return {'trades': trades}