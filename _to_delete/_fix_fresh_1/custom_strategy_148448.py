import pandas as pd
import numpy as np
from strategy_kernel import StrategyKernel, SignalEvent

class NeuralKernelBands(StrategyKernel):
    strategy_id = "custom_strategy_148448"
    display_name = "Neural Kernel Bands [ATR-Trailing v6]"
    live_capable = False

    def __init__(self):
        super().__init__()
        self.lookback = 20
        self.base_h = 6.0
        self.atr_factor = 2.0
        self.sigma_mult = 2.0
        self.max_trades = 10
        self.atr_period = 14
        self.trail_activation_atr = 1.5
        self.trail_offset_atr = 2.0
        self.trades_today = 0
        self.last_closed_count = 0
        self.trend_state = "Neutral"
        self.last_state = "Neutral"

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['atr'] = df['close'].rolling(window=self.atr_period).apply(lambda x: np.mean(np.abs(np.diff(x))), raw=False).bfill().fillna(10.0)
        df['norm_atr'] = (df['atr'] / df['close']) * self.atr_factor
        df['effective_h'] = self.base_h * (1.0 + df['norm_atr'])

        kernel_ma = []
        for i in range(len(df)):
            if i < self.lookback:
                kernel_ma.append(df['close'].iloc[i])
                continue
            
            sum_weights = 0.0
            sum_prices = 0.0
            
            for j in range(self.lookback):
                u = float(j)
                eff_h = df['effective_h'].iloc[i]
                w = np.exp(-0.5 * ((u / (eff_h + 1e-6)) ** 2))
                sum_weights += w
                sum_prices += w * df['close'].iloc[i - j]
            
            kernel_ma.append(sum_prices / (sum_weights + 1e-6))

        df['kernel_ma'] = kernel_ma
        residuals = df['close'] - df['kernel_ma']
        rolling_std = residuals.rolling(window=self.lookback).std().bfill().fillna(1.0)
        
        df['upper_band'] = df['kernel_ma'] + (self.sigma_mult * rolling_std)
        df['lower_band'] = df['kernel_ma'] - (self.sigma_mult * rolling_std)

        df['state_flipped_bull'] = (df['close'] > df['upper_band']) & (df['close'].shift(1) <= df['upper_band'].shift(1))
        df['state_flipped_bear'] = (df['close'] < df['lower_band']) & (df['close'].shift(1) >= df['lower_band'].shift(1))

        return df

    def run_backtest(self, frames: dict, initial_capital: float = 500000, lot_size: int = 15, lot_multiplier: int = 1, start_date=None, end_date=None) -> dict:
        df = frames.get('5') if '5' in frames else (frames.get('data') if 'data' in frames else list(frames.values())[0])
        df = self.compute_indicators(df.copy())
        trades = []
        active_trade = None

        for idx, row in df.iterrows():
            if active_trade is not None:
                trailing_atr = row.get('atr', 10.0)
                if pd.isna(trailing_atr):
                    trailing_atr = 10.0
                ticks_offset = round(float(trailing_atr) * self.trail_offset_atr)
                
                if active_trade['direction'] == 'LONG':
                    if row['close'] < active_trade['entry_price'] - ticks_offset or row.get('state_flipped_bear', False):
                        active_trade['exit_time'] = row['time']
                        active_trade['exit_price'] = row['close']
                        active_trade['pnl'] = active_trade['exit_price'] - active_trade['entry_price']
                        active_trade['exit_reason'] = 'ATR Trail Long'
                        trades.append(active_trade)
                        active_trade = None
                elif active_trade['direction'] == 'SHORT':
                    if row['close'] > active_trade['entry_price'] + ticks_offset or row.get('state_flipped_bull', False):
                        active_trade['exit_time'] = row['time']
                        active_trade['exit_price'] = row['close']
                        active_trade['pnl'] = active_trade['entry_price'] - active_trade['exit_price']
                        active_trade['exit_reason'] = 'ATR Trail Short'
                        trades.append(active_trade)
                        active_trade = None

            if active_trade is None and self.trades_today < self.max_trades:
                if row.get('state_flipped_bull', False):
                    self.trades_today += 1
                    active_trade = {
                        'entry_time': row['time'],
                        'direction': 'LONG',
                        'entry_price': row['close'],
                        'exit_time': None,
                        'exit_price': None,
                        'pnl': None,
                        'exit_reason': None
                    }
                elif row.get('state_flipped_bear', False):
                    self.trades_today += 1
                    active_trade = {
                        'entry_time': row['time'],
                        'direction': 'SHORT',
                        'entry_price': row['close'],
                        'exit_time': None,
                        'exit_price': None,
                        'pnl': None,
                        'exit_reason': None
                    }

        if active_trade is not None:
            active_trade['exit_time'] = df.iloc[-1]['time']
            active_trade['exit_price'] = df.iloc[-1]['close']
            if active_trade['direction'] == 'LONG':
                active_trade['pnl'] = active_trade['exit_price'] - active_trade['entry_price']
            else:
                active_trade['pnl'] = active_trade['entry_price'] - active_trade['exit_price']
            active_trade['exit_reason'] = 'EOD Flat'
            trades.append(active_trade)

        return {'trades': trades}