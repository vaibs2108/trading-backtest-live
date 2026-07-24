"""
multi_agent_kernel.py — Unified StrategyKernel for Multi-Agent v3 strategy.

Wraps root strategy.py into the StrategyKernel interface.
"""
import logging
import pandas as pd
from typing import Optional

import strategy as root_strategy
from config import get_settings
from strategy_kernel import StrategyKernel, SignalEvent, register_kernel

logger = logging.getLogger(__name__)


class MultiAgentV3Kernel(StrategyKernel):
    """Multi-Agent v3 (scenario-based) strategy kernel."""

    strategy_id = "multi_agent"
    live_capable = False  # Backtest only for now

    def reset(self):
        pass

    def on_bar(self, bar_idx: int, base_df, row, position: str,
               context: dict) -> Optional[SignalEvent]:
        # Delegate to root_strategy.get_current_signal
        frames = context.get("frames", {})
        sig = root_strategy.get_current_signal(frames, position)
        if sig.get("signal") in ("LONG", "SHORT", "LONG_EXIT", "SHORT_EXIT"):
            ts_str = pd.to_datetime(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            return SignalEvent(
                signal=sig["signal"],
                direction="LONG" if "LONG" in sig["signal"] else "SHORT",
                timestamp=ts_str,
                strategy_id=self.strategy_id,
                entry_price=float(row["close"]),
                sl=sig.get("sl", 0.0),
                target1=sig.get("target1", 0.0),
                target2=sig.get("target2", 0.0),
                reasons=sig.get("reasons", []),
                regime=sig.get("regime", ""),
                atr=float(row.get("atr", 0)),
            )
        return None

    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 15, lot_multiplier: int = 1,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> dict:
        return root_strategy.run_backtest(frames, initial_capital, lot_size, lot_multiplier, start_date, end_date)
