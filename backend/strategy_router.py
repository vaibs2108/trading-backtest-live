"""
strategy_router.py — Unified Router powered by StrategyKernel registry.

Routes get_current_signal / run_backtest / add_indicators to registered strategy kernels.
"""
import logging
from config import get_settings
from strategy_kernel import get_kernel, get_kernel_list, init_kernels

logger = logging.getLogger(__name__)


def _get_strategy_module(strategy_name: str = None):
    """Return strategy module for legacy compatibility."""
    if strategy_name is None:
        strategy_name = get_settings().strategy

    if strategy_name == "multi_agent":
        import strategy as mod
    elif strategy_name == "regime_trend_range":
        from strategies import regime_strategy as mod
    elif strategy_name == "trend_reversal":
        from strategies import trend_reversal_strategy as mod
    elif strategy_name == "regime_reversal":
        from strategies import regime_reversal_strategy as mod
    else:
        from strategies import regime_strategy as mod
    return mod


def get_current_signal(frames: dict, position: str = "NONE") -> dict:
    """Route to active strategy's get_current_signal."""
    mod = _get_strategy_module()
    sig = mod.get_current_signal(frames, position)
    sig.setdefault("strategy", get_settings().strategy)
    return sig


def run_backtest(frames: dict, initial_capital: float = 500_000,
                 lot_size: int = 15, lot_multiplier: int = 1,
                 start_date=None, end_date=None) -> dict:
    """Route to active kernel's run_backtest."""
    strat = get_settings().strategy
    kernel = get_kernel(strat)
    if kernel:
        return kernel.run_backtest(frames, initial_capital, lot_size, lot_multiplier, start_date, end_date)
    mod = _get_strategy_module(strat)
    return mod.run_backtest(frames, initial_capital, lot_size, lot_multiplier, start_date, end_date)


def add_indicators(df):
    """Route to active strategy's add_indicators."""
    mod = _get_strategy_module()
    return mod.add_indicators(df)


STRATEGY_OPTIONS = {
    "regime_reversal": "Regime + Reversal Combined",
    "regime_trend_range": "Regime Trend/Range Optimized (6 agents)",
}


def get_strategy_list() -> list:
    """Return list of {id, label} for registered strategies."""
    return get_kernel_list()
