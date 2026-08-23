"""
regime_reversal_kernel.py — Unified StrategyKernel for Regime + Reversal strategy.

Wraps existing regime agents + regression reversal band indicators.
Provides identical on_bar() and run_backtest() logic with isolated state.
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
from .trend_reversal_strategy import add_indicators as reversal_add_indicators

logger = logging.getLogger(__name__)


class RegimeReversalKernel(StrategyKernel):
    """Combined Regime Trend + Regression Reversal strategy kernel."""

    strategy_id = "regime_reversal"
    live_capable = True

    TRAIL_MULT = 1.5
    TRAIL_ACTIVATION = 0.3
    BE_TRIGGER = 0.4
    BE_BUFFER = 0.3

    _EOD_EXIT_NSE = 15 * 60 + 20
    _EOD_EXIT_MCX = 23 * 60 + 20

    def __init__(self):
        self._regime_agent = RegimeDetectionAgent(lookback=15, swing_order=3)
        self._trend_agent = TrendEntryAgent(swing_order=3, min_pullback=0.20, max_pullback=0.65)
        self._range_agent = RangeEntryAgent(lookback=40, proximity_pct=0.15, min_range_atr=2.0)
        self._htf_agent = HTFStructureAgent(swing_order=5, proximity_atr=0.5)
        self._momentum_agent = MomentumVolumeAgent(lookback=15)
        self._orchestrator = RegimeOrchestrator(min_quality=0.25, min_rr=0.0, min_regime_age=1)

        self._regime = "SIDEWAYS"
        self._regime_age = 0
        self._htf_cache = None
        self._htf_cache_hour = -1
        self.trade_source = "NONE"
        self.last_regime_direction = "NONE"
        self.last_regime_sl = 0.0

    def reset(self):
        self._regime = "SIDEWAYS"
        self._regime_age = 0
        self._htf_cache = None
        self._htf_cache_hour = -1
        self.trade_source = "NONE"
        self.last_regime_direction = "NONE"
        self.last_regime_sl = 0.0

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
            df_slice, row, regime.regime, regime.confidence, position, atr
        )
        range_entry = RangeEntryState(signal="HOLD", reasons=["Trend-only mode"])
        proposed_signal = trend_entry.signal if trend_entry.signal in ("LONG", "SHORT") else "HOLD"
        momentum = self._momentum_agent.evaluate(df_slice, proposed_signal, regime.regime)

        signal = self._orchestrator.evaluate(
            row, regime, trend_entry, range_entry, htf, momentum, position
        )
        return signal

    def on_bar(self, bar_idx: int, base_df, row, position: str,
               context: dict) -> Optional[SignalEvent]:
        cfg = context.get("cfg") or get_settings()
        df_1h = context.get("df_1h")
        df_1d = context.get("df_1d")
        df_1w = context.get("df_1w")

        context_start = max(0, bar_idx - 80)
        df_slice = base_df.iloc[context_start:bar_idx + 1]

        cur_ts = row["timestamp"]
        df_1h_cur = df_1h[df_1h["timestamp"] <= cur_ts].tail(200) if df_1h is not None else None
        df_1d_cur = df_1d[df_1d["timestamp"] <= cur_ts].tail(200) if df_1d is not None else None
        df_1w_cur = df_1w[df_1w["timestamp"] <= cur_ts].tail(200) if df_1w is not None else None

        has_top_sig = bool(row.get("top_sig", False))
        has_bot_sig = bool(row.get("bot_sig", False))
        upper_val = float(row.get("upper", 0))
        lower_val = float(row.get("lower", 0))
        close_val = float(row["close"])
        ts_str = pd.to_datetime(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        atr_v = float(row.get("atr", close_val * 0.002))

        regime_sig = self._run_agents(df_slice, row, position, cfg, df_1h_cur, df_1d_cur, df_1w_cur)

        if position in ("LONG", "SHORT"):
            if position == "LONG" and has_top_sig:
                self.trade_source = "REVERSAL"
                self.last_regime_direction = "LONG"
                return SignalEvent(
                    signal="LONG_EXIT", direction="LONG", timestamp=ts_str,
                    strategy_id=self.strategy_id, exit_price=close_val,
                    exit_reason="REV_OVERRIDE", reasons=["Bearish reversal signal at upper band", "Counter-trend override"],
                    regime=self._regime, trade_source="REVERSAL", atr=atr_v
                )
            elif position == "SHORT" and has_bot_sig:
                self.trade_source = "REVERSAL"
                self.last_regime_direction = "SHORT"
                return SignalEvent(
                    signal="SHORT_EXIT", direction="SHORT", timestamp=ts_str,
                    strategy_id=self.strategy_id, exit_price=close_val,
                    exit_reason="REV_OVERRIDE", reasons=["Bullish reversal signal at lower band", "Counter-trend override"],
                    regime=self._regime, trade_source="REVERSAL", atr=atr_v
                )

            if regime_sig.signal in ("LONG_EXIT", "SHORT_EXIT"):
                self.trade_source = "NONE"
                self.last_regime_direction = "NONE"
                return SignalEvent(
                    signal=regime_sig.signal, direction="LONG" if "LONG" in regime_sig.signal else "SHORT",
                    timestamp=ts_str, strategy_id=self.strategy_id, exit_price=close_val,
                    exit_reason="REGIME_EXIT", reasons=regime_sig.reasons if hasattr(regime_sig, "reasons") else [],
                    regime=self._regime, trade_source="REGIME", atr=atr_v
                )
            return None

        if self.trade_source == "REVERSAL" and self.last_regime_direction != "NONE":
            if regime_sig.signal == self.last_regime_direction:
                self.trade_source = "REGIME"
                dir_str = self.last_regime_direction
                return SignalEvent(
                    signal=dir_str, direction=dir_str, timestamp=ts_str,
                    strategy_id=self.strategy_id, entry_price=close_val, sl=regime_sig.sl,
                    reasons=["Re-entering regime trend after reversal scalp"], regime=self._regime,
                    trade_source="REGIME", atr=atr_v
                )
            else:
                self.trade_source = "NONE"
                self.last_regime_direction = "NONE"

        if has_bot_sig:
            self.trade_source = "REVERSAL"
            return SignalEvent(
                signal="LONG", direction="LONG", timestamp=ts_str,
                strategy_id=self.strategy_id, entry_price=close_val, sl=lower_val,
                reasons=["Bullish reversal candlestick pattern", "Price at lower regression band"],
                regime=self._regime, trade_source="REVERSAL", atr=atr_v
            )
        elif has_top_sig:
            self.trade_source = "REVERSAL"
            return SignalEvent(
                signal="SHORT", direction="SHORT", timestamp=ts_str,
                strategy_id=self.strategy_id, entry_price=close_val, sl=upper_val,
                reasons=["Bearish reversal candlestick pattern", "Price at upper regression band"],
                regime=self._regime, trade_source="REVERSAL", atr=atr_v
            )

        if regime_sig.signal in ("LONG", "SHORT"):
            self.trade_source = "REGIME"
            self.last_regime_direction = regime_sig.signal
            self.last_regime_sl = regime_sig.sl
            return SignalEvent(
                signal=regime_sig.signal, direction=regime_sig.signal, timestamp=ts_str,
                strategy_id=self.strategy_id, entry_price=close_val, sl=regime_sig.sl,
                reasons=regime_sig.reasons if hasattr(regime_sig, "reasons") else [],
                regime=self._regime, trade_source="REGIME", atr=atr_v
            )
        return None

    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 30, lot_multiplier: int = 1,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> dict:
        from .regime_reversal_strategy import run_backtest as reversal_run_backtest
        return reversal_run_backtest(frames, initial_capital, lot_size, lot_multiplier, start_date, end_date)
