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
        filterop = "Gaussian Filter"
        filter = 1.0
        filterperiod = 10
        colorbars = True
        showSigs = True

        # Function definitions
        def fact(n):
            a = 1.0
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
            filtdev = filter * np.std(src[-length:])
            if abs(price - src[-2]) < filtdev:
                price = src[-2]
            return price

        # Source selection
        src = df['close'].values  # Assuming 'close' is the selected source

        # Apply filter if needed
        if filterop in ["Both", "Price"] and filter > 0:
            src = _filt(src, filterperiod, filter)

        out = _npolegf(src, period, order)

        if filterop in ["Both", "Gaussian Filter"] and filter > 0:
            out = _filt(out, filterperiod, filter)

        sig = np.roll(out, 1)  # Equivalent to nz(out[1])

        # State management
        state = np.zeros(len(df))
        for i in range(len(df)):
            if out[i] > sig[i]:
                state[i] = 1
            elif out[i] < sig[i]:
                state[i] = -1

        pregoLong = (out > sig) & (np.roll(out, 1) < np.roll(sig, 1)) | (np.roll(out, 1) == np.roll(sig, 1))
        pregoShort = (out < sig) & (np.roll(out, 1) > np.roll(sig, 1)) | (np.roll(out, 1) == np.roll(sig, 1))

        contsw = 0
        contsw_values = []
        for i in range(len(df)):
            contsw = contsw_values[-1] if contsw_values else 0
            contsw = 1 if pregoLong[i] else -1 if pregoShort[i] else contsw
            contsw_values.append(contsw)

        goLong = pregoLong & (np.roll(contsw_values, 1) == -1)
        goShort = pregoShort & (np.roll(contsw_values, 1) == 1)

        # Assign calculated columns to df
        df['out'] = out
        df['sig'] = sig
        df['state'] = state
        df['goLong'] = goLong
        df['goShort'] = goShort
        df['contsw'] = pd.Series(contsw_values, index=df.index)

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

        for idx in range(len(df)):
            row = df.iloc[idx]
            if open_trade is None:
                if row['goLong']:
                    open_trade = {
                        'entry_time': str(row['timestamp']),
                        'entry_price': float(row['out']),
                        'direction': 'LONG'
                    }
                elif row['goShort']:
                    open_trade = {
                        'entry_time': str(row['timestamp']),
                        'entry_price': float(row['out']),
                        'direction': 'SHORT'
                    }
            else:
                if open_trade['direction'] == 'LONG' and row['goShort'].any():
                    open_trade['exit_time'] = str(row['timestamp'])
                    open_trade['exit_price'] = float(row['out'])
                    open_trade['pnl'] = open_trade['exit_price'] - open_trade['entry_price']
                    trades.append(open_trade)
                    open_trade = None
                elif open_trade['direction'] == 'SHORT' and row['goLong'].any():
                    open_trade['exit_time'] = str(row['timestamp'])
                    open_trade['exit_price'] = float(row['out'])
                    open_trade['pnl'] = open_trade['entry_price'] - open_trade['exit_price']
                    trades.append(open_trade)
                    open_trade = None

        if open_trade is not None:
            open_trade['exit_time'] = None
            open_trade['exit_price'] = None
            open_trade['pnl'] = None
            trades.append(open_trade)

        return {'trades': trades}