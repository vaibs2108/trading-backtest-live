"""
donchian_kernel.py — Donchian 5m channel-breakout kernels (from-scratch family).

Winner of the from-scratch exploration round (ORB, VWAP-reversion, momentum
ignition and Donchian breakout were prototyped on 13 months of BANKNIFTY 5m
data with a train Jul-25→Mar-26 / hold-out Apr-26→Jul-26 split; only Donchian
was profitable in BOTH windows).

Two profiles are registered:

  * donchian_5m_swing    — N=36 channel (3 hours), SL 3 ATR, trail 5 ATR.
        May hold positions OVERNIGHT (same engine semantics as the other
        strategies' backtests). 13-mo validation: 262 trades (~1/day),
        avg +62.7 pts/trade (win +400 / loss -178), gross +₹4.93L,
        net +₹3.47L futures / +₹3.64L ITM-options, DD -16%/-12%.
        REQUIRES product_type=NRML and auto square-off disabled to match live.

  * donchian_5m_intraday — N=48 channel (4 hours), SL 3 ATR, trail 4 ATR,
        force-flat at 15:15 — NEVER holds overnight. 13-mo validation:
        353 trades, avg +26.3 pts/trade, gross +₹2.78L; net +₹0.82L futures
        (marginal) but +₹1.63L via ITM option buying — the only strictly
        intraday configuration found that clears costs. Best traded via
        ITM options, not futures.

Execution integrity (same rules as the V2 kernels):
  - signals from COMPLETED 5m bars only; entry at signal-bar close
  - SL fills only at prices that traded within the exit bar's [low, high];
    stale levels fill at the bar's real open — never a fictitious price
  - deterministic, causal run_backtest() → live diff-processor signals are
    identical to backtest by construction
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional

import strategy as main_strategy
from config import get_settings, INSTRUMENT_META
from strategy_kernel import StrategyKernel, SignalEvent, register_kernel

logger = logging.getLogger(__name__)

# ITM option-buying economics (reporting only)
_OPT_CAPTURE = 0.85     # delta capture of index points
# Rs 20/order brokerage (both legs) + GST on it + small STT/exchange/stamp on
# premium (Dhan pricing page, checked 2026-10-01) -- matches user's real Dhan
# contract notes, ~Rs 50-55 per round trip. See _fut_cost's docstring below
# for the same correction (that one previously computed on index notional).
_OPT_FIXED = 55.0       # brokerage + statutory per round trip
_OPT_SLIP_PTS = 2.0     # premium points lost per side


class DonchianSwingKernel(StrategyKernel):
    """Donchian 5m breakout — swing profile (may hold overnight)."""

    strategy_id = "donchian_5m_swing"
    live_capable = True

    N_CHANNEL = 36          # breakout channel length in 5m bars
    SL_ATR = 3.0            # initial stop distance
    TRAIL_MULT = 5.0        # trailing distance = ATR × this
    TRAIL_ACTIVATION = 0.3  # profit (ATR) before trailing starts
    MAX_PER_DAY = 2
    COOLDOWN_BARS = 12      # bars flat after a losing trade
    INTRADAY_ONLY = False   # swing: EOD exit disabled (matches engine semantics)

    def __init__(self):
        super().__init__()
        self.reset()

    def reset(self):
        pass  # stateless between runs — all state lives inside run_backtest

    # ── helpers ──────────────────────────────────────────────────────────

    def _session_mask(self, base: pd.DataFrame, cfg) -> pd.Series:
        exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
        mins = base["_hour"] * 60 + base["_minute"]
        if exch == "MCX":
            start, end_excl = 9 * 60, 23 * 60 + 30
        else:
            start, end_excl = 9 * 60 + 20, 15 * 60 + 20
        return (mins >= start) & (mins < end_excl)

    def _intraday_flat_minute(self, cfg) -> int:
        exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
        return (23 * 60 + 15) if exch == "MCX" else (15 * 60 + 15)

    @staticmethod
    def _fut_cost(entry_px: float, exit_px: float, qty: int) -> float:
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

    @staticmethod
    def _opt_net(points: float, qty: int) -> float:
        return points * qty * _OPT_CAPTURE - (_OPT_FIXED + _OPT_SLIP_PTS * 2 * qty)

    # ── on_bar: single-bar breakout check (used for API compat) ──────────

    def on_bar(self, bar_idx: int, base_df, row, position: str,
               context: dict) -> Optional[SignalEvent]:
        n_ch = self.N_CHANNEL
        if bar_idx < n_ch + 1 or position != "NONE":
            return None
        h = base_df["high"].values[bar_idx - n_ch:bar_idx]
        l = base_df["low"].values[bar_idx - n_ch:bar_idx]
        c = float(row["close"])
        atr = float(row.get("atr", c * 0.002))
        if pd.isna(atr) or atr < 5:
            atr = c * 0.002
        ts_str = pd.to_datetime(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        if c > float(np.max(h)):
            return SignalEvent(signal="LONG", direction="LONG", timestamp=ts_str,
                               strategy_id=self.strategy_id, entry_price=c,
                               sl=round(c - self.SL_ATR * atr, 2),
                               reasons=[f"Close broke {n_ch}-bar high"],
                               trade_source="DONCHIAN", atr=atr)
        if c < float(np.min(l)):
            return SignalEvent(signal="SHORT", direction="SHORT", timestamp=ts_str,
                               strategy_id=self.strategy_id, entry_price=c,
                               sl=round(c + self.SL_ATR * atr, 2),
                               reasons=[f"Close broke {n_ch}-bar low"],
                               trade_source="DONCHIAN", atr=atr)
        return None

    # ── run_backtest: the full engine ────────────────────────────────────

    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 30, lot_multiplier: int = 1,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> dict:
        cfg = get_settings()
        qty = lot_size * lot_multiplier
        n_ch = self.N_CHANNEL
        flat_min = self._intraday_flat_minute(cfg) if self.INTRADAY_ONLY else 10 ** 6

        base = main_strategy.build_merged_table(frames, with_patterns=False)
        if base.empty:
            return {"error": "No data after merging", "trades": [], "stats": {}}
        base = base[self._session_mask(base, cfg)].copy().reset_index(drop=True)
        if base.empty:
            return {"error": "No data in trading window", "trades": [], "stats": {}}

        _start = pd.to_datetime(start_date) if start_date else None
        if end_date:
            _end = pd.to_datetime(end_date).date()
            base = base[base["timestamp"].dt.date <= _end].copy().reset_index(drop=True)

        ts = pd.DatetimeIndex(base["timestamp"])
        o = base["open"].values.astype(float)
        h = base["high"].values.astype(float)
        l = base["low"].values.astype(float)
        c = base["close"].values.astype(float)
        atr_raw = base["atr"].values.astype(float) if "atr" in base.columns \
            else np.full(len(base), np.nan)
        atr = np.where(np.isnan(atr_raw) | (atr_raw < 5), c * 0.002, atr_raw)
        mins = (ts.hour * 60 + ts.minute).values
        dates = ts.date
        n = len(base)

        hs = pd.Series(h)
        ls = pd.Series(l)
        roll_hi = hs.rolling(n_ch).max().shift(1).values   # prior N bars, excl current
        roll_lo = ls.rolling(n_ch).min().shift(1).values

        logger.info(f"[{self.strategy_id}] Backtest on {n} bars, "
                    f"range: {ts.min()} -> {ts.max()}")

        trades = []
        position = 0
        entry_px = sl = 0.0
        entry_i = 0
        hi = 0.0
        lo = 9e9
        per_day = 0
        cur_day = None
        cd_until = -1

        for i in range(n):
            if cur_day != dates[i]:
                cur_day = dates[i]
                per_day = 0
            bh, bl, bc, bo = h[i], l[i], c[i], o[i]
            a = atr[i]

            if position != 0:
                exit_px = None
                reason = ""
                if mins[i] >= flat_min:
                    exit_px, reason = bc, "EOD_EXIT"
                elif position == 1:
                    if bl <= sl:
                        exit_px, reason = (sl if sl <= bh + 0.01 else bo), "SL_HIT"
                else:
                    if bh >= sl:
                        exit_px, reason = (sl if sl >= bl - 0.01 else bo), "SL_HIT"

                if exit_px is None:
                    # trailing update (after exit checks, mirrors validated engine)
                    if position == 1:
                        hi = max(hi, bh)
                        profit = hi - entry_px
                        if profit >= a * self.TRAIL_ACTIVATION:
                            t = hi - a * self.TRAIL_MULT
                            t = min(t, hi)
                            if t > sl:
                                sl = t
                    else:
                        lo = min(lo, bl)
                        profit = entry_px - lo
                        if profit >= a * self.TRAIL_ACTIVATION:
                            t = lo + a * self.TRAIL_MULT
                            t = max(t, lo)
                            if t < sl:
                                sl = t
                else:
                    pts = (exit_px - entry_px) if position == 1 else (entry_px - exit_px)
                    gross = pts * qty
                    trades.append({
                        "entry_time": ts[entry_i].strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_time": ts[i].strftime("%Y-%m-%d %H:%M:%S"),
                        "direction": "LONG" if position == 1 else "SHORT",
                        "entry_price": round(entry_px, 2),
                        "sl": round(sl, 2),
                        "target1": 0.0, "target2": 0.0,
                        "exit_price": round(exit_px, 2),
                        "exit_reason": reason,
                        "pnl": round(gross, 2),
                        "pnl_pts": round(pts, 2),
                        "pnl_inr": round(gross, 2),
                        "est_cost": round(self._fut_cost(entry_px, exit_px, qty), 2),
                        "net_opt": round(self._opt_net(pts, qty), 2),
                    })
                    if gross < 0 and self.COOLDOWN_BARS > 0:
                        cd_until = i + self.COOLDOWN_BARS
                    position = 0
                    if reason == "EOD_EXIT":
                        continue

            if position == 0:
                if mins[i] >= flat_min or per_day >= self.MAX_PER_DAY or i <= cd_until:
                    continue
                rh, rl = roll_hi[i], roll_lo[i]
                if np.isnan(rh):
                    continue
                if bc > rh:
                    position = 1
                    sl = bc - self.SL_ATR * a
                elif bc < rl:
                    position = -1
                    sl = bc + self.SL_ATR * a
                else:
                    continue
                entry_px = bc
                entry_i = i
                hi, lo = bh, bl
                per_day += 1

        # open position row (diff processor needs it)
        if position != 0:
            trades.append({
                "entry_time": ts[entry_i].strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": "",
                "direction": "LONG" if position == 1 else "SHORT",
                "entry_price": round(entry_px, 2),
                "sl": round(sl, 2),
                "target1": 0.0, "target2": 0.0,
                "exit_price": None,
                "exit_reason": "OPEN",
                "pnl": 0, "pnl_pts": 0, "pnl_inr": 0,
                "est_cost": 0, "net_opt": 0,
            })

        if _start is not None and trades:
            trades = [t for t in trades if pd.to_datetime(t["entry_time"]) >= _start]

        tdf = pd.DataFrame(trades)
        if tdf.empty:
            return {"error": "No trades generated", "trades": [], "stats": {}}

        total = len(tdf)
        wins = (tdf["pnl"] > 0).sum()
        gp = tdf[tdf["pnl"] > 0]["pnl"].sum()
        gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
        equity = initial_capital + tdf["pnl"].cumsum()
        dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

        net_fut = tdf["pnl"] - tdf["est_cost"]
        nf_gp, nf_gl = net_fut[net_fut > 0].sum(), abs(net_fut[net_fut <= 0].sum())
        net_opt = tdf["net_opt"]
        no_gp, no_gl = net_opt[net_opt > 0].sum(), abs(net_opt[net_opt <= 0].sum())

        stats = {
            "total_trades": total,
            "wins": int(wins),
            "losses": int(total - wins),
            "win_rate_pct": round(wins / total * 100, 1),
            "profit_factor": round(gp / gl, 2) if gl > 0 else 0,
            "total_pnl": round(tdf["pnl"].sum(), 0),
            "avg_win": round(tdf[tdf["pnl"] > 0]["pnl"].mean(), 0) if wins > 0 else 0,
            "avg_loss": round(tdf[tdf["pnl"] <= 0]["pnl"].mean(), 0) if (total - wins) > 0 else 0,
            "max_drawdown_pct": round(float(dd), 2),
            "expectancy": round(tdf["pnl"].mean(), 0),
            "final_capital": round(float(equity.iloc[-1]), 0),
            "exit_distribution": tdf["exit_reason"].value_counts().to_dict(),
            # honest net views + points metrics
            "est_total_costs": round(tdf["est_cost"].sum(), 0),
            "net_pnl_after_costs": round(net_fut.sum(), 0),
            "net_profit_factor": round(nf_gp / nf_gl, 2) if nf_gl > 0 else 0,
            "net_pnl_option_basis": round(net_opt.sum(), 0),
            "net_option_profit_factor": round(no_gp / no_gl, 2) if no_gl > 0 else 0,
            "avg_pts_per_trade": round(tdf["pnl_pts"].mean(), 1),
            "avg_win_pts": round(tdf[tdf["pnl_pts"] > 0]["pnl_pts"].mean(), 1) if wins else 0,
            "avg_loss_pts": round(tdf[tdf["pnl_pts"] <= 0]["pnl_pts"].mean(), 1) if wins < total else 0,
            "regime_distribution": {},
            "entry_signals": total,
            "vetoed_signals": 0,
        }

        tdf["equity"] = initial_capital + tdf["pnl"].cumsum()
        equity_curve = tdf[["entry_time", "equity", "pnl"]].to_dict(orient="records")
        return {"stats": stats, "trades": tdf.to_dict(orient="records"),
                "equity_curve": equity_curve}


class DonchianIntradayKernel(DonchianSwingKernel):
    """Donchian 5m breakout — strict intraday profile (flat by 15:15).

    Never holds overnight. Net-positive only via ITM option buying
    (~+₹1.63L/13mo); roughly breakeven in futures after costs.
    """

    strategy_id = "donchian_5m_intraday"
    live_capable = True

    N_CHANNEL = 48
    TRAIL_MULT = 4.0
    INTRADAY_ONLY = True
