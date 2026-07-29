"""
strategy_kernel.py — Abstract Base Class for all trading strategies.

Modeled on TradingView's Pine Script execution model:
  - Every strategy implements ONE function: on_bar(bar, context) → SignalEvent
  - The SAME on_bar() is called for BOTH backtest and live execution
  - Each strategy defines its OWN trade management (SL, trailing, exits)
  - The engine is agnostic — it just calls on_bar() on each completed bar

Usage:
  class MyStrategy(StrategyKernel):
      strategy_id = "my_strategy"
      live_capable = True

      def on_bar(self, bar, context):
          ...
          return SignalEvent(...) or None
"""
import sys, os
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# Data Classes (shared across all kernels)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class SignalEvent:
    """A trading signal produced by a strategy kernel.

    This is the single output type for on_bar(). Contains everything needed
    for chart rendering, broker execution, and journal logging.
    """
    signal: str           # "LONG", "SHORT", "LONG_EXIT", "SHORT_EXIT", "HOLD"
    direction: str        # "LONG" or "SHORT"
    timestamp: str        # ISO timestamp of the bar that produced this signal
    strategy_id: str      # which kernel produced this signal
    entry_price: float = 0.0
    exit_price: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pts: float = 0.0
    reasons: List[str] = field(default_factory=list)
    regime: str = ""
    regime_confidence: float = 0.0
    trade_source: str = ""   # "REGIME", "REVERSAL", etc.
    atr: float = 0.0
    weighted_score: float = 0.0

    def to_chart_marker(self) -> dict:
        """Convert to a chart marker dict for lightweight-charts setMarkers()."""
        is_entry = self.signal in ("LONG", "SHORT")
        is_long = "LONG" in self.signal
        return {
            "signal": self.signal,
            "time": self.timestamp,
            "direction": self.direction,
            "entry": self.entry_price if is_entry else self.exit_price,
            "sl": self.sl,
            "target1": self.target1,
            "target2": self.target2,
            "strategy": self.strategy_id,
            "reasons": self.reasons,
            "regime": self.regime,
            "trade_source": self.trade_source,
            "atr_5m": self.atr,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl": self.pnl,
            "pnl_pts": self.pnl_pts,
        }

    def to_signal_dict(self) -> dict:
        """Convert to the existing UI signal panel format."""
        return {
            "signal": self.signal,
            "strategy": self.strategy_id,
            "time": self.timestamp,
            "entry": self.entry_price,
            "sl": round(self.sl, 2),
            "target1": round(self.target1, 2),
            "target2": round(self.target2, 2),
            "regime": self.regime,
            "regime_confidence": self.regime_confidence,
            "playbook": "",
            "weighted_score": self.weighted_score,
            "atr_5m": round(self.atr, 2),
            "close": round(self.entry_price, 2),
            "h1_trend": "",
            "macro_bias": "",
            "adx_1h": 0.0,
            "ml_prob": 0.0,
            "rr_t1": 0.0,
            "risk_pts": 0.0,
            "long_score": 0.0,
            "short_score": 0.0,
            "reasons": self.reasons,
            "trade_source": self.trade_source,
        }


# ═════════════════════════════════════════════════════════════════════════════
# Strategy Kernel ABC
# ═════════════════════════════════════════════════════════════════════════════

