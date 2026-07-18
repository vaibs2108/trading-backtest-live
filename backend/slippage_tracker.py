"""
slippage_tracker.py — Persistent slippage & fill tracking.

Logs every entry and exit fill with expected vs actual prices.
Provides aggregate stats for analysis (avg slippage, worst fills, etc.).
Data persisted to CSV for easy Excel/pandas analysis.
"""
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

logger = logging.getLogger(__name__)
_IST = pytz.timezone("Asia/Kolkata")

_SLIPPAGE_LOG_PATH = Path(__file__).parent / "data" / "slippage_log.csv"
_CSV_HEADERS = [
    "timestamp", "symbol", "direction", "side",  # side = ENTRY or EXIT
    "expected_price", "actual_price", "slippage_abs", "slippage_pct",
    "qty", "order_id", "trade_mode", "instrument",
]


def _ensure_csv():
    """Create CSV with headers if it doesn't exist."""
    _SLIPPAGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _SLIPPAGE_LOG_PATH.exists():
        with open(_SLIPPAGE_LOG_PATH, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_CSV_HEADERS)


def log_entry_fill(
    symbol: str,
    direction: str,
    expected_price: float,
    actual_price: float,
    qty: int,
    order_id: str,
    trade_mode: str = "",
    instrument: str = "",
):
    """Log an entry order fill with slippage data."""
    _ensure_csv()
    slippage_abs = actual_price - expected_price
    # For LONG: positive slippage = worse (bought higher)
    # For SHORT: negative slippage = worse (sold lower)
    slippage_pct = (slippage_abs / expected_price * 100) if expected_price > 0 else 0.0

    row = [
        datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S"),
        symbol, direction, "ENTRY",
        round(expected_price, 2), round(actual_price, 2),
        round(slippage_abs, 2), round(slippage_pct, 4),
        qty, order_id, trade_mode, instrument,
    ]
    try:
        with open(_SLIPPAGE_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
        logger.info(
            f"Slippage logged: ENTRY {direction} {symbol} "
            f"expected={expected_price:.2f} actual={actual_price:.2f} "
            f"slip={slippage_abs:+.2f} ({slippage_pct:+.4f}%)"
        )
    except Exception as e:
        logger.warning(f"Failed to log entry slippage: {e}")


def log_exit_fill(
    symbol: str,
    direction: str,
    expected_price: float,
    actual_price: float,
    qty: int,
    order_id: str,
    exit_reason: str = "",
    trade_mode: str = "",
    instrument: str = "",
):
    """Log an exit order fill with slippage data."""
    _ensure_csv()
    slippage_abs = actual_price - expected_price
    slippage_pct = (slippage_abs / expected_price * 100) if expected_price > 0 else 0.0

    row = [
        datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S"),
        symbol, direction, f"EXIT_{exit_reason}",
        round(expected_price, 2), round(actual_price, 2),
        round(slippage_abs, 2), round(slippage_pct, 4),
        qty, order_id, trade_mode, instrument,
    ]
    try:
        with open(_SLIPPAGE_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
        logger.info(
            f"Slippage logged: EXIT {direction} {symbol} "
            f"expected={expected_price:.2f} actual={actual_price:.2f} "
            f"slip={slippage_abs:+.2f} ({slippage_pct:+.4f}%)"
        )
    except Exception as e:
        logger.warning(f"Failed to log exit slippage: {e}")


def get_slippage_stats(last_n: int = 50) -> dict:
    """
    Compute aggregate slippage stats from the log.
    Returns stats for the last N fills.
    """
    _ensure_csv()
    rows = []
    try:
        with open(_SLIPPAGE_LOG_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        logger.warning(f"Failed to read slippage log: {e}")
        return {"total_fills": 0}

    if not rows:
        return {"total_fills": 0}

    # Take last N
    recent = rows[-last_n:]

    entry_slips = []
    exit_slips = []
    for r in recent:
        slip = float(r.get("slippage_abs", 0))
        side = r.get("side", "")
        if side == "ENTRY":
            entry_slips.append(slip)
        elif side.startswith("EXIT"):
            exit_slips.append(slip)

    all_slips = entry_slips + exit_slips

    def _stats(arr):
        if not arr:
            return {"count": 0, "avg": 0, "min": 0, "max": 0, "total": 0}
        return {
            "count": len(arr),
            "avg": round(sum(arr) / len(arr), 2),
            "min": round(min(arr), 2),
            "max": round(max(arr), 2),
            "total": round(sum(arr), 2),
        }

    return {
        "total_fills": len(rows),
        "recent_window": len(recent),
        "all": _stats(all_slips),
        "entry": _stats(entry_slips),
        "exit": _stats(exit_slips),
        "recent_fills": [
            {
                "time": r["timestamp"],
                "symbol": r["symbol"],
                "direction": r["direction"],
                "side": r["side"],
                "expected": float(r.get("expected_price", 0)),
                "actual": float(r.get("actual_price", 0)),
                "slippage": float(r.get("slippage_abs", 0)),
                "slippage_pct": float(r.get("slippage_pct", 0)),
            }
            for r in recent[-10:]  # last 10 for display
        ],
    }
