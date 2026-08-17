from strategy_kernel import StrategyKernel, SignalEvent
import pandas as pd
import numpy as np

class AllBankATM(StrategyKernel):
    strategy_id = 'all_bank_atm'
    display_name = 'All Bank ATM'
    live_capable = False

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        length = 11
        mult = 2.0
        amplitude = 2
        channelDeviation = 2
        showArrows = True
        showChannels = False

        short_len = int(length / 2)
        hull_len = int(np.sqrt(length))
        den1 = short_len * (short_len + 1) / 2
        den2 = length * (length + 1) / 2
        den3 = hull_len * (hull_len + 1) / 2

        lcwa_coeffs = []
        hull_coeffs = []

        # Initialize coefficients
        for i in range(length):
            sum1 = max(short_len - i, 0)
            sum2 = length - i
            lcwa_coeffs.append(2 * (sum1 / den1) - (sum2 / den2))

        lcwa_coeffs = lcwa_coeffs[::-1] + [0] * (hull_len - 1)  # Fill with zeros for hull coefficients
        for i in range(hull_len, len(lcwa_coeffs)):
            sum3 = sum(lcwa_coeffs[j] * (i - j) for j in range(i - hull_len, i))
            hull_coeffs.append(sum3 / den3)

        os_values = []
        hma_values = []
        inv_hma_values = []

        for idx in range(len(df)):
            hma = 0.0
            inv_hma = 0.0
            for i in range(len(hull_coeffs) + 1):
                if idx - i >= 0:
                    hma += df['close'].iloc[idx - i] * hull_coeffs[i]
                    inv_hma += df['close'].iloc[idx - (len(hull_coeffs) - i)] * hull_coeffs[i]
            hma_values.append(hma)
            inv_hma_values.append(inv_hma)

        hso = np.array(hma_values) - np.array(inv_hma_values)
        cmean = df['close'].rolling(window=len(df)).apply(lambda x: np.sum(np.abs(x))) / len(df) * mult

        os = []
        for idx in range(len(df)):
            if idx == 0:
                os.append(0)
            else:
                os_prev = os[-1]
                if (hso[idx] > cmean.iloc[idx]) and (hso[idx - 1] <= cmean.iloc[idx - 1]):
                    os.append(1)
                elif (hso[idx] < -cmean.iloc[idx]) and (hso[idx - 1] >= -cmean.iloc[idx - 1]):
                    os.append(-1)
                elif (hso[idx] < hso[idx - 1]) and (hso[idx] > cmean.iloc[idx]):
                    os.append(-1)
                elif (hso[idx] > hso[idx - 1]) and (hso[idx] < -cmean.iloc[idx]):
                    os.append(1)
                else:
                    os.append(os_prev)

        df['os'] = pd.Series(os, index=df.index)

        trend = 0
        nextTrend = 0
        maxLowPrice = df['low'].shift(1).fillna(df['low'])
        minHighPrice = df['high'].shift(1).fillna(df['high'])

        up = 0.0
        down = 0.0
        atrHigh = 0.0
        atrLow = 0.0
        arrowUp = np.nan
        arrowDown = np.nan

        atr2 = df['close'].rolling(window=100).apply(lambda x: np.std(x)) / 2
        dev = channelDeviation * atr2

        highPrice = df['high'].rolling(window=amplitude).max()
        lowPrice = df['low'].rolling(window=amplitude).min()
        highma = df['high'].rolling(window=amplitude).mean()
        lowma = df['low'].rolling(window=amplitude).mean()

        for idx in range(len(df)):
            if nextTrend == 1:
                maxLowPrice = max(lowPrice.iloc[idx], maxLowPrice)
                if highma.iloc[idx] < maxLowPrice and df['close'].iloc[idx] < df['low'].iloc[idx - 1]:
                    trend = 1
                    nextTrend = 0
                    minHighPrice = highPrice.iloc[idx]
            else:
                minHighPrice = min(highPrice.iloc[idx], minHighPrice)
                if lowma.iloc[idx] > minHighPrice and df['close'].iloc[idx] > df['high'].iloc[idx - 1]:
                    trend = 0
                    nextTrend = 1
                    maxLowPrice = lowPrice.iloc[idx]

            if trend == 0:
                if idx > 0 and trend != 0:
                    up = down if pd.isna(down) else down
                    arrowUp = up - atr2.iloc[idx]
                else:
                    up = max(maxLowPrice, up) if pd.isna(up) else up
                atrHigh = up + dev.iloc[idx]
                atrLow = up - dev.iloc[idx]
            else:
                if idx > 0 and trend != 1:
                    down = up if pd.isna(up) else up
                    arrowDown = down + atr2.iloc[idx]
                else:
                    down = min(minHighPrice, down) if pd.isna(down) else down
                atrHigh = down + dev.iloc[idx]
                atrLow = down - dev.iloc[idx]

            df.loc[idx, 'ht'] = up if trend == 0 else down
            df.loc[idx, 'atrHigh'] = atrHigh
            df.loc[idx, 'atrLow'] = atrLow

        buySignal = (arrowUp is not np.nan) and (trend == 0) and (trend.shift(1) == 1)
        sellSignal = (arrowDown is not np.nan) and (trend == 1) and (trend.shift(1) == 0)

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