from strategy_kernel import StrategyKernel, SignalEvent
import pandas as pd
import numpy as np

class StdFilteredNPoleGaussianFilter(StrategyKernel):
    strategy_id = "std_filtered_n_pole_gaussian_filter"
    display_name = "STD-Filtered N-Pole Gaussian Filter"
    live_capable = False

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Input parameters
        period = 25
        order = 5
        filter_op = "Gaussian Filter"
        filter_dev = 1.0
        filter_period = 10
        
        # Initialize lists for stateful variables
        out_values = []
        sig_values = []
        state_values = []
        contsw_values = []
        colorout_values = []
        
        # Initialize state variables
        contsw = 0
        colorout = np.nan
        
        for idx in range(len(df)):
            # Get the source value
            src = df['close'].iloc[idx]  # Assuming 'close' is the selected source
            
            # Apply filter if needed
            if filter_op in ["Both", "Price"] and filter_dev > 0:
                price = src
                filtdev = filter_dev * df['close'].rolling(window=filter_period).std().iloc[idx]
                price = price if abs(price - df['close'].iloc[idx - 1]) >= filtdev else df['close'].iloc[idx - 1]
                src = price
            
            # N-Pole Gaussian Filter calculation
            coeffs = self._make_coeffs(period, order)
            filt = src * coeffs[order, 1]
            sign = 1
            
            for r in range(1, order + 1):
                if idx - r >= 0:
                    filt += sign * coeffs[r, 0] * coeffs[r, 2] * out_values[idx - r] if idx - r < len(out_values) else 0
                sign *= -1
            
            out_values.append(filt)
            
            # Apply Gaussian filter if needed
            if filter_op in ["Both", "Gaussian Filter"] and filter_dev > 0:
                price = filt
                filtdev = filter_dev * df['close'].rolling(window=filter_period).std().iloc[idx]
                price = price if abs(price - out_values[idx - 1]) >= filtdev else out_values[idx - 1]
                filt = price
            
            out = filt
            
            # Signal calculation
            sig = out_values[idx - 1] if idx > 0 else 0
            sig_values.append(sig)
            
            # State management
            state = 0
            if out > sig:
                state = 1
            elif out < sig:
                state = -1
            
            state_values.append(state)
            
            pregoLong = out > sig and (out_values[idx - 1] < sig_values[idx - 1] or out_values[idx - 1] == sig_values[idx - 1])
            pregoShort = out < sig and (out_values[idx - 1] > sig_values[idx - 1] or out_values[idx - 1] == sig_values[idx - 1])
            
            contsw = contsw_values[idx - 1] if idx > 0 else 0
            contsw = 1 if pregoLong else -1 if pregoShort else contsw
            
            contsw_values.append(contsw)
            
            goLong = pregoLong and (contsw_values[idx - 1] if idx > 0 else 0) == -1
            goShort = pregoShort and (contsw_values[idx - 1] if idx > 0 else 0) == 1
            
            # Color output management
            colorout = np.nan
            if state == -1:
                colorout = '#D2042D'  # red
            elif state == 1:
                colorout = '#2DD204'  # green
            
            colorout_values.append(colorout)
        
        # Assign calculated values to DataFrame
        df['out'] = pd.Series(out_values, index=df.index)
        df['sig'] = pd.Series(sig_values, index=df.index)
        df['state'] = pd.Series(state_values, index=df.index)
        df['contsw'] = pd.Series(contsw_values, index=df.index)
        df['colorout'] = pd.Series(colorout_values, index=df.index)
        
        return df

    def _make_coeffs(self, period, order):
        coeffs = np.zeros((order + 1, 3))
        a = self._alpha(period, order)
        for r in range(order + 1):
            out = self.fact(order) / (self.fact(order - r) * self.fact(r)) if (order - r) >= 0 and r >= 0 else 1
            coeffs[r, 0] = out
            coeffs[r, 1] = a ** r
            coeffs[r, 2] = (1.0 - a) ** r
        return coeffs

    def _alpha(self, period, poles):
        w = 2.0 * np.pi / period
        b = (1.0 - np.cos(w)) / (np.power(1.414, 2.0 / poles) - 1.0)
        a = -b + np.sqrt(b * b + 2.0 * b)
        return a

    def fact(self, n):
        a = 1
        for i in range(1, n + 1):
            a *= i
        return a

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
        open_trade = None
        
        for idx in range(len(df)):
            row = df.iloc[idx]
            if open_trade is None:
                if row['contsw'] == 1:  # Go long
                    open_trade = {
                        'entry_time': str(row['timestamp']),
                        'entry_price': float(row['close']),
                        'direction': 'LONG'
                    }
                elif row['contsw'] == -1:  # Go short
                    open_trade = {
                        'entry_time': str(row['timestamp']),
                        'entry_price': float(row['close']),
                        'direction': 'SHORT'
                    }
            else:
                # Check for exit conditions
                if open_trade['direction'] == 'LONG':
                    if row['contsw'] == -1:  # Exit long
                        open_trade['exit_time'] = str(row['timestamp'])
                        open_trade['exit_price'] = float(row['close'])
                        open_trade['pnl'] = open_trade['exit_price'] - open_trade['entry_price']
                        trades.append(open_trade)
                        open_trade = None
                elif open_trade['direction'] == 'SHORT':
                    if row['contsw'] == 1:  # Exit short
                        open_trade['exit_time'] = str(row['timestamp'])
                        open_trade['exit_price'] = float(row['close'])
                        open_trade['pnl'] = open_trade['entry_price'] - open_trade['exit_price']
                        trades.append(open_trade)
                        open_trade = None
        
        # If there's an open trade at the end of the data
        if open_trade is not None:
            open_trade['exit_time'] = None
            open_trade['exit_price'] = None
            open_trade['pnl'] = None
            trades.append(open_trade)
        
        return {'trades': trades}