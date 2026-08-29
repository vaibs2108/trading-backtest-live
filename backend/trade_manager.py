"""
trade_manager.py — Tracks live position state, daily P&L, and enforces risk limits.
"""
import json
import logging
from datetime import datetime, date
import pytz
_IST = pytz.timezone("Asia/Kolkata")
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

_DAY_STATS_PATH = Path(__file__).parent / "data" / "day_stats.json"
_ACTIVE_POSITION_PATH = Path(__file__).parent / "data" / "active_position.json"


@dataclass
class ActivePosition:
    instrument:   str
    symbol:       str
    exchange:     str
    direction:    str        # LONG | SHORT
    entry_price:  float
    sl:           float
    target1:      float
    target2:      float
    qty:          int
    order_id:     str
    entry_time:   str
    trade_mode:   str        # INDEX | OPTIONS
    t1_hit:       bool = False
    sl_moved_to_be: bool = False
    current_pnl:  float = 0.0
    index_entry_price: float = 0.0  # Index LTP at entry time — used for display/journal (entry_price holds option premium for PnL calc)
    highest_since_entry: float = 0.0  # Track highest index LTP since entry (for trailing SL)
    lowest_since_entry: float = 99999999.0  # Track lowest index LTP since entry (for trailing SL)
    expected_entry_price: float = 0.0  # LTP at signal time (for slippage calc)
    entry_slippage: float = 0.0  # entry_price - expected_entry_price
    trail_step: int = 0  # 0=initial, 1=breakeven, 2=trailing (for multi_agent V3 trailing SL)
    entry_atr: float = 0.0  # ATR at entry time (for trailing SL calculations)
    sl_order_id: Optional[str] = None  # Broker-side stop loss order ID
    strategy: str = ""  # Strategy ID that owns this position (e.g. multi_agent, regime_trend_range, etc.)



@dataclass
class DayStats:
    date:          str = ""
    total_trades:  int = 0
    wins:          int = 0
    losses:        int = 0
    gross_pnl:     float = 0.0
    trade_log:     list = field(default_factory=list)


