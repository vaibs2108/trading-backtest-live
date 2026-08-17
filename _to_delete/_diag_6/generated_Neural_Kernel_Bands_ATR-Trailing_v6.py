from strategy_kernel import StrategyKernel, SignalEvent
import pandas as pd
import numpy as np

class NeuralKernelBands(StrategyKernel):
    strategy_id = "neural_kernel_bands"
    display_name = "Neural Kernel Bands [ATR-Trailing v6]"
    live_capable = False

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Calculate ATR
        df.ta.atr(length=14, append=True)
        df['norm_atr'] = (df['ATRr_14'] / df['close']) * 2.0  # atr_factor = 2.0
        df['effective_h'] = 6.0 * (1.0 + df['norm_atr'])  # base_h = 6.0

        # Initialize variables for kernel calculation
        sum_weights = []
        sum_prices = []

        for idx in range(len(df)):
            effective_h = float(df['effective_h'].iloc[idx])
            sum_w = 0.0
            sum_p = 0.0
            
            for i in range(20):  # lookback = 20
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
        df.ta.stdev(length=20, append=True)  # lookback = 20
        df['upper_band'] = df['kernel_ma'] + (df['STDEV_20'] * 2.0)  # sigma_mult = 2.0
        df['lower_band'] = df['kernel_ma'] - (df['STDEV_20'] * 2.0)

        # Initialize state variables
        if 'trades_today' not in df.columns:
            df['trades_today'] = 0
        if 'last_closed_count' not in df.columns:
            df['last_closed_count'] = 0
        if 'trend_state' not in df.columns:
            df['trend_state'] = "Neutral"
        if 'last_state' not in df.columns:
            df['last_state'] = "Neutral"

        # Update trades_today
        for idx in range(len(df)):
            if idx > 0 and df['timestamp'].iloc[idx].date() != df['timestamp'].iloc[idx - 1].date():
                df['trades_today'].iloc[idx] = 0
            else:
                df['trades_today'].iloc[idx] = df['trades_today'].iloc[idx - 1]

            if df['trades_today'].iloc[idx] < 10:  # max_trades = 10
                if df['close'].iloc[idx] > df['upper_band'].iloc[idx]:
                    df['trend_state'].iloc[idx] = "Bullish"
                elif df['close'].iloc[idx] < df['lower_band'].iloc[idx]:
                    df['trend_state'].iloc[idx] = "Bearish"

            # Check for state changes
            if df['trend_state'].iloc[idx] != df['last_state'].iloc[idx]:
                if df['trend_state'].iloc[idx] == "Bullish":
                    df['trades_today'].iloc[idx] += 1
                elif df['trend_state'].iloc[idx] == "Bearish":
                    df['trades_today'].iloc[idx] += 1

            df['last_state'].iloc[idx] = df['trend_state'].iloc[idx]

        return df.bfill().fillna(0.0)

    def run_backtest(self, frames: dict, initial_capital=500000, lot_size=15, lot_multiplier=1, start_date=None, end_date=None) -> dict:
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
        highest_price_since_entry = 0.0
        trailing_atr = []

        for idx in range(len(df)):
            current_price = float(df['close'].iloc[idx])
            if open_trade is not None:
                if open_trade['direction'] == 'LONG':
                    if current_price < open_trade['trail_stop']:
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(df['timestamp'].iloc[idx]),
                            'direction': 'LONG',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': current_price,
                            'pnl': current_price - open_trade['entry_price'],
                            'exit_reason': 'Trailing Stop Hit'
                        })
                        open_trade = None
                elif open_trade['direction'] == 'SHORT':
                    if current_price > open_trade['trail_stop']:
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(df['timestamp'].iloc[idx]),
                            'direction': 'SHORT',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': current_price,
                            'pnl': open_trade['entry_price'] - current_price,
                            'exit_reason': 'Trailing Stop Hit'
                        })
                        open_trade = None

            # Entry logic
            if open_trade is None:
                if df['trend_state'].iloc[idx] == "Bullish" and df['trades_today'].iloc[idx] < 10:
                    open_trade = {
                        'entry_time': str(df['timestamp'].iloc[idx]),
                        'entry_price': current_price,
                        'direction': 'LONG',
                        'trail_stop': current_price - (df['ATRr_14'].iloc[idx] * 1.5),  # trail_activation_atr = 1.5
                    }
                    highest_price_since_entry = current_price
                elif df['trend_state'].iloc[idx] == "Bearish" and df['trades_today'].iloc[idx] < 10:
                    open_trade = {
                        'entry_time': str(df['timestamp'].iloc[idx]),
                        'entry_price': current_price,
                        'direction': 'SHORT',
                        'trail_stop': current_price + (df['ATRr_14'].iloc[idx] * 2.0),  # trail_offset_atr = 2.0
                    }
                    highest_price_since_entry = current_price

            # Update trailing stop
            if open_trade is not None:
                if open_trade['direction'] == 'LONG':
                    highest_price_since_entry = max(highest_price_since_entry, current_price)
                    open_trade['trail_stop'] = max(open_trade['trail_stop'], highest_price_since_entry - (df['ATRr_14'].iloc[idx] * 2.0))
                elif open_trade['direction'] == 'SHORT':
                    open_trade['trail_stop'] = min(open_trade['trail_stop'], current_price + (df['ATRr_14'].iloc[idx] * 2.0))

        # Handle any open trades at the end
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