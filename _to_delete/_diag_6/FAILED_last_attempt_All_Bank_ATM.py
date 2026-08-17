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
        
        lcwa_coeffs = [0] * hull_len
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

        os = 0
        len_hull = len(hull_coeffs) - 1
        hma = 0.0
        inv_hma = 0.0

        hma_values = []
        inv_hma_values = []

        for i in range(len_hull + 1):
            hma += df['close'].iloc[i] * hull_coeffs[i]
            inv_hma += df['close'].iloc[len_hull - i] * hull_coeffs[i]
            hma_values.append(hma)
            inv_hma_values.append(inv_hma)

        hso = np.array(hma_values) - np.array(inv_hma_values)
        cmean = df['close'].rolling(window=len(df)).apply(lambda x: np.sum(np.abs(x)), raw=True) / len(df) * mult

        os_values = []
        for idx in range(len(df)):
            if idx == 0:
                os_values.append(0)
                continue
            if (hso[idx] > cmean.iloc[idx]) and (hso[idx - 1] <= cmean.iloc[idx - 1]):
                os_values.append(0)
            elif (hso[idx] < cmean.iloc[idx]) and (hso[idx - 1] >= cmean.iloc[idx - 1]):
                os_values.append(0)
            elif (hso[idx] < hso[idx - 1]) and (hso[idx] > cmean.iloc[idx]):
                os_values.append(-1)
            elif (hso[idx] > hso[idx - 1]) and (hso[idx] < -cmean.iloc[idx]):
                os_values.append(1)
            else:
                os_values.append(os_values[-1])

        # Initialize trend variables
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

        atr2 = df['close'].rolling(window=100).apply(lambda x: np.std(x), raw=True) / 2
        dev = channelDeviation * atr2

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

            ht = up if trend == 0 else down

            # Store results in DataFrame
            df.loc[idx, 'hso'] = hso[idx]
            df.loc[idx, 'cmean'] = cmean.iloc[idx]
            df.loc[idx, 'atrHigh'] = atrHigh
            df.loc[idx, 'atrLow'] = atrLow
            df.loc[idx, 'ht'] = ht
            df.loc[idx, 'arrowUp'] = arrowUp
            df.loc[idx, 'arrowDown'] = arrowDown
            df.loc[idx, 'trend'] = trend
            df.loc[idx, 'os'] = os_values[idx]

        return df.bfill().fillna(0.0)

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
            longEntry = not pd.isna(row['arrowUp']) and row['trend'] == 0 and row['trend'] != 1
            shortEntry = not pd.isna(row['arrowDown']) and row['trend'] == 1 and row['trend'] != 0

            if longEntry and open_trade is None:
                open_trade = {
                    'entry_time': str(row['timestamp']),
                    'entry_price': float(row['atrLow']),
                    'direction': 'LONG'
                }

            if shortEntry and open_trade is None:
                open_trade = {
                    'entry_time': str(row['timestamp']),
                    'entry_price': float(row['atrHigh']),
                    'direction': 'SHORT'
                }

            if open_trade is not None:
                if open_trade['direction'] == 'LONG' and float(row['close']) >= open_trade['entry_price'] + 1:  # Example exit condition
                    trades.append({
                        'entry_time': open_trade['entry_time'],
                        'exit_time': str(row['timestamp']),
                        'direction': 'LONG',
                        'entry_price': open_trade['entry_price'],
                        'exit_price': float(row['close']),
                        'pnl': float(row['close']) - open_trade['entry_price'],
                        'exit_reason': 'Take Profit'
                    })
                    open_trade = None
                elif open_trade['direction'] == 'SHORT' and float(row['close']) <= open_trade['entry_price'] - 1:  # Example exit condition
                    trades.append({
                        'entry_time': open_trade['entry_time'],
                        'exit_time': str(row['timestamp']),
                        'direction': 'SHORT',
                        'entry_price': open_trade['entry_price'],
                        'exit_price': float(row['close']),
                        'pnl': open_trade['entry_price'] - float(row['close']),
                        'exit_reason': 'Take Profit'
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
                'exit_reason': 'Open Position'
            })

        return {'trades': trades}