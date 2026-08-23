"""
regime_trend_kernel.py — Unified StrategyKernel for Regime Trend/Range strategy.

Wraps the existing regime agents (RegimeDetection, TrendEntry, HTFStructure,
MomentumVolume, Orchestrator) into the StrategyKernel interface.

on_bar() is the SINGLE function called for BOTH backtest and live.
Trade management (trailing SL, breakeven, EOD exit) is strategy-specific
and preserved exactly from the original regime_strategy.py.

Trailing SL Config (tight for 20-30pt BankNifty trades):
  TRAIL_MULT = 1.5       (trailing distance = ATR × 1.5)
  TRAIL_ACTIVATION = 0.3 (min profit in ATR before trailing starts)
  BE_TRIGGER = 0.4       (profit in ATR to lock breakeven)
  BE_BUFFER = 0.3        (buffer above entry for BE lock, in ATR)
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

# ── Agent instances (isolated per-kernel — no module globals shared) ─────────
# These are the SAME agent classes as regime_strategy.py but owned by this kernel.


class RegimeTrendKernel(StrategyKernel):
    """Regime-based trend-only strategy kernel.

    Detects trending markets and enters on pullbacks.
    Does NOT trade sideways/range markets — sits flat.
    Exits when the trend regime ends or via trailing SL.
    """

    strategy_id = "regime_trend_range"
    live_capable = True

    # ── Trailing SL config (preserved exactly from regime_strategy.py) ────
    TRAIL_MULT = 1.5
    TRAIL_ACTIVATION = 0.3
    BE_TRIGGER = 0.4
    BE_BUFFER = 0.3

    _TREND_ONLY = True
    _EOD_EXIT_NSE = 15 * 60 + 20   # 3:20 PM IST
    _EOD_EXIT_MCX = 23 * 60 + 20   # 11:20 PM IST

    def __init__(self):
        # ── Agent instances (isolated to THIS kernel) ────────────────────
        self._regime_agent = RegimeDetectionAgent(lookback=15, swing_order=3)
        self._trend_agent = TrendEntryAgent(swing_order=3, min_pullback=0.20, max_pullback=0.65)
        self._range_agent = RangeEntryAgent(lookback=40, proximity_pct=0.15, min_range_atr=2.0)
        self._htf_agent = HTFStructureAgent(swing_order=5, proximity_atr=0.5)
        self._momentum_agent = MomentumVolumeAgent(lookback=15)
        self._orchestrator = RegimeOrchestrator(min_quality=0.25, min_rr=0.0, min_regime_age=1)

        # ── Internal state (replaces module-level globals) ───────────────
        self._regime = "SIDEWAYS"
        self._regime_age = 0
        self._htf_cache = None
        self._htf_cache_hour = -1

    def reset(self):
        """Reset all internal state."""
        self._regime = "SIDEWAYS"
        self._regime_age = 0
        self._htf_cache = None
        self._htf_cache_hour = -1

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

    def _run_agents(self, df_slice, row, position, cfg, df_1h, df_1d, df_1w):
        """Run agents for a single bar. Uses self state (no globals)."""
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if pd.isna(atr) or atr < 1:
            atr = close * 0.002

        # 1. Regime Detection
        regime = self._regime_agent.evaluate(df_slice, self._regime, self._regime_age)
        self._regime = regime.regime
        self._regime_age = regime.regime_age

        # HTF cache (refresh hourly)
        cur_hour = int(row.get("_hour", -1)) * 100 + int(row.get("_minute", 0)) // 60
        if self._htf_cache is None or cur_hour != self._htf_cache_hour:
            self._htf_cache = self._htf_agent.evaluate(close, atr, df_1h, df_1d, df_1w)
            self._htf_cache_hour = cur_hour
        htf = self._htf_cache

        # 2. Trend Entry
        trend_entry = self._trend_agent.evaluate(
            df_slice, row, regime.regime, regime.confidence, position, atr
        )

        # 3. Range Entry (disabled in trend-only mode)
        if self._TREND_ONLY:
            range_entry = RangeEntryState(signal="HOLD", reasons=["Trend-only mode"])
        else:
            range_entry = self._range_agent.evaluate(
                df_slice, row, regime.regime, regime.confidence, position, atr
            )

        # 4. Momentum/Volume
        proposed_signal = trend_entry.signal if trend_entry.signal in ("LONG", "SHORT") else "HOLD"
        momentum = self._momentum_agent.evaluate(df_slice, proposed_signal, regime.regime)

        # 5. Orchestrator
        signal = self._orchestrator.evaluate(
            row, regime, trend_entry, range_entry, htf, momentum, position
        )
        return signal

    # ═════════════════════════════════════════════════════════════════════════
    # on_bar() — THE single entry point for both backtest and live
    # ═════════════════════════════════════════════════════════════════════════

    def on_bar(self, bar_idx: int, base_df, row, position: str,
               context: dict) -> Optional[SignalEvent]:
        """Process one completed bar.

        Returns SignalEvent for LONG/SHORT/LONG_EXIT/SHORT_EXIT, or None for HOLD.
        """
        cfg = context.get("cfg") or get_settings()
        df_1h = context.get("df_1h")
        df_1d = context.get("df_1d")
        df_1w = context.get("df_1w")

        context_start = max(0, bar_idx - 80)
        df_slice = base_df.iloc[context_start:bar_idx + 1]

        # Slice HTF data to prevent lookahead
        cur_ts = row["timestamp"]
        df_1h_cur = df_1h[df_1h["timestamp"] <= cur_ts].tail(200) if df_1h is not None else None
        df_1d_cur = df_1d[df_1d["timestamp"] <= cur_ts].tail(200) if df_1d is not None else None
        df_1w_cur = df_1w[df_1w["timestamp"] <= cur_ts].tail(200) if df_1w is not None else None

        sig = self._run_agents(df_slice, row, position, cfg, df_1h_cur, df_1d_cur, df_1w_cur)

        if sig.signal in ("LONG", "SHORT"):
            ts_str = pd.to_datetime(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            return SignalEvent(
                signal=sig.signal,
                direction=sig.signal,
                timestamp=ts_str,
                strategy_id=self.strategy_id,
                entry_price=float(row["close"]),
                sl=sig.sl,
                target1=getattr(sig, "target1", 0.0),
                target2=getattr(sig, "target2", 0.0),
                reasons=sig.reasons if hasattr(sig, "reasons") else [],
                regime=self._regime,
                trade_source="REGIME",
                atr=float(row.get("atr", 0)),
            )
        elif sig.signal in ("LONG_EXIT", "SHORT_EXIT"):
            ts_str = pd.to_datetime(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            return SignalEvent(
                signal=sig.signal,
                direction="LONG" if "LONG" in sig.signal else "SHORT",
                timestamp=ts_str,
                strategy_id=self.strategy_id,
                exit_price=float(row["close"]),
                reasons=sig.reasons if hasattr(sig, "reasons") else [],
                regime=self._regime,
                trade_source="REGIME",
                atr=float(row.get("atr", 0)),
            )
        return None

    # ═════════════════════════════════════════════════════════════════════════
    # run_backtest() — Strategy-specific trade management
    # ═════════════════════════════════════════════════════════════════════════

    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 30, lot_multiplier: int = 1,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> dict:
        """Backtest with regime-specific trailing SL, breakeven, EOD exits.

        Preserved exactly from regime_strategy.run_backtest() — same trailing
        parameters, same EOD exit logic, same trade management.
        """
        cfg = get_settings()
        eod_minute = self._eod_exit_minute(cfg)
        trail_mult = getattr(cfg, "regime_trail_mult", self.TRAIL_MULT)
        trail_activation = getattr(cfg, "regime_trail_activation", self.TRAIL_ACTIVATION)
        be_trigger = getattr(cfg, "regime_be_trigger", self.BE_TRIGGER)
        be_buffer = getattr(cfg, "regime_be_buffer", self.BE_BUFFER)
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

        # ── Reset kernel state for this backtest run ─────────────────────
        self.reset()

        # Diagnostics
        regime_counts = {"TRENDING_UP": 0, "TRENDING_DOWN": 0, "SIDEWAYS": 0, "TRANSITION": 0}
        entry_signals = 0
        vetoed = 0

        trades = []
        position = "NONE"
        entry_price = sl = 0.0
        entry_idx = 0
        highest_since_entry = 0.0
        lowest_since_entry = 99999999.0
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
                    nonlocal position
                    net = (exit_px - entry_price) * qty if position == "LONG" else (entry_price - exit_px) * qty
                    _pnl_pts = round(exit_px - entry_price, 2) if position == "LONG" else round(entry_price - exit_px, 2)
                    entry_ts_ist = pd.to_datetime(base.iloc[entry_idx]["timestamp"])
                    exit_ts_ist = pd.to_datetime(ts)
                    trades.append({
                        "entry_time": entry_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                        "exit_time": exit_ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                        "direction": position,
                        "entry_price": round(entry_price, 2),
                        "sl": round(sl, 2),
                        "exit_price": round(exit_px, 2),
                        "exit_reason": reason,
                        "pnl": round(net, 2),
                        "pnl_pts": _pnl_pts,
                        "pnl_inr": round(net, 2),
                    })
                    position = "NONE"

                # ── 0. END OF DAY EXIT — only enforced in INTRADAY mode ──
                if intraday_mode:
                    if prev_date is not None and cur_date != prev_date:
                        book(prev_close, "EOD_EXIT")
                    if position != "NONE" and cur_mins >= eod_minute:
                        book(bc, "EOD_EXIT")
                        prev_date = cur_date
                        prev_close = bc
                        continue

                # ── 1. STOP LOSS HIT ────────────────────────────────────
                # Real-fill check: only book AT the sl level if price actually
                # traded there this bar (bl <= sl <= bh for LONG, mirrored for
                # SHORT). If sl sits on the wrong side of the bar's own real
                # range, the "hit" is mechanical — a stale trailing/breakeven
                # level the market had already moved past before this bar even
                # opened — not a genuine touch. Book the real, actually-reached
                # price (this bar's own open) instead, so every trade is a real,
                # traceable fill and never a fictitious one.
                if position == "LONG" and bl <= sl:
                    real_exit = sl if sl <= bh + 0.01 else bo
                    book(real_exit, "SL_HIT")
                elif position == "SHORT" and bh >= sl:
                    real_exit = sl if sl >= bl - 0.01 else bo
                    book(real_exit, "SL_HIT")

                # ── 2. TRAILING STOP (continuous) ───────────────────────
                elif position == "LONG":
                    if bh > highest_since_entry:
                        highest_since_entry = bh
                    profit = highest_since_entry - entry_price
                    if profit >= atr_v * trail_activation:
                        trail_sl = highest_since_entry - atr_v * trail_mult
                        if be_trigger > 0 and profit > atr_v * be_trigger:
                            trail_sl = max(trail_sl, entry_price + atr_v * be_buffer)
                        # Clamp: the breakeven-buffer level is ATR-derived and can
                        # otherwise be pushed above the highest price this LONG has
                        # ever actually traded at (a live, decaying ATR compared
                        # against a frozen peak-profit value) — producing an exit
                        # price the market never reached. Bound it to reality.
                        trail_sl = min(trail_sl, highest_since_entry)
                        if trail_sl > sl:
                            sl = trail_sl

                    # ── 3. REGIME FLIP EXIT ─────────────────────────────
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
                        # Clamp: same fix as the LONG side, mirrored — the
                        # breakeven-buffer level must never be pushed below the
                        # lowest price this SHORT has ever actually traded at.
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

                sig_ev = self.on_bar(i, base, row, "NONE", context)
                regime_counts[self._regime] = regime_counts.get(self._regime, 0) + 1

                if sig_ev and sig_ev.signal in ("LONG", "SHORT"):
                    entry_signals += 1
                    entry_price = bc
                    sl = sig_ev.sl
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
                "exit_price": None,
                "exit_reason": "OPEN",
                "pnl": 0,
                "pnl_pts": 0,
                "pnl_inr": 0,
            })

        # ── FILTER BY USER'S DATE RANGE ─────────────────────────────────
        if _trade_start_date is not None and trades:
            trades = [t for t in trades
                      if pd.to_datetime(t["entry_time"]) >= _trade_start_date]

        # ── COMPUTE STATS ───────────────────────────────────────────────
        tdf = pd.DataFrame(trades)
        if tdf.empty:
            return {"error": "No trades generated", "trades": [], "stats": {}}

        total = len(tdf)
        wins = (tdf["pnl"] > 0).sum()
        gp = tdf[tdf["pnl"] > 0]["pnl"].sum()
        gl = abs(tdf[tdf["pnl"] <= 0]["pnl"].sum())
        equity = initial_capital + tdf["pnl"].cumsum()
        dd = ((equity - equity.cummax()) / equity.cummax() * 100).min()

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
        }

        tdf["equity"] = initial_capital + tdf["pnl"].cumsum()
        equity_curve = tdf[["entry_time", "equity", "pnl"]].to_dict(orient="records")

        stats["regime_distribution"] = regime_counts
        stats["entry_signals"] = entry_signals
        stats["vetoed_signals"] = vetoed

        return {
            "stats": stats,
            "trades": tdf.to_dict(orient="records"),
            "equity_curve": equity_curve,
        }
