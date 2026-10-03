"""
signal_journal_manager.py — Tracks strategy-generated signals and theoretical P&L.

Completely independent of actual Dhan broker trades. Records every LONG/SHORT signal
the strategy fires, pairs each with the next EXIT signal, and computes theoretical P&L
as (exit_price - entry_price) * lot_size (for LONG) or (entry_price - exit_price) * lot_size (for SHORT).

This answers "how would this strategy have performed if I followed every signal with 1 lot?"
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

import pytz

_IST = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger(__name__)


def _get_path() -> Path:
    from config import get_settings
    cfg = get_settings()
    path = Path(cfg.data_dir) / "signal_journal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> List[Dict]:
    path = _get_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Error loading signal journal: {e}")
        return []


def _save(entries: List[Dict]):
    try:
        path = _get_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving signal journal: {e}")


def _bar_key(ts) -> str:
    """Entry time as 'YYYY-MM-DD HH:MM' -- the two code paths that open entries write the
    same bar as '2026-09-29 10:55:00' and '2026-09-29T10:55:00+05:30'."""
    s = str(ts or "").strip().replace("T", " ")
    return s[:16]


def open_entry(sig: dict, instrument: str, lot_size: int):
    """Record a strategy LONG or SHORT signal as an open journal entry."""
    direction = sig.get("signal")
    if direction not in ("LONG", "SHORT"):
        return
    entries = _load()

    # Skip if identical entry already exists (same strategy + time + instrument + direction
    # still OPEN). Strategy is part of the key: without it, when several strategies entered
    # on the same bar only the first one was journaled (12 of the last 100 signals).
    # Times are compared per bar (_bar_key): comparing the text let 14 entries in twice.
    entry_time = sig.get("time", "")
    strategy = str(sig.get("strategy", ""))
    if any(
        e.get("instrument") == instrument and
        e.get("direction") == direction and
        _bar_key(e.get("entry_time")) == _bar_key(entry_time) and
        str(e.get("strategy", "")) == strategy and
        e.get("status") == "OPEN"
        for e in entries
    ):
        return

    entry = {
        "instrument":     instrument,
        "direction":      direction,
        "entry_time":     entry_time,
        "entry_price":    float(sig.get("entry", 0) or 0),
        "sl":             float(sig.get("sl", 0) or 0),
        "target1":        float(sig.get("target1", 0) or 0),
        "target2":        float(sig.get("target2", 0) or 0),
        "ml_prob":        float(sig.get("ml_prob", 0) or 0),
        "weighted_score": float(sig.get("weighted_score", 0) or 0),
        "macro_bias":     str(sig.get("macro_bias", "")),
        "adx_1h":         float(sig.get("adx_1h", 0) or 0),
        "rr_t1":          float(sig.get("rr_t1", 0) or 0),
        "regime":         str(sig.get("regime", "")),
        "regime_confidence": float(sig.get("regime_confidence", 0) or 0),
        "playbook":       str(sig.get("playbook", "")),
        "entry_quality":  float(sig.get("entry_quality", 0) or 0),
        "atr_5m":         float(sig.get("atr_5m", 0) or 0),
        "strategy":       str(sig.get("strategy", "")),
        "lot_size":       int(lot_size),
        "reasons":        list(sig.get("reasons", [])),
        # Exit fields — filled when the strategy fires an exit signal
        "exit_time":      None,
        "exit_price":     None,
        "exit_reason":    None,
        "pnl_pts":        None,
        "pnl_inr":        None,
        "status":         "OPEN",
    }
    entries.append(entry)
    entries = entries[-300:]  # keep last 300 strategy signals
    _save(entries)
    logger.info(
        f"Signal journal: OPEN {direction} @ {entry['entry_price']} "
        f"[{instrument}] lot={lot_size} ML={entry['ml_prob']:.0%}"
    )


def close_entry(exit_sig: dict, instrument: str, exit_reason: str = "", index_price: float = 0.0):
    """
    Find the most recent OPEN entry for this instrument+direction, compute P&L, close it.
    LONG_EXIT closes a LONG entry; SHORT_EXIT closes a SHORT entry.

    index_price: preferred index-level exit price (pass this to avoid using option premium).
                 Falls back to exit_sig["entry"] if 0.
    """
    entries = _load()
    exit_signal = exit_sig.get("signal", "")
    entry_direction = "LONG" if "LONG" in exit_signal else "SHORT"

    # Try to resolve strategy to prevent cross-closing entries of different strategies
    strat_id = exit_sig.get("strategy")
    if not strat_id:
        try:
            from config import get_settings
            strat_id = get_settings().strategy
        except Exception:
            pass

    # Find the most recent OPEN entry matching instrument + direction + strategy
    open_idx = None
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        if (e.get("instrument") == instrument and
                e.get("direction") == entry_direction and
                (not strat_id or e.get("strategy") == strat_id) and
                e.get("status") == "OPEN"):
            open_idx = i
            break

    if open_idx is None:
        return  # No open entry — signal fired without a prior entry (e.g. on startup)

    exit_price  = index_price if index_price > 0 else float(exit_sig.get("entry", 0) or exit_sig.get("close", 0) or 0)
    entry_price = float(entries[open_idx].get("entry_price", 0) or 0)
    lot_size    = int(entries[open_idx].get("lot_size", 1))

    if entry_direction == "LONG":
        pnl_pts = round(exit_price - entry_price, 2)
    else:
        pnl_pts = round(entry_price - exit_price, 2)

    pnl_inr = round(pnl_pts * lot_size, 2)

    entries[open_idx].update({
        "exit_time":   exit_sig.get("time", datetime.now(_IST).isoformat()),
        "exit_price":  exit_price,
        "exit_reason": exit_reason or exit_sig.get("reason", "SIGNAL_EXIT"),
        "pnl_pts":     pnl_pts,
        "pnl_inr":     pnl_inr,
        "status":      "WIN" if pnl_pts > 0 else ("LOSS" if pnl_pts < 0 else "FLAT"),
    })
    _save(entries)
    logger.info(
        f"Signal journal: CLOSE {entry_direction} @ {exit_price} -> "
        f"{pnl_pts:+.0f} pts / Rs.{pnl_inr:+,.0f} [{instrument}]"
    )


def get_journal(instrument: Optional[str] = None) -> List[Dict]:
    """Return all entries newest-first, optionally filtered by instrument."""
    entries = _load()
    if instrument:
        entries = [e for e in entries if e.get("instrument") == instrument]
    return list(reversed(entries))


def auto_close_stale_entries():
    """Auto-close any OPEN entries from previous days at day boundary.

    Prevents stale "OPEN: 1" counts in the signal log that block the UI
    from showing today's trades cleanly.
    """
    entries = _load()
    today = datetime.now(_IST).date()
    changed = False

    for e in entries:
        if e.get("status") != "OPEN":
            continue
        entry_time_str = e.get("entry_time", "")
        if not entry_time_str:
            continue
        try:
            entry_date = datetime.fromisoformat(entry_time_str).date()
        except (ValueError, TypeError):
            continue
        if entry_date < today:
            entry_price = float(e.get("entry_price", 0) or 0)
            # Close at entry price (flat) since we don't know the actual close. Status
            # DAY_END (not LOSS): no exit price was seen, so it isn't counted as a result.
            e.update({
                "exit_time": datetime.now(_IST).isoformat(),
                "exit_price": entry_price,
                "exit_reason": "STALE_DAY_CLOSE",
                "pnl_pts": 0.0,
                "pnl_inr": 0.0,
                "status": "DAY_END",
            })
            changed = True
            logger.info(f"Auto-closed stale OPEN entry from {entry_date}: {e.get('direction')} {e.get('instrument')}")

    if changed:
        _save(entries)


def clear_journal() -> int:
    """Remove closed entries. OPEN entries are kept: Live Trading reads them to know each
    strategy's position and to send virtual-exit alerts. Returns how many were kept."""
    kept = [e for e in _load() if e.get("status") == "OPEN"]
    _save(kept)
    return len(kept)
