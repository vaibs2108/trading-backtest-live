"""
manual_positions.py -- the MANUAL track: your own Dhan positions, kept apart from auto-trade.

Decided 2026-10-06: auto-trade and manual trades run in parallel. Until then a single position
slot held either one; a manual trade blocked or replaced the strategy's position, and network
drops made the app "close" the manual trade (05/06 Oct). Now:
  * auto-trade lives in TradeManager.position (paper or live orders the app placed itself);
  * every other open Dhan position lives here -- imported, watched for its stop / target at
    index level, alerted ("MANUAL EXIT NEEDED", at most once an hour), and recorded when it
    closes on Dhan. This module never places, modifies or cancels an order.
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pytz

logger = logging.getLogger(__name__)
_IST = pytz.timezone("Asia/Kolkata")
_PATH = Path(__file__).parent / "data" / "manual_positions.json"
_LOG_PATH = Path(__file__).parent / "data" / "manual_closed.json"

_lock = threading.Lock()
_book: Dict[str, dict] = {}
_loaded = False
_alert_last: Dict[str, datetime] = {}
_notify: Optional[Callable[[str], None]] = None
REMINDER_SECONDS = 3600


def set_notify(fn: Optional[Callable[[str], None]]):
    global _notify
    _notify = fn


def _send(msg: str):
    if _notify:
        try:
            _notify(msg)
        except Exception as e:
            logger.warning(f"Manual-position alert failed: {e}")


def _load():
    global _loaded, _book
    if _loaded:
        return
    _loaded = True
    try:
        if _PATH.exists():
            _book = {p["symbol"]: p for p in json.loads(_PATH.read_text(encoding="utf-8"))}
    except Exception as e:
        logger.warning(f"Could not read {_PATH.name}: {e}")


def _save():
    try:
        _PATH.write_text(json.dumps(list(_book.values()), indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not save {_PATH.name}: {e}")


def get_all() -> List[dict]:
    with _lock:
        _load()
        return [dict(p) for p in _book.values()]


def symbols() -> set:
    with _lock:
        _load()
        return set(_book)


def upsert(pos: dict) -> bool:
    """Add a manual position (True if new). An existing one keeps its levels; qty/entry refresh."""
    with _lock:
        _load()
        sym = pos["symbol"]
        if sym in _book:
            old = _book[sym]
            changed = (old.get("qty") != pos.get("qty")) or (abs(float(old.get("entry_price", 0)) - float(pos.get("entry_price", 0))) > 0.005)
            if changed:
                old.update(qty=pos["qty"], entry_price=pos["entry_price"])
                _save()
            return False
        _book[sym] = pos
        _save()
    logger.info(f"Manual track: tracking {pos['direction']} {sym} x{pos['qty']} @ {pos['entry_price']} "
                f"(SL {pos.get('sl')} / T2 {pos.get('target2')} on the index)")
    _send(f"\U0001f464 MANUAL position tracked\n{pos['direction']} {sym} x{pos['qty']} @ ₹{pos['entry_price']}\n"
          f"Index SL {pos.get('sl')} / T2 {pos.get('target2')} — alerts only, the app never exits it.")
    return True


def close(symbol: str, exit_price: Optional[float], pnl: Optional[float], reason: str = "closed on Dhan"):
    with _lock:
        _load()
        pos = _book.pop(symbol, None)
        if pos is None:
            return
        _save()
        rec = dict(pos, exit_time=datetime.now(_IST).isoformat(), exit_price=exit_price, pnl=pnl, exit_reason=reason)
        try:
            hist = json.loads(_LOG_PATH.read_text(encoding="utf-8")) if _LOG_PATH.exists() else []
            hist = (hist + [rec])[-500:]
            _LOG_PATH.write_text(json.dumps(hist, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save {_LOG_PATH.name}: {e}")
    _alert_last.pop(symbol, None)
    pnl_txt = f"₹{pnl:+,.0f}" if pnl is not None else "unknown"
    logger.info(f"Manual track: {symbol} {reason} @ {exit_price} | gross P&L {pnl_txt}")
    _send(f"\U0001f464 MANUAL position closed\n{pos['direction']} {symbol} x{pos['qty']}\n"
          f"Entry ₹{pos['entry_price']} → exit ₹{exit_price} ({reason})\nGross P&L {pnl_txt}")


def check_levels(index_ltp_by_instrument: Dict[str, float]):
    """Index-level stop / target check for every manual position: alert, never trade."""
    now = datetime.now(_IST)
    for pos in get_all():
        ltp = index_ltp_by_instrument.get(pos.get("instrument", ""))
        if not ltp:
            continue
        d, sl, t2 = pos["direction"], float(pos.get("sl") or 0), float(pos.get("target2") or 0)
        reason = None
        if sl and ((d == "LONG" and ltp <= sl) or (d == "SHORT" and ltp >= sl)):
            reason = "SL_HIT"
        elif t2 and ((d == "LONG" and ltp >= t2) or (d == "SHORT" and ltp <= t2)):
            reason = "T2_HIT"
        if not reason:
            continue
        last = _alert_last.get(pos["symbol"])
        if last and (now - last).total_seconds() < REMINDER_SECONDS:
            continue
        _alert_last[pos["symbol"]] = now
        logger.warning(f"Manual track: {pos['symbol']} {reason} (index {ltp}) -- alert sent, no order")
        _send(f"⚠️ MANUAL EXIT NEEDED\n{d} {pos['symbol']}\nReason: {reason} (index {ltp})\n"
              f"Your own position — the app does not exit it. Reminder every 1 hour until closed.")
