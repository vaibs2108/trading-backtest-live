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

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Calculate ATR
        df.ta.atr(length=14, append=True)
        df['norm_atr'] = (df['ATRr_14'] / df['close']) * self.atr_factor
        df['effective_h'] = self.base_h * (1.0 + df['norm_atr'])

        # Initialize variables for kernel calculation
        sum_weights = []
        sum_prices = []
        
        for idx in range(len(df)):
            effective_h = float(df['effective_h'].iloc[idx])
            sum_w = 0.0
            sum_p = 0.0
            
            for i in range(self.lookback):
                if idx - i >= 0:
                    u = float(i)
                    kernel_weight = np.exp(-(np.power(u, 2)) / (2 * np.power(effective_h, 2)))
                    sum_w += kernel_weight
                    sum_p += (df['close'].iloc[idx - i] * kernel_weight)

            sum_weights.append(sum_w)
            sum_prices.append(sum_p)

        df['sum_weights'] = pd.Series(sum_weights, index=df.index)
        df['sum_prices'] = pd.Series(sum_prices, index=df.index)

        df['kernel_ma'] = np.where(df['sum_weights'] > 0, df['sum_prices'] / df['sum_weights'], df['close'])

        # Calculate residual and bands
        df['residual'] = df['close'] - df['kernel_ma']
        df.ta.stdev(column='residual', length=self.lookback, append=True)
        df['upper_band'] = df['kernel_ma'] + (df['STDEV_' + str(self.lookback)] * self.sigma_mult)
        df['lower_band'] = df['kernel_ma'] - (df['STDEV_' + str(self.lookback)] * self.sigma_mult)

        # Initialize state variables
        df['trades_today'] = 0
        df['last_closed_count'] = 0
        df['trend_state'] = "Neutral"
        df['last_state'] = "Neutral"
        df['closedtrades'] = 0  # Initialize closed trades count

        for idx in range(len(df)):
            if idx > 0 and df['timestamp'].iloc[idx].date() != df['timestamp'].iloc[idx - 1].date():
                df.at[idx, 'trades_today'] = 0
            
            if df['closedtrades'].iloc[idx] > df['last_closed_count'].iloc[idx]:
                df.at[idx, 'trades_today'] += (df['closedtrades'].iloc[idx] - df['last_closed_count'].iloc[idx])
                df.at[idx, 'last_closed_count'] = df['closedtrades'].iloc[idx]

            if df['close'].iloc[idx] > df['upper_band'].iloc[idx]:
                df.at[idx, 'trend_state'] = "Bullish"
            elif df['close'].iloc[idx] < df['lower_band'].iloc[idx]:
                df.at[idx, 'trend_state'] = "Bearish"

            state_flipped_bull = (df['trend_state'].iloc[idx] == "Bullish" and df['last_state'].iloc[idx] != "Bullish")
            state_flipped_bear = (df['trend_state'].iloc[idx] == "Bearish" and df['last_state'].iloc[idx] != "Bearish")

            if state_flipped_bull or state_flipped_bear:
                df.at[idx, 'last_state'] = df['trend_state'].iloc[idx]

        return df

    def run_backtest(self, frames: dict, initial_capital=100000, lot_size=15, lot_multiplier=1, start_date=None, end_date=None) -> dict:
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
        open_trade = None

        for idx in range(len(df)):
            row = df.iloc[idx]
            trailing_atr = row['ATRr_14']
            ticks_activation = round((trailing_atr * self.trail_activation_atr) / 0.01)  # Assuming mintick is 0.01
            ticks_offset = round((trailing_atr * self.trail_offset_atr) / 0.01)

            if open_trade is None:
                if row['trend_state'] == "Bullish" and row['trades_today'] < self.max_trades:
                    open_trade = {
                        'entry_time': str(row['timestamp']),
                        'entry_price': float(row['close']),
                        'direction': 'LONG'
                    }
                elif row['trend_state'] == "Bearish" and row['trades_today'] < self.max_trades:
                    open_trade = {
                        'entry_time': str(row['timestamp']),
                        'entry_price': float(row['close']),
                        'direction': 'SHORT'
                    }
            else:
                if open_trade['direction'] == 'LONG':
                    if float(row['close']) <= (open_trade['entry_price'] - ticks_offset):
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(row['timestamp']),
                            'direction': 'LONG',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': float(row['close']),
                            'pnl': float(row['close']) - open_trade['entry_price'],
                            'exit_reason': 'ATR Trail Long'
                        })
                        open_trade = None
                elif open_trade['direction'] == 'SHORT':
                    if float(row['close']) >= (open_trade['entry_price'] + ticks_offset):
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(row['timestamp']),
                            'direction': 'SHORT',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': float(row['close']),
                            'pnl': open_trade['entry_price'] - float(row['close']),
                            'exit_reason': 'ATR Trail Short'
                        })
                        open_trade = None

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