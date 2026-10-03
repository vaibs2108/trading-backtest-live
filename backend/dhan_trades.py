"""
dhan_trades.py — real broker trades rebuilt from Dhan executions (read-only).

Used by the Broker Journal and Performance pages. Before this, each page had its own copy
of the rebuild and both had the same gaps:
  * only the requested dates were fetched, so a trade opened before the range showed its
    closing order as a brand-new OPEN position (57100 PUT: bought 27 Aug, sold 28 Sep for
    +Rs.62,988, shown as "OPEN" since 28 Sep);
  * no exit date was kept, so a trade held 29 Sep -> 1 Oct looked like a same-day trade and
    Performance booked it on the entry day;
  * options that expired while held stayed OPEN forever (expiry isn't an execution);
  * broker.get_trade_history read only the first page (20 executions), so anything older
    than the newest 20 executions was missing (fixed there, 2026-10-03).

Nothing here places or changes orders; it only reads Dhan's trade history.
"""
import logging
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

import pytz

logger = logging.getLogger(__name__)
_IST = pytz.timezone("Asia/Kolkata")

# Extra history fetched before the requested range, so a trade opened earlier is matched
# with its exit instead of the exit looking like a new position.
LOOKBACK_DAYS = 120


def _exec_time(t: dict) -> str:
    for key in ("createTime", "exchangeTime", "updateTime"):
        val = t.get(key)
        if val and val != "NA":
            return str(val)
    return ""


def _split_ts(ts: str):
    parts = ts.replace("T", " ").split(" ")
    return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")


def _guess_instrument(symbol: str) -> str:
    s = symbol.upper()
    for name in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "CRUDEOIL", "SENSEX", "BANKEX", "NIFTY"):
        if name in s:
            return name
    return "INDEX"


def _parse_contract(symbol: str, tx_type: str):
    """Direction is market exposure: buying a PUT is SHORT, buying a CALL is LONG."""
    sym_u = symbol.upper()
    is_put = "PUT" in sym_u or " PE" in sym_u
    is_call = "CALL" in sym_u or " CE" in sym_u
    opt_type = "PUT" if is_put else ("CALL" if is_call else None)
    m = re.search(r"\b(\d{4,6})\b", symbol)
    opt_strike = m.group(1) if m else None
    if is_put:
        direction = "SHORT" if tx_type == "BUY" else "LONG"
    else:
        direction = "LONG" if tx_type == "BUY" else "SHORT"
    return direction, opt_type, opt_strike


def reconstruct(executions: list, today: str = None) -> list:
    """Pair executions per contract into trades (average-in, full/partial closes, flips).
    Each trade has entry_date/entry_time and exit_date/exit_time. A position still open
    after its option expiry date is marked EXPIRED (settled by the exchange -- that
    settlement isn't in trade history, so its P&L is unknown here)."""
    today = today or datetime.now(_IST).strftime("%Y-%m-%d")
    by_symbol = defaultdict(list)
    for t in sorted(executions, key=_exec_time):
        sym = t.get("customSymbol") or t.get("tradingSymbol")
        if sym:
            by_symbol[sym].append(t)

    out = []
    for symbol, sym_trades in by_symbol.items():
        active = None
        expiry = ""
        for t_exec in sym_trades:
            qty = int(t_exec.get("tradedQuantity", 0) or t_exec.get("quantity", 0) or 0)
            if qty <= 0:
                continue
            price = float(t_exec.get("tradedPrice", 0.0) or t_exec.get("price", 0.0) or 0.0)
            tx_type = (t_exec.get("transactionType") or t_exec.get("type") or "").upper()
            if not tx_type:
                continue
            expiry = str(t_exec.get("drvExpiryDate") or expiry or "")[:10]
            date_part, time_part = _split_ts(_exec_time(t_exec))
            etid = t_exec.get("exchangeTradeId") or t_exec.get("tradeId")
            trade_id = etid if etid and etid != "0" else f"DHAN_{t_exec.get('orderId')}_{tx_type}"
            direction, opt_type, opt_strike = _parse_contract(symbol, tx_type)

            def _new(q):
                return {
                    "trade_id": trade_id, "status": "OPEN",
                    "instrument": _guess_instrument(symbol), "symbol": symbol,
                    "direction": direction, "open_tx_type": tx_type,
                    "option_type": opt_type, "option_strike": opt_strike, "expiry_date": expiry,
                    "qty": q, "entry_date": date_part, "entry_time": time_part, "entry_price": price,
                    "exit_price": None, "exit_date": None, "exit_time": None,
                    "exit_reason": None, "pnl": None, "reasons": [],
                }

            if active is None:
                active = _new(qty)
                continue
            if active["open_tx_type"] == tx_type:
                total_qty = active["qty"] + qty
                active["entry_price"] = round((active["entry_price"] * active["qty"] + price * qty) / total_qty, 2)
                active["qty"] = total_qty
                continue
            closed_qty = min(qty, active["qty"])
            if active["open_tx_type"] == "BUY":
                pnl = round((price - active["entry_price"]) * closed_qty, 2)
            else:
                pnl = round((active["entry_price"] - price) * closed_qty, 2)
            exit_fields = {"status": "CLOSED", "exit_price": price, "exit_date": date_part,
                           "exit_time": time_part, "pnl": pnl}
            if qty >= active["qty"]:
                active.update(exit_fields, exit_reason="DHAN_CLOSED")
                out.append(active)
                rem_qty = qty - closed_qty
                active = _new(rem_qty) if rem_qty > 0 else None
            else:
                part = dict(active, qty=closed_qty, **exit_fields)
                part["exit_reason"] = "DHAN_PARTIAL_CLOSE"
                out.append(part)
                active["qty"] -= closed_qty
        if active is not None:
            if active.get("expiry_date") and active["expiry_date"] < today:
                active.update(status="EXPIRED", exit_date=active["expiry_date"],
                              exit_reason="EXPIRED (settled by exchange)")
            out.append(active)
    return out


