import numpy as np
import pandas as pd

def apply_strategy_ichimoku_trend_optimized(df):
    """Optimized version based on backtest results"""
    return apply_strategy_ichimoku_with_trend(
        df,
        use_trend_confirmation=True,    # ✅ Keep trend confirmation ON
        use_adx_filter=False,           # ✅ Turn ADX filter OFF
        adx_threshold=20,               # ✅ Lower threshold (though filter is off)
        use_volume_confirmation=False   # ✅ No volume confirmation
    )

def apply_strategy_ichimoku_with_trend(df,
                                      use_trend_confirmation=True,
                                      use_volume_confirmation=False,
                                      use_adx_filter=False,
                                      adx_threshold=20):
    """
    Ichimoku Strategy with Trend Confirmation for Bank Nifty 5-minute trading.
    Adds trend features to improve win rate and profit factor.
    
    Parameters:
    - use_trend_confirmation: Use EMA trends to filter trades
    - use_volume_confirmation: Use volume to confirm trends (if volume data available)
    - use_adx_filter: Use ADX to filter low-trend periods
    - adx_threshold: Minimum ADX for trending markets
    
    Returns: df_with_features, long_entries, long_exits, short_entries, short_exits
    """
    
    # Make a copy to avoid modifying original
    df_out = df.copy()
    
    # ==================== ICHIMOKU CLOUD CALCULATIONS ====================
    print("Calculating Ichimoku Cloud features...")
    
    # 1. Tenkan-sen (Conversion Line): (9-period high + 9-period low)/2
    period9_high = df_out['high'].rolling(window=9, min_periods=1).max()
    period9_low = df_out['low'].rolling(window=9, min_periods=1).min()
    df_out['tenkan_sen'] = (period9_high + period9_low) / 2
    
    # 2. Kijun-sen (Base Line): (26-period high + 26-period low)/2
    period26_high = df_out['high'].rolling(window=26, min_periods=1).max()
    period26_low = df_out['low'].rolling(window=26, min_periods=1).min()
    df_out['kijun_sen'] = (period26_high + period26_low) / 2
    
    # 3. Senkou Span A (Leading Span A): (Tenkan + Kijun)/2 shifted 26 periods forward
    df_out['senkou_span_a'] = ((df_out['tenkan_sen'] + df_out['kijun_sen']) / 2).shift(26)
    
    # 4. Senkou Span B (Leading Span B): (52-period high + 52-period low)/2 shifted 26 periods forward
    period52_high = df_out['high'].rolling(window=52, min_periods=1).max()
    period52_low = df_out['low'].rolling(window=52, min_periods=1).min()
    df_out['senkou_span_b'] = ((period52_high + period52_low) / 2).shift(26)
    
    # 5. Chikou Span (Lagging Span): Current Close (plotted 26 bars back)
    df_out['chikou_span'] = df_out['close']
    
    # ==================== TREND FEATURES ====================
    if use_trend_confirmation or use_adx_filter:
        print("Calculating Trend features...")
        
        # Multiple timeframe EMAs for trend confirmation
        df_out['ema_9'] = df_out['close'].ewm(span=9, adjust=False).mean()
        df_out['ema_21'] = df_out['close'].ewm(span=21, adjust=False).mean()
        df_out['ema_50'] = df_out['close'].ewm(span=50, adjust=False).mean()
        df_out['ema_100'] = df_out['close'].ewm(span=100, adjust=False).mean()
        
        # Trend direction (1: bullish, -1: bearish, 0: neutral)
        df_out['trend_short'] = np.where(df_out['ema_9'] > df_out['ema_21'], 1, -1)
        df_out['trend_medium'] = np.where(df_out['ema_21'] > df_out['ema_50'], 1, -1)
        df_out['trend_long'] = np.where(df_out['ema_50'] > df_out['ema_100'], 1, -1)
        
        # Trend alignment score (0-3, higher = better alignment)
        df_out['trend_alignment'] = (
            (df_out['trend_short'] == df_out['trend_medium']).astype(int) +
            (df_out['trend_medium'] == df_out['trend_long']).astype(int) +
            (df_out['trend_short'] == df_out['trend_long']).astype(int)
        )
        
        # Trend strength indicators
        df_out['ema_slope_9'] = df_out['ema_9'] - df_out['ema_9'].shift(3)
        df_out['ema_slope_21'] = df_out['ema_21'] - df_out['ema_21'].shift(5)
        
        # Price position relative to EMAs
        df_out['price_above_ema21'] = df_out['close'] > df_out['ema_21']
        df_out['price_above_ema50'] = df_out['close'] > df_out['ema_50']
        df_out['price_above_ema100'] = df_out['close'] > df_out['ema_100']
        
        # EMA alignment (bullish when all aligned above)
        df_out['ema_aligned_bullish'] = (
            (df_out['ema_9'] > df_out['ema_21']) & 
            (df_out['ema_21'] > df_out['ema_50']) & 
            (df_out['ema_50'] > df_out['ema_100'])
        )
        
        df_out['ema_aligned_bearish'] = (
            (df_out['ema_9'] < df_out['ema_21']) & 
            (df_out['ema_21'] < df_out['ema_50']) & 
            (df_out['ema_50'] < df_out['ema_100'])
        )
    
    # ==================== ADX FOR TREND STRENGTH ====================
    if use_adx_filter:
        print("Calculating ADX for trend strength...")
        
        # Simplified ADX calculation
        high = df_out['high'].values
        low = df_out['low'].values
        close = df_out['close'].values
        n = len(df_out)
        
        # Calculate True Range
        tr = np.zeros(n)
        for i in range(1, n):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i-1])
            lc = abs(low[i] - close[i-1])
            tr[i] = max(hl, hc, lc)
        
        # Calculate ATR
        atr = np.zeros(n)
        atr_period = 14
        for i in range(n):
            if i < atr_period:
                atr[i] = np.mean(tr[:i+1])
            else:
                atr[i] = (atr[i-1] * (atr_period - 1) + tr[i]) / atr_period
        
        # Calculate +DM and -DM
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        
        for i in range(1, n):
            up_move = high[i] - high[i-1]
            down_move = low[i-1] - low[i]
            
            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            else:
                plus_dm[i] = 0
                
            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move
            else:
                minus_dm[i] = 0
        
        # Smooth +DM and -DM
        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        
        for i in range(n):
            if i < atr_period:
                plus_di[i] = 100 * np.mean(plus_dm[:i+1]) / atr[i] if atr[i] != 0 else 0
                minus_di[i] = 100 * np.mean(minus_dm[:i+1]) / atr[i] if atr[i] != 0 else 0
            else:
                plus_di[i] = 100 * np.mean(plus_dm[i-atr_period+1:i+1]) / atr[i] if atr[i] != 0 else 0
                minus_di[i] = 100 * np.mean(minus_dm[i-atr_period+1:i+1]) / atr[i] if atr[i] != 0 else 0
        
        # Calculate DX and ADX
        dx = np.zeros(n)
        adx = np.zeros(n)
        
        for i in range(n):
            if plus_di[i] + minus_di[i] != 0:
                dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / (plus_di[i] + minus_di[i])
            else:
                dx[i] = 0
        
        # Smooth DX to get ADX
        for i in range(n):
            if i < atr_period:
                adx[i] = np.mean(dx[:i+1])
            else:
                adx[i] = (adx[i-1] * (atr_period - 1) + dx[i]) / atr_period
        
        df_out['adx'] = adx
        df_out['plus_di'] = plus_di
        df_out['minus_di'] = minus_di
        
        # Trend strength signal
        df_out['strong_trend'] = df_out['adx'] > adx_threshold
        df_out['bullish_trend_strength'] = (df_out['plus_di'] > df_out['minus_di']) & (df_out['adx'] > adx_threshold)
        df_out['bearish_trend_strength'] = (df_out['minus_di'] > df_out['plus_di']) & (df_out['adx'] > adx_threshold)
    
    # ==================== VOLUME CONFIRMATION ====================
    if use_volume_confirmation and 'volume' in df_out.columns:
        print("Calculating Volume features...")
        
        # Volume indicators
        df_out['volume_sma_20'] = df_out['volume'].rolling(window=20, min_periods=1).mean()
        df_out['volume_ratio'] = df_out['volume'] / df_out['volume_sma_20'].replace(0, 1)
        
        # Volume trend
        df_out['volume_increasing'] = df_out['volume'] > df_out['volume'].shift(5) * 1.2
    
    # ==================== ICHIMOKU SIGNALS ====================
    print("Generating Ichimoku signals...")
    
    # Cloud (Kumo) calculations
    df_out['kumo_top'] = df_out[['senkou_span_a', 'senkou_span_b']].max(axis=1)
    df_out['kumo_bottom'] = df_out[['senkou_span_a', 'senkou_span_b']].min(axis=1)
    df_out['kumo_width'] = df_out['kumo_top'] - df_out['kumo_bottom']
    
    # Position relative to cloud
    df_out['price_above_kumo'] = df_out['close'] > df_out['kumo_top']
    df_out['price_below_kumo'] = df_out['close'] < df_out['kumo_bottom']
    df_out['price_in_kumo'] = (~df_out['price_above_kumo']) & (~df_out['price_below_kumo'])
    
    # Tenkan-Kijun relationship (TK Cross)
    df_out['tenkan_above_kijun'] = df_out['tenkan_sen'] > df_out['kijun_sen']
    df_out['tk_cross_up'] = (df_out['tenkan_above_kijun'] == True) & (df_out['tenkan_above_kijun'].shift(1) == False)
    df_out['tk_cross_down'] = (df_out['tenkan_above_kijun'] == False) & (df_out['tenkan_above_kijun'].shift(1) == True)
    
    # Price relative to Kijun
    df_out['price_above_kijun'] = df_out['close'] > df_out['kijun_sen']
    df_out['price_below_kijun'] = df_out['close'] < df_out['kijun_sen']
    
    # Chikou span signals
    df_out['chikou_above_price_26'] = df_out['chikou_span'] > df_out['close'].shift(26)
    df_out['chikou_below_price_26'] = df_out['chikou_span'] < df_out['close'].shift(26)
    
    # ==================== TRADING SIGNALS WITH TREND CONFIRMATION ====================
    print("Generating trading signals with trend confirmation...")
    
    n = len(df_out)
    long_entries = np.zeros(n, dtype=bool)
    short_entries = np.zeros(n, dtype=bool)
    long_exits = np.zeros(n, dtype=bool)
    short_exits = np.zeros(n, dtype=bool)
    
    # Fill NaN values
    df_out_filled = df_out.copy()
    for col in df_out_filled.columns:
        if df_out_filled[col].dtype in ['float64', 'int64']:
            df_out_filled[col] = df_out_filled[col].ffill().fillna(0)
    
    # Track trade quality metrics and active position state
    trade_quality_long = np.zeros(n, dtype=float)
    trade_quality_short = np.zeros(n, dtype=float)
    
    in_long = False
    in_short = False
    
    for i in range(26, n):  # Start at 26 to have all Ichimoku components
        # Current values
        current_close = df_out_filled['close'].iloc[i]
        current_tenkan = df_out_filled['tenkan_sen'].iloc[i]
        current_kijun = df_out_filled['kijun_sen'].iloc[i]
        current_kumo_top = df_out_filled['kumo_top'].iloc[i]
        current_kumo_bottom = df_out_filled['kumo_bottom'].iloc[i]
        
        # Previous values
        prev_tenkan = df_out_filled['tenkan_sen'].iloc[i-1]
        prev_kijun = df_out_filled['kijun_sen'].iloc[i-1]
        prev_close = df_out_filled['close'].iloc[i-1]
        
        # Chikou position: current close vs close 26 bars ago
        current_chikou = current_close
        price_26_ago = df_out_filled['close'].iloc[i-26]
        
        # ========== TREND CONFIRMATION SCORES ==========
        trend_score_long = 0
        trend_score_short = 0
        
        if use_trend_confirmation:
            # EMA alignment for trend
            if df_out_filled['ema_aligned_bullish'].iloc[i]:
                trend_score_long += 3
            if df_out_filled['ema_aligned_bearish'].iloc[i]:
                trend_score_short += 3
            
            # Price above/below EMAs
            if df_out_filled['price_above_ema21'].iloc[i]:
                trend_score_long += 1
            if df_out_filled['price_above_ema50'].iloc[i]:
                trend_score_long += 1
            if not df_out_filled['price_above_ema21'].iloc[i]:
                trend_score_short += 1
            if not df_out_filled['price_above_ema50'].iloc[i]:
                trend_score_short += 1
            
            # EMA slopes
            if df_out_filled['ema_slope_9'].iloc[i] > 0:
                trend_score_long += 1
            if df_out_filled['ema_slope_9'].iloc[i] < 0:
                trend_score_short += 1
        
        if use_adx_filter:
            # ADX trend strength
            if df_out_filled['bullish_trend_strength'].iloc[i]:
                trend_score_long += 2
            if df_out_filled['bearish_trend_strength'].iloc[i]:
                trend_score_short += 2
        
        if use_volume_confirmation and 'volume' in df_out_filled.columns:
            # Volume confirmation
            if df_out_filled['volume_ratio'].iloc[i] > 1.2:
                trend_score_long += 1
                trend_score_short += 1
        
        # ========== ICHIMOKU ENTRY CONDITIONS ==========
        long_signal = False
        short_signal = False
        
        # Condition 1: Strong Bullish - Price above cloud, TK cross up, Chikou bullish
        strong_bullish = (
            current_close > current_kumo_top and  # Price above cloud
            current_tenkan > prev_tenkan and  # Tenkan rising
            current_tenkan > current_kijun and  # Tenkan above Kijun
            prev_tenkan <= prev_kijun and  # TK just crossed up
            current_chikou > price_26_ago  # Chikou bullish
        )
        
        # Condition 2: Cloud Breakout - Price breaks above cloud
        cloud_breakout = (
            current_close > current_kumo_top and  # Price above cloud
            prev_close <= df_out_filled['kumo_top'].iloc[i-1] and  # Was at or below cloud
            current_tenkan > current_kijun  # Bullish TK relationship
        )
        
        # Condition 3: Kijun Bounce - Price bounces off Kijun in uptrend
        kijun_bounce = (
            current_close > current_kijun and  # Price above Kijun
            prev_close <= prev_kijun and  # Price was at or below Kijun
            current_close > current_kumo_bottom and  # At least above cloud bottom
            current_tenkan > current_kijun  # Bullish TK
        )
        
        # Condition 4: Cloud Support - Price finds support at cloud
        cloud_support = (
            current_close > current_kumo_bottom and  # Price above cloud bottom
            prev_close <= df_out_filled['kumo_bottom'].iloc[i-1] and  # Was at or below cloud bottom
            current_tenkan > current_kijun  # Bullish TK
        )
        
        # SHORT CONDITIONS (opposite)
        strong_bearish = (
            current_close < current_kumo_bottom and  # Price below cloud
            current_tenkan < prev_tenkan and  # Tenkan falling
            current_tenkan < current_kijun and  # Tenkan below Kijun
            prev_tenkan >= prev_kijun and  # TK just crossed down
            current_chikou < price_26_ago  # Chikou bearish
        )
        
        cloud_breakdown = (
            current_close < current_kumo_bottom and  # Price below cloud
            prev_close >= df_out_filled['kumo_bottom'].iloc[i-1] and  # Was at or above cloud
            current_tenkan < current_kijun  # Bearish TK relationship
        )
        
        kijun_resistance = (
            current_close < current_kijun and  # Price below Kijun
            prev_close >= prev_kijun and  # Price was at or above Kijun
            current_close < current_kumo_top and  # At least below cloud top
            current_tenkan < current_kijun  # Bearish TK
        )
        
        cloud_resistance = (
            current_close < current_kumo_top and  # Price below cloud top
            prev_close >= df_out_filled['kumo_top'].iloc[i-1] and  # Was at or above cloud top
            current_tenkan < current_kijun  # Bearish TK
        )
        
        # ========== COMBINE ICHIMOKU + TREND ==========
        # Calculate overall signal quality
        ichimoku_long_quality = 0
        ichimoku_short_quality = 0
        
        if strong_bullish: ichimoku_long_quality += 3
        if cloud_breakout: ichimoku_long_quality += 2
        if kijun_bounce: ichimoku_long_quality += 1
        if cloud_support: ichimoku_long_quality += 1
        
        if strong_bearish: ichimoku_short_quality += 3
        if cloud_breakdown: ichimoku_short_quality += 2
        if kijun_resistance: ichimoku_short_quality += 1
        if cloud_resistance: ichimoku_short_quality += 1
        
        # Minimum trend score required (adjustable based on how strict you want to be)
        min_trend_score = 2 if use_trend_confirmation or use_adx_filter else 0
        
        # Entry decision with trend confirmation
        if ichimoku_long_quality >= 2 and trend_score_long >= min_trend_score:
            long_signal = True
            trade_quality_long[i] = ichimoku_long_quality + trend_score_long
        
        if ichimoku_short_quality >= 2 and trend_score_short >= min_trend_score:
            short_signal = True
            trade_quality_short[i] = ichimoku_short_quality + trend_score_short
        
        # ========== EXIT CONDITIONS WITH TREND ==========
        if in_long:
            long_exit_condition = (
                (current_tenkan < current_kijun and prev_tenkan >= prev_kijun) or  # TK cross down
                (current_close < current_kijun) or  # Price below Kijun
                (use_trend_confirmation and trend_score_short > trend_score_long and trend_score_short > 3)  # Trend reversed
            )
            if long_exit_condition:
                long_exits[i] = True
                in_long = False

        if in_short:
            short_exit_condition = (
                (current_tenkan > current_kijun and prev_tenkan <= prev_kijun) or  # TK cross up
                (current_close > current_kijun) or  # Price above Kijun
                (use_trend_confirmation and trend_score_long > trend_score_short and trend_score_long > 3)  # Trend reversed
            )
            if short_exit_condition:
                short_exits[i] = True
                in_short = False

        # ========== ENTRY LOGIC ==========
        if long_signal and not in_long:
            if in_short:
                short_exits[i] = True
                in_short = False
            long_entries[i] = True
            in_long = True
        elif short_signal and not in_short:
            if in_long:
                long_exits[i] = True
                in_long = False
            short_entries[i] = True
            in_short = True
    
    # ==================== CONVERT TO SERIES ====================
    long_entries = pd.Series(long_entries, index=df_out.index)
    short_entries = pd.Series(short_entries, index=df_out.index)
    long_exits = pd.Series(long_exits, index=df_out.index)
    short_exits = pd.Series(short_exits, index=df_out.index)
    
    # Shift signals by 1 to avoid look-ahead bias
    long_entries = long_entries.shift(1).fillna(False).infer_objects().astype(bool)
    short_entries = short_entries.shift(1).fillna(False).infer_objects().astype(bool)
    long_exits = long_exits.shift(1).fillna(False).infer_objects().astype(bool)
    short_exits = short_exits.shift(1).fillna(False).infer_objects().astype(bool)
    
    # ==================== ADD TRADE QUALITY METRICS ====================
    df_out['trade_quality_long'] = trade_quality_long
    df_out['trade_quality_short'] = trade_quality_short
    df_out['overall_trade_quality'] = np.maximum(trade_quality_long, trade_quality_short)
    
    # Calculate signal strength with trend
    df_out['ichimoku_signal_strength'] = 0
    
    # Bullish strength factors
    bullish_factors = (
        df_out['price_above_kumo'].astype(int) * 3 +
        df_out['tenkan_above_kijun'].astype(int) * 2 +
        df_out['chikou_above_price_26'].astype(int) * 2 +
        df_out['price_above_kijun'].astype(int) * 1
    )
    
    # Add trend factors if enabled
    if use_trend_confirmation:
        bullish_factors += (
            df_out['ema_aligned_bullish'].astype(int) * 3 +
            df_out['price_above_ema50'].astype(int) * 2 +
            (df_out['ema_slope_9'] > 0).astype(int) * 1
        )
    
    # Bearish strength factors
    bearish_factors = (
        df_out['price_below_kumo'].astype(int) * 3 +
        (~df_out['tenkan_above_kijun']).astype(int) * 2 +
        df_out['chikou_below_price_26'].astype(int) * 2 +
        df_out['price_below_kijun'].astype(int) * 1
    )
    
    # Add trend factors if enabled
    if use_trend_confirmation:
        bearish_factors += (
            df_out['ema_aligned_bearish'].astype(int) * 3 +
            (~df_out['price_above_ema50']).astype(int) * 2 +
            (df_out['ema_slope_9'] < 0).astype(int) * 1
        )
    
    df_out['ichimoku_signal_strength'] = bullish_factors - bearish_factors
    
    # Trend direction with Ichimoku
    df_out['combined_trend'] = np.where(
        df_out['ichimoku_signal_strength'] > 3, 1,
        np.where(df_out['ichimoku_signal_strength'] < -3, -1, 0)
    )
    
    # ==================== STRATEGY SUMMARY ====================
    print(f"\nIchimoku + Trend Strategy Summary:")
    print(f"Long entries: {long_entries.sum()}")
    print(f"Short entries: {short_entries.sum()}")
    print(f"Total trades: {long_entries.sum() + short_entries.sum()}")
    
    # Calculate average trade quality
    if long_entries.sum() > 0:
        avg_long_quality = df_out.loc[long_entries, 'trade_quality_long'].mean()
        print(f"Average long trade quality: {avg_long_quality:.2f}")
    
    if short_entries.sum() > 0:
        avg_short_quality = df_out.loc[short_entries, 'trade_quality_short'].mean()
        print(f"Average short trade quality: {avg_short_quality:.2f}")
    
    # Show trend alignment statistics
    if use_trend_confirmation:
        trend_alignment_counts = df_out['trend_alignment'].value_counts().sort_index()
        print(f"\nTrend Alignment Distribution:")
        for score, count in trend_alignment_counts.items():
            print(f"  Score {score}: {count} bars")
    
    return df_out, long_entries, long_exits, short_entries, short_exits


# ==================== VERSION WITH DIFFERENT TREND STRICTNESS ====================
def apply_strategy_ichimoku_trend_moderate(df):
    """Moderate trend confirmation"""
    return apply_strategy_ichimoku_with_trend(
        df,
        use_trend_confirmation=True,
        use_adx_filter=True,
        adx_threshold=20
    )

def apply_strategy_ichimoku_trend_lenient(df):
    """Lenient trend confirmation (more trades)"""
    return apply_strategy_ichimoku_with_trend(
        df,
        use_trend_confirmation=True,
        use_adx_filter=False,  # No ADX filter
        adx_threshold=15  # Lower threshold
    )

def apply_strategy_ichimoku_trend_strict(df):
    """Strict trend confirmation (higher quality trades)"""
    return apply_strategy_ichimoku_with_trend(
        df,
        use_trend_confirmation=True,
        use_adx_filter=True,
        adx_threshold=25,  # Higher threshold
        use_volume_confirmation=True
    )