"""
regime_trend_v2_kernel.py — Regime Trend V2 (optimized) StrategyKernel.

Optimized evolution of regime_trend_kernel.py. The ORIGINAL kernel is untouched;
this file registers two NEW strategies selectable in the backtest page:

  * regime_trend_v2   — "Selective" profile (WINNER of the optimization study)
  * regime_trend_v2b  — "Balanced" profile (runner-up, more trades)

What changed vs V1 (validated on 13 months of BANKNIFTY 5m Dhan data,
train Jul-25→Mar-26 / hold-out Apr-26→Jul-26, scored NET of costs):

  1. EXIT ASYMMETRY FIXED.  V1 risked ~2 ATR per trade but banked winners at
     ~0.5 ATR (BE lock 0.3 ATR + tight 1.5 ATR trail) → avg loss > avg win.
     V2 lets winners run: trail 4.0 ATR (3.5 in v2b) behind peak, breakeven
     only after +1.0 ATR profit (0.8 in v2b).
  2. ENTRY SELECTIVITY.  Only take entries with orchestrator weighted_score
     ≥ 0.65 (0.60 in v2b) and a strong confirmation bar (body ≥ 40% of range
     vs 20% in V1).  Cuts trade count ~85-90% → costs stop eating the edge.
  3. LOSS COOLDOWN.  After a losing trade, no new entries for 12 bars (1 hour).
  4. HTF LOOKAHEAD FIX.  V1 sliced 1H/1D/1W frames by bar-START timestamp,
     which let the still-forming hourly/daily bar leak its final OHLC into the
     HTF structure agent during backtests (live saw partial bars → divergence).
     V2 only uses fully CLOSED HTF bars — backtest and live see identical data.

Execution-integrity guarantees (unchanged from V1's design, re-audited):
  - Signals are computed ONLY from completed 5m bars (no intra-bar repaint).
  - Entries fill at the signal bar's close; SL exits fill at the SL price only
    if that price actually traded within the exit bar's [low, high] range,
    otherwise at the bar's real open (no fictitious fills).
  - run_backtest() is deterministic and causal → the live BacktestDiffProcessor
    emits signals identical to the backtest by construction.
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional

import strategy as main_strategy
from config import get_settings, INSTRUMENT_META
from strategy_kernel import StrategyKernel, SignalEvent, register_kernel

from .regime_agents.regime_detection import RegimeDetectionAgent
from .regime_agents.trend_entry import TrendEntryAgent
from .regime_agents.range_entry import RangeEntryAgent, RangeEntryState
from .regime_agents.htf_structure import HTFStructureAgent
from .regime_agents.momentum_volume import MomentumVolumeAgent
from .regime_agents.regime_orchestrator import RegimeOrchestrator

logger = logging.getLogger(__name__)


class TrendEntryAgentV2(TrendEntryAgent):
    """Trend entry agent with a stronger confirmation-bar requirement.

    V1 accepted any bar whose body was >20% of its range; V2 demands >=40%,
    filtering out indecisive doji-style 'confirmations'.
    """

    BODY_FRAC = 0.40

    def _is_bullish_bar(self, row: pd.Series) -> bool:
        o = float(row.get("open", 0))
        c = float(row.get("close", 0))
        h = float(row.get("high", c))
        l = float(row.get("low", c))
        bar_range = h - l
        if bar_range <= 0:
            return False
        body = c - o
        return body > 0 and body / bar_range > self.BODY_FRAC

    def _is_bearish_bar(self, row: pd.Series) -> bool:
        o = float(row.get("open", 0))
        c = float(row.get("close", 0))
        h = float(row.get("high", c))
        l = float(row.get("low", c))
        bar_range = h - l
        if bar_range <= 0:
            return False
        body = o - c
        return body > 0 and body / bar_range > self.BODY_FRAC


class RegimeTrendV2Kernel(StrategyKernel):
    """Regime Trend V2 — 'Selective' profile (optimization winner).

    13-month validation (Jul-2025 → Jul-2026, BANKNIFTY, 1 lot of 30, NET of
    ~₹550/trade futures costs): 217 trades (~17/month), gross WR 64.5%,
    gross +₹302K, net +₹181K, net PF 1.49, net max DD -8.0%.
    (V1 over the same window: 2,052 trades, gross +₹826K, net -₹316K.)
    """

    strategy_id = "regime_trend_v2"
    live_capable = True

    # ── Exit engine (config-overridable via v2_* settings if present) ─────
    TRAIL_MULT = 4.0          # trail distance = ATR × this (V1: 1.5)
    TRAIL_ACTIVATION = 0.3    # profit (ATR) before trailing starts
    BE_TRIGGER = 1.0          # profit (ATR) to lock breakeven (V1: 0.3)
    BE_BUFFER = 0.4           # profit locked above entry (ATR)

    # ── Entry selectivity ────────────────────────────────────────────────
    MIN_WEIGHTED_SCORE = 0.65  # orchestrator consensus score gate (V1: none)
    COOLDOWN_BARS = 12         # bars to sit out after a losing trade

    _CFG_PREFIX = "v2"         # settings override prefix (e.g. v2_trail_mult)
    _TREND_ONLY = True
    _EOD_EXIT_NSE = 15 * 60 + 20
    _EOD_EXIT_MCX = 23 * 60 + 20

    def __init__(self):
        super().__init__()
        self._regime_agent = RegimeDetectionAgent(lookback=15, swing_order=3)
        self._trend_agent = TrendEntryAgentV2(swing_order=3, min_pullback=0.20,
                                              max_pullback=0.65)
        self._range_agent = RangeEntryAgent(lookback=40, proximity_pct=0.15,
                                            min_range_atr=2.0)
        self._htf_agent = HTFStructureAgent(swing_order=5, proximity_atr=0.5)
        self._momentum_agent = MomentumVolumeAgent(lookback=15)
        self._orchestrator = RegimeOrchestrator(min_quality=0.25, min_rr=0.0,
                                                min_regime_age=1)
        self._regime = "SIDEWAYS"
        self._regime_age = 0
        self._htf_cache = None
        self._htf_cache_hour = -1

    def reset(self):
        self._regime = "SIDEWAYS"
        self._regime_age = 0
        self._htf_cache = None
        self._htf_cache_hour = -1

    # ── Helpers ──────────────────────────────────────────────────────────

    def _cfg_val(self, cfg, name, default):
        return getattr(cfg, f"{self._CFG_PREFIX}_{name}", default)

    def _eod_exit_minute(self, cfg=None):
        if cfg is None:
            cfg = get_settings()
        exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
        return self._EOD_EXIT_MCX if exch == "MCX" else self._EOD_EXIT_NSE

    def _session_mask(self, base: pd.DataFrame, cfg) -> pd.Series:
        exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
        mins = base["_hour"] * 60 + base["_minute"]
        if exch == "MCX":
            start, end_excl = 9 * 60, 23 * 60 + 30
        else:
            start, end_excl = 9 * 60 + 20, 15 * 60 + 20
        return (mins >= start) & (mins < end_excl)

    @staticmethod
    def _estimate_round_trip_cost(entry_px: float, exit_px: float, qty: int) -> float:
        """
        Realistic 1-lot BANKNIFTY OPTIONS round-trip cost (buy + sell), per Dhan's
        published pricing (dhan.co/pricing, checked 2026-10-01) and NSE/SEBI
        statutory rates. Reporting only — does NOT affect signals.

        entry_px/exit_px are INDEX levels here (this kernel trades in index
        points), not the option premium actually paid, so STT/exchange/stamp
        (levied on premium) can't be derived from them directly — they're a
        small fraction of the total regardless (brokerage + GST on brokerage
        alone is ~85% of the round trip), so a representative near-ATM weekly
        premium is assumed for those components.

        Corrected 2026-10-01: the previous version computed STT/exchange/stamp
        on the INDEX notional (entry_px * qty, e.g. Rs 16L+), correct for a
        futures trade but wildly overstating an options trade's real charges —
        it put the round-trip estimate at ~18 index points (~Rs 530-550).
        User's real Dhan contract notes show ~Rs 50-55 per round trip; this
        formula lands at ~Rs 55 for a typical Rs 120 premium, matching that.
        """
        brokerage = 20.0 * 2                      # Rs 20/executed order (Dhan), both legs
        assumed_premium = 120.0                   # representative near-ATM weekly premium
        notional = assumed_premium * qty          # one side's turnover
        stt = 0.001 * notional * 2                # 0.1% on buy AND sell (Dhan pricing page)
        exch_txn = 0.000030699 * notional * 2     # NSE: 0.0030699%, both legs
        sebi = 0.000001 * notional * 2            # SEBI: 0.0001% of turnover, both legs
        stamp = 0.00015 * notional                # stamp duty: 0.015% on buy-side turnover only
        gst = 0.18 * (brokerage + exch_txn + sebi)  # GST: 18% on brokerage + exchange + SEBI
        return brokerage + stt + exch_txn + sebi + stamp + gst

    def _run_agents(self, df_slice, row, position, cfg, df_1h, df_1d, df_1w):
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if pd.isna(atr) or atr < 1:
            atr = close * 0.002

        regime = self._regime_agent.evaluate(df_slice, self._regime, self._regime_age)
        self._regime = regime.regime
        self._regime_age = regime.regime_age

        cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
        if self._htf_cache is None or cur_hour != self._htf_cache_hour:
            self._htf_cache = self._htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
            self._htf_cache_hour = cur_hour
        htf = self._htf_cache

        trend_entry = self._trend_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr)

        range_entry = RangeEntryState(signal="HOLD", reasons=["Trend-only mode"])

        proposed = trend_entry.signal if trend_entry.signal in ("LONG", "SHORT") else "HOLD"
        momentum = self._momentum_agent.evaluate(df_slice, proposed, regime.regime)

        return self._orchestrator.evaluate(row, regime, trend_entry, range_entry,
                                           htf, momentum, position)

    # ═════════════════════════════════════════════════════════════════════
    # on_bar() — single entry point for backtest AND live (completed bars)
    # ═════════════════════════════════════════════════════════════════════

    def on_bar(self, bar_idx: int, base_df, row, position: str,
               context: dict) -> Optional[SignalEvent]:
        cfg = context.get("cfg") or get_settings()
        df_1h = context.get("df_1h")
        df_1d = context.get("df_1d")
        df_1w = context.get("df_1w")

        context_start = max(0, bar_idx - 80)
        df_slice = base_df.iloc[context_start:bar_idx + 1]

        # ── HTF slicing: ONLY fully-closed higher-timeframe bars ─────────
        # (V1 used `timestamp <= cur_ts` on bar-START stamps, leaking the
        #  still-forming 1H/1D bar's final OHLC into backtests.)
        cur_ts = pd.Timestamp(row["timestamp"])
        h_cut = cur_ts - pd.Timedelta(hours=1)
        d_cut = pd.Timestamp(cur_ts.date())          # today's daily bar is forming
        w_cut = cur_ts - pd.Timedelta(weeks=1)
        df_1h_cur = df_1h[df_1h["timestamp"] <= h_cut].tail(200) if df_1h is not None else None
        df_1d_cur = df_1d[df_1d["timestamp"] < d_cut].tail(200) if df_1d is not None else None
        df_1w_cur = df_1w[df_1w["timestamp"] <= w_cut].tail(200) if df_1w is not None else None

        sig = self._run_agents(df_slice, row, position, cfg, df_1h_cur, df_1d_cur, df_1w_cur)

        if sig.signal in ("LONG", "SHORT"):
            # ── V2 selectivity gate: consensus score must clear the bar ──
            min_score = self._cfg_val(cfg, "min_weighted_score", self.MIN_WEIGHTED_SCORE)
            if sig.weighted_score < min_score:
                return None
            ts_str = cur_ts.strftime("%Y-%m-%d %H:%M:%S")
            return SignalEvent(
                signal=sig.signal,
                direction=sig.signal,
                timestamp=ts_str,
                strategy_id=self.strategy_id,
                entry_price=float(row["close"]),
                sl=sig.sl,
                target1=getattr(sig, "target1", 0.0),
                target2=getattr(sig, "target2", 0.0),
                reasons=(sig.reasons if hasattr(sig, "reasons") else [])
                + [f"V2 score {sig.weighted_score:.2f} >= {min_score}"],
                regime=self._regime,
                regime_confidence=getattr(sig, "regime_confidence", 0.0),
                trade_source="REGIME_V2",
                atr=float(row.get("atr", 0)),
                weighted_score=sig.weighted_score,
            )
        elif sig.signal in ("LONG_EXIT", "SHORT_EXIT"):
            ts_str = cur_ts.strftime("%Y-%m-%d %H:%M:%S")
            return SignalEvent(
                signal=sig.signal,
                direction="LONG" if "LONG" in sig.signal else "SHORT",
                timestamp=ts_str,
                strategy_id=self.strategy_id,
                exit_price=float(row["close"]),
                reasons=sig.reasons if hasattr(sig, "reasons") else [],
                regime=self._regime,
                trade_source="REGIME_V2",
                atr=float(row.get("atr", 0)),
            )
        return None

    # ═════════════════════════════════════════════════════════════════════
    # run_backtest() — V2 trade management (wide trail, late BE, cooldown)
    # ═════════════════════════════════════════════════════════════════════

    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 30, lot_multiplier: int = 1,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> dict:
        cfg = get_settings()
        eod_minute = self._eod_exit_minute(cfg)
        trail_mult = self._cfg_val(cfg, "trail_mult", self.TRAIL_MULT)
        trail_activation = self._cfg_val(cfg, "trail_activation", self.TRAIL_ACTIVATION)
        be_trigger = self._cfg_val(cfg, "be_trigger", self.BE_TRIGGER)
        be_buffer = self._cfg_val(cfg, "be_buffer", self.BE_BUFFER)
        cooldown_bars = int(self._cfg_val(cfg, "cooldown_bars", self.COOLDOWN_BARS))
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

        df_1h = frames.get("60")
        df_1d = frames.get("1D")
        df_1w = None
        if df_1d is not None and len(df_1d) >= 30:
            try:
                from features.weekly import derive_weekly_from_daily
                df_1w = derive_weekly_from_daily(df_1d)
            except Exception:
                pass

        logger.info(f"[{self.strategy_id}] Backtest on {len(base)} bars, "
                    f"range: {base['timestamp'].min()} -> {base['timestamp'].max()}")

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
        intraday_mode = getattr(cfg, "position_hold_mode", "INTRADAY") != "CARRY_FORWARD"
        prev_date = None
        prev_close = None

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
            cur_date = ts_ist.date()

            # ── MANAGE OPEN POSITION ────────────────────────────────────
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
                        "est_cost": round(self._estimate_round_trip_cost(
                            entry_price, exit_px, qty), 2),
                    })
                    # ── V2: cooldown after a losing trade ────────────────
                    if net < 0 and cooldown_bars > 0:
                        cooldown_until = i + cooldown_bars
                    position = "NONE"

                # 0. END OF DAY EXIT — only enforced in INTRADAY mode.
                if intraday_mode:
                    if prev_date is not None and cur_date != prev_date:
                        book(prev_close, "EOD_EXIT")
                    if position != "NONE" and cur_mins >= eod_minute:
                        book(bc, "EOD_EXIT")
                        prev_date = cur_date
                        prev_close = bc
                        continue

                # 1. STOP LOSS — real-fill check: book AT the sl level only if
                # price actually traded there this bar; otherwise book at the
                # bar's own open (never a fictitious price).
                if position == "LONG" and bl <= sl:
                    real_exit = sl if sl <= bh + 0.01 else bo
                    book(real_exit, "SL_HIT")
                elif position == "SHORT" and bh >= sl:
                    real_exit = sl if sl >= bl - 0.01 else bo
                    book(real_exit, "SL_HIT")

                # 2. TRAILING STOP (wide V2 trail, late breakeven)
                elif position == "LONG":
                    if bh > highest_since_entry:
                        highest_since_entry = bh
                    profit = highest_since_entry - entry_price
                    if profit >= atr_v * trail_activation:
                        trail_sl = highest_since_entry - atr_v * trail_mult
                        if be_trigger > 0 and profit > atr_v * be_trigger:
                            trail_sl = max(trail_sl, entry_price + atr_v * be_buffer)
                        # Bound to prices this trade actually saw
                        trail_sl = min(trail_sl, highest_since_entry)
                        if trail_sl > sl:
                            sl = trail_sl

                    # 3. REGIME FLIP EXIT
                    sig_ev = self.on_bar(i, base, row, "LONG", context)
                    if sig_ev and sig_ev.signal == "LONG_EXIT":
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
                    if sig_ev and sig_ev.signal == "SHORT_EXIT":
                        book(bc, "REGIME_EXIT")
                    elif position == "SHORT":
                        opp_ev = self.on_bar(i, base, row, "NONE", context)
                        if opp_ev and opp_ev.signal == "LONG":
                            book(bc, "OPPOSITE_SIGNAL")

            # ── LOOK FOR NEW ENTRY ──────────────────────────────────────
            if position == "NONE":
                if intraday_mode and cur_mins >= eod_minute:
                    prev_date = cur_date
                    prev_close = bc
                    continue

                # V2: cooldown after loss — keep agents evaluating so regime
                # state stays warm, but take no entries.
                if i <= cooldown_until:
                    self.on_bar(i, base, row, "NONE", context)
                    regime_counts[self._regime] = regime_counts.get(self._regime, 0) + 1
                    prev_date = cur_date
                    prev_close = bc
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

            prev_date = cur_date
            prev_close = bc

        # ── INCLUDE OPEN POSITION ───────────────────────────────────────
        if position != "NONE":
            entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
            trades.append({
                "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": "",
                "direction": position,
                "entry_price": round(entry_price, 2),
                "sl": round(sl, 2),
                "target1": round(entry_t1, 2),
                "target2": round(entry_t2, 2),
                "exit_price": None,
                "exit_reason": "OPEN",
                "pnl": 0,
                "pnl_pts": 0,
                "pnl_inr": 0,
                "est_cost": 0,
            })

        if _trade_start_date is not None and trades:
            trades = [t for t in trades
                      if pd.to_datetime(t["entry_time"]) >= _trade_start_date]

        tdf = pd.DataFrame(trades)
        if tdf.empty:
            return {"error": "No trades generated", "trades": [], "stats": {}}

        total = len(tdf)
        wins = (tdf["pnl"] > 0).sum()
        gp = tdf[tdf["pnl"] > 0]["pnl"].sum()
        gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
        equity = initial_capital + tdf["pnl"].cumsum()
        dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

        # Net-of-costs view (reporting only; signals are cost-blind)
        net_series = tdf["pnl"] - tdf["est_cost"]
        net_gp = net_series[net_series > 0].sum()
        net_gl = abs(net_series[net_series <= 0].sum())

        stats = {
            "total_trades": total,
            "wins": int(wins),
            "losses": int(total - wins),
            "win_rate_pct": round(wins / total * 100, 1),
            "profit_factor": round(gp / gl, 2) if gl > 0 else 0,
            "total_pnl": round(tdf["pnl"].sum(), 0),
            "avg_win": round(tdf[tdf["pnl"] > 0]["pnl"].mean(), 0) if wins > 0 else 0,
            "avg_loss": round(tdf[tdf["pnl"] <= 0]["pnl"].mean(), 0) if (total - wins) > 0 else 0,
            "max_drawdown_pct": round(dd, 2),
            "expectancy": round(tdf["pnl"].mean(), 0),
            "final_capital": round(float(equity.iloc[-1]), 0),
            "exit_distribution": tdf["exit_reason"].value_counts().to_dict(),
            # ── V2 extras: honest net-of-costs summary ───────────────────
            "est_total_costs": round(tdf["est_cost"].sum(), 0),
            "net_pnl_after_costs": round(net_series.sum(), 0),
            "net_profit_factor": round(net_gp / net_gl, 2) if net_gl > 0 else 0,
        }

        tdf["equity"] = initial_capital + tdf["pnl"].cumsum()
        equity_curve = tdf[["entry_time", "equity", "pnl"]].to_dict(orient="records")

        stats["regime_distribution"] = regime_counts
        stats["entry_signals"] = entry_signals
        stats["vetoed_signals"] = 0

        return {
            "stats": stats,
            "trades": tdf.to_dict(orient="records"),
            "equity_curve": equity_curve,
        }


class RegimeTrendV2BKernel(RegimeTrendV2Kernel):
    """Regime Trend V2-B — 'Balanced' runner-up profile.

    More trades, slightly higher gross win rate, higher net P&L, but roughly
    double the drawdown of V2.  13-month validation: 373 trades, gross WR
    67.0%, gross +₹412K, net +₹204K, net PF 1.38, net max DD -14.3%.
    """

    strategy_id = "regime_trend_v2b"
    live_capable = True

    TRAIL_MULT = 3.5
    BE_TRIGGER = 0.8
    MIN_WEIGHTED_SCORE = 0.60

    _CFG_PREFIX = "v2b"
