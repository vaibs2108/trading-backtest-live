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
                filt += sign * coeffs[r, 0] * coeffs[r, 2] * (filt if r < len(filt) else 0)
                sign *= -1
            return filt

        def _filt(src, length, filter):
            price = src.copy()
            filtdev = filter * src.rolling(length).std()
            for i in range(len(df)):
                if i > 0:
                    price[i] = price[i] if abs(price[i] - price[i - 1]) >= filtdev[i] else price[i - 1]
            return price

        # Main calculations
        src = df['close']  # Assuming 'close' is the default source
        if filterop in ["Both", "Price"] and filter > 0:
            src = _filt(src, filterperiod, filter)

        out = _npolegf(src, period, order)

        if filterop in ["Both", "Gaussian Filter"] and filter > 0:
            out = _filt(out, filterperiod, filter)

        sig = out.shift(1).fillna(0)

        state = np.zeros(len(df))
        state = np.where(out > sig, 1, np.where(out < sig, -1, state))

        pregoLong = (out > sig) & ((out.shift(1) < sig.shift(1)) | (out.shift(1) == sig.shift(1)))
        pregoShort = (out < sig) & ((out.shift(1) > sig.shift(1)) | (out.shift(1) == sig.shift(1)))

        contsw_list = []
        contsw = 0
        for i in range(len(df)):
            if i == 0:
                contsw_list.append(0)
            else:
                contsw = contsw_list[-1]
                contsw = 1 if pregoLong[i] else -1 if pregoShort[i] else contsw
                contsw_list.append(contsw)

        df['contsw'] = pd.Series(contsw_list, index=df.index)

        goLong = pregoLong & (df['contsw'].shift(1) == -1)
        goShort = pregoShort & (df['contsw'].shift(1) == 1)

        colorout = np.where(state == -1, '#D2042D', np.where(state == 1, '#2DD204', np.nan))
        df['colorout'] = pd.Series(colorout, index=df.index)

        df['out'] = out
        df['sig'] = sig

        # Plotting and signals
        if colorbars:
            df['barcolor'] = df['colorout']

        df['goLong'] = goLong
        df['goShort'] = goShort

        return df.bfill().fillna(0.0)

    def run_backtest(self, frames: dict, initial_capital=500000, lot_size=15, lot_multiplier=1, start_date=None, end_date=None) -> dict:
        df = frames.get('5') if '5' in frames else (frames.get('data') if 'data' in frames else list(frames.values())[0])
        df = self.compute_indicators(df.copy())

        if start_date:
            df = df[df['timestamp'] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df['timestamp'] <= pd.to_datetime(end_date)]
        df = df.reset_index(drop=True)

        trades = []

        for idx, row in df.iterrows():
            bar_ts = pd.to_datetime(row['timestamp'])
            entry_time = str(bar_ts)
            if row.get('goLong', False):
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': None,
                    'direction': 'LONG',
                    'entry_price': float(row['close']),
                    'exit_price': None,
                    'pnl': None,
                    'exit_reason': None
                })
            elif row.get('goShort', False):
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': None,
                    'direction': 'SHORT',
                    'entry_price': float(row['close']),
                    'exit_price': None,
                    'pnl': None,
                    'exit_reason': None
                })

        # Handle open trades at the end of the data
        for trade in trades:
            if trade['exit_time'] is None:
                trade['exit_time'] = None
                trade['exit_price'] = None
                trade['pnl'] = None

        return {'trades': trades}