from strategy_kernel import StrategyKernel
import pandas as pd
import numpy as np

class NeuralKernelBandsATR(StrategyKernel):
    strategy_id = "neural_kernel_bands_atr"
    display_name = "Neural Kernel Bands [ATR-Trailing v6]"
    live_capable = False

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Compute ATR
        df.ta.atr(length=14, append=True)
        df['norm_atr'] = (df['ATRr_14'] / df['close']) * self.atr_factor
        df['effective_h'] = self.base_h * (1.0 + df['norm_atr'])

        # Initialize state variables
        sum_weights = []
        sum_prices = []
        trades_today = 0
        last_closed_count = 0
        trend_state = "Neutral"
        last_state = "Neutral"

        for idx in range(len(df)):
            # Reset for each bar
            if idx == 0 or df['timestamp'].iloc[idx].date() != df['timestamp'].iloc[idx - 1].date():
                trades_today = 0
            
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

            # Calculate residual and bands
            residual = df['close'].iloc[idx] - kernel_ma
            sigma_dev = df['close'].iloc[max(0, idx - self.lookback):idx + 1].std()  # Standard deviation over lookback
            upper_band = kernel_ma + (sigma_dev * self.sigma_mult)
            lower_band = kernel_ma - (sigma_dev * self.sigma_mult)

            # Update trend state
            if df['close'].iloc[idx] > upper_band:
                trend_state = "Bullish"
            elif df['close'].iloc[idx] < lower_band:
                trend_state = "Bearish"

            # Check for state flip
            state_flipped_bull = (trend_state == "Bullish" and last_state != "Bullish")
            state_flipped_bear = (trend_state == "Bearish" and last_state != "Bearish")

            if state_flipped_bull or state_flipped_bear:
                last_state = trend_state

            # Update trades today
            if trades_today < self.max_trades:
                if state_flipped_bull:
                    df.loc[idx, 'signal'] = "NK Long"
                elif state_flipped_bear:
                    df.loc[idx, 'signal'] = "NK Short"

            # Update trades count
            if trades_today > last_closed_count:
                trades_today += (trades_today - last_closed_count)
                last_closed_count = trades_today

            # Store values in DataFrame
            df.loc[idx, 'kernel_ma'] = kernel_ma
            df.loc[idx, 'upper_band'] = upper_band
            df.loc[idx, 'lower_band'] = lower_band
            df.loc[idx, 'trades_today'] = trades_today
            df.loc[idx, 'trend_state'] = trend_state

        # Fill NaNs
        df = df.bfill().fillna(0.0)
        return df

    def run_backtest(self, frames: dict, initial_capital=500000, lot_size=15, lot_multiplier=1, start_date=None, end_date=None) -> dict:
        df = frames.get('5') if '5' in frames else (frames.get('data') if 'data' in frames else list(frames.values())[0])
        df = self.compute_indicators(df.copy())

        if start_date:
            df = df[df['timestamp'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['timestamp'] <= pd.to_datetime(end_date)]
        df = df.reset_index(drop=True)

        trades = []
        open_trade = None

        for idx in range(len(df)):
            bar_ts = pd.to_datetime(df['timestamp'].iloc[idx])
            current_price = df['close'].iloc[idx]

            # Check for open trades
            if open_trade:
                # Check exit conditions
                if open_trade['direction'] == 'LONG':
                    # Exit logic for long
                    if current_price < open_trade['trailing_stop']:
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(bar_ts),
                            'direction': 'LONG',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': current_price,
                            'pnl': current_price - open_trade['entry_price'],
                            'exit_reason': 'Trailing Stop'
                        })
                        open_trade = None
                elif open_trade['direction'] == 'SHORT':
                    # Exit logic for short
                    if current_price > open_trade['trailing_stop']:
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(bar_ts),
                            'direction': 'SHORT',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': current_price,
                            'pnl': open_trade['entry_price'] - current_price,
                            'exit_reason': 'Trailing Stop'
                        })
                        open_trade = None

            # Entry logic
            if df['trades_today'].iloc[idx] < self.max_trades:
                if df['trend_state'].iloc[idx] == "Bullish" and not open_trade:
                    open_trade = {
                        'entry_time': str(bar_ts),
                        'entry_price': current_price,
                        'direction': 'LONG',
                        'trailing_stop': current_price - (self.trail_offset_atr * df['ATRr_14'].iloc[idx])
                    }
                elif df['trend_state'].iloc[idx] == "Bearish" and not open_trade:
                    open_trade = {
                        'entry_time': str(bar_ts),
                        'entry_price': current_price,
                        'direction': 'SHORT',
                        'trailing_stop': current_price + (self.trail_offset_atr * df['ATRr_14'].iloc[idx])
                    }

        # If there's an open trade at the end of the data
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

    def __init__(self):
        self.lookback = 20
        self.base_h = 6.0
        self.atr_factor = 2.0
        self.sigma_mult = 2.0
        self.max_trades = 10
        self.atr_period = 14
        self.trail_activation_atr = 1.5
        self.trail_offset_atr = 2.0
        self.trades_today = 0