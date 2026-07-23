"""
strategy_router.py — Dispatches strategy calls to the active strategy module.

Reads `cfg.strategy` and routes get_current_signal / run_backtest / add_indicators
to the matching strategy implementation.

Registered strategies:
  - "multi_agent"  -> strategy.py  (original 6-agent + ML + orchestrator)
  - "regime_trend_range" -> strategies/regime_strategy.py  (regime-based trend/range)
"""
import logging
from config import get_settings

logger = logging.getLogger(__name__)

# Lazy-import strategy modules to avoid circular imports at module load time
_strategy_modules = {}


def _get_strategy_module(strategy_name: str = None):
    """Return the strategy module for the given name (or current config)."""
    if strategy_name is None:
        strategy_name = get_settings().strategy

    if strategy_name not in _strategy_modules:
        if strategy_name == "multi_agent":
            import strategy as mod
        elif strategy_name == "regime_trend_range":
            from strategies import regime_strategy as mod
        elif strategy_name == "trend_reversal":
            from strategies import trend_reversal_strategy as mod
        elif strategy_name == "regime_reversal":
            from strategies import regime_reversal_strategy as mod
        else:
            logger.warning(f"Unknown strategy '{strategy_name}', falling back to multi_agent")
            import strategy as mod
        _strategy_modules[strategy_name] = mod

    return _strategy_modules[strategy_name]


# ── Public API (same signatures as strategy.py) ─────────────────────────────

def get_current_signal(frames: dict, position: str = "NONE") -> dict:
    """Route to active strategy's get_current_signal."""
    mod = _get_strategy_module()
    sig = mod.get_current_signal(frames, position)
    # Tag with strategy name so UI/logs can tell which strategy produced it
    sig.setdefault("strategy", get_settings().strategy)
    return sig


def run_backtest(frames: dict, initial_capital: float = 500_000,
                 lot_size: int = 15, lot_multiplier: int = 1,
                 start_date=None, end_date=None) -> dict:
    """Route to active strategy's run_backtest."""
    mod = _get_strategy_module()
    return mod.run_backtest(frames, initial_capital, lot_size, lot_multiplier,
                            start_date, end_date)


def add_indicators(df):
    """Route to active strategy's add_indicators."""
    mod = _get_strategy_module()
    return mod.add_indicators(df)


# ── Strategy registry (for UI dropdowns) ────────────────────────────────────

STRATEGY_OPTIONS = {
    "regime_reversal": "Regime + Reversal Combined",
    "regime_trend_range": "Regime Trend/Range Optimized (6 agents)",
}


def get_strategy_list() -> list:
    """Return list of {id, label} for all registered strategies."""
    return [{"id": k, "label": v} for k, v in STRATEGY_OPTIONS.items()]