# Executions cache shared by both pages. Reading ~4 months of history is ~15 pages (~7-10 s),
# and executions older than a few days never change -- so the OLD part is read once a day
# (or when an earlier start is needed), and only the last RECENT_DAYS are re-read every
# CACHE_SECONDS. One fetch at a time (the Broker Journal polls every 5 s).
CACHE_SECONDS = 120
RECENT_DAYS = 3
_old = {"from": None, "day": None, "execs": {}}      # executions before the recent window
_recent = {"at": 0.0, "since": None, "execs": {}}    # executions in the recent window
_cache_lock = threading.Lock()


def _keyed(raw: list) -> dict:
    out = {}
    for idx, t in enumerate(raw):
        etid = t.get("exchangeTradeId") or t.get("tradeId")
        if etid and etid != "0":
            key = etid
        else:
            key = f"{t.get('orderId')}_{t.get('transactionType')}_{t.get('tradedPrice')}_{idx}"
        out[key] = t
    return out


def _executions_since(fetch_from: str) -> list:
    """All executions from fetch_from up to today (cached as described above)."""
    import broker
    with _cache_lock:
        now = datetime.now(_IST)
        today = now.strftime("%Y-%m-%d")
        recent_from = (now - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
        old_to = (now - timedelta(days=RECENT_DAYS + 1)).strftime("%Y-%m-%d")
        # Always up to today: a trade opened inside the range but closed after it still
        # needs its exit (otherwise it looks open / expired).
        if fetch_from <= old_to and (_old["day"] != today or _old["from"] is None or fetch_from < _old["from"]):
            # read far enough back for both pages at once (Performance: 30 days + look-back)
            start = min(fetch_from, (now - timedelta(days=30 + LOOKBACK_DAYS)).strftime("%Y-%m-%d"))
            raw = broker.get_trade_history(start, old_to) or []
            _old.update(**{"from": start, "day": today, "execs": _keyed(raw)})
            logger.info(f"{len(raw)} executions {start}..{old_to} (older part, kept for the day)")
        if time.time() - _recent["at"] >= CACHE_SECONDS or _recent["since"] != recent_from:
            raw = broker.get_trade_history(recent_from, today) or []
            _recent.update(**{"at": time.time(), "since": recent_from, "execs": _keyed(raw)})
        merged = dict(_old["execs"]) if fetch_from <= old_to else {}
        merged.update(_recent["execs"])
        return list(merged.values())


def fetch_trades(from_date: str, to_date: str) -> list:
    """Trades overlapping [from_date, to_date]: opened on/before to_date and not closed
    before from_date. Executions are read from LOOKBACK_DAYS before from_date so trades
    opened earlier are paired correctly. Returns [] when Dhan isn't connected."""
    import broker
    if not broker.is_connected():
        return []
    fetch_from = (datetime.strptime(from_date, "%Y-%m-%d") - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    trades = reconstruct(_executions_since(fetch_from))
    return [t for t in trades
            if t["entry_date"] <= to_date and (t.get("exit_date") or "9999-12-31") >= from_date]
