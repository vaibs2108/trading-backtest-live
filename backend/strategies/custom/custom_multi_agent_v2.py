"""
custom_multi_agent_v2.py — StrategyKernel wrapper for Multi-Agent V2 (Loss Reduction Optimized).
This strategy applies optimal loss-reduction parameters (Cooldown, StochRSI Veto, Breakeven Lock)
to the Multi-Agent V3 engine for testing directly on the Backtest Page.
"""
import sys, os
import pandas as pd
import numpy as np
from strategy_kernel import StrategyKernel

# Add backend directory to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import strategy as root_strategy
from config import get_settings

class MultiAgentV2Kernel(StrategyKernel):
    strategy_id = "custom_multi_agent_v2"
    display_name = "Multi-Agent V2 — Loss Reduction (Optimized)"
    live_capable = True

    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 15, lot_multiplier: int = 1,
                     start_date: str = None, end_date: str = None) -> dict:
        cfg = get_settings()
        
        # Save original values
        orig_cooldown = getattr(cfg, 'cooldown_bars', 0)
        orig_block_os = getattr(cfg, 'block_short_oversold', False)
        orig_be = getattr(cfg, 'trailing_be_trigger_atr', 1.0)
        orig_max_daily_loss = getattr(cfg, 'max_daily_loss', 5000.0)

        try:
            # Apply Optimal Loss-Reduction Settings for V2
            cfg.cooldown_bars = 4                # 20m pause after loss
            cfg.block_short_oversold = True      # Veto short entries when StochRSI < 20
            cfg.trailing_be_trigger_atr = 0.8    # Move SL to BE at +0.8 ATR profit
            cfg.max_daily_loss = 3500.0          # Daily loss cap

            res = root_strategy.run_backtest(
                frames=frames,
                initial_capital=initial_capital,
                lot_size=lot_size,
                lot_multiplier=lot_multiplier,
                start_date=start_date,
                end_date=end_date
            )
            return res
        finally:
            # Restore original settings to avoid side-effects
            cfg.cooldown_bars = orig_cooldown
            cfg.block_short_oversold = orig_block_os
            cfg.trailing_be_trigger_atr = orig_be
            cfg.max_daily_loss = orig_max_daily_loss