class TradeManager:
    """
    Singleton managing the live trading state for the session.
    Enforces daily loss limit, daily profit target, and position tracking.
    DayStats are persisted to disk so they survive restarts.
    """
    def __init__(self):
        self.position: Optional[ActivePosition] = None
        self.day_stats = DayStats(date=datetime.now(_IST).strftime("%Y-%m-%d"))
        self._max_daily_loss   = 5000.0
        self._max_daily_profit = 15000.0
        self._order_failure_count = 0  # consecutive order failures for kill switch
        self._load_day_stats()
        self._load_position()

    def _load_day_stats(self):
        """Load persisted DayStats from file if date matches today."""
        try:
            if _DAY_STATS_PATH.exists():
                with open(_DAY_STATS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("date") == datetime.now(_IST).strftime("%Y-%m-%d"):
                    self.day_stats = DayStats(
                        date=data["date"],
                        total_trades=data.get("total_trades", 0),
                        wins=data.get("wins", 0),
                        losses=data.get("losses", 0),
                        gross_pnl=data.get("gross_pnl", 0.0),
                        trade_log=data.get("trade_log", []),
                    )
                    logger.info(f"Loaded persisted DayStats: {self.day_stats.total_trades} trades, PnL: Rs.{self.day_stats.gross_pnl:,.0f}")
                else:
                    logger.info("Persisted DayStats is from a different day, starting fresh.")
        except Exception as e:
            logger.warning(f"Could not load persisted DayStats: {e}")

    def _save_day_stats(self):
        """Persist current DayStats to file."""
        try:
            _DAY_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_DAY_STATS_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(self.day_stats), f, indent=2)
        except Exception as e:
            logger.warning(f"Could not persist DayStats: {e}")

    def _load_position(self):
        """Restore an open position across a restart.

        Positions previously only lived in memory — a crash or restart while a
        real order (paper or live) was open silently forgot about it, and the
        next broker reconciliation cycle would then import whatever Dhan shows
        into the now-empty slot, sometimes replacing a position the app itself
        had opened. This restores whatever was open before the restart so that
        doesn't happen.
        """
        try:
            if _ACTIVE_POSITION_PATH.exists():
                with open(_ACTIVE_POSITION_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data:
                    self.position = ActivePosition(**data)
                    logger.info(
                        f"Restored open position from disk across restart: "
                        f"{self.position.direction} {self.position.symbol} "
                        f"(order_id={self.position.order_id})"
                    )
        except Exception as e:
            logger.warning(f"Could not restore persisted position: {e}")

    def save_position_state(self):
        """Persist the current position (or its absence) to disk.

        Call this after opening/closing a position and after any in-place
        mutation of the tracked position (e.g. trailing SL updates), so the
        on-disk copy never goes stale. A restart then restores exactly what
        was open instead of silently forgetting it.
        """
        try:
            _ACTIVE_POSITION_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_ACTIVE_POSITION_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(self.position) if self.position else None, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not persist position state: {e}")

    def update_limits(self, max_loss: float, max_profit: float):
        self._max_daily_loss   = max_loss
        self._max_daily_profit = max_profit

    def reset_day(self):
        today = datetime.now(_IST).strftime("%Y-%m-%d")
        if self.day_stats.date != today:
            self.day_stats = DayStats(date=today)
            self._save_day_stats()
            logger.info("New trading day — stats reset")

    @property
    def can_trade(self) -> tuple[bool, str]:
        self.reset_day()
        if self.position is not None:
            return False, "Position already open"
        if self.day_stats.gross_pnl <= -self._max_daily_loss:
            return False, f"Daily loss limit hit (Rs.{self._max_daily_loss:,.0f})"
        if self.day_stats.gross_pnl >= self._max_daily_profit:
            return False, f"Daily profit target hit (Rs.{self._max_daily_profit:,.0f})"
        return True, "OK"

    def open_position(self, pos: ActivePosition):
        self.position = pos
        logger.info(f"Position opened: {pos.direction} {pos.symbol} @ {pos.entry_price}")
        self.save_position_state()

    def close_position(self, exit_price: float, reason: str, pnl_override: Optional[float] = None) -> dict:
        if self.position is None:
            return {}
        pos = self.position
        if pnl_override is not None:
            pnl = pnl_override
        else:
            # T1 partial-book split uses pos.target1 directly against
            # entry_price -- correct for INDEX mode (both are the same
            # index-level unit), but for OPTIONS mode entry_price is the
            # option PREMIUM while target1 is an INDEX-level trigger price,
            # never comparable. There's no stored premium value from the
            # moment T1 was actually crossed, so for OPTIONS mode compute
            # the whole position's PnL off the full qty instead of inventing
            # a phantom partial-book profit from mixed units (found
            # 2026-08-25: a real T2 exit reported +Rs.8.5L on a position
            # whose own option premium hadn't moved between entry and exit).
            _use_t1_split = pos.t1_hit and pos.trade_mode != "OPTIONS"
            if pos.direction == "LONG":
                t1p = (pos.target1 - pos.entry_price) * int(pos.qty*0.5) if _use_t1_split else 0
                rp  = (exit_price - pos.entry_price) * (pos.qty - int(pos.qty*0.5) if _use_t1_split else pos.qty)
            else:
                t1p = (pos.entry_price - pos.target1) * int(pos.qty*0.5) if _use_t1_split else 0
                rp  = (pos.entry_price - exit_price) * (pos.qty - int(pos.qty*0.5) if _use_t1_split else pos.qty)
            cost = (pos.entry_price + exit_price) * pos.qty * 0.0002
            pnl  = t1p + rp - cost

        trade_rec = {
            "time":        pos.entry_time,
            "symbol":      pos.symbol,
            "direction":   pos.direction,
            "entry":       pos.entry_price,
            "exit":        exit_price,
            "pnl":         round(pnl, 2),
            "reason":      reason,
            "entry_slippage": round(pos.entry_slippage, 2),
        }
        self.day_stats.total_trades += 1
        self.day_stats.gross_pnl   += pnl
        if pnl > 0:
            self.day_stats.wins += 1
        else:
            self.day_stats.losses += 1
        self.day_stats.trade_log.append(trade_rec)
        self._save_day_stats()

        logger.info(f"Position closed: {pos.direction} {pos.symbol} @ {exit_price} | PnL: Rs.{pnl:,.0f} | Reason: {reason}")
        self.position = None
        self.save_position_state()
        return trade_rec

    def update_pnl(self, current_price: float):
        if self.position is None:
            return
        pos = self.position
        if pos.direction == "LONG":
            pos.current_pnl = (current_price - pos.entry_price) * pos.qty
        else:
            pos.current_pnl = (pos.entry_price - current_price) * pos.qty

    def mark_t1_hit(self):
        if self.position:
            self.position.t1_hit = True
            self.position.sl = self.position.entry_price  # trail SL to BE
            self.position.sl_moved_to_be = True
            logger.info("T1 hit — SL moved to breakeven")
            self.save_position_state()

    def sync_from_broker(self, broker_pos: dict, instrument: str):
        """Restore position state from broker data on startup.
        broker_pos: dict from broker.sync_position_from_broker()
        """
        if not broker_pos or not broker_pos.get("has_position"):
            return
        if self.position is not None:
            logger.info("Position already tracked, skipping broker sync")
            return

        pos = ActivePosition(
            instrument    = instrument,
            symbol        = broker_pos["symbol"],
            exchange      = broker_pos.get("exchange", ""),
            direction     = broker_pos["direction"],
            entry_price   = broker_pos["entry_price"],
            sl            = 0.0,   # unknown from broker — poll loop will update from signal
            target1       = 0.0,
            target2       = 0.0,
            qty           = broker_pos["qty"],
            order_id      = "BROKER_SYNC_" + broker_pos["symbol"],
            entry_time    = datetime.now(_IST).isoformat(),
            trade_mode    = "INDEX" if "FUT" in broker_pos["symbol"].upper() else "OPTIONS",
            highest_since_entry = broker_pos["entry_price"],
            lowest_since_entry = broker_pos["entry_price"],
            strategy      = "broker_sync",
        )
        self.position = pos
        logger.info(f"Position synced from broker: {pos.direction} {pos.symbol} x{pos.qty} @ {pos.entry_price}")
        self.save_position_state()

    def record_order_failure(self) -> int:
        """Record an order failure. Returns current consecutive failure count."""
        self._order_failure_count += 1
        logger.warning(f"Order failure #{self._order_failure_count}")
        return self._order_failure_count

    def reset_order_failures(self):
        """Reset failure count after a successful order."""
        if self._order_failure_count > 0:
            logger.info(f"Order failure count reset (was {self._order_failure_count})")
        self._order_failure_count = 0

    @property
    def order_failure_count(self) -> int:
        return self._order_failure_count

    def get_state(self) -> dict:
        self.reset_day()
        return {
            "position": asdict(self.position) if self.position else None,
            "day_stats": asdict(self.day_stats),
            "can_trade": self.can_trade[0],
            "trade_blocked_reason": self.can_trade[1] if not self.can_trade[0] else None,
            "order_failure_count": self._order_failure_count,
        }


# Singleton instance
_manager = TradeManager()

def get_trade_manager() -> TradeManager:
    return _manager

