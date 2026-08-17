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

        # Calculate coefficients
        for i in range(length):
            sum1 = max(short_len - i, 0)
            sum2 = length - i
            lcwa_coeffs.append(2 * (sum1 / den1) - (sum2 / den2))

        for i in range(hull_len - 1):
            lcwa_coeffs.append(0)

        for i in range(hull_len, len(lcwa_coeffs)):
            sum3 = 0.0
            for j in range(i - hull_len, i):
                sum3 += lcwa_coeffs[j] * (i - j)
            hull_coeffs.append(sum3 / den3)

        os_list = []
        hma = 0.0
        inv_hma = 0.0
        len_hull = len(hull_coeffs)

        for i in range(len_hull):
            hma += df['close'].iloc[i] * hull_coeffs[i]
            inv_hma += df['close'].iloc[len_hull - 1 - i] * hull_coeffs[i]

        hso = hma - inv_hma
        cmean = df['close'].rolling(window=len(df)).apply(lambda x: np.sum(np.abs(x))) / len(df) * mult

        os = 0
        os_values = []
        for i in range(len(df)):
            if i == 0:
                os_values.append(0)
            else:
                if (hso[i] > cmean[i] and hso[i - 1] <= cmean[i - 1]) or (hso[i] < -cmean[i] and hso[i - 1] >= -cmean[i - 1]):
                    os_values.append(0)
                elif hso[i] < hso[i - 1] and hso[i] > cmean[i]:
                    os_values.append(-1)
                elif hso[i] > hso[i - 1] and hso[i] < -cmean[i]:
                    os_values.append(1)
                else:
                    os_values.append(os_values[-1])

        df['os'] = pd.Series(os_values, index=df.index)

        # Trend and price calculations
        trend = 0
        nextTrend = 0
        maxLowPrice = df['low'].shift(1).fillna(df['low'])
        minHighPrice = df['high'].shift(1).fillna(df['high'])

        up = 0.0
        down = 0.0
        atrHigh = 0.0
        atrLow = 0.0

        atr2 = df['close'].rolling(window=100).apply(lambda x: np.std(x)) / 2
        dev = channelDeviation * atr2

        for idx in range(len(df)):
            if idx >= amplitude:
                highPrice = df['high'].iloc[idx - abs(df['high'].rolling(window=amplitude).apply(lambda x: np.argmax(x)).iloc[idx])]
                lowPrice = df['low'].iloc[idx - abs(df['low'].rolling(window=amplitude).apply(lambda x: np.argmin(x)).iloc[idx])]
            else:
                highPrice = df['high'].iloc[idx]
                lowPrice = df['low'].iloc[idx]

            highma = df['high'].rolling(window=amplitude).mean().iloc[idx]
            lowma = df['low'].rolling(window=amplitude).mean().iloc[idx]

            if nextTrend == 1:
                maxLowPrice = max(lowPrice, maxLowPrice)
                if highma < maxLowPrice and df['close'].iloc[idx] < df['low'].shift(1).fillna(df['low']).iloc[idx]:
                    trend = 1
                    nextTrend = 0
                    minHighPrice = highPrice
            else:
                minHighPrice = min(highPrice, minHighPrice)
                if lowma > minHighPrice and df['close'].iloc[idx] > df['high'].shift(1).fillna(df['high']).iloc[idx]:
                    trend = 0
                    nextTrend = 1
                    maxLowPrice = lowPrice

            if trend == 0:
                if idx > 0 and trend != 0:
                    up = down if pd.isna(down) else down
                    arrowUp = up - atr2.iloc[idx]
                else:
                    up = maxLowPrice if pd.isna(up) else max(maxLowPrice, up)
                atrHigh = up + dev.iloc[idx]
                atrLow = up - dev.iloc[idx]
            else:
                if idx > 0 and trend != 1:
                    down = up if pd.isna(up) else up
                    arrowDown = down + atr2.iloc[idx]
                else:
                    down = minHighPrice if pd.isna(down) else min(minHighPrice, down)
                atrHigh = down + dev.iloc[idx]
                atrLow = down - dev.iloc[idx]

            ht = up if trend == 0 else down
            df.loc[idx, 'ht'] = ht  # Use loc to assign value to specific row

        # Entry signals
        buySignal = (not pd.isna(arrowUp) and trend == 0 and trend != 1) if idx > 0 else False
        sellSignal = (not pd.isna(arrowDown) and trend == 1 and trend != 0) if idx > 0 else False

        df['buySignal'] = buySignal
        df['sellSignal'] = sellSignal

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
        open_trade = None

        for idx in range(len(df)):
            row = df.iloc[idx]
            if row['buySignal'] and not open_trade:
                open_trade = {
                    'entry_time': str(row['timestamp']),
                    'entry_price': float(row['close']),
                    'direction': 'LONG'
                }
            elif row['sellSignal'] and open_trade:
                open_trade['exit_time'] = str(row['timestamp'])
                open_trade['exit_price'] = float(row['close'])
                open_trade['pnl'] = open_trade['exit_price'] - open_trade['entry_price']
                open_trade['exit_reason'] = 'sellSignal'
                trades.append(open_trade)
                open_trade = None

        if open_trade:
            open_trade['exit_time'] = None
            open_trade['exit_price'] = None
            open_trade['pnl'] = None
            trades.append(open_trade)

        return {'trades': trades}