class StrategyKernel(ABC):
    """Abstract base class for all trading strategies.

    Modeled on TradingView's Pine Script execution model:
      - on_bar() is the SINGLE entry point — called for every completed bar
      - Same function for backtest AND live
      - Each strategy manages its own state (regime, trailing SL, etc.)
      - run_backtest() loops on_bar() — subclasses MUST override to implement
        their own trade management (SL logic, trailing, EOD exits)
    """

    # ── Identity (subclasses MUST set these) ────────────────────────────
    strategy_id: str = ""
    live_capable: bool = False

    def __init__(self):
        import threading
        self._lock = threading.Lock()

    def safe_run_backtest(self, frames: dict, initial_capital: float = 500_000,
                          lot_size: int = 15, lot_multiplier: int = 1,
                          start_date: Optional[str] = None,
                          end_date: Optional[str] = None) -> dict:
        """Thread-safe wrapper around run_backtest to prevent state race conditions."""
        if not hasattr(self, "_lock"):
            import threading
            self._lock = threading.Lock()
        with self._lock:
            return self.run_backtest(frames, initial_capital, lot_size, lot_multiplier, start_date, end_date)

    def get_current_signal(self, frames: dict, position: str = "NONE") -> dict:
        """Evaluate current signal state from trailing frames for manual/API queries."""
        if not frames or "5" not in frames or frames["5"] is None or frames["5"].empty:
            return {"signal": "HOLD", "strategy": self.strategy_id, "reasons": ["Insufficient data"]}

        result = self.safe_run_backtest(frames)
        trades = result.get("trades", [])
        last_bar = frames["5"].iloc[-1]
        ts = str(last_bar.get("timestamp", ""))
        close_price = float(last_bar.get("close", 0.0))
        atr_val = float(last_bar.get("atr", 0.0))

        if trades:
            last_trade = trades[-1]
            if last_trade.get("entry_time") == ts:
                sig_type = last_trade.get("direction", "HOLD")
                return {
                    "signal": sig_type,
                    "strategy": self.strategy_id,
                    "time": ts,
                    "entry": float(last_trade.get("entry_price", close_price)),
                    "sl": float(last_trade.get("sl", 0.0)),
                    "target1": float(last_trade.get("target1", 0.0)),
                    "target2": float(last_trade.get("target2", 0.0)),
                    "close": close_price,
                    "atr_5m": atr_val,
                    "reasons": [f"{sig_type} signal generated by {self.strategy_id}"],
                    "trade_source": self.strategy_id,
                }
            elif last_trade.get("exit_time") == ts:
                exit_reason = last_trade.get("exit_reason", "")
                return {
                    "signal": "LONG_EXIT" if last_trade.get("direction") == "LONG" else "SHORT_EXIT",
                    "strategy": self.strategy_id,
                    "time": ts,
                    "entry": float(last_trade.get("exit_price", close_price)),
                    "close": close_price,
                    "atr_5m": atr_val,
                    "reasons": [f"Exit: {exit_reason}"],
                    "trade_source": self.strategy_id,
                }

        return {
            "signal": "HOLD",
            "strategy": self.strategy_id,
            "time": ts,
            "entry": close_price,
            "close": close_price,
            "atr_5m": atr_val,
            "reasons": ["Hold position"],
            "trade_source": self.strategy_id,
        }

    @abstractmethod
    def on_bar(self, bar_idx: int, base_df, row, position: str,
               context: dict) -> Optional[SignalEvent]:
        """Process one completed bar and return a signal (or None for HOLD).

        Args:
            bar_idx:   Index of the current bar in base_df
            base_df:   Full DataFrame (session-filtered, indicators applied)
            row:       Current bar (base_df.iloc[bar_idx])
            position:  Current position state ("NONE", "LONG", "SHORT")
            context:   Strategy-specific context dict with:
                       - 'cfg': settings
                       - 'df_1h', 'df_1d', 'df_1w': HTF frames (sliced to cur_ts)
                       - Any additional state the strategy maintains

        Returns:
            SignalEvent if entry/exit signal, None for HOLD
        """
        ...

    @abstractmethod
    def reset(self):
        """Reset all internal state. Called at daily boundary or restart."""
        ...

    @abstractmethod
    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                     lot_size: int = 15, lot_multiplier: int = 1,
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> dict:
        """Run a full backtest over historical frames.

        Each strategy MUST implement its own run_backtest() with its specific
        trade management logic (trailing SL, breakeven, EOD exit, etc.).

        This is intentionally NOT a default implementation — strategies like
        regime_trend have tight 20-30 point BankNifty trades with specific
        ATR-based trailing that must be preserved exactly as-is.

        Returns:
            dict with keys: 'trades', 'stats', 'equity_curve'
        """
        ...


# ═════════════════════════════════════════════════════════════════════════════
# Kernel Registry
# ═════════════════════════════════════════════════════════════════════════════

_kernel_registry: dict[str, StrategyKernel] = {}


def register_kernel(kernel: StrategyKernel):
    """Register a strategy kernel instance."""
    _kernel_registry[kernel.strategy_id] = kernel
    logger.info(f"Registered strategy kernel: {kernel.strategy_id} "
                f"(live_capable={kernel.live_capable})")


def init_kernels():
    """Lazily load and register all 4 strategy kernels."""
    if _kernel_registry:
        return
    try:
        from strategies.regime_trend_kernel import RegimeTrendKernel
        register_kernel(RegimeTrendKernel())
    except Exception as e:
        logger.error(f"Failed to register RegimeTrendKernel: {e}")

    try:
        from strategies.regime_trend_v2_kernel import RegimeTrendV2Kernel, RegimeTrendV2BKernel
        register_kernel(RegimeTrendV2Kernel())
        register_kernel(RegimeTrendV2BKernel())
    except Exception as e:
        logger.error(f"Failed to register RegimeTrendV2 kernels: {e}")

    try:
        from strategies.donchian_kernel import DonchianSwingKernel, DonchianIntradayKernel
        register_kernel(DonchianSwingKernel())
        register_kernel(DonchianIntradayKernel())
    except Exception as e:
        logger.error(f"Failed to register Donchian kernels: {e}")

    try:
        from strategies.regime_reversal_kernel import RegimeReversalKernel
        register_kernel(RegimeReversalKernel())
    except Exception as e:
        logger.error(f"Failed to register RegimeReversalKernel: {e}")

    try:
        from strategies.trend_reversal_kernel import TrendReversalKernel
        register_kernel(TrendReversalKernel())
    except Exception as e:
        logger.error(f"Failed to register TrendReversalKernel: {e}")

    try:
        from strategies.multi_agent_kernel import MultiAgentV3Kernel
        register_kernel(MultiAgentV3Kernel())
    except Exception as e:
        logger.error(f"Failed to register MultiAgentV3Kernel: {e}")


def get_kernel(strategy_id: str) -> Optional[StrategyKernel]:
    """Get a registered kernel by strategy_id."""
    init_kernels()
    return _kernel_registry.get(strategy_id)


def get_all_kernels() -> dict[str, StrategyKernel]:
    """Return all registered kernels."""
    init_kernels()
    return dict(_kernel_registry)


def get_live_kernels() -> dict[str, StrategyKernel]:
    """Return only kernels marked as live-capable."""
    init_kernels()
    return {k: v for k, v in _kernel_registry.items() if v.live_capable}


def get_kernel_list() -> list:
    """Return list of {id, label, live} for UI dropdowns."""
    init_kernels()
    return [
        {
            "id": k.strategy_id,
            "label": k.strategy_id.replace("_", " ").title(),
            "live": k.live_capable,
        }
        for k in _kernel_registry.values()
    ]

