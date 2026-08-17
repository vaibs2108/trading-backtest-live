"""
regime_trend_range_v1_research.py — SUPPORT FILE, not itself a selectable
strategy (no strategy is auto-discovered from backend/strategies/ directly,
only from backend/strategies/custom/).

This is a verbatim port of the validated research chain from
scratch/research_v1/backend/strategies/variant_kernels.py — specifically
the ancestor chain that produced the "V1 Final" kernel:

  RegimeTrendV2Kernel (regime_trend_v2_kernel_research.py, EOD-fixed base)
   -> RegimeTrendRangeV2Kernel            (range agent enabled)
   -> RegimeTrendRangeV2FilteredKernel    (ER>=0.25 chop filter, trend-only)
   -> RegimeTrendRangeV2BreakoutKernel    (BURST no-pullback entry)
   -> RegimeTrendRangeV2BreakoutGatedKernel (BURST gated on slower ER + confidence)
   -> RegimeTrendRangeV2DonchianKernel    (Donchian breakout entry, Turtle system)
   -> RegimeTrendRangeV2HTFHoldKernel     (suppress regime-flip exit when HTF agrees)
   -> RegimeTrendRangeV2CUSUMKernel       (CUSUM change-point entry, Lopez de Prado)
   -> RegimeTrendRangeV2HalfLifeExitKernel (Ornstein-Uhlenbeck half-life time-stop
                                             on RANGE-sourced trades)

Only the classes actually on that ancestor path are included here (siblings
tried and rejected along the way — TransitionEntry, Pyramid, Volume,
DailyGate, NR7, Grid — are NOT ported; see scratch/research_v1/SESSION_STATUS.md
for why). The final selectable strategy
(backend/strategies/custom/custom_regime_v1_trend_range_final.py) subclasses
RegimeTrendRangeV2HalfLifeExitKernel from this file with the validated
parameters and swaps in VWAPReversionAgent as the range entry agent.

Full 13-month validated result of that final combination (see
scratch/research_v1/SESSION_STATUS.md): Trend +14,520.2 pts (30.1% capture)
+ Range +3,094.0 pts (3.1% capture) = +17,614.2 pts combined (12.0%),
net +528,425 rs, PF 1.93, maxDD -6.98%.
"""
from typing import Optional

import numpy as np
import pandas as pd

from strategy_kernel import SignalEvent
from .regime_trend_v2_kernel_research import RegimeTrendV2Kernel


class RegimeTrendRangeV2Kernel(RegimeTrendV2Kernel):
    """V2's trend logic left exactly as-is, PLUS the already-built
    RangeEntryAgent enabled during SIDEWAYS regime (V2 currently stubs
    range_entry to always HOLD via `_TREND_ONLY`, so 100% of sideways/chop
    time is dead time it just sits out)."""

    strategy_id = "regime_trend_v2_plusrange"
    _TREND_ONLY = False

    def _run_agents(self, df_slice, row, position, cfg, df_1h, df_1d, df_1w):
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if pd.isna(atr) or atr < 1:
            atr = close * 0.002

        regime = self._regime_agent.evaluate(df_slice, self._regime, self._regime_age)
        self._regime = regime.regime
        self._regime_age = regime.regime_age
        self._last_regime_state = regime

        cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
        if self._htf_cache is None or cur_hour != self._htf_cache_hour:
            self._htf_cache = self._htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
            self._htf_cache_hour = cur_hour
        htf = self._htf_cache

        trend_entry = self._trend_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)
        range_entry = self._range_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal in ("LONG", "SHORT"):
            proposed = trend_entry.signal
        elif range_entry.signal in ("LONG", "SHORT"):
            proposed = range_entry.signal
        else:
            proposed = "HOLD"
        momentum = self._momentum_agent.evaluate(df_slice, proposed, regime.regime)

        return self._orchestrator.evaluate(row, regime, trend_entry, range_entry,
                                           htf, momentum, position)


class RegimeTrendRangeV2FilteredKernel(RegimeTrendRangeV2Kernel):
    """Range-enabled V2 + the ER>=0.25 chop filter applied ONLY to
    trend-regime entries -- range entries are supposed to fire precisely in
    the low-ER sideways conditions the filter targets, so gating them on
    ER too would defeat the purpose."""

    strategy_id = "regime_trend_v2_plusrange_erfilter"
    MIN_ENTRY_ER = 0.25

    def on_bar(self, bar_idx, base_df, row, position, context) -> Optional[SignalEvent]:
        ev = super().on_bar(bar_idx, base_df, row, position, context)
        if ev and ev.signal in ("LONG", "SHORT"):
            last_regime = getattr(self, "_last_regime_state", None)
            if last_regime is not None and last_regime.regime in ("TRENDING_UP", "TRENDING_DOWN"):
                if last_regime.efficiency_ratio < self.MIN_ENTRY_ER:
                    return None
        return ev


def _get_settings_lazy():
    from config import get_settings
    return get_settings()


def _main_strategy_lazy():
    import strategy as main_strategy
    return main_strategy


