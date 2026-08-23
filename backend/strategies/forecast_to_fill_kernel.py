"""
forecast_to_fill_kernel.py -- Adaptations of the "Forecast-to-Fill" trend +
momentum architecture (Singha, Aguilera-Toste & Lahiri, arXiv:2511.08571,
Nov 2025) for BankNifty. Two variants in this file:

  - ForecastToFillKernel: DAILY bars, faithful to the paper's own
    timeframe and lookback windows (252-day z-score, 50-day momentum,
    30-day max hold).
  - ForecastToFill5mKernel: 5-MINUTE bars, with every lookback window
    re-derived for intraday dynamics rather than literally converting
    "days" to "bars" (see its own docstring for the full reasoning).

RESEARCH ONLY, BOTH VARIANTS. Neither is registered with strategy_kernel's
registry or reachable from the backtest page -- this project is in an
explicit discovery/testing phase for this line of research and must not
be wired in without direct approval.

The daily variant's design notes follow; the 5m variant's own class
docstring covers what's different about it.

Deviations from the paper (documented, not hidden):
  - The paper freezes z-score/EMA statistics on a rolling 10-year TRAIN
    window and holds them fixed through a 6-month TEST window, re-fit
    monthly. Our available daily history (~4 years, from 2022-07) is far
    too short for a proper 10-year freeze. Instead we use a ROLLING
    252-day window for the slope mean/std (causal -- only past data up to
    bar t is used, so there is still no lookahead), which is a standard,
    defensible equivalent that trades the paper's "freeze and don't
    re-adapt for 6 months" discipline for continuous, always-past-only
    adaptation. This is a real methodological difference worth knowing
    if comparing directly to the paper's own reported numbers.
  - The paper is LONG-ONLY (deliberately, citing gold's asymmetric
    drift/storage-cost structure). This adaptation is BIDIRECTIONAL
    (mirrors the long rules for shorts) to stay comparable with every
    other strategy in this project, which all trade both directions.
  - The paper sizes positions via a friction-adjusted, volatility-targeted
    fractional-Kelly formula. This kernel uses the SAME fixed-lot sizing
    (qty = lot_size * lot_multiplier) as every other strategy tested this
    session, specifically so the backtest P&L numbers are apples-to-apples
    comparable -- Kelly/vol-target sizing would change the dollar P&L
    scale and break that comparison. A sizing overlay could be added later
    as a separate, explicit step.
  - "De-risk: halve position if bear probability > 0.5" from the paper
    becomes a full close here (no partial-position mechanics in this
    engine's fixed-lot-per-trade model).

Signal construction (matches the paper 1:1 otherwise):
  1. EMA-smooth log(close), span=EMA_SPAN.
  2. Slope = first difference of the smoothed series.
  3. Standardize slope via a rolling 252-day mean/std (causal), clip to
     [-3, 3], map to p_trend in [0, 1].
  4. Blend with a MOMENTUM_K-day momentum indicator (price above vs below
     its value K days ago): p_bull = OMEGA * p_trend + (1-OMEGA) * momentum.
  5. Enter LONG when p_bull >= ENTRY_THRESHOLD and slope > 0; enter SHORT
     (mirrored) when p_bull <= 1-ENTRY_THRESHOLD and slope < 0.
  6. Exit on whichever binds first: hard stop (entry -/+ HARD_STOP_ATR x
     ATR14), a ratcheting trailing stop (peak -/+ TRAIL_STOP_ATR x ATR14,
     only ever tightens), a MAX_HOLD_DAYS timeout, or a regime de-risk
     close if the opposite-side probability crosses DERISK_THRESHOLD.
"""
import numpy as np
import pandas as pd
from typing import Optional

from strategy_kernel import StrategyKernel, SignalEvent
from config import get_settings, INSTRUMENT_META


