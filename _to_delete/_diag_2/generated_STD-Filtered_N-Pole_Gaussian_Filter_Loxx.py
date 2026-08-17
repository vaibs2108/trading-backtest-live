import pandas as pd
import numpy as np
from strategy_kernel import StrategyKernel, SignalEvent

class StdFilteredNPoleGaussianFilter(StrategyKernel):
    strategy_id = "std_filtered_n_pole_gaussian_filter"
    display_name = "STD-Filtered N-Pole Gaussian Filter"
    live_capable = False

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Input parameters
        period = 25
        order = 5
        filterop = "Gaussian Filter"
        filter = 1.0
        filterperiod = 10
        colorbars = True
        showSigs = True

        # Function definitions
        def fact(n):
            a = 1
            for i in range(1, n + 1):
                a *= i
            return a

        def _alpha(period, poles):
            w = 2.0 * np.pi / period
            b = (1.0 - np.cos(w)) / (np.power(1.414, 2.0 / poles) - 1.0)
            a = -b + np.sqrt(b * b + 2.0 * b)
            return a

        def _makeCoeffs(period, order):
            coeffs = np.zeros((order + 1, 3))
            a = _alpha(period, order)
            for r in range(order + 1):
                out = fact(order) / (fact(order - r) * fact(r)) if r <= order else 1
                coeffs[r, 0] = out
                coeffs[r, 1] = np.power(a, r)
                coeffs[r, 2] = np.power(1.0 - a, r)
            return coeffs

        def _npolegf(src, period, order):
            coeffs = _makeCoeffs(period, order)
            filt = src * coeffs[order, 1]
            sign = 1
            for r in range(1, order + 1):
                if r < len(src):
                    filt += sign * coeffs[r, 0] * coeffs[r, 2] * src[r]
                sign *= -1
            return filt

        def _filt(src, length, filter):
            price = src
            filtdev = filter * df['close'].rolling(length).std().fillna(0)
            for i in range(len(df)):
                if i > 0:
                    price[i] = price[i-1] if abs(price[i] - price[i-1]) < filtdev[i] else price[i]
            return price

        # Main calculations
        src = df['close'].copy()  # Replace with appropriate source based on input options
        if filterop in ["Both", "Price"] and filter > 0:
            src = _filt(src, filterperiod, filter)

        out = _npolegf(src, period, order)

        if filterop in ["Both", "Gaussian Filter"] and filter > 0:
            out = _filt(out, filterperiod, filter)

        sig = out.shift(1).fillna(0)

        state = 0
        df['state'] = 0
        for i in range(len(df)):
            if out[i] > sig[i]:
                state = 1
            elif out[i] < sig[i]:
                state = -1
            df.at[i, 'state'] = state

        pregoLong = (out > sig) & ((out.shift(1) < sig.shift(1)) | (out.shift(1) == sig.shift(1)))
        pregoShort = (out < sig) & ((out.shift(1) > sig.shift(1)) | (out.shift(1) == sig.shift(1)))

        contsw = 0
        df['contsw'] = 0
        for i in range(len(df)):
            if i > 0:
                contsw = df['contsw'].iloc[i - 1]
            contsw = 1 if pregoLong[i] else -1 if pregoShort[i] else contsw
            df.at[i, 'contsw'] = contsw

        goLong = pregoLong & (df['contsw'].shift(1) == -1)
        goShort = pregoShort & (df['contsw'].shift(1) == 1)

        # Assigning output columns
        df['out'] = out
        df['goLong'] = goLong
        df['goShort'] = goShort

        return df.bfill().fillna(0.0)

    def run_backtest(self, frames: dict, initial_capital=500000, lot_size=15, lot_multiplier=1, start_date=None, end_date=None) -> dict:
        df = frames.get('5') if '5' in frames else (frames.get('data') if 'data' in frames else list(frames.values())[0])
        df = self.compute_indicators(df.copy())

        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if start_date: df = df[df['timestamp'] >= pd.to_datetime(start_date)]
        if end_date: df = df[df['timestamp'] <= pd.to_datetime(end_date)]
        df = df.reset_index(drop=True)

        trades = []
        open_trade = None

        for idx in range(len(df)):
            row = df.iloc[idx]
            if open_trade is None:
                if row['goLong']:
                    open_trade = {
                        'entry_time': str(row['timestamp']),
                        'entry_price': float(row['close']),
                        'direction': 'LONG'
                    }
                elif row['goShort']:
                    open_trade = {
                        'entry_time': str(row['timestamp']),
                        'entry_price': float(row['close']),
                        'direction': 'SHORT'
                    }
            else:
                if open_trade['direction'] == 'LONG':
                    if row['goShort'] or (row['close'] < open_trade['entry_price']):
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(row['timestamp']),
                            'direction': 'LONG',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': float(row['close']),
                            'pnl': float(row['close']) - open_trade['entry_price'],
                            'exit_reason': 'Stop Loss or Opposite Signal'
                        })
                        open_trade = None
                elif open_trade['direction'] == 'SHORT':
                    if row['goLong'] or (row['close'] > open_trade['entry_price']):
                        trades.append({
                            'entry_time': open_trade['entry_time'],
                            'exit_time': str(row['timestamp']),
                            'direction': 'SHORT',
                            'entry_price': open_trade['entry_price'],
                            'exit_price': float(row['close']),
                            'pnl': open_trade['entry_price'] - float(row['close']),
                            'exit_reason': 'Stop Loss or Opposite Signal'
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