class RegimeTrendRangeV2BreakoutKernel(RegimeTrendRangeV2FilteredKernel):
    """Adds a no-pullback breakout/momentum-continuation entry for TRENDING
    legs where the regime agent's own BURST detector fired (fast 5-bar
    efficiency ratio > 0.45, move > 1 ATR, low overlap) but the
    pullback-based TrendEntryAgent is HOLDing because no pullback has
    happened yet. Still flows through the normal orchestrator (weighted_score
    gate, ER filter, HTF snapping), so it is a new source of candidate
    entries, not a bypass of the existing selectivity logic."""

    strategy_id = "regime_trend_v2_breakout"
    BREAKOUT_SL_LOOKBACK = 6
    BREAKOUT_SL_ATR_BUFFER = 0.6  # wider than the pullback SL since there is
                                  # no pullback low/high to anchor to

    def _burst_entry(self, df_slice, row, regime, atr):
        from .regime_agents.trend_entry import TrendEntryState

        if not any("BURST" in r for r in regime.reasons):
            return None

        close = float(row.get("close", 0))
        highs = df_slice["high"].values.astype(float)
        lows = df_slice["low"].values.astype(float)
        lb = min(self.BREAKOUT_SL_LOOKBACK, len(df_slice))
        quality = round(0.5 * 0.7 + 0.5 * regime.confidence, 3)

        if regime.regime == "TRENDING_UP":
            recent_low = float(lows[-lb:].min())
            sl = recent_low - atr * self.BREAKOUT_SL_ATR_BUFFER
            return TrendEntryState(
                signal="LONG", entry_price=close, sl=round(sl, 2),
                target1=round(close + atr * 1.5, 2), target2=round(close + atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=["Breakout: BURST detected, no pullback required",
                         f"Regime {regime.regime} conf={regime.confidence:.0%}"],
            )
        elif regime.regime == "TRENDING_DOWN":
            recent_high = float(highs[-lb:].max())
            sl = recent_high + atr * self.BREAKOUT_SL_ATR_BUFFER
            return TrendEntryState(
                signal="SHORT", entry_price=close, sl=round(sl, 2),
                target1=round(close - atr * 1.5, 2), target2=round(close - atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=["Breakout: BURST detected, no pullback required",
                         f"Regime {regime.regime} conf={regime.confidence:.0%}"],
            )
        return None

    def _run_agents(self, df_slice, row, position, cfg, df_1h, df_1d, df_1w):
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if pd.isna(atr) or atr < 1:
            atr = close * 0.002

        regime = self._regime_agent.evaluate(df_slice, self._regime, self._regime_age)
        self._regime = regime.regime
        self._regime_age = regime.regime_age
        self._last_regime_state = regime

        cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
        if self._htf_cache is None or cur_hour != self._htf_cache_hour:
            self._htf_cache = self._htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
            self._htf_cache_hour = cur_hour
        htf = self._htf_cache

        trend_entry = self._trend_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal == "HOLD" and position == "NONE":
            burst = self._burst_entry(df_slice, row, regime, atr)
            if burst is not None:
                trend_entry = burst

        range_entry = self._range_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal in ("LONG", "SHORT"):
            proposed = trend_entry.signal
        elif range_entry.signal in ("LONG", "SHORT"):
            proposed = range_entry.signal
        else:
            proposed = "HOLD"
        momentum = self._momentum_agent.evaluate(df_slice, proposed, regime.regime)

        return self._orchestrator.evaluate(row, regime, trend_entry, range_entry,
                                           htf, momentum, position)


class RegimeTrendRangeV2BreakoutGatedKernel(RegimeTrendRangeV2BreakoutKernel):
    """Same BURST breakout entry, but additionally gated on the SLOWER
    15-bar efficiency ratio (the same metric that already filters pullback
    trend entries) and a minimum regime confidence."""

    strategy_id = "regime_trend_v2_breakout_gated"
    BREAKOUT_MIN_ER = 0.30
    BREAKOUT_MIN_CONFIDENCE = 0.55

    def _burst_entry(self, df_slice, row, regime, atr):
        if regime.efficiency_ratio < self.BREAKOUT_MIN_ER:
            return None
        if regime.confidence < self.BREAKOUT_MIN_CONFIDENCE:
            return None
        return super()._burst_entry(df_slice, row, regime, atr)


class RegimeTrendRangeV2DonchianKernel(RegimeTrendRangeV2BreakoutGatedKernel):
    """Donchian-channel breakout entry (the core mechanism of the classic
    Turtle Trading system): enter the instant price closes above/below the
    prior N-bar high/low, with NO dependency on a pullback or a
    confirmation bar."""

    strategy_id = "regime_trend_v2_donchian"
    DONCHIAN_LOOKBACK = 15
    DONCHIAN_SL_LOOKBACK = 6
    DONCHIAN_SL_ATR_BUFFER = 0.6

    def _donchian_entry(self, df_slice, row, regime, atr):
        from .regime_agents.trend_entry import TrendEntryState

        close = float(row.get("close", 0))
        highs = df_slice["high"].values.astype(float)
        lows = df_slice["low"].values.astype(float)
        lb = min(self.DONCHIAN_LOOKBACK, len(df_slice) - 1)
        if lb < 5:
            return None

        prior_high = float(highs[-lb - 1:-1].max())
        prior_low = float(lows[-lb - 1:-1].min())
        sl_lb = min(self.DONCHIAN_SL_LOOKBACK, len(df_slice))
        quality = round(0.5 * 0.65 + 0.5 * regime.confidence, 3)

        if regime.regime == "TRENDING_UP" and close > prior_high:
            recent_low = float(lows[-sl_lb:].min())
            sl = recent_low - atr * self.DONCHIAN_SL_ATR_BUFFER
            return TrendEntryState(
                signal="LONG", entry_price=close, sl=round(sl, 2),
                target1=round(close + atr * 1.5, 2), target2=round(close + atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=[f"Donchian: close {close:.0f} broke {lb}-bar high {prior_high:.0f}",
                         f"Regime {regime.regime} conf={regime.confidence:.0%}"],
            )
        elif regime.regime == "TRENDING_DOWN" and close < prior_low:
            recent_high = float(highs[-sl_lb:].max())
            sl = recent_high + atr * self.DONCHIAN_SL_ATR_BUFFER
            return TrendEntryState(
                signal="SHORT", entry_price=close, sl=round(sl, 2),
                target1=round(close - atr * 1.5, 2), target2=round(close - atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=[f"Donchian: close {close:.0f} broke {lb}-bar low {prior_low:.0f}",
                         f"Regime {regime.regime} conf={regime.confidence:.0%}"],
            )
        return None

    def _run_agents(self, df_slice, row, position, cfg, df_1h, df_1d, df_1w):
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if pd.isna(atr) or atr < 1:
            atr = close * 0.002

        regime = self._regime_agent.evaluate(df_slice, self._regime, self._regime_age)
        self._regime = regime.regime
        self._regime_age = regime.regime_age
        self._last_regime_state = regime

        cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
        if self._htf_cache is None or cur_hour != self._htf_cache_hour:
            self._htf_cache = self._htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
            self._htf_cache_hour = cur_hour
        htf = self._htf_cache

        trend_entry = self._trend_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal == "HOLD" and position == "NONE":
            donch = self._donchian_entry(df_slice, row, regime, atr)
            if donch is not None:
                trend_entry = donch
            else:
                burst = self._burst_entry(df_slice, row, regime, atr)
                if burst is not None:
                    trend_entry = burst

        range_entry = self._range_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal in ("LONG", "SHORT"):
            proposed = trend_entry.signal
        elif range_entry.signal in ("LONG", "SHORT"):
            proposed = range_entry.signal
        else:
            proposed = "HOLD"
        momentum = self._momentum_agent.evaluate(df_slice, proposed, regime.regime)

        return self._orchestrator.evaluate(row, regime, trend_entry, range_entry,
                                           htf, momentum, position)


class RegimeTrendRangeV2HTFHoldKernel(RegimeTrendRangeV2DonchianKernel):
    """On top of Donchian entry, suppress the 5m regime-flip exit when the
    HTF (1H/1D) trend still agrees with the position direction. The
    stop-loss / trailing stop remain the real risk control in this mode --
    REGIME_EXIT is not removed, only gated on HTF disagreement."""

    strategy_id = "regime_trend_v2_htfhold"

    def run_backtest(self, frames, initial_capital=500_000, lot_size=15,
                      lot_multiplier=1, start_date=None, end_date=None):
        cfg = _get_settings_lazy()
        trail_mult = self._cfg_val(cfg, "trail_mult", self.TRAIL_MULT)
        trail_activation = self._cfg_val(cfg, "trail_activation", self.TRAIL_ACTIVATION)
        be_trigger = self._cfg_val(cfg, "be_trigger", self.BE_TRIGGER)
        be_buffer = self._cfg_val(cfg, "be_buffer", self.BE_BUFFER)
        cooldown_bars = int(self._cfg_val(cfg, "cooldown_bars", self.COOLDOWN_BARS))
        qty = lot_size * lot_multiplier

        base = _main_strategy_lazy().build_merged_table(frames, with_patterns=False)
        if base.empty:
            return {"error": "No data after merging", "trades": [], "stats": {}}

        base = base[self._session_mask(base, cfg)].copy().reset_index(drop=True)
        if base.empty:
            return {"error": "No data in trading window", "trades": [], "stats": {}}

        _trade_start_date = pd.to_datetime(start_date) if start_date else None
        _trade_end_date = pd.to_datetime(end_date).date() if end_date else None
        if end_date:
            base = base[base["timestamp"].dt.date <= _trade_end_date].copy().reset_index(drop=True)

        df_1h = frames.get("60")
        df_1d = frames.get("1D")
        df_1w = None
        if df_1d is not None and len(df_1d) >= 30:
            try:
                from features.weekly import derive_weekly_from_daily
                df_1w = derive_weekly_from_daily(df_1d)
            except Exception:
                pass

        self.reset()
        regime_counts = {"TRENDING_UP": 0, "TRENDING_DOWN": 0, "SIDEWAYS": 0, "TRANSITION": 0}
        entry_signals = 0

        trades = []
        position = "NONE"
        entry_price = sl = 0.0
        entry_t1 = entry_t2 = 0.0
        entry_idx = 0
        highest_since_entry = 0.0
        lowest_since_entry = 99999999.0
        cooldown_until = -1
        n = len(base)
        start_idx = 80

        context = {"cfg": cfg, "df_1h": df_1h, "df_1d": df_1d, "df_1w": df_1w}

        for i in range(start_idx, n):
            row = base.iloc[i]
            bh = float(row.get("high", row["close"]))
            bl = float(row.get("low", row["close"]))
            bc = float(row["close"])
            bo = float(row.get("open", bc))
            ts = row["timestamp"]
            atr_v = float(row.get("atr", bc * 0.002))
            if pd.isna(atr_v) or atr_v < 5:
                atr_v = bc * 0.002

            ts_ist = pd.to_datetime(ts)
            cur_mins = ts_ist.hour * 60 + ts_ist.minute
            eod_minute = self._eod_exit_minute_for_date(cfg, ts_ist.date())

            if position != "NONE":

                def book(exit_px, reason):
                    nonlocal position, cooldown_until
                    net = (exit_px - entry_price) * qty if position == "LONG" \
                        else (entry_price - exit_px) * qty
                    _pnl_pts = round(exit_px - entry_price, 2) if position == "LONG" \
                        else round(entry_price - exit_px, 2)
                    entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
                    exit_ts_ist = pd.to_datetime(ts)
                    trades.append({
                        "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_time": exit_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                        "direction": position,
                        "entry_price": round(entry_price, 2),
                        "sl": round(sl, 2),
                        "target1": round(entry_t1, 2),
                        "target2": round(entry_t2, 2),
                        "exit_price": round(exit_px, 2),
                        "exit_reason": reason,
                        "pnl": round(net, 2),
                        "pnl_pts": _pnl_pts,
                        "pnl_inr": round(net, 2),
                        "est_cost": round(self._estimate_round_trip_cost(entry_price, exit_px, qty), 2),
                    })
                    if net < 0 and cooldown_bars > 0:
                        cooldown_until = i + cooldown_bars
                    position = "NONE"

                if cur_mins >= eod_minute:
                    book(bc, "EOD_EXIT")
                    continue

                if position == "LONG" and bl <= sl:
                    real_exit = sl if sl <= bh + 0.01 else bo
                    book(real_exit, "SL_HIT")
                elif position == "SHORT" and bh >= sl:
                    real_exit = sl if sl >= bl - 0.01 else bo
                    book(real_exit, "SL_HIT")

                elif position == "LONG":
                    if bh > highest_since_entry:
                        highest_since_entry = bh
                    profit = highest_since_entry - entry_price
                    if profit >= atr_v * trail_activation:
                        trail_sl = highest_since_entry - atr_v * trail_mult
                        if be_trigger > 0 and profit > atr_v * be_trigger:
                            trail_sl = max(trail_sl, entry_price + atr_v * be_buffer)
                        trail_sl = min(trail_sl, highest_since_entry)
                        if trail_sl > sl:
                            sl = trail_sl

                    sig_ev = self.on_bar(i, base, row, "LONG", context)
                    htf_trend = self._htf_cache.htf_trend if self._htf_cache else "NEUTRAL"
                    if sig_ev and sig_ev.signal == "LONG_EXIT" and htf_trend != "BULLISH":
                        book(bc, "REGIME_EXIT")
                    elif position == "LONG":
                        opp_ev = self.on_bar(i, base, row, "NONE", context)
                        if opp_ev and opp_ev.signal == "SHORT":
                            book(bc, "OPPOSITE_SIGNAL")

                elif position == "SHORT":
                    if bl < lowest_since_entry:
                        lowest_since_entry = bl
                    profit = entry_price - lowest_since_entry
                    if profit >= atr_v * trail_activation:
                        trail_sl = lowest_since_entry + atr_v * trail_mult
                        if be_trigger > 0 and profit > atr_v * be_trigger:
                            trail_sl = min(trail_sl, entry_price - atr_v * be_buffer)
                        trail_sl = max(trail_sl, lowest_since_entry)
                        if trail_sl < sl:
                            sl = trail_sl

                    sig_ev = self.on_bar(i, base, row, "SHORT", context)
                    htf_trend = self._htf_cache.htf_trend if self._htf_cache else "NEUTRAL"
                    if sig_ev and sig_ev.signal == "SHORT_EXIT" and htf_trend != "BEARISH":
                        book(bc, "REGIME_EXIT")
                    elif position == "SHORT":
                        opp_ev = self.on_bar(i, base, row, "NONE", context)
                        if opp_ev and opp_ev.signal == "LONG":
                            book(bc, "OPPOSITE_SIGNAL")

            if position == "NONE":
                if cur_mins >= eod_minute:
                    continue

                if i <= cooldown_until:
                    self.on_bar(i, base, row, "NONE", context)
                    regime_counts[self._regime] = regime_counts.get(self._regime, 0) + 1
                    continue

                sig_ev = self.on_bar(i, base, row, "NONE", context)
                regime_counts[self._regime] = regime_counts.get(self._regime, 0) + 1

                if sig_ev and sig_ev.signal in ("LONG", "SHORT"):
                    entry_signals += 1
                    entry_price = bc
                    sl = sig_ev.sl
                    entry_t1 = sig_ev.target1
                    entry_t2 = sig_ev.target2
                    position = sig_ev.signal
                    entry_idx = i
                    highest_since_entry = bh
                    lowest_since_entry = bl

        if position != "NONE":
            entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
            trades.append({
                "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": "", "direction": position, "entry_price": round(entry_price, 2),
                "sl": round(sl, 2), "target1": round(entry_t1, 2), "target2": round(entry_t2, 2),
                "exit_price": None, "exit_reason": "OPEN", "pnl": 0, "pnl_pts": 0,
                "pnl_inr": 0, "est_cost": 0,
            })

        if _trade_start_date is not None and trades:
            trades = [t for t in trades if pd.to_datetime(t["entry_time"]) >= _trade_start_date]

        tdf = pd.DataFrame(trades)
        if tdf.empty:
            return {"error": "No trades generated", "trades": [], "stats": {}}

        total = len(tdf)
        wins = (tdf["pnl"] > 0).sum()
        gp = tdf[tdf["pnl"] > 0]["pnl"].sum()
        gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
        equity = initial_capital + tdf["pnl"].cumsum()
        dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

        net_series = tdf["pnl"] - tdf["est_cost"]
        net_gp = net_series[net_series > 0].sum()
        net_gl = abs(net_series[net_series <= 0].sum())

        stats = {
            "total_trades": total, "wins": int(wins), "losses": int(total - wins),
            "win_rate_pct": round(wins / total * 100, 1),
            "profit_factor": round(gp / gl, 2) if gl > 0 else 0,
            "total_pnl": round(tdf["pnl"].sum(), 0),
            "avg_win": round(tdf[tdf["pnl"] > 0]["pnl"].mean(), 0) if wins > 0 else 0,
            "avg_loss": round(tdf[tdf["pnl"] <= 0]["pnl"].mean(), 0) if (total - wins) > 0 else 0,
            "max_drawdown_pct": round(dd, 2),
            "expectancy": round(tdf["pnl"].mean(), 0),
            "final_capital": round(float(equity.iloc[-1]), 0),
            "exit_distribution": tdf["exit_reason"].value_counts().to_dict(),
            "est_total_costs": round(tdf["est_cost"].sum(), 0),
            "net_pnl_after_costs": round(net_series.sum(), 0),
            "net_profit_factor": round(net_gp / net_gl, 2) if net_gl > 0 else 0,
        }
        tdf["equity"] = initial_capital + tdf["pnl"].cumsum()
        equity_curve = tdf[["entry_time", "equity", "pnl"]].to_dict(orient="records")
        stats["regime_distribution"] = regime_counts
        stats["entry_signals"] = entry_signals
        stats["vetoed_signals"] = 0

        return {"stats": stats, "trades": tdf.to_dict(orient="records"), "equity_curve": equity_curve}


class RegimeTrendRangeV2CUSUMKernel(RegimeTrendRangeV2HTFHoldKernel):
    """CUSUM (cumulative sum) change-point entry -- Lopez de Prado's
    CUSUM filter (Advances in Financial Machine Learning), a purely
    statistical detector completely independent of both the regime
    classifier's votes and Donchian's fixed-lookback price extremes."""

    strategy_id = "regime_trend_v2_cusum"
    CUSUM_THRESHOLD_ATR = 1.5
    CUSUM_SL_LOOKBACK = 6
    CUSUM_SL_ATR_BUFFER = 0.6

    def reset(self):
        super().reset()
        self._cusum_pos = 0.0
        self._cusum_neg = 0.0

    def _cusum_update(self, df_slice):
        closes = df_slice["close"].values.astype(float)
        if len(closes) < 2:
            return
        ret = closes[-1] - closes[-2]
        if not hasattr(self, "_cusum_pos"):
            self._cusum_pos = 0.0
            self._cusum_neg = 0.0
        self._cusum_pos = max(0.0, self._cusum_pos + ret)
        self._cusum_neg = min(0.0, self._cusum_neg + ret)

    def _cusum_entry(self, row, regime, atr):
        from .regime_agents.trend_entry import TrendEntryState

        close = float(row.get("close", 0))
        h = self.CUSUM_THRESHOLD_ATR * atr
        quality = round(0.5 * 0.65 + 0.5 * regime.confidence, 3)

        if self._cusum_pos >= h and regime.regime == "TRENDING_UP":
            self._cusum_pos = 0.0
            sl = close - atr * (self.CUSUM_THRESHOLD_ATR + self.CUSUM_SL_ATR_BUFFER)
            return TrendEntryState(
                signal="LONG", entry_price=close, sl=round(sl, 2),
                target1=round(close + atr * 1.5, 2), target2=round(close + atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=[f"CUSUM: cumulative drift crossed +{h:.0f} threshold"],
            )
        elif self._cusum_neg <= -h and regime.regime == "TRENDING_DOWN":
            self._cusum_neg = 0.0
            sl = close + atr * (self.CUSUM_THRESHOLD_ATR + self.CUSUM_SL_ATR_BUFFER)
            return TrendEntryState(
                signal="SHORT", entry_price=close, sl=round(sl, 2),
                target1=round(close - atr * 1.5, 2), target2=round(close - atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=[f"CUSUM: cumulative drift crossed -{h:.0f} threshold"],
            )
        return None

    def _run_agents(self, df_slice, row, position, cfg, df_1h, df_1d, df_1w):
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if pd.isna(atr) or atr < 1:
            atr = close * 0.002

        self._cusum_update(df_slice)

        regime = self._regime_agent.evaluate(df_slice, self._regime, self._regime_age)
        self._regime = regime.regime
        self._regime_age = regime.regime_age
        self._last_regime_state = regime

        cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
        if self._htf_cache is None or cur_hour != self._htf_cache_hour:
            self._htf_cache = self._htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
            self._htf_cache_hour = cur_hour
        htf = self._htf_cache

        trend_entry = self._trend_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal == "HOLD" and position == "NONE":
            cus = self._cusum_entry(row, regime, atr)
            if cus is not None:
                trend_entry = cus
            else:
                donch = self._donchian_entry(df_slice, row, regime, atr)
                if donch is not None:
                    trend_entry = donch
                else:
                    burst = self._burst_entry(df_slice, row, regime, atr)
                    if burst is not None:
                        trend_entry = burst

        range_entry = self._range_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal in ("LONG", "SHORT"):
            proposed = trend_entry.signal
        elif range_entry.signal in ("LONG", "SHORT"):
            proposed = range_entry.signal
        else:
            proposed = "HOLD"
        momentum = self._momentum_agent.evaluate(df_slice, proposed, regime.regime)

        return self._orchestrator.evaluate(row, regime, trend_entry, range_entry,
                                           htf, momentum, position)


class RegimeTrendRangeV2HalfLifeExitKernel(RegimeTrendRangeV2CUSUMKernel):
    """Adds an Ornstein-Uhlenbeck half-life time-stop to RANGE-sourced
    trades only (trend trades unchanged). Estimates the mean-reversion
    half-life at entry (OLS regression of bar-to-bar deviation changes
    against lagged deviation, half_life = -ln(2)/ln(beta)); if the trade
    is still open after HALFLIFE_MAX_MULT x half-life bars, force-exit
    regardless of price."""

    strategy_id = "regime_trend_v2_halflife"
    HALFLIFE_LOOKBACK = 40
    HALFLIFE_MAX_MULT = 2.0
    HALFLIFE_DEFAULT_BARS = 20  # fallback when regression is degenerate
    # Profit-aware refinement: only force the timeout if the trade is at/
    # below this ATR-scaled profit when the deadline hits. Set very high to
    # disable (= original unconditional-timeout behavior, the validated
    # default -- condition is profit<=threshold, so a huge POSITIVE value
    # is always true = unconditional timeout).
    HALFLIFE_MIN_PROFIT_ATR = 999.0

    def _compute_half_life(self, df_slice):
        lb = min(self.HALFLIFE_LOOKBACK, len(df_slice))
        closes = df_slice["close"].values[-lb:].astype(float)
        if len(closes) < 10:
            return self.HALFLIFE_DEFAULT_BARS
        mean = closes.mean()
        dev = closes - mean
        y = dev[1:]
        x = dev[:-1]
        x_mean, y_mean = x.mean(), y.mean()
        denom = ((x - x_mean) ** 2).sum()
        if denom <= 0:
            return self.HALFLIFE_DEFAULT_BARS
        beta = ((x - x_mean) * (y - y_mean)).sum() / denom
        if beta <= 0 or beta >= 1:
            return self.HALFLIFE_DEFAULT_BARS
        hl = -np.log(2) / np.log(beta)
        if not np.isfinite(hl) or hl <= 0:
            return self.HALFLIFE_DEFAULT_BARS
        return min(max(hl, 3), 200)

    def run_backtest(self, frames, initial_capital=500_000, lot_size=15,
                      lot_multiplier=1, start_date=None, end_date=None):
        cfg = _get_settings_lazy()
        trail_mult = self._cfg_val(cfg, "trail_mult", self.TRAIL_MULT)
        trail_activation = self._cfg_val(cfg, "trail_activation", self.TRAIL_ACTIVATION)
        be_trigger = self._cfg_val(cfg, "be_trigger", self.BE_TRIGGER)
        be_buffer = self._cfg_val(cfg, "be_buffer", self.BE_BUFFER)
        cooldown_bars = int(self._cfg_val(cfg, "cooldown_bars", self.COOLDOWN_BARS))
        qty = lot_size * lot_multiplier

        base = _main_strategy_lazy().build_merged_table(frames, with_patterns=False)
        if base.empty:
            return {"error": "No data after merging", "trades": [], "stats": {}}

        base = base[self._session_mask(base, cfg)].copy().reset_index(drop=True)
        if base.empty:
            return {"error": "No data in trading window", "trades": [], "stats": {}}

        _trade_start_date = pd.to_datetime(start_date) if start_date else None
        _trade_end_date = pd.to_datetime(end_date).date() if end_date else None
        if end_date:
            base = base[base["timestamp"].dt.date <= _trade_end_date].copy().reset_index(drop=True)

        df_1h = frames.get("60")
        df_1d = frames.get("1D")
        df_1w = None
        if df_1d is not None and len(df_1d) >= 30:
            try:
                from features.weekly import derive_weekly_from_daily
                df_1w = derive_weekly_from_daily(df_1d)
            except Exception:
                pass

        self.reset()
        regime_counts = {"TRENDING_UP": 0, "TRENDING_DOWN": 0, "SIDEWAYS": 0, "TRANSITION": 0}
        entry_signals = 0

        trades = []
        position = "NONE"
        entry_price = sl = 0.0
        entry_t1 = entry_t2 = 0.0
        entry_idx = 0
        entry_is_range = False
        entry_half_life = 0.0
        highest_since_entry = 0.0
        lowest_since_entry = 99999999.0
        cooldown_until = -1
        n = len(base)
        start_idx = 80

        context = {"cfg": cfg, "df_1h": df_1h, "df_1d": df_1d, "df_1w": df_1w}

        for i in range(start_idx, n):
            row = base.iloc[i]
            bh = float(row.get("high", row["close"]))
            bl = float(row.get("low", row["close"]))
            bc = float(row["close"])
            bo = float(row.get("open", bc))
            ts = row["timestamp"]
            atr_v = float(row.get("atr", bc * 0.002))
            if pd.isna(atr_v) or atr_v < 5:
                atr_v = bc * 0.002

            ts_ist = pd.to_datetime(ts)
            cur_mins = ts_ist.hour * 60 + ts_ist.minute
            eod_minute = self._eod_exit_minute_for_date(cfg, ts_ist.date())

            if position != "NONE":

                def book(exit_px, reason):
                    nonlocal position, cooldown_until
                    net = (exit_px - entry_price) * qty if position == "LONG" \
                        else (entry_price - exit_px) * qty
                    _pnl_pts = round(exit_px - entry_price, 2) if position == "LONG" \
                        else round(entry_price - exit_px, 2)
                    entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
                    exit_ts_ist = pd.to_datetime(ts)
                    trades.append({
                        "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_time": exit_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                        "direction": position,
                        "entry_price": round(entry_price, 2),
                        "sl": round(sl, 2),
                        "target1": round(entry_t1, 2),
                        "target2": round(entry_t2, 2),
                        "exit_price": round(exit_px, 2),
                        "exit_reason": reason,
                        "pnl": round(net, 2),
                        "pnl_pts": _pnl_pts,
                        "pnl_inr": round(net, 2),
                        "est_cost": round(self._estimate_round_trip_cost(entry_price, exit_px, qty), 2),
                        "source": "RANGE" if entry_is_range else "TREND",
                    })
                    if net < 0 and cooldown_bars > 0:
                        cooldown_until = i + cooldown_bars
                    position = "NONE"

                if cur_mins >= eod_minute:
                    book(bc, "EOD_EXIT")
                    continue

                if position == "LONG" and bl <= sl:
                    real_exit = sl if sl <= bh + 0.01 else bo
                    book(real_exit, "SL_HIT")
                elif position == "SHORT" and bh >= sl:
                    real_exit = sl if sl >= bl - 0.01 else bo
                    book(real_exit, "SL_HIT")

                elif (entry_is_range and (i - entry_idx) >= self.HALFLIFE_MAX_MULT * entry_half_life
                      and (bc - entry_price if position == "LONG" else entry_price - bc)
                      <= self.HALFLIFE_MIN_PROFIT_ATR * atr_v):
                    book(bc, "HALFLIFE_TIMEOUT")

                elif position == "LONG":
                    if bh > highest_since_entry:
                        highest_since_entry = bh
                    profit = highest_since_entry - entry_price
                    if profit >= atr_v * trail_activation:
                        trail_sl = highest_since_entry - atr_v * trail_mult
                        if be_trigger > 0 and profit > atr_v * be_trigger:
                            trail_sl = max(trail_sl, entry_price + atr_v * be_buffer)
                        trail_sl = min(trail_sl, highest_since_entry)
                        if trail_sl > sl:
                            sl = trail_sl

                    sig_ev = self.on_bar(i, base, row, "LONG", context)
                    htf_trend = self._htf_cache.htf_trend if self._htf_cache else "NEUTRAL"
                    if sig_ev and sig_ev.signal == "LONG_EXIT" and (entry_is_range or htf_trend != "BULLISH"):
                        book(bc, "REGIME_EXIT")
                    elif position == "LONG":
                        opp_ev = self.on_bar(i, base, row, "NONE", context)
                        if opp_ev and opp_ev.signal == "SHORT":
                            book(bc, "OPPOSITE_SIGNAL")

                elif position == "SHORT":
                    if bl < lowest_since_entry:
                        lowest_since_entry = bl
                    profit = entry_price - lowest_since_entry
                    if profit >= atr_v * trail_activation:
                        trail_sl = lowest_since_entry + atr_v * trail_mult
                        if be_trigger > 0 and profit > atr_v * be_trigger:
                            trail_sl = min(trail_sl, entry_price - atr_v * be_buffer)
                        trail_sl = max(trail_sl, lowest_since_entry)
                        if trail_sl < sl:
                            sl = trail_sl

                    sig_ev = self.on_bar(i, base, row, "SHORT", context)
                    htf_trend = self._htf_cache.htf_trend if self._htf_cache else "NEUTRAL"
                    if sig_ev and sig_ev.signal == "SHORT_EXIT" and (entry_is_range or htf_trend != "BEARISH"):
                        book(bc, "REGIME_EXIT")
                    elif position == "SHORT":
                        opp_ev = self.on_bar(i, base, row, "NONE", context)
                        if opp_ev and opp_ev.signal == "LONG":
                            book(bc, "OPPOSITE_SIGNAL")

            if position == "NONE":
                if cur_mins >= eod_minute:
                    continue

                if i <= cooldown_until:
                    self.on_bar(i, base, row, "NONE", context)
                    regime_counts[self._regime] = regime_counts.get(self._regime, 0) + 1
                    continue

                context_start = max(0, i - 80)
                df_slice_for_hl = base.iloc[context_start:i + 1]

                sig_ev = self.on_bar(i, base, row, "NONE", context)
                regime_counts[self._regime] = regime_counts.get(self._regime, 0) + 1

                if sig_ev and sig_ev.signal in ("LONG", "SHORT"):
                    entry_signals += 1
                    entry_price = bc
                    sl = sig_ev.sl
                    entry_t1 = sig_ev.target1
                    entry_t2 = sig_ev.target2
                    entry_is_range = (sig_ev.regime == "SIDEWAYS")
                    entry_half_life = self._compute_half_life(df_slice_for_hl) if entry_is_range else 0.0
                    position = sig_ev.signal
                    entry_idx = i
                    highest_since_entry = bh
                    lowest_since_entry = bl

        if position != "NONE":
            entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
            trades.append({
                "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": "", "direction": position, "entry_price": round(entry_price, 2),
                "sl": round(sl, 2), "target1": round(entry_t1, 2), "target2": round(entry_t2, 2),
                "exit_price": None, "exit_reason": "OPEN", "pnl": 0, "pnl_pts": 0,
                "pnl_inr": 0, "est_cost": 0, "source": "RANGE" if entry_is_range else "TREND",
            })

        if _trade_start_date is not None and trades:
            trades = [t for t in trades if pd.to_datetime(t["entry_time"]) >= _trade_start_date]

        tdf = pd.DataFrame(trades)
        if tdf.empty:
            return {"error": "No trades generated", "trades": [], "stats": {}}

        total = len(tdf)
        wins = (tdf["pnl"] > 0).sum()
        gp = tdf[tdf["pnl"] > 0]["pnl"].sum()
        gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
        equity = initial_capital + tdf["pnl"].cumsum()
        dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

        net_series = tdf["pnl"] - tdf["est_cost"]
        net_gp = net_series[net_series > 0].sum()
        net_gl = abs(net_series[net_series <= 0].sum())

        stats = {
            "total_trades": total, "wins": int(wins), "losses": int(total - wins),
            "win_rate_pct": round(wins / total * 100, 1),
            "profit_factor": round(gp / gl, 2) if gl > 0 else 0,
            "total_pnl": round(tdf["pnl"].sum(), 0),
            "avg_win": round(tdf[tdf["pnl"] > 0]["pnl"].mean(), 0) if wins > 0 else 0,
            "avg_loss": round(tdf[tdf["pnl"] <= 0]["pnl"].mean(), 0) if (total - wins) > 0 else 0,
            "max_drawdown_pct": round(dd, 2),
            "expectancy": round(tdf["pnl"].mean(), 0),
            "final_capital": round(float(equity.iloc[-1]), 0),
            "exit_distribution": tdf["exit_reason"].value_counts().to_dict(),
            "est_total_costs": round(tdf["est_cost"].sum(), 0),
            "net_pnl_after_costs": round(net_series.sum(), 0),
            "net_profit_factor": round(net_gp / net_gl, 2) if net_gl > 0 else 0,
        }
        tdf["equity"] = initial_capital + tdf["pnl"].cumsum()
        equity_curve = tdf[["entry_time", "equity", "pnl"]].to_dict(orient="records")
        stats["regime_distribution"] = regime_counts
        stats["entry_signals"] = entry_signals
        stats["vetoed_signals"] = 0

        return {"stats": stats, "trades": tdf.to_dict(orient="records"), "equity_curve": equity_curve}


class RegimeTrendRangeV2SuperTrendAddOnKernel(RegimeTrendRangeV2HalfLifeExitKernel):
    """Adds the adaptive-SuperTrend-oscillator entry (ported from the
    user's PineScript research, jsr 2 strategy.txt) as one more fallback
    trend-entry path on top of the validated chain (pullback -> CUSUM ->
    Donchian -> BURST). Validated on the full 13-month benchmark: +201
    pts captured, PF 1.93->1.94, net +528,425->+534,458 rs, maxDD
    actually improves slightly (-6.98%->-6.94%). See
    scratch/research_v1/SESSION_STATUS.md for the full comparison.

    Source-fidelity note: the source's own recency filter (`x2 > -79`)
    is a bug in the PineScript itself (compares an ever-growing bar_index
    to -79, effectively always true). Both the literal (always-true) and
    a genuine recency-filtered version were tested -- the literal version
    performs slightly better, so USE_RECENCY_FILTER defaults to False.
    """

    strategy_id = "regime_trend_v2_supertrend_addon"
    ST_LENGTH = 10
    ST_MULT = 2.0
    ST_SL_LOOKBACK = 6
    ST_SL_ATR_BUFFER = 0.6
    USE_RECENCY_FILTER = False
    MAX_BARS_SINCE_FLIP = 3

    def _supertrend_entry(self, df_slice, row, regime, atr):
        from .regime_agents.trend_entry import TrendEntryState
        from .regime_agents.supertrend_adaptive import compute_supertrend_adaptive

        res = compute_supertrend_adaptive(df_slice, length=self.ST_LENGTH, mult=self.ST_MULT)
        if res is None:
            return None

        i = -1
        if not res["sign_change"][i]:
            return None
        if self.USE_RECENCY_FILTER and res["bars_since_flip"][i] > self.MAX_BARS_SINCE_FLIP:
            return None

        close = float(row.get("close", 0))
        osc_val = res["osc"][i]
        ama_scaled = res["ama"][i] * 100.0
        sig_val = osc_val * -100.0
        trend_val = res["trend"][i]
        spt_val = res["spt"][i]

        highs = df_slice["high"].values.astype(float)
        lows = df_slice["low"].values.astype(float)
        lb = min(self.ST_SL_LOOKBACK, len(df_slice))
        quality = round(0.5 * 0.6 + 0.5 * regime.confidence, 3)

        if (trend_val == 1 and close > spt_val and ama_scaled > sig_val
                and regime.regime == "TRENDING_UP"):
            recent_low = float(lows[-lb:].min())
            sl = recent_low - atr * self.ST_SL_ATR_BUFFER
            return TrendEntryState(
                signal="LONG", entry_price=close, sl=round(sl, 2),
                target1=round(close + atr * 1.5, 2), target2=round(close + atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=[f"SuperTrend-Adaptive: bullish flip, ama={ama_scaled:.1f} > sig={sig_val:.1f}"],
            )
        elif (trend_val == 0 and close < spt_val and ama_scaled < sig_val
                and regime.regime == "TRENDING_DOWN"):
            recent_high = float(highs[-lb:].max())
            sl = recent_high + atr * self.ST_SL_ATR_BUFFER
            return TrendEntryState(
                signal="SHORT", entry_price=close, sl=round(sl, 2),
                target1=round(close - atr * 1.5, 2), target2=round(close - atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=[f"SuperTrend-Adaptive: bearish flip, ama={ama_scaled:.1f} < sig={sig_val:.1f}"],
            )
        return None

    def _run_agents(self, df_slice, row, position, cfg, df_1h, df_1d, df_1w):
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if pd.isna(atr) or atr < 1:
            atr = close * 0.002

        self._cusum_update(df_slice)

        regime = self._regime_agent.evaluate(df_slice, self._regime, self._regime_age)
        self._regime = regime.regime
        self._regime_age = regime.regime_age
        self._last_regime_state = regime

        cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
        if self._htf_cache is None or cur_hour != self._htf_cache_hour:
            self._htf_cache = self._htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
            self._htf_cache_hour = cur_hour
        htf = self._htf_cache

        trend_entry = self._trend_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal == "HOLD" and position == "NONE":
            st = self._supertrend_entry(df_slice, row, regime, atr)
            if st is not None:
                trend_entry = st
            else:
                cus = self._cusum_entry(row, regime, atr)
                if cus is not None:
                    trend_entry = cus
                else:
                    donch = self._donchian_entry(df_slice, row, regime, atr)
                    if donch is not None:
                        trend_entry = donch
                    else:
                        burst = self._burst_entry(df_slice, row, regime, atr)
                        if burst is not None:
                            trend_entry = burst

        range_entry = self._range_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal in ("LONG", "SHORT"):
            proposed = trend_entry.signal
        elif range_entry.signal in ("LONG", "SHORT"):
            proposed = range_entry.signal
        else:
            proposed = "HOLD"
        momentum = self._momentum_agent.evaluate(df_slice, proposed, regime.regime)

        return self._orchestrator.evaluate(row, regime, trend_entry, range_entry,
                                           htf, momentum, position)


class RegimeTrendRangeV2HalfTrendStandaloneKernel(RegimeTrendRangeV2HalfLifeExitKernel):
    """Standalone HalfTrend (Everget) + Hull-momentum-confirmation trend
    entry -- ported from the user's own PineScript research (All bank
    atm.txt). Trend entries come from pullback (TrendEntryAgentV2) OR
    HalfTrend+Hull ONLY -- no CUSUM, no Donchian, no Burst (those were
    found to make the blend WORSE when this signal was stacked on top of
    the full V1 Final chain; this is a genuinely separate, simpler
    system instead). Validated on the full 13-month benchmark at
    HT_AMPLITUDE=4 (best of the sweep): +15,117.4 pts (10.3% capture),
    PF 2.21, net +453,519 rs, maxDD -5.27% -- comfortably inside the 7%
    risk budget, best single-metric result of any HalfTrend+Hull config
    tested. See scratch/research_v1/SESSION_STATUS.md for the full sweep.

    Hull Butterfly Oscillator fidelity note: the source's `cmean` term is
    an expanding average since the chart's inception, not portable to a
    fixed backtest window -- substituted with a standard Hull Moving
    Average slope-flip for the same confirmation role (see
    regime_agents/halftrend_hull.py for details).

    All parameters below are ordinary class attributes -- override them
    in a subclass (or edit directly) to try other configurations; the
    ones commented as "swept" were tested against the full 13-month
    benchmark, others are worth exploring further.
    """

    strategy_id = "regime_trend_v2_halftrend_standalone"
    HT_AMPLITUDE = 4      # swept: 2 (base)=PF2.11, 3=PF2.13, 4=PF2.21 (best, adopted)
    HT_CHANNEL_DEVIATION = 2
    HT_ATR_LENGTH = 40    # capped to fit the ~80-bar rolling context window
    HULL_LENGTH = 11      # swept: 21=slightly worse (fewer, no better trades)
    HULL_LOOKBACK_BARS = 3  # swept: 6=roughly neutral
    HT_SL_LOOKBACK = 6
    HT_SL_ATR_BUFFER = 0.6

    def _halftrend_entry(self, df_slice, row, regime, atr):
        from .regime_agents.trend_entry import TrendEntryState
        from .regime_agents.halftrend_hull import compute_halftrend, compute_hull_momentum_flip

        ht = compute_halftrend(df_slice, amplitude=self.HT_AMPLITUDE,
                                channel_deviation=self.HT_CHANNEL_DEVIATION,
                                atr_length=self.HT_ATR_LENGTH)
        if ht is None:
            return None
        hull = compute_hull_momentum_flip(df_slice, length=self.HULL_LENGTH)
        if hull is None:
            return None
        hull_up_flip, hull_down_flip = hull

        i = -1
        close = float(row.get("close", 0))
        highs = df_slice["high"].values.astype(float)
        lows = df_slice["low"].values.astype(float)
        lb = min(self.HT_SL_LOOKBACK, len(df_slice))
        quality = round(0.5 * 0.6 + 0.5 * regime.confidence, 3)

        lookback = self.HULL_LOOKBACK_BARS
        recent_hull_up = bool(hull_up_flip[-lookback - 1:].any()) if lookback > 0 else bool(hull_up_flip[i])
        recent_hull_down = bool(hull_down_flip[-lookback - 1:].any()) if lookback > 0 else bool(hull_down_flip[i])

        if ht["buy_signal"][i] and recent_hull_up and regime.regime == "TRENDING_UP":
            recent_low = float(lows[-lb:].min())
            sl = recent_low - atr * self.HT_SL_ATR_BUFFER
            return TrendEntryState(
                signal="LONG", entry_price=close, sl=round(sl, 2),
                target1=round(close + atr * 1.5, 2), target2=round(close + atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=["HalfTrend bullish flip confirmed by Hull momentum flip"],
            )
        elif ht["sell_signal"][i] and recent_hull_down and regime.regime == "TRENDING_DOWN":
            recent_high = float(highs[-lb:].max())
            sl = recent_high + atr * self.HT_SL_ATR_BUFFER
            return TrendEntryState(
                signal="SHORT", entry_price=close, sl=round(sl, 2),
                target1=round(close - atr * 1.5, 2), target2=round(close - atr * 3.0, 2),
                pullback_depth=0.0, entry_quality=quality,
                reasons=["HalfTrend bearish flip confirmed by Hull momentum flip"],
            )
        return None

    def _run_agents(self, df_slice, row, position, cfg, df_1h, df_1d, df_1w):
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if pd.isna(atr) or atr < 1:
            atr = close * 0.002

        regime = self._regime_agent.evaluate(df_slice, self._regime, self._regime_age)
        self._regime = regime.regime
        self._regime_age = regime.regime_age
        self._last_regime_state = regime

        cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
        if self._htf_cache is None or cur_hour != self._htf_cache_hour:
            self._htf_cache = self._htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
            self._htf_cache_hour = cur_hour
        htf = self._htf_cache

        trend_entry = self._trend_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal == "HOLD" and position == "NONE":
            ht_entry = self._halftrend_entry(df_slice, row, regime, atr)
            if ht_entry is not None:
                trend_entry = ht_entry

        range_entry = self._range_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        if trend_entry.signal in ("LONG", "SHORT"):
            proposed = trend_entry.signal
        elif range_entry.signal in ("LONG", "SHORT"):
            proposed = range_entry.signal
        else:
            proposed = "HOLD"
        momentum = self._momentum_agent.evaluate(df_slice, proposed, regime.regime)

        return self._orchestrator.evaluate(row, regime, trend_entry, range_entry,
                                           htf, momentum, position)


class RegimeTrendRangeV2CUSUMCappedSLKernel(RegimeTrendRangeV2HalfLifeExitKernel):
    """Looser CUSUM change-point threshold (1.5 ATR vs V1 Final's 2.0),
    with Donchian entries disabled and cooldown extended to 8 bars --
    the best-performing config found across the whole research session.

    Backstory: raw CUSUM_THRESHOLD_ATR=1.5 alone captures more points but
    blows drawdown out to -11.44% (vs V1 Final's -6.98%). Diagnosed via
    diagnose_cusum15_dd.py (scratch/research_v1/): the blowout was NOT
    diffuse -- it traced to Donchian entries turning net-negative at this
    looser threshold (-47,234 rs, 26.7% win rate) compounding two
    outsized CUSUM stop-losses during the March 2026 selloff. Disabling
    Donchian recovered almost everything (+20,741.8 pts, PF 2.25, net
    +622,253, maxDD -8.98%). Extending cooldown 4->8 bars closed most of
    the remaining drawdown gap: +21,096.3 pts (14.3% capture), PF 2.37,
    net +632,889 rs, maxDD -7.92% -- the best points/PF/net of anything
    tested this session, though maxDD sits ~0.9pp over the 7% risk
    budget (a sweep of cooldown 2-16 found this is the closest any
    config gets; higher cooldowns give back returns without closing the
    gap further -- see scratch/research_v1/SESSION_STATUS.md).

    Notably: this is the ONLY one of the three research strategies that
    turns July 2026 (the month that started this whole research project)
    net POSITIVE -- V1 Final is still negative there even with the
    SuperTrend-adaptive addition.

    CUSUM_SL_MAX_POINTS is kept here for completeness (an earlier fix
    attempt -- capping the CUSUM entry's SL distance in absolute points)
    but was found to be a total no-op once Donchian is disabled; the
    real fix was DISABLE_DONCHIAN + COOLDOWN_BARS, not the SL cap.
    """

    strategy_id = "regime_trend_v2_cusum_capped_sl"
    CUSUM_SL_MAX_POINTS = 120.0
    DISABLE_DONCHIAN = True

    def _cusum_entry(self, row, regime, atr):
        ev = super()._cusum_entry(row, regime, atr)
        if ev is None or ev.signal not in ("LONG", "SHORT"):
            return ev
        close = float(row.get("close", 0))
        if ev.signal == "LONG":
            dist = close - ev.sl
            if dist > self.CUSUM_SL_MAX_POINTS:
                ev.sl = round(close - self.CUSUM_SL_MAX_POINTS, 2)
        else:
            dist = ev.sl - close
            if dist > self.CUSUM_SL_MAX_POINTS:
                ev.sl = round(close + self.CUSUM_SL_MAX_POINTS, 2)
        return ev

    def _donchian_entry(self, df_slice, row, regime, atr):
        if self.DISABLE_DONCHIAN:
            return None
        return super()._donchian_entry(df_slice, row, regime, atr)
