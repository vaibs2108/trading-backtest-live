"""
cas_paper_squeeze.py -- CAS Mode C PAPER strategy: "14:45 expiry squeeze" (NIFTY, BANKNIFTY, SENSEX).

PAPER ONLY: never places an order. Telegram alerts + a record on the CAS page, to forward-test the rule.

Rules are FROZEN as reviewed on 2026-10-04 (market_data_store/research/cas_mode_c/
cas_mode_c_1lot_strategy_REVIEWED_2026-10-04.py -- the 15:36 version; the research file was later
changed to a different "tight-SL" variant, which is NOT this one):
  * Only on the index's own expiry day (Dhan's expiry list).
  * At 14:45: day trend = 09:15 -> 14:45 close, momentum = 14:30 -> 14:45 close; both up -> CALL,
    both down -> PUT, otherwise no trade.
  * Buy the 1-strike in-the-money option at 14:46 (CALL: ATM - step, PUT: ATM + step); skip if under Rs.8.
  * No initial stop. Peak gain >= +20% -> stop = entry x 1.075. Peak gain >= +35% -> trail 15% below
    the peak (never below entry x 1.075). 15:15: exit if the peak gain stayed under +8%. 15:25: exit.
  * Paper P&L with 3% slippage on each side, 1 lot at today's lot size.
Backtest (review_1lot_moc_results.txt): Jan 2025 - Oct 2026 +6.9%/trade; unseen Jun-Dec 2024 -0.9%/trade
(no edge overall); SENSEX positive in 2024/2025/2026 but driven by 3 trades. Hence: paper forward test.

Live differences from the backtest: entry is the option's live price just after 14:46:00 (the backtest used
the 14:46 minute's close); the option price is checked about every 20 s (the backtest used minute closes).
"""
import json
import logging
import threading
import time as _time
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))
_PATH = Path(__file__).parent / "data" / "cas_paper_trades.json"
STEP = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}
SIGNAL_AT, ENTRY_AT, GIVE_UP_AT = time(14, 46), time(14, 46), time(14, 50)
BE_AT, BE_LOCK, TRAIL_AT, TRAIL = 0.20, 1.075, 0.35, 0.15
STAG_AT, STAG_MIN, EXIT_AT = time(15, 15), 0.08, time(15, 25)
MIN_PREMIUM, SLIP, CHECK_EVERY = 8.0, 0.03, 20

_lock = threading.Lock()
_trades = []          # every evaluation (NO_TRADE / OPEN / CLOSED), newest last
_last_check = {}      # index -> epoch of last price check
_loaded = False


def _now():
    return datetime.now(_IST)


def _load():
    global _trades, _loaded
    if _loaded:
        return
    try:
        _trades = json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else []
    except Exception as e:
        logger.warning(f"paper squeeze: could not read {_PATH.name}: {e}")
        _trades = []
    _loaded = True


