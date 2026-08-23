"""
strategy_router.py — Unified Router powered by StrategyKernel registry.

Routes get_current_signal / run_backtest / add_indicators to registered strategy kernels.
"""
import logging
from config import get_settings
from strategy_kernel import get_kernel, get_kernel_list, init_kernels

logger = logging.getLogger(__name__)


def get_current_signal(frames: dict, position: str = "NONE") -> dict:
    """Route to active kernel's get_current_signal."""
    strat = get_settings().strategy
    kernel = get_kernel(strat)
    if kernel and hasattr(kernel, "get_current_signal"):
        sig = kernel.get_current_signal(frames, position)
    else:
        import strategy as mod
        sig = mod.get_current_signal(frames, position)
    sig.setdefault("strategy", strat)
    return sig


def run_backtest(frames: dict, initial_capital: float = 500_000,
                 lot_size: int = 30, lot_multiplier: int = 1,
                 start_date=None, end_date=None) -> dict:
    """Route to active kernel's run_backtest."""
    strat = get_settings().strategy
    kernel = get_kernel(strat)
    if kernel:
        return kernel.safe_run_backtest(frames, initial_capital, lot_size, lot_multiplier, start_date, end_date)
    raise RuntimeError(f"StrategyKernel for '{strat}' not registered in strategy registry.")


def add_indicators(df):
    """Route to active kernel's add_indicators."""
    strat = get_settings().strategy
    kernel = get_kernel(strat)
    if kernel and hasattr(kernel, "add_indicators"):
        return kernel.add_indicators(df)
    import strategy as mod
    return mod.add_indicators(df)


STRATEGY_OPTIONS = {
    "regime_reversal": "Regime + Reversal Combined",
    "regime_trend_range": "Regime Trend/Range Optimized (6 agents)",
    "regime_trend_v2": "Regime Trend V2 — Selective (optimized)",
    "regime_trend_v2b": "Regime Trend V2-B — Balanced (runner-up)",
    "donchian_5m_swing": "Donchian 5m Swing (overnight)",
    "donchian_5m_intraday": "Donchian 5m Intraday (flat 15:15)",
}


def get_strategy_list() -> list:
    """Return list of {id, label} for registered strategies."""
    return get_kernel_list()
