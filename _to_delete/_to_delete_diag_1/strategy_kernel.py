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

def format_and_enrich_backtest_result(res: dict, frames: dict = None, initial_capital: float = 500_000.0,
                                       start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict:
    """
    Guarantees that every backtest result dictionary contains standard 'trades', 'stats', and 'equity_curve' keys.
    Computes quantitative metrics and equity curve automatically if not provided by the strategy.
    Standardizes trade directions to uppercase 'LONG' / 'SHORT' and applies date filtering.
    """
    if not isinstance(res, dict):
        res = {"trades": []}

    raw_trades = res.get("trades") or res.get("closed_trades") or []
    formatted_trades = []

    import pandas as pd

    s_dt = pd.to_datetime(start_date) if start_date else None
    e_dt = pd.to_datetime(end_date) if end_date else None

    for idx, t in enumerate(raw_trades):
        if not isinstance(t, dict):
            continue

        e_time = t.get("entry_time")
        if e_time:
            try:
                t_dt = pd.to_datetime(e_time)
                if s_dt and t_dt < s_dt:
                    continue
                if e_dt and t_dt > e_dt:
                    continue
            except Exception:
                pass

        direction = str(t.get("direction", "LONG")).upper()
        if direction not in ("LONG", "SHORT"):
            direction = "LONG"

        entry_p = float(t.get("entry_price") or 0.0)
        exit_p = float(t.get("exit_price") or entry_p)
        pnl_val = t.get("pnl")
        if pnl_val is None:
            pnl_pts = (exit_p - entry_p) if direction == "LONG" else (entry_p - exit_p)
            pnl_val = pnl_pts * 15
        else:
            pnl_val = float(pnl_val)

        trade_item = {
            "trade_num": idx + 1,
            "direction": direction,
            "entry_time": str(e_time or "-"),
            "entry_price": round(entry_p, 2),
            "exit_time": str(t.get("exit_time") or "-"),
            "exit_price": round(exit_p, 2),
            "exit_reason": t.get("exit_reason") or "CLOSED",
            "pnl": round(pnl_val, 2),
            "pnl_pts": round(float(t.get("pnl_pts") or 0.0), 2),
            "sl": float(t.get("sl") or 0.0),
            "target1": float(t.get("target1") or 0.0),
            "target2": float(t.get("target2") or 0.0),
        }
        formatted_trades.append(trade_item)

    res["trades"] = formatted_trades

    existing_stats = res.get("stats")
    if not existing_stats or not isinstance(existing_stats, dict) or "win_rate_pct" not in existing_stats:
        total_t = len(formatted_trades)
        wins = [t for t in formatted_trades if t["pnl"] > 0]
        losses = [t for t in formatted_trades if t["pnl"] < 0]
        
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        total_pnl = sum(t["pnl"] for t in formatted_trades)
        
        win_rate = round((len(wins) / total_t) * 100, 2) if total_t > 0 else 0.0
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (round(gross_win, 2) if gross_win > 0 else 0.0)
        avg_win = round(gross_win / len(wins), 2) if wins else 0.0
        avg_loss = round(-gross_loss / len(losses), 2) if losses else 0.0
        expectancy = round(total_pnl / total_t, 2) if total_t > 0 else 0.0

        curr_capital = float(initial_capital)
        peak = curr_capital
        max_dd_val = 0.0
        max_dd_pct = 0.0

        equity_list = []
        for t in formatted_trades:
            curr_capital += t["pnl"]
            if curr_capital > peak:
                peak = curr_capital
            dd = peak - curr_capital
            if dd > max_dd_val:
                max_dd_val = dd
                if peak > 0:
                    max_dd_pct = (dd / peak) * 100

            equity_list.append({
                "time": t["entry_time"],
                "equity": round(curr_capital, 2),
                "pnl": t["pnl"]
            })

        res["stats"] = {
            "total_trades": total_t,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": round(total_pnl, 2),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "max_drawdown_pts": round(max_dd_val, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "expectancy": expectancy,
            "initial_capital": initial_capital,
            "final_capital": round(curr_capital, 2)
        }

        if "equity_curve" not in res or not res["equity_curve"]:
            res["equity_curve"] = equity_list

    return res


class StrategyKernel(ABC):
    """Abstract base class for all trading strategies."""

    strategy_id: str = ""
    live_capable: bool = False

    def __init__(self):
        import threading
        self._lock = threading.Lock()

    def safe_run_backtest(self, frames: dict, initial_capital: float = 500_000,
                          lot_size: int = 15, lot_multiplier: int = 1,
                          start_date: Optional[str] = None,
                          end_date: Optional[str] = None) -> dict:
        """Thread-safe wrapper around run_backtest to prevent state race conditions.

        Also catches any exception raised by the strategy's own run_backtest()
        (most likely a buggy custom/AI-generated strategy) and turns it into a
        clean, structured error instead of letting a raw Python exception
        surface all the way to the API caller — a broken custom strategy should
        degrade gracefully (a clear message + zeroed stats), not blow up the
        request or leave the Backtest page showing nothing at all.
        """
        if not hasattr(self, "_lock"):
            import threading
            self._lock = threading.Lock()
        with self._lock:
            try:
                raw_res = self.run_backtest(frames, initial_capital, lot_size, lot_multiplier, start_date, end_date)
            except Exception as e:
                logger.error(f"[{getattr(self, 'strategy_id', '?')}] run_backtest() raised {type(e).__name__}: {e}", exc_info=True)
                raw_res = {
                    "error": f"Strategy code raised {type(e).__name__}: {e}",
                    "trades": [],
                }
            return format_and_enrich_backtest_result(raw_res, frames, initial_capital, start_date, end_date)

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

    def on_bar(self, bar_idx: int, base_df, row, position: str,
               context: dict) -> Optional[SignalEvent]:
        """Process one completed bar and return a signal (or None for HOLD)."""
        return None

    def reset(self):
        """Reset all internal state. Called at daily boundary or restart."""
        pass

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

    # Dynamic Custom Strategy Auto-Discovery
    try:
        custom_dir = os.path.join(os.path.dirname(__file__), "strategies", "custom")
        if os.path.exists(custom_dir):
            import importlib.util
            for fname in os.listdir(custom_dir):
                if fname.endswith(".py") and not fname.startswith("__"):
                    mod_path = os.path.join(custom_dir, fname)
                    file_strat_id = fname[:-3]
                    mod_name = f"strategies.custom.{file_strat_id}"
                    try:
                        spec = importlib.util.spec_from_file_location(mod_name, mod_path)
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        for item_name in dir(mod):
                            obj = getattr(mod, item_name)
                            if isinstance(obj, type) and issubclass(obj, StrategyKernel) and obj is not StrategyKernel:
                                try:
                                    inst = obj()
                                    register_kernel(inst)
                                    if file_strat_id and file_strat_id != inst.strategy_id:
                                        _kernel_registry[file_strat_id] = inst
                                        logger.info(f"Registered custom kernel alias: {file_strat_id} -> {inst.strategy_id}")
                                except Exception as ex:
                                    logger.warning(f"Could not instantiate custom kernel {item_name}: {ex}")
                    except Exception as ex:
                        logger.warning(f"Could not load custom strategy {fname}: {ex}")
    except Exception as e:
        logger.error(f"Failed to auto-discover custom strategy kernels: {e}")


def reload_custom_kernels():
    """Reload all dynamic custom kernels from backend/strategies/custom/."""
    custom_dir = os.path.join(os.path.dirname(__file__), "strategies", "custom")
    
    # Remove existing custom kernels from registry before re-discovering
    custom_keys = [k for k in list(_kernel_registry.keys()) if k.startswith("custom_")]
    for k in custom_keys:
        del _kernel_registry[k]

    if not os.path.exists(custom_dir):
        return
    import importlib.util
    for fname in os.listdir(custom_dir):
        if fname.endswith(".py") and not fname.startswith("__"):
            mod_path = os.path.join(custom_dir, fname)
            file_strat_id = fname[:-3]
            mod_name = f"strategies.custom.{file_strat_id}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, mod_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for item_name in dir(mod):
                    obj = getattr(mod, item_name)
                    if isinstance(obj, type) and issubclass(obj, StrategyKernel) and obj is not StrategyKernel:
                        inst = obj()
                        register_kernel(inst)
                        if file_strat_id and file_strat_id != inst.strategy_id:
                            _kernel_registry[file_strat_id] = inst
                            logger.info(f"Registered custom kernel alias: {file_strat_id} -> {inst.strategy_id}")
            except Exception as ex:
                logger.warning(f"Could not load custom strategy {fname}: {ex}")



def get_kernel(strategy_id: str) -> Optional[StrategyKernel]:
    """Get a registered kernel by strategy_id."""
    init_kernels()
    if strategy_id not in _kernel_registry:
        reload_custom_kernels()
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
    reload_custom_kernels()
    return [
        {
            "id": k.strategy_id,
            "label": getattr(k, "display_name", k.strategy_id.replace("_", " ").title()),
            "live": k.live_capable,
        }
        for k in _kernel_registry.values()
    ]

