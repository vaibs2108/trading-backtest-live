import pandas as pd
import numpy as np
from strategy_kernel import StrategyKernel, SignalEvent

class StdFilteredNPoleGaussianFilter(StrategyKernel):
    strategy_id = "std_filtered_n_pole_gaussian_filter"
    display_name = "STD-Filtered N-Pole Gaussian Filter [Loxx]"
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

        # Initialize variables
        coeffs = np.zeros((order + 1, 3))
        out = np.zeros(len(df))
        sig = np.zeros(len(df))
        state = np.zeros(len(df))
        contsw = np.zeros(len(df))
        colorout = np.full(len(df), np.nan)

        # Helper functions
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
                out = fact(order) / (fact(order - r) * fact(r)) if (order - r) >= 0 else 1
                coeffs[r, 0] = out
                coeffs[r, 1] = np.power(a, r)
                coeffs[r, 2] = np.power(1.0 - a, r)
            return coeffs

        def _npolegf(src, period, order):
            coeffs = _makeCoeffs(period, order)
            filt = src * coeffs[order, 1]
            sign = 1
            for r in range(1, order + 1):
                if idx - r >= 0:  # Ensure we are within bounds
                    filt += sign * coeffs[r, 0] * coeffs[r, 2] * out[idx - r]
                sign *= -1
            return filt

        def _filt(src, length, filter):
            price = src
            filtdev = filter * np.std(src[-length:])  # Using numpy std for standard deviation
            if np.abs(price - price[-1]) < filtdev:
                price = price[-1]
            return price

        # Main computation loop
        for idx in range(len(df)):
            src = df['close'].iloc[idx]  # Assuming 'close' is the selected source
            if filterop in ["Both", "Price"] and filter > 0:
                src = _filt(np.array([src]), filterperiod, filter)[0]  # Wrap in array for filtering

            out[idx] = _npolegf(src, period, order)

            if filterop in ["Both", "Gaussian Filter"] and filter > 0:
                out[idx] = _filt(np.array([out[idx]]), filterperiod, filter)[0]  # Wrap in array for filtering

            sig[idx] = out[idx - 1] if idx > 0 else 0

            if out[idx] > sig[idx]:
                state[idx] = 1
            elif out[idx] < sig[idx]:
                state[idx] = -1

            pregoLong = out[idx] > sig[idx] and (out[idx - 1] < sig[idx - 1] if idx > 0 else False)
            pregoShort = out[idx] < sig[idx] and (out[idx - 1] > sig[idx - 1] if idx > 0 else False)

            contsw[idx] = contsw[idx - 1] if idx > 0 else 0
            if pregoLong:
                contsw[idx] = 1
            elif pregoShort:
                contsw[idx] = -1
            else:
                contsw[idx] = contsw[idx - 1]

            goLong = pregoLong and (contsw[idx - 1] == -1 if idx > 0 else False)
            goShort = pregoShort and (contsw[idx - 1] == 1 if idx > 0 else False)

            colorout[idx] = 0  # Default color
            if state[idx] == -1:
                colorout[idx] = 1  # Red
            elif state[idx] == 1:
                colorout[idx] = 2  # Green

        # Assign computed values to DataFrame
        df['out'] = out
        df['sig'] = sig
        df['state'] = state
        df['contsw'] = contsw
        df['colorout'] = colorout

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
            bar_ts = pd.to_datetime(df['timestamp'].iloc[idx])
            current_price = float(df['close'].iloc[idx])
            goLong = bool(df['contsw'].iloc[idx] == 1)
            goShort = bool(df['contsw'].iloc[idx] == -1)

            if open_trade is None:
                if goLong:
                    open_trade = {
                        'entry_time': str(bar_ts),
                        'entry_price': current_price,
                        'direction': 'LONG'
                    }
                elif goShort:
                    open_trade = {
                        'entry_time': str(bar_ts),
                        'entry_price': current_price,
                        'direction': 'SHORT'
                    }
            else:
                if open_trade['direction'] == 'LONG' and goShort:
                    open_trade['exit_time'] = str(bar_ts)
                    open_trade['exit_price'] = current_price
                    open_trade['pnl'] = current_price - open_trade['entry_price']
                    trades.append(open_trade)
                    open_trade = None
                elif open_trade['direction'] == 'SHORT' and goLong:
                    open_trade['exit_time'] = str(bar_ts)
                    open_trade['exit_price'] = current_price
                    open_trade['pnl'] = open_trade['entry_price'] - current_price
                    trades.append(open_trade)
                    open_trade = None

        if open_trade is not None:
            open_trade['exit_time'] = None
            open_trade['exit_price'] = None
            open_trade['pnl'] = None
            trades.append(open_trade)

        return {'trades': trades}