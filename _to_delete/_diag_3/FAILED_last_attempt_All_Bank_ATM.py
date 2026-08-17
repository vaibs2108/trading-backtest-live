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
        
        # Standard indicators
        df.ta.atr(length=100, append=True)
        df['ATRr_100'] = df['ATRr_100'] / 2  # Adjust ATR

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

        lcwa_coeffs = [0] * (hull_len - 1) + lcwa_coeffs  # Prepend zeros for hull coefficients

        for i in range(hull_len, len(lcwa_coeffs)):
            sum3 = sum(lcwa_coeffs[j] * (i - j) for j in range(i - hull_len, i))
            hull_coeffs.append(sum3 / den3)

        os_list = []
        hma_list = []
        inv_hma_list = []
        len_hull = len(hull_coeffs)

        for idx in range(len(df)):
            hma = 0.0
            inv_hma = 0.0
            for i in range(len_hull):
                if idx - i >= 0:
                    hma += df['close'].iloc[idx - i] * hull_coeffs[i]
                    inv_hma += df['close'].iloc[idx - (len_hull - 1 - i)] * hull_coeffs[i]
            hma_list.append(hma)
            inv_hma_list.append(inv_hma)

        df['hma'] = pd.Series(hma_list, index=df.index).bfill().fillna(0.0)
        df['inv_hma'] = pd.Series(inv_hma_list, index=df.index).bfill().fillna(0.0)

        hso = df['hma'] - df['inv_hma']
        cmean = df['close'].rolling(window=len(df)).apply(lambda x: np.sum(np.abs(x))) / len(df) * mult

        os = 0
        os_list = []

        for i in range(len(df)):
            if i == 0:
                os_list.append(0)
            else:
                if (hso[i] > cmean[i] and hso[i - 1] <= cmean[i - 1]) or (hso[i] < -cmean[i] and hso[i - 1] >= -cmean[i - 1]):
                    os_list.append(0)
                elif hso[i] < hso[i - 1] and hso[i] > cmean[i]:
                    os_list.append(-1)
                elif hso[i] > hso[i - 1] and hso[i] < -cmean[i]:
                    os_list.append(1)
                else:
                    os_list.append(os_list[-1])

        df['os'] = pd.Series(os_list, index=df.index).bfill().fillna(0.0)

        # Trend and price tracking
        trend = 0
        nextTrend = 0
        maxLowPrice = df['low'].iloc[0]
        minHighPrice = df['high'].iloc[0]

        up = 0.0
        down = 0.0
        atrHigh = 0.0
        atrLow = 0.0
        arrowUp = np.nan
        arrowDown = np.nan

        dev = channelDeviation * df['ATRr_100']

        for idx in range(len(df)):
            highPrice = df['high'].iloc[idx - abs(df['high'].rolling(window=amplitude).apply(lambda x: np.argmax(x)))]
            lowPrice = df['low'].iloc[idx - abs(df['low'].rolling(window=amplitude).apply(lambda x: np.argmin(x)))]

            if nextTrend == 1:
                maxLowPrice = max(lowPrice, maxLowPrice)
                if df['close'].iloc[idx] < df['low'].iloc[idx - 1] and df['close'].iloc[idx] < df['low'].iloc[idx]:
                    trend = 1
                    nextTrend = 0
                    minHighPrice = highPrice
            else:
                minHighPrice = min(highPrice, minHighPrice)
                if df['close'].iloc[idx] > df['high'].iloc[idx - 1] and df['close'].iloc[idx] > df['high'].iloc[idx]:
                    trend = 0
                    nextTrend = 1
                    maxLowPrice = lowPrice

            if trend == 0:
                if idx > 0 and trend != 0:
                    up = down if np.isnan(down) else down
                    arrowUp = up - df['ATRr_100'].iloc[idx]
                else:
                    up = max(maxLowPrice, up) if np.isnan(up) else up
                atrHigh = up + dev.iloc[idx]
                atrLow = up - dev.iloc[idx]
            else:
                if idx > 0 and trend != 1:
                    down = up if np.isnan(up) else up
                    arrowDown = down + df['ATRr_100'].iloc[idx]
                else:
                    down = min(minHighPrice, down) if np.isnan(down) else down
                atrHigh = down + dev.iloc[idx]
                atrLow = down - dev.iloc[idx]

            ht = up if trend == 0 else down

        df['ht'] = ht
        df['atrHigh'] = atrHigh
        df['atrLow'] = atrLow

        return df

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
            longEntry = (not np.isnan(row.get('arrowUp', np.nan)) and row['trend'] == 0 and row['trend'] != 1)
            shortEntry = (not np.isnan(row.get('arrowDown', np.nan)) and row['trend'] == 1 and row['trend'] != 0)

            if longEntry and open_trade is None:
                open_trade = {
                    'entry_time': str(row['timestamp']),
                    'entry_price': float(row['atrLow']),
                    'direction': 'LONG'
                }
            elif shortEntry and open_trade is None:
                open_trade = {
                    'entry_time': str(row['timestamp']),
                    'entry_price': float(row['atrHigh']),
                    'direction': 'SHORT'
                }
            elif open_trade is not None:
                if open_trade['direction'] == 'LONG' and float(row['close']) >= open_trade['entry_price']:
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
                elif open_trade['direction'] == 'SHORT' and float(row['close']) <= open_trade['entry_price']:
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