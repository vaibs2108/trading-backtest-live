"""
journal_manager.py — Local file-based database for the Trading Journal.
Parses options contracts and tracks open/closed trade entries.
"""
import os
import json
import re
import logging
import pytz
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple

_IST = pytz.timezone("Asia/Kolkata")

logger = logging.getLogger(__name__)


def _get_journal_path() -> Path:
    """Get the absolute path to trading_journal.json based on config."""
    from config import get_settings
    cfg = get_settings()
    data_dir = Path(cfg.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "trading_journal.json"


def parse_option_symbol(symbol: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Parse strike price and option type (CE/PE) from a symbol string.
    Matches formats like "BANKNIFTY 24 Jun 49500 CE" or "BANKNIFTY24JUN49500CE".
    Returns (strike_price, option_type) or (None, None) if not an option.
    """
    if not symbol:
        return None, None
    # Look for a number followed by CE or PE (optional space)
    match = re.search(r"(\d+)\s*(CE|PE)", symbol, re.IGNORECASE)
    if match:
        try:
            strike = int(match.group(1))
            opt_type = match.group(2).upper()
            return strike, opt_type
        except Exception:
            pass
    return None, None


def load_journal() -> List[Dict]:
    """Load all trade logs from trading_journal.json."""
    path = _get_journal_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception as e:
        logger.warning(f"Error loading trading journal: {e}")
        return []


def save_journal(entries: List[Dict]):
    """Save all trade logs to trading_journal.json."""
    path = _get_journal_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save trading journal: {e}")


def add_journal_entry(pos, sig: Dict):
    """
    Record an entry signal trade immediately when executed.
    Sets status to 'OPEN' with model reasons and consensus scores.
    """
    entries = load_journal()
    
    # Parse options contract if applicable
    strike, opt_type = parse_option_symbol(pos.symbol)
    
    # Extract entry date and time (in localized IST)
    try:
        dt = datetime.fromisoformat(pos.entry_time)
        entry_date = dt.strftime("%Y-%m-%d")
        entry_time = dt.strftime("%H:%M:%S")
    except Exception:
        entry_date = datetime.now(_IST).strftime("%Y-%m-%d")
        entry_time = datetime.now(_IST).strftime("%H:%M:%S")

    entry = {
        "trade_id": pos.order_id,
        "status": "OPEN",
        "instrument": pos.instrument,
        "trade_mode": pos.trade_mode,
        "symbol": pos.symbol,
        "direction": pos.direction,
        "qty": pos.qty,
        "entry_date": entry_date,
        "entry_time": entry_time,
        "entry_timestamp": pos.entry_time,
        "option_type": opt_type,
        "option_strike": strike,
        "entry_price": float(getattr(pos, 'index_entry_price', 0) or pos.entry_price),
        "sl": float(pos.sl),
        "target1": float(pos.target1),
        "target2": float(pos.target2),
        "ml_prob": float(sig.get("ml_prob", 0.0)) if sig.get("ml_prob") is not None else None,
        "weighted_score": float(sig.get("weighted_score", 0.0)) if sig.get("weighted_score") is not None else None,
        "macro_bias": sig.get("macro_bias", "NEUTRAL"),
        "h1_trend": sig.get("h1_trend", "NEUTRAL"),
        "reasons": sig.get("reasons", []),
        "exit_price": None,
        "exit_time": None,
        "exit_reason": None,
        "pnl": None
    }
    
    # De-duplicate entries by trade_id
    entries = [e for e in entries if e.get("trade_id") != pos.order_id]
    entries.append(entry)
    save_journal(entries)
    logger.info(f"Journaled entry: {pos.direction} {pos.symbol} @ {pos.entry_price}")


def close_journal_entry(
    symbol: str,
    exit_price: float,
    exit_time: str,
    exit_reason: str,
    pnl: float,
    order_id: Optional[str] = None
) -> Optional[Dict]:
    """
    Close an open trade log, updating it with exit execution details.
    Matches based on order_id first, then falls back to open status and symbol.
    """
    entries = load_journal()
    updated_entry = None
    
    # 1st Pass: Match by order ID
    if order_id:
        for entry in reversed(entries):
            if entry.get("trade_id") == order_id:
                entry["status"] = "CLOSED"
                entry["exit_price"] = float(exit_price)
                entry["exit_time"] = exit_time
                entry["exit_reason"] = exit_reason
                entry["pnl"] = round(float(pnl), 2)
                updated_entry = entry
                break
                
    # 2nd Pass: Match by open status and symbol name (fallback)
    if not updated_entry:
        for entry in reversed(entries):
            if entry.get("status") == "OPEN" and entry.get("symbol") == symbol:
                entry["status"] = "CLOSED"
                entry["exit_price"] = float(exit_price)
                entry["exit_time"] = exit_time
                entry["exit_reason"] = exit_reason
                entry["pnl"] = round(float(pnl), 2)
                updated_entry = entry
                break
                
    if updated_entry:
        save_journal(entries)
        logger.info(f"Journaled exit: {symbol} closed at {exit_price} | PnL: Rs.{pnl} | Reason: {exit_reason}")
    else:
        logger.warning(f"Could not find open journal entry for {symbol} (ID: {order_id}) to close.")
        
    return updated_entry


def get_today_journal_stats(instrument: Optional[str] = None) -> Dict:
    """
    Compute today's win/loss/P&L stats from the persisted trading journal.
    Returns stats dict with: total_trades, wins, losses, gross_pnl, trade_log.
    This is the authoritative source of truth for daily stats (survives restarts).
    """
    entries = load_journal()
    today_str = datetime.now(_IST).strftime("%Y-%m-%d")
    
    today_closed = [
        e for e in entries
        if e.get("status") == "CLOSED" and e.get("entry_date") == today_str
        and not str(e.get("trade_id", "")).startswith("PAPER_")  # Exclude paper trades
    ]
    if instrument:
        today_closed = [e for e in today_closed if e.get("instrument") == instrument]
    
    wins = sum(1 for e in today_closed if (e.get("pnl") or 0) > 0)
    losses = sum(1 for e in today_closed if (e.get("pnl") or 0) <= 0)
    gross_pnl = sum(e.get("pnl", 0) or 0 for e in today_closed)
    
    # Build trade log in the same format as DayStats.trade_log
    trade_log = []
    for e in today_closed:
        trade_log.append({
            "time": e.get("entry_timestamp", ""),
            "symbol": e.get("symbol", ""),
            "direction": e.get("direction", ""),
            "entry": e.get("entry_price", 0),
            "exit": e.get("exit_price", 0),
            "pnl": round(e.get("pnl", 0) or 0, 2),
            "reason": e.get("exit_reason", ""),
        })
    
    today_open = [
        e for e in entries
        if e.get("status") == "OPEN" and e.get("entry_date") == today_str
        and not str(e.get("trade_id", "")).startswith("PAPER_")  # Exclude paper trades
    ]
    if instrument:
        today_open = [e for e in today_open if e.get("instrument") == instrument]
    
    return {
        "date": today_str,
        "total_trades": len(today_closed),
        "wins": wins,
        "losses": losses,
        "gross_pnl": round(gross_pnl, 2),
        "trade_log": trade_log,
        "open_trades": len(today_open),
    }

