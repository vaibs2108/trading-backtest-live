"""
trend_reversal_kernel.py — Unified StrategyKernel for Regression Trend Reversal strategy.

Port of AlgoAlpha's "Regression Trend Reversal Signals & Forecasts" PineScript.
Implements 6 regression methods with adaptive bands and candlestick reversal signals.
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional

import strategy as main_strategy
from config import get_settings, INSTRUMENT_META
from strategy_kernel import StrategyKernel, SignalEvent, register_kernel
from .trend_reversal_strategy import add_indicators as reversal_add_indicators, run_backtest as reversal_run_backtest

logger = logging.getLogger(__name__)


class TrendReversalKernel(StrategyKernel):
    """Regression Trend Reversal strategy kernel."""

    strategy_id = "trend_reversal"
    live_capable = False  # Backtest only for now

    def reset(self):
        pass

    def on_bar(self, bar_idx: int, base_df, row, position: str,
               context: dict) -> Optional[SignalEvent]:
        has_top_sig = bool(row.get("top_sig", False))
        has_bot_sig = bool(row.get("bot_sig", False))
        upper_val = float(row.get("upper", 0))
        lower_val = float(row.get("lower", 0))
        close_val = float(row["close"])
        ts_str = pd.to_datetime(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        atr_v = float(row.get("atr", close_val * 0.002))

        if position == "LONG":
            if has_top_sig:
                return SignalEvent(
                    signal="LONG_EXIT", direction="LONG", timestamp=ts_str,
                    strategy_id=self.strategy_id, exit_price=close_val,
                    exit_reason="BEARISH_REVERSAL", reasons=["Bearish reversal signal at upper band"],
                    atr=atr_v
                )
        elif position == "SHORT":
            if has_bot_sig:
                return SignalEvent(
                    signal="SHORT_EXIT", direction="SHORT", timestamp=ts_str,
                    strategy_id=self.strategy_id, exit_price=close_val,
                    exit_reason="BULLISH_REVERSAL", reasons=["Bullish reversal signal at lower band"],
                    atr=atr_v
                )

        if position == "NONE":
            if has_bot_sig:
                return SignalEvent(
                    signal="LONG", direction="LONG", timestamp=ts_str,
                    strategy_id=self.strategy_id, entry_price=close_val, sl=lower_val,
                    reasons=["Bullish reversal candlestick pattern", "Price at lower band"],
                    atr=atr_v
                )
            elif has_top_sig:
                return SignalEvent(
                    signal="SHORT", direction="SHORT", timestamp=ts_str,
                    strategy_id=self.strategy_id, entry_price=close_val, sl=upper_val,
                    reasons=["Bearish reversal candlestick pattern", "Price at upper band"],
                    atr=atr_v
                )
        return None

    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 15, lot_multiplier: int = 1,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> dict:
        return reversal_run_backtest(frames, initial_capital, lot_size, lot_multiplier, start_date, end_date)