def _save():
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(_trades, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning(f"paper squeeze: could not save: {e}")


def _today_rec(index, day):
    return next((t for t in _trades if t["index"] == index and t["date"] == day), None)


def _ltp(chain, strike, side):
    from cas_scanner import _extract_oc
    _, oc = _extract_oc(chain)
    for k, v in (oc or {}).items():
        try:
            if abs(float(k) - strike) < 0.01:
                return (v.get(side.lower()) or {}).get("last_price")
        except (TypeError, ValueError):
            continue
    return None


def _fmt(x):
    return f"Rs.{x:,.1f}" if x < 1000 else f"Rs.{x:,.0f}"


def _evaluate(idx, expiry, day, notify):
    """At 14:46: signal + paper entry (or NO_TRADE)."""
    import cas_broker
    closes = cas_broker.index_minute_closes(idx["security_id"], day)
    if closes is None or "14:45" not in closes:
        return False                      # data not in yet -- retry on the next tick
    name = idx["symbol"]
    rec = {"index": name, "date": day, "expiry": expiry, "status": "NO_TRADE", "reason": None}
    s0915, s1430, s1445 = closes.get("09:15"), closes.get("14:30"), closes.get("14:45")
    if not (s0915 and s1430 and s1445):
        rec["reason"] = "index minute data incomplete"
    else:
        up_day, up_15 = s1445 > s0915, s1445 > s1430
        rec.update(day_trend_pct=round((s1445 / s0915 - 1) * 100, 2), momentum_15m_pct=round((s1445 / s1430 - 1) * 100, 2))
        if s1445 == s1430 or up_day != up_15:
            rec["reason"] = (f"day trend {'up' if up_day else 'down'} but last 15 min "
                             f"{'up' if up_15 else ('down' if s1445 < s1430 else 'flat')}")
        else:
            side = "CE" if up_day else "PE"
            step = STEP[name]
            atm = round(s1445 / step) * step
            strike = float(atm - step if side == "CE" else atm + step)
            chain = cas_broker.option_chain(idx["security_id"], idx["exchange_segment"], expiry)
            px = _ltp(chain, strike, side) if chain else None
            if px is None:
                return False              # chain refused -- retry on the next tick
            if px < MIN_PREMIUM:
                rec["reason"] = f"premium {_fmt(px)} under Rs.{MIN_PREMIUM:.0f}"
            else:
                import broker
                lot = broker.get_lot_size(name)
                rec.update(status="OPEN", side=side, strike=strike, entry_time=_now().strftime("%H:%M:%S"),
                           entry_price=px, exec_entry=round(px * (1 + SLIP), 2), lot=lot, peak=px, stop=None,
                           be_alerted=False)
                notify(f"\U0001F4DD PAPER · {name} {strike:.0f} {side} (1 ITM) @ {_fmt(px)} — expiry squeeze: "
                       f"day {'↑' if up_day else '↓'} and last 15 min {'↑' if up_15 else '↓'}\n"
                       f"Plan: no stop until +20% (then stop {_fmt(px * BE_LOCK)}); from +35% ({_fmt(px * (1 + TRAIL_AT))}) "
                       f"trail 15% below the peak; exit at 15:15 if still under +8%, otherwise by 15:25.\n"
                       f"1 lot = {lot} → {_fmt(px * lot)}. Paper only — no order placed.")
    if rec["status"] == "NO_TRADE":
        notify(f"\U0001F4DD PAPER · {name} expiry squeeze: no trade today — {rec['reason']}.")
    _trades.append(rec)
    _save()
    return True


def _close(rec, px, why, notify):
    rec.update(status="CLOSED", exit_time=_now().strftime("%H:%M:%S"), exit_price=px, exit_reason=why,
               exec_exit=round(px * (1 - SLIP), 2))
    rec["return_pct"] = round((rec["exec_exit"] - rec["exec_entry"]) / rec["exec_entry"] * 100, 2)
    rec["rupees"] = round((rec["exec_exit"] - rec["exec_entry"]) * rec["lot"], 0)
    label = {"STOP": "stop hit", "TRAIL": "trailing stop", "STAGNANT": "15:15 — under +8%", "TIME": "15:25 time exit"}[why]
    notify(f"\U0001F4DD PAPER RESULT · {rec['index']} {rec['strike']:.0f} {rec['side']}: exit {_fmt(px)} at "
           f"{rec['exit_time'][:5]} ({label}) → {rec['return_pct']:+.1f}% after 3% slippage each side "
           f"({'+' if rec['rupees'] >= 0 else '-'}Rs.{abs(rec['rupees']):,.0f} on 1 lot of {rec['lot']}). "
           f"Peak was {_fmt(rec['peak'])} ({(rec['peak'] / rec['entry_price'] - 1) * 100:+.0f}%).")


def _manage(idx, rec, notify):
    import cas_broker
    chain = cas_broker.option_chain(idx["security_id"], idx["exchange_segment"], rec["expiry"])
    px = _ltp(chain, rec["strike"], rec["side"]) if chain else None
    now = _now().time()
    if px is None:
        if now >= time(15, 29):           # can't price it any more: close at the last price seen
            _close(rec, rec.get("last_price", rec["entry_price"]), "TIME", notify)
            _save()
        return
    rec["last_price"] = px
    rec["peak"] = max(rec["peak"], px)
    raw = rec["entry_price"]
    gain = rec["peak"] / raw - 1
    if gain >= BE_AT:
        rec["stop"] = max(rec["stop"] or 0, raw * BE_LOCK)
        if not rec["be_alerted"]:
            rec["be_alerted"] = True
            notify(f"\U0001F4DD PAPER · {rec['index']} {rec['strike']:.0f} {rec['side']}: +20% reached "
                   f"({_fmt(rec['peak'])}) — stop now {_fmt(raw * BE_LOCK)} (locks a small gain).")
    if gain >= TRAIL_AT:
        rec["stop"] = max(rec["stop"] or 0, rec["peak"] * (1 - TRAIL), raw * BE_LOCK)
    if rec["stop"] and px <= rec["stop"]:
        _close(rec, px, "TRAIL" if gain >= TRAIL_AT else "STOP", notify)
    elif now >= STAG_AT and gain < STAG_MIN:
        _close(rec, px, "STAGNANT", notify)
    elif now >= EXIT_AT:
        _close(rec, px, "TIME", notify)
    _save()


def tick(notify, broadcast=None):
    """Called every ~2 s from the CAS index thread while the market is open."""
    import cas_broker
    now = _now()
    if now.time() < SIGNAL_AT:
        return
    with _lock:
        _load()
        day = now.date().isoformat()
        changed = False
        for idx in cas_broker.INDEX_UNIVERSE:
            name = idx["symbol"]
            rec = _today_rec(name, day)
            if rec is None:
                if now.time() > GIVE_UP_AT:
                    continue
                expiry = cas_broker.nearest_expiry_for_index(idx["security_id"], idx["exchange_segment"])
                if expiry != day:
                    continue              # not this index's expiry day
                changed |= _evaluate(idx, expiry, day, notify)
            elif rec["status"] == "OPEN" and _time.time() - _last_check.get(name, 0) >= CHECK_EVERY:
                _last_check[name] = _time.time()
                _manage(idx, rec, notify)
                changed = True
        if changed and broadcast:
            broadcast({"type": "cas_paper_update"})


def get_state(limit: int = 200) -> dict:
    with _lock:
        _load()
        closed = [t for t in _trades if t["status"] == "CLOSED"]
        rets = [t["return_pct"] for t in closed]
        by_index = {}
        for t in closed:
            b = by_index.setdefault(t["index"], {"trades": 0, "wins": 0, "rupees": 0.0})
            b["trades"] += 1
            b["wins"] += t["return_pct"] > 0
            b["rupees"] += t["rupees"]
        return {
            "trades": list(reversed(_trades[-limit:])),
            "stats": {"trades": len(closed), "wins": sum(r > 0 for r in rets),
                      "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
                      "rupees": round(sum(t["rupees"] for t in closed), 0), "by_index": by_index,
                      "no_trade_days": sum(1 for t in _trades if t["status"] == "NO_TRADE")},
        }