class ForecastToFillKernel(StrategyKernel):
    """Trend+momentum daily swing strategy adapted from arXiv:2511.08571."""

    strategy_id = "forecast_to_fill_daily"
    live_capable = False

    EMA_SPAN = 20
    ZSCORE_WINDOW = 252
    MOMENTUM_K = 50
    BLEND_OMEGA = 0.6
    ENTRY_THRESHOLD = 0.52
    ATR_PERIOD = 14
    HARD_STOP_ATR = 2.0
    TRAIL_STOP_ATR = 1.5
    MAX_HOLD_DAYS = 30
    DERISK_THRESHOLD = 0.5
    MIN_WARMUP = 260  # >= ZSCORE_WINDOW + a small buffer

    def reset(self):
        pass

    def on_bar(self, bar_idx, base_df, row, position, context):
        """Not used -- this kernel's run_backtest() is self-contained on
        daily bars and does not go through the shared 5m on_bar chain."""
        return None

    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 30, lot_multiplier: int = 1,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> dict:
        daily = frames.get("1D")
        if daily is None or len(daily) < self.MIN_WARMUP + 10:
            return {"error": "Insufficient daily data", "trades": [], "stats": {}}

        base = daily.sort_values("timestamp").reset_index(drop=True).copy()
        base["timestamp"] = pd.to_datetime(base["timestamp"])

        qty = lot_size * lot_multiplier
        closes = base["close"].values.astype(float)
        highs = base["high"].values.astype(float)
        lows = base["low"].values.astype(float)
        n = len(base)

        # -- Signal construction (vectorized, causal) --
        log_close = np.log(closes)
        ema = pd.Series(log_close).ewm(span=self.EMA_SPAN, adjust=False).mean().values
        slope = np.empty(n)
        slope[0] = 0.0
        slope[1:] = ema[1:] - ema[:-1]

        slope_s = pd.Series(slope)
        roll_mean = slope_s.rolling(self.ZSCORE_WINDOW, min_periods=self.ZSCORE_WINDOW).mean().values
        roll_std = slope_s.rolling(self.ZSCORE_WINDOW, min_periods=self.ZSCORE_WINDOW).std().values
        with np.errstate(invalid="ignore", divide="ignore"):
            z = (slope - roll_mean) / roll_std
        z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
        z_clip = np.clip(z, -3, 3)
        p_trend = (z_clip + 3) / 6.0

        mom = np.zeros(n)
        mom[self.MOMENTUM_K:] = (closes[self.MOMENTUM_K:] > closes[:-self.MOMENTUM_K]).astype(float)

        p_bull = self.BLEND_OMEGA * p_trend + (1 - self.BLEND_OMEGA) * mom

        # ATR(14), Wilder-style (matches the "atr" column convention used
        # elsewhere in this codebase: pandas_ta .ta.atr(length=14)).
        prev_close = np.empty(n)
        prev_close[0] = closes[0]
        prev_close[1:] = closes[:-1]
        tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))
        atr = pd.Series(tr).ewm(alpha=1.0 / self.ATR_PERIOD, adjust=False).mean().values

        _trade_start_date = pd.to_datetime(start_date) if start_date else None
        _trade_end_date = pd.to_datetime(end_date).date() if end_date else None

        trades = []
        position = "NONE"
        entry_price = sl = 0.0
        entry_idx = 0
        peak = 0.0

        def book(idx, exit_px, reason):
            nonlocal position
            net = (exit_px - entry_price) * qty if position == "LONG" \
                else (entry_price - exit_px) * qty
            pnl_pts = round(exit_px - entry_price, 2) if position == "LONG" \
                else round(entry_price - exit_px, 2)
            trades.append({
                "entry_time": base.iloc[entry_idx]["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": base.iloc[idx]["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "direction": position,
                "entry_price": round(entry_price, 2),
                "sl": round(sl, 2),
                "exit_price": round(exit_px, 2),
                "exit_reason": reason,
                "pnl": round(net, 2),
                "pnl_pts": pnl_pts,
                "pnl_inr": round(net, 2),
                "hold_days": idx - entry_idx,
            })
            position = "NONE"

        for i in range(self.MIN_WARMUP, n):
            if _trade_end_date is not None and base.iloc[i]["timestamp"].date() > _trade_end_date:
                break

            c, h, l = closes[i], highs[i], lows[i]
            atr_v = atr[i]
            if pd.isna(atr_v) or atr_v <= 0:
                atr_v = c * 0.01

            if position == "LONG":
                if h > peak:
                    peak = h
                trail = peak - self.TRAIL_STOP_ATR * atr_v
                if trail > sl:
                    sl = trail
                if l <= sl:
                    book(i, sl if sl <= h else base.iloc[i]["open"], "SL_HIT")
                elif (i - entry_idx) >= self.MAX_HOLD_DAYS:
                    book(i, c, "TIMEOUT")
                elif p_bull[i] <= (1 - self.DERISK_THRESHOLD):
                    book(i, c, "DERISK_EXIT")

            elif position == "SHORT":
                if l < peak:
                    peak = l
                trail = peak + self.TRAIL_STOP_ATR * atr_v
                if trail < sl:
                    sl = trail
                if h >= sl:
                    book(i, sl if sl >= l else base.iloc[i]["open"], "SL_HIT")
                elif (i - entry_idx) >= self.MAX_HOLD_DAYS:
                    book(i, c, "TIMEOUT")
                elif p_bull[i] >= self.DERISK_THRESHOLD:
                    book(i, c, "DERISK_EXIT")

            if position == "NONE" and roll_std[i] > 0 and not np.isnan(roll_std[i]):
                if p_bull[i] >= self.ENTRY_THRESHOLD and slope[i] > 0:
                    position = "LONG"
                    entry_price = c
                    entry_idx = i
                    peak = h
                    sl = c - self.HARD_STOP_ATR * atr_v
                elif p_bull[i] <= (1 - self.ENTRY_THRESHOLD) and slope[i] < 0:
                    position = "SHORT"
                    entry_price = c
                    entry_idx = i
                    peak = l
                    sl = c + self.HARD_STOP_ATR * atr_v

        if position != "NONE":
            trades.append({
                "entry_time": base.iloc[entry_idx]["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": "", "direction": position, "entry_price": round(entry_price, 2),
                "sl": round(sl, 2), "exit_price": None, "exit_reason": "OPEN",
                "pnl": 0, "pnl_pts": 0, "pnl_inr": 0, "hold_days": (n - 1) - entry_idx,
            })

        if _trade_start_date is not None and trades:
            trades = [t for t in trades if pd.to_datetime(t["entry_time"]) >= _trade_start_date]

        if not trades:
            return {"error": "No trades generated", "trades": [], "stats": {}}

        tdf = pd.DataFrame(trades)
        total = len(tdf)
        wins = (tdf["pnl"] > 0).sum()
        gp = tdf[tdf["pnl"] > 0]["pnl"].sum()
        gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
        equity = initial_capital + tdf["pnl"].cumsum()
        dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()
        pf = round(gp / gl, 2) if gl > 0 else float("inf")
        stats = {
            "total_trades": total,
            "win_rate_pct": round(100 * wins / total, 1) if total else 0,
            "profit_factor": pf,
            "total_pnl": round(tdf["pnl"].sum(), 2),
            "max_drawdown_pct": round(dd, 2),
            "avg_hold_days": round(tdf["hold_days"].mean(), 1),
        }
        return {"trades": trades, "stats": stats, "error": None}


class ForecastToFill5mKernel(StrategyKernel):
    """5-minute-bar adaptation of the same arXiv:2511.08571 signal
    architecture (EMA-slope + momentum blend -> bounded p_bull, ATR hard
    + trailing stop, regime de-risk). Unlike ForecastToFillKernel (daily,
    faithful to the paper's own timeframe), every lookback window here is
    RE-DERIVED for 5m BankNifty bars rather than literally converting
    "days" to "bars" 1:1 (a session is ~72 5m bars; converting the paper's
    252-day/50-day windows literally would need 3-4 YEARS of 5m warmup
    and would still describe week-scale trends, not anything an intraday
    system could act on). Instead:
      - EMA_SPAN = 20 bars (~100 min) -- a short intraday trend read.
      - ZSCORE_WINDOW = 500 bars (~7 trading days) -- enough for a stable
        rolling mean/std without years of warmup.
      - MOMENTUM_K = 50 bars (~4 hours, most of one session) -- intraday
        momentum confirmation, not multi-day.
      - ATR_PERIOD = 14 bars -- unchanged, matches the "atr" column
        convention used everywhere else in this codebase.
      - HARD_STOP_ATR / TRAIL_STOP_ATR ratios (2.0 / 1.5) kept as-is --
        these are scale-invariant since ATR itself scales with timeframe.
      - MAX_HOLD_BARS = 96 (~1.3 sessions) replaces the paper's 30-day
        timeout -- a bounded intraday-scale backstop rather than an
        attempted multi-day conversion.

    Reuses this project's existing session-mask / date-aware EOD-cutoff /
    position_hold_mode (INTRADAY vs CARRY_FORWARD) machinery so it is
    directly, fairly comparable to every other 5m strategy on the SAME
    train/validate/full windows -- unlike the daily version, which needed
    its own separate calendar due to a completely different warmup need.

    RESEARCH ONLY. Not registered with strategy_kernel's registry, not
    reachable from the backtest page, and must not be wired in without
    explicit approval -- this project is still in discovery/testing phase
    for this line of research.
    """

    strategy_id = "forecast_to_fill_5m"
    live_capable = False

    EMA_SPAN = 20
    ZSCORE_WINDOW = 500
    MOMENTUM_K = 50
    BLEND_OMEGA = 0.6
    ENTRY_THRESHOLD = 0.52
    HARD_STOP_ATR = 2.0
    TRAIL_STOP_ATR = 1.5
    # Trail activation gate (NOT in the paper -- added after diagnosing the
    # naive port): the paper's "trailing stop = peak - 1.5xATR" starts
    # ratcheting from the entry bar's own high immediately. On DAILY bars
    # that's fine (a day's high barely moves relative to its own ATR), but
    # on 5m bars the peak jitters with ordinary noise almost every bar,
    # racing the trailing level up to near-breakeven within a handful of
    # bars and choking positions off long before the real 2xATR hard stop
    # would ever bind. Confirmed empirically: 86% of exits were SL_HIT at
    # a median 3-bar hold and only -9pt average loss (far short of the
    # ~122pt hard-stop distance) before this gate was added. Every other
    # strategy in this codebase already avoids this via a trail-activation
    # threshold (see TRAIL_ACTIVATION on RegimeTrendKernel etc.) -- this
    # kernel now does the same: the trailing stop only starts tightening
    # once the position is TRAIL_ACTIVATION x ATR in profit; before that,
    # only the original hard stop applies, giving the position room to
    # develop.
    TRAIL_ACTIVATION = 0.5
    MAX_HOLD_BARS = 96
    DERISK_THRESHOLD = 0.5

    _EOD_EXIT_NSE = 15 * 60 + 20
    _EOD_EXIT_NSE_POST_CAS = 15 * 60 + 12
    _EOD_EXIT_MCX = 23 * 60 + 20
    _CAS_START_DATE = pd.Timestamp("2026-08-03").date()

    def reset(self):
        pass

    def on_bar(self, bar_idx, base_df, row, position, context):
        return None

    def _eod_exit_minute_for_date(self, cfg, bar_date) -> int:
        exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
        if exch == "MCX":
            return self._EOD_EXIT_MCX
        if bar_date >= self._CAS_START_DATE:
            return self._EOD_EXIT_NSE_POST_CAS
        return self._EOD_EXIT_NSE

    def _session_mask(self, base: pd.DataFrame, cfg) -> pd.Series:
        exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
        mins = base["_hour"] * 60 + base["_minute"]
        if exch == "MCX":
            start, end_excl = 9 * 60, 23 * 60 + 30
        else:
            start, end_excl = 9 * 60 + 20, 15 * 60 + 20
        return (mins >= start) & (mins < end_excl)

    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 30, lot_multiplier: int = 1,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> dict:
        import strategy as main_strategy

        cfg = get_settings()
        qty = lot_size * lot_multiplier

        base = main_strategy.build_merged_table(frames, with_patterns=False)
        if base.empty:
            return {"error": "No data after merging", "trades": [], "stats": {}}
        base = base[self._session_mask(base, cfg)].copy().reset_index(drop=True)
        if base.empty:
            return {"error": "No data in trading window", "trades": [], "stats": {}}

        _trade_start_date = pd.to_datetime(start_date) if start_date else None
        _trade_end_date = pd.to_datetime(end_date).date() if end_date else None
        if end_date:
            base = base[base["timestamp"].dt.date <= _trade_end_date].copy().reset_index(drop=True)

        n = len(base)
        min_warmup = self.ZSCORE_WINDOW + 10
        if n < min_warmup + 10:
            return {"error": "Insufficient 5m data", "trades": [], "stats": {}}

        closes = base["close"].values.astype(float)
        highs = base["high"].values.astype(float)
        lows = base["low"].values.astype(float)
        atr_col = base["atr"].values.astype(float) if "atr" in base.columns else None

        log_close = np.log(closes)
        ema = pd.Series(log_close).ewm(span=self.EMA_SPAN, adjust=False).mean().values
        slope = np.empty(n)
        slope[0] = 0.0
        slope[1:] = ema[1:] - ema[:-1]

        slope_s = pd.Series(slope)
        roll_mean = slope_s.rolling(self.ZSCORE_WINDOW, min_periods=self.ZSCORE_WINDOW).mean().values
        roll_std = slope_s.rolling(self.ZSCORE_WINDOW, min_periods=self.ZSCORE_WINDOW).std().values
        with np.errstate(invalid="ignore", divide="ignore"):
            z = (slope - roll_mean) / roll_std
        z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
        z_clip = np.clip(z, -3, 3)
        p_trend = (z_clip + 3) / 6.0

        mom = np.zeros(n)
        mom[self.MOMENTUM_K:] = (closes[self.MOMENTUM_K:] > closes[:-self.MOMENTUM_K]).astype(float)
        p_bull = self.BLEND_OMEGA * p_trend + (1 - self.BLEND_OMEGA) * mom

        intraday_mode = getattr(cfg, "position_hold_mode", "INTRADAY") != "CARRY_FORWARD"

        trades = []
        position = "NONE"
        entry_price = sl = 0.0
        entry_idx = 0
        peak = 0.0
        prev_date = None
        prev_close = None

        def book(idx, exit_px, reason):
            nonlocal position
            net = (exit_px - entry_price) * qty if position == "LONG" \
                else (entry_price - exit_px) * qty
            pnl_pts = round(exit_px - entry_price, 2) if position == "LONG" \
                else round(entry_price - exit_px, 2)
            trades.append({
                "entry_time": base.iloc[entry_idx]["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": base.iloc[idx]["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "direction": position,
                "entry_price": round(entry_price, 2),
                "sl": round(sl, 2),
                "exit_price": round(exit_px, 2),
                "exit_reason": reason,
                "pnl": round(net, 2),
                "pnl_pts": pnl_pts,
                "pnl_inr": round(net, 2),
                "hold_bars": idx - entry_idx,
            })
            position = "NONE"

        for i in range(min_warmup, n):
            row = base.iloc[i]
            c, h, l = closes[i], highs[i], lows[i]
            ts_ist = pd.to_datetime(row["timestamp"])
            cur_mins = ts_ist.hour * 60 + ts_ist.minute
            cur_date = ts_ist.date()
            eod_minute = self._eod_exit_minute_for_date(cfg, cur_date)

            atr_v = atr_col[i] if atr_col is not None else c * 0.002
            if pd.isna(atr_v) or atr_v < 1:
                atr_v = c * 0.002

            if position != "NONE":
                if intraday_mode:
                    if prev_date is not None and cur_date != prev_date:
                        book(i, prev_close, "EOD_EXIT")
                    if position != "NONE" and cur_mins >= eod_minute:
                        book(i, c, "EOD_EXIT")
                        prev_date = cur_date
                        prev_close = c
                        continue

                if position == "LONG":
                    if h > peak:
                        peak = h
                    profit = peak - entry_price
                    if profit >= self.TRAIL_ACTIVATION * atr_v:
                        trail = peak - self.TRAIL_STOP_ATR * atr_v
                        if trail > sl:
                            sl = trail
                    if l <= sl:
                        real_exit = sl if sl <= h else float(row.get("open", c))
                        book(i, real_exit, "SL_HIT")
                    elif (i - entry_idx) >= self.MAX_HOLD_BARS:
                        book(i, c, "TIMEOUT")
                    elif p_bull[i] <= (1 - self.DERISK_THRESHOLD):
                        book(i, c, "DERISK_EXIT")

                elif position == "SHORT":
                    if l < peak:
                        peak = l
                    profit = entry_price - peak
                    if profit >= self.TRAIL_ACTIVATION * atr_v:
                        trail = peak + self.TRAIL_STOP_ATR * atr_v
                        if trail < sl:
                            sl = trail
                    if h >= sl:
                        real_exit = sl if sl >= l else float(row.get("open", c))
                        book(i, real_exit, "SL_HIT")
                    elif (i - entry_idx) >= self.MAX_HOLD_BARS:
                        book(i, c, "TIMEOUT")
                    elif p_bull[i] >= self.DERISK_THRESHOLD:
                        book(i, c, "DERISK_EXIT")

            if position == "NONE":
                if intraday_mode and cur_mins >= eod_minute:
                    prev_date = cur_date
                    prev_close = c
                    continue
                if roll_std[i] > 0 and not np.isnan(roll_std[i]):
                    if p_bull[i] >= self.ENTRY_THRESHOLD and slope[i] > 0:
                        position = "LONG"
                        entry_price = c
                        entry_idx = i
                        peak = h
                        sl = c - self.HARD_STOP_ATR * atr_v
                    elif p_bull[i] <= (1 - self.ENTRY_THRESHOLD) and slope[i] < 0:
                        position = "SHORT"
                        entry_price = c
                        entry_idx = i
                        peak = l
                        sl = c + self.HARD_STOP_ATR * atr_v

            prev_date = cur_date
            prev_close = c

        if position != "NONE":
            trades.append({
                "entry_time": base.iloc[entry_idx]["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": "", "direction": position, "entry_price": round(entry_price, 2),
                "sl": round(sl, 2), "exit_price": None, "exit_reason": "OPEN",
                "pnl": 0, "pnl_pts": 0, "pnl_inr": 0, "hold_bars": (n - 1) - entry_idx,
            })

        if _trade_start_date is not None and trades:
            trades = [t for t in trades if pd.to_datetime(t["entry_time"]) >= _trade_start_date]

        if not trades:
            return {"error": "No trades generated", "trades": [], "stats": {}}

        tdf = pd.DataFrame(trades)
        total = len(tdf)
        wins = (tdf["pnl"] > 0).sum()
        gp = tdf[tdf["pnl"] > 0]["pnl"].sum()
        gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
        equity = initial_capital + tdf["pnl"].cumsum()
        dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()
        pf = round(gp / gl, 2) if gl > 0 else float("inf")
        stats = {
            "total_trades": total,
            "win_rate_pct": round(100 * wins / total, 1) if total else 0,
            "profit_factor": pf,
            "total_pnl": round(tdf["pnl"].sum(), 2),
            "max_drawdown_pct": round(dd, 2),
            "avg_hold_bars": round(tdf["hold_bars"].mean(), 1),
        }
        return {"trades": trades, "stats": stats, "error": None}
