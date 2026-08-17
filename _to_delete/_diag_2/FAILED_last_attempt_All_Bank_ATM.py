from strategy_kernel import StrategyKernel, SignalEvent
import pandas as pd
import numpy as np

class AllBankATM(StrategyKernel):
    strategy_id = 'all_bank_atm'
    display_name = 'All Bank ATM'
    live_capable = False

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Input parameters
        length = 11
        mult = 2.0
        amplitude = 2
        channelDeviation = 2
        showArrows = True
        showChannels = False

        # Initialize variables
        short_len = int(length / 2)
        hull_len = int(np.sqrt(length))
        den1 = short_len * (short_len + 1) / 2
        den2 = length * (length + 1) / 2
        den3 = hull_len * (hull_len + 1) / 2

        lcwa_coeffs = []
        hull_coeffs = []

        # Compute coefficients
        for i in range(length):
            sum1 = max(short_len - i, 0)
            sum2 = length - i
            lcwa_coeffs.append(2 * (sum1 / den1) - (sum2 / den2))

        lcwa_coeffs = [0] * (hull_len - 1) + lcwa_coeffs  # Padding with zeros
        for i in range(hull_len, len(lcwa_coeffs)):
            sum3 = sum(lcwa_coeffs[j] * (i - j) for j in range(i - hull_len, i))
            hull_coeffs.append(sum3 / den3)

        # Initialize state variables
        os = 0
        hma_values = []
        inv_hma_values = []

        # Calculate HMA
        len_hull = len(hull_coeffs)

        for idx in range(len(df)):
            hma = 0.0
            inv_hma = 0.0
            for i in range(len_hull):
                if idx - i >= 0:
                    hma += df['close'].iloc[idx - i] * hull_coeffs[i]
                    inv_hma += df['close'].iloc[idx - (len_hull - 1 - i)] * hull_coeffs[i]
            hma_values.append(hma)
            inv_hma_values.append(inv_hma)

        df['hma'] = pd.Series(hma_values, index=df.index)
        df['inv_hma'] = pd.Series(inv_hma_values, index=df.index)

        hso = df['hma'] - df['inv_hma']
        cmean = df['close'].abs().cumsum() / df.index.to_series() * mult

        os_values = []
        for idx in range(len(df)):
            if idx == 0:
                os_values.append(0)
            else:
                if (hso.iloc[idx] > cmean.iloc[idx]) or (hso.iloc[idx] < -cmean.iloc[idx]):
                    os_values.append(0)
                elif (hso.iloc[idx] < hso.iloc[idx - 1]) and (hso.iloc[idx] > cmean.iloc[idx]):
                    os_values.append(-1)
                elif (hso.iloc[idx] > hso.iloc[idx - 1]) and (hso.iloc[idx] < -cmean.iloc[idx]):
                    os_values.append(1)
                else:
                    os_values.append(os_values[-1])

        df['os'] = pd.Series(os_values, index=df.index)

        # Trend detection
        trend = 0
        nextTrend = 0
        maxLowPrice = df['low'].shift(1).fillna(df['low'])
        minHighPrice = df['high'].shift(1).fillna(df['high'])

        up = 0.0
        down = 0.0
        atrHigh = 0.0
        atrLow = 0.0

        atr2 = df['close'].rolling(window=100).apply(lambda x: np.std(x) * np.sqrt(len(x))) / 2  # Approximation of ATR
        dev = channelDeviation * atr2

        # Calculate high and low prices
        highPrice = df['high'].rolling(window=amplitude).max()
        lowPrice = df['low'].rolling(window=amplitude).min()
        highma = df['high'].rolling(window=amplitude).mean()
        lowma = df['low'].rolling(window=amplitude).mean()

        for idx in range(len(df)):
            if nextTrend == 1:
                maxLowPrice = max(lowPrice.iloc[idx], maxLowPrice)
                if highma.iloc[idx] < maxLowPrice and df['close'].iloc[idx] < df['low'].shift(1).iloc[idx]:
                    trend = 1
                    nextTrend = 0
                    minHighPrice = highPrice.iloc[idx]
            else:
                minHighPrice = min(highPrice.iloc[idx], minHighPrice)
                if lowma.iloc[idx] > minHighPrice and df['close'].iloc[idx] > df['high'].shift(1).iloc[idx]:
                    trend = 0
                    nextTrend = 1
                    maxLowPrice = lowPrice.iloc[idx]

            if trend == 0:
                if idx > 0 and trend != 0:
                    up = down if pd.isna(down) else down
                    arrowUp = up - atr2.iloc[idx]
                else:
                    up = max(maxLowPrice, up) if pd.isna(up) else max(maxLowPrice, up)
                atrHigh = up + dev.iloc[idx]
                atrLow = up - dev.iloc[idx]
            else:
                if idx > 0 and trend != 1:
                    down = up if pd.isna(up) else up
                    arrowDown = down + atr2.iloc[idx]
                else:
                    down = min(minHighPrice, down) if pd.isna(down) else min(minHighPrice, down)
                atrHigh = down + dev.iloc[idx]
                atrLow = down - dev.iloc[idx]

            ht = up if trend == 0 else down

        df['ht'] = ht
        df['atrHigh'] = atrHigh
        df['atrLow'] = atrLow

        # Generate signals
        buySignal = (not pd.isna(arrowUp)) & (trend == 0) & (trend.shift(1) == 1)
        sellSignal = (not pd.isna(arrowDown)) & (trend == 1) & (trend.shift(1) == 0)

        df['longEntry'] = buySignal
        df['shortEntry'] = sellSignal

        return df

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
            if row['longEntry'] and not open_trade:
                open_trade = {
                    'entry_time': str(row['timestamp']),
                    'entry_price': float(row['close']),
                    'direction': 'LONG',
                }
            elif row['shortEntry'] and not open_trade:
                open_trade = {
                    'entry_time': str(row['timestamp']),
                    'entry_price': float(row['close']),
                    'direction': 'SHORT',
                }
            if open_trade:
                if open_trade['direction'] == 'LONG' and float(row['close']) <= row['atrLow']:
                    open_trade['exit_time'] = str(row['timestamp'])
                    open_trade['exit_price'] = float(row['close'])
                    open_trade['pnl'] = open_trade['exit_price'] - open_trade['entry_price']
                    trades.append(open_trade)
                    open_trade = None
                elif open_trade['direction'] == 'SHORT' and float(row['close']) >= row['atrHigh']:
                    open_trade['exit_time'] = str(row['timestamp'])
                    open_trade['exit_price'] = float(row['close'])
                    open_trade['pnl'] = open_trade['entry_price'] - open_trade['exit_price']
                    trades.append(open_trade)
                    open_trade = None

        if open_trade:
            open_trade['exit_time'] = None
            open_trade['exit_price'] = None
            open_trade['pnl'] = None
            trades.append(open_trade)

        return {'trades': trades}