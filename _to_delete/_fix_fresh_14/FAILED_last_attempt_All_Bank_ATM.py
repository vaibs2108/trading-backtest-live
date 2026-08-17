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
            lcwa_coeffs.insert(0, 2 * (sum1 / den1) - (sum2 / den2))

        for i in range(hull_len - 1):
            lcwa_coeffs.insert(0, 0)

        for i in range(hull_len, len(lcwa_coeffs)):
            sum3 = 0.0
            for j in range(i - hull_len, i):
                sum3 += lcwa_coeffs[j] * (i - j)
            hull_coeffs.insert(0, sum3 / den3)

        os_list = []
        hma = 0.0
        inv_hma = 0.0
        len_hull = len(hull_coeffs)

        for i in range(len_hull):
            hma += df['close'].iloc[i] * hull_coeffs[i]
            inv_hma += df['close'].iloc[len_hull - 1 - i] * hull_coeffs[i]

        hso = hma - inv_hma
        cmean = (df['close'].abs().cumsum() / df.index.to_series() * mult).fillna(0)

        os = 0
        os_list = []
        for i in range(len(df)):
            if i == 0:
                os = 0
            else:
                if (hso > cmean).iloc[i] and (hso <= cmean).iloc[i - 1] or (hso < -cmean).iloc[i] and (hso >= -cmean).iloc[i - 1]:
                    os = 0
                elif hso.iloc[i] < hso.iloc[i - 1] and hso.iloc[i] > cmean.iloc[i]:
                    os = -1
                elif hso.iloc[i] > hso.iloc[i - 1] and hso.iloc[i] < -cmean.iloc[i]:
                    os = 1
            os_list.append(os)

        df['os'] = pd.Series(os_list, index=df.index)

        # Trend and ATR calculations
        trend = 0
        nextTrend = 0
        maxLowPrice = df['low'].shift(1).fillna(df['low'])
        minHighPrice = df['high'].shift(1).fillna(df['high'])

        up = 0.0
        down = 0.0
        atrHigh = 0.0
        atrLow = 0.0

        atr2 = df['close'].ta.atr(100) / 2
        dev = channelDeviation * atr2

        highPrice = df['high'].rolling(window=amplitude).max()
        lowPrice = df['low'].rolling(window=amplitude).min()
        highma = df['high'].ta.sma(amplitude)
        lowma = df['low'].ta.sma(amplitude)

        for i in range(len(df)):
            if nextTrend == 1:
                maxLowPrice = max(lowPrice.iloc[i], maxLowPrice)
                if highma.iloc[i] < maxLowPrice and df['close'].iloc[i] < df['low'].shift(1).iloc[i]:
                    trend = 1
                    nextTrend = 0
                    minHighPrice = highPrice.iloc[i]
            else:
                minHighPrice = min(highPrice.iloc[i], minHighPrice)
                if lowma.iloc[i] > minHighPrice and df['close'].iloc[i] > df['high'].shift(1).iloc[i]:
                    trend = 0
                    nextTrend = 1
                    maxLowPrice = lowPrice.iloc[i]

            if trend == 0:
                if i > 0 and trend != 0:
                    up = down if pd.isna(down) else down
                    arrowUp = up - atr2.iloc[i]
                else:
                    up = max(maxLowPrice, up) if pd.isna(up) else max(maxLowPrice, up)
                atrHigh = up + dev.iloc[i]
                atrLow = up - dev.iloc[i]
            else:
                if i > 0 and trend != 1:
                    down = up if pd.isna(up) else up
                    arrowDown = down + atr2.iloc[i]
                else:
                    down = min(minHighPrice, down) if pd.isna(down) else min(minHighPrice, down)
                atrHigh = down + dev.iloc[i]
                atrLow = down - dev.iloc[i]

            ht = up if trend == 0 else down

        df['ht'] = ht
        df['atrHigh'] = atrHigh
        df['atrLow'] = atrLow

        # Buy and Sell signals
        buySignal = (not pd.isna(arrowUp)) & (trend == 0) & (trend.shift(1) == 1)
        sellSignal = (not pd.isna(arrowDown)) & (trend == 1) & (trend.shift(1) == 0)

        df['buySignal'] = buySignal
        df['sellSignal'] = sellSignal

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
        for idx, row in df.iterrows():
            bar_ts = pd.to_datetime(row['timestamp'])
            longEntry = row.get('buySignal', False)
            shortEntry = row.get('sellSignal', False)

            if longEntry:
                trades.append({
                    'entry_time': str(bar_ts),
                    'exit_time': None,
                    'direction': 'LONG',
                    'entry_price': float(row['close']),
                    'exit_price': None,
                    'pnl': None,
                    'exit_reason': None
                })
            elif shortEntry:
                trades.append({
                    'entry_time': str(bar_ts),
                    'exit_time': None,
                    'direction': 'SHORT',
                    'entry_price': float(row['close']),
                    'exit_price': None,
                    'pnl': None,
                    'exit_reason': None
                })

        # Finalize trades
        for trade in trades:
            if trade['exit_time'] is None:
                trade['exit_time'] = str(bar_ts)
                trade['exit_price'] = float(row['close'])
                trade['pnl'] = trade['exit_price'] - trade['entry_price'] if trade['direction'] == 'LONG' else trade['entry_price'] - trade['exit_price']
        
        return {'trades': trades}