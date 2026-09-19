"""
investment_timing.py — evidence for the "should I wait for a lower price?" question.

Advice like "wait for a dip to Rs X" is only useful if a dip that size actually
tends to happen, and only worth taking if waiting does not usually cost more
than it saves. Neither is obvious, and both can be measured from the stock's
own price history:

  * how often the price fell by X% (to a given level) within the next 3 / 6 / 12
    months, across every starting week in the stock's history;
  * what a random walk with the stock's own volatility would predict for the
    same thing (a sanity check on the empirical count);
  * a simple experiment run over the same history: place a buy order at that
    level for up to 12 months, buy at the 12-month mark if it never filled, and
    compare the 2-year price return with simply buying on the starting day.

Deliberately modest about what it proves: windows overlap heavily (a 20-year
history is roughly 20 independent years, not 1,000 observations), prices are
weekly closes (an intraday dip is missed, so dips are, if anything, undercounted),
and a stock that mostly rose over the sample will show few dips. It describes
the past; it does not predict the next year.

Pure functions, no I/O.
"""
import math
from datetime import datetime
from typing import Optional

GENERIC_DIPS_PCT = (5, 10, 20, 30)
HORIZONS_WEEKS = {"3m": 13, "6m": 26, "12m": 52}
WAIT_LIMIT_WEEKS = 52          # how long the buy order is left open
HOLD_WEEKS = 104               # 2 years from the starting week
MIN_WEEKS = 3 * 52             # need at least ~3 years of weekly prices
MIN_WAIT_WINDOWS = 60


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _weekly(price_hist: list) -> list:
    """[(date, close)] one point per ISO week (the last close of the week), oldest first."""
    by_week = {}
    for p in price_hist or []:
        try:
            d = datetime.fromisoformat(str(p.get("ts")).replace("Z", "+00:00")).date()
            lp = float(p.get("lp"))
        except (TypeError, ValueError):
            continue
        if lp > 0:
            by_week[d.isocalendar()[:2]] = (d, lp)
    return [by_week[k] for k in sorted(by_week)]


def touch_probability_random_walk(drop_frac: float, weeks: int, weekly_sigma: float, weekly_drift: float = 0.0) -> Optional[float]:
    """Chance a random walk with this weekly volatility and (log) drift falls to a level `drop_frac`
    below the start at some point within `weeks` (first-passage probability of Brownian motion with
    drift). Watched continuously, so a little generous versus weekly closes."""
    if not (0 < drop_frac < 1) or weekly_sigma <= 0:
        return None
    b = -math.log(1 - drop_frac)
    sd = weekly_sigma * math.sqrt(weeks)
    mu_t = weekly_drift * weeks
    tail = math.exp(-2 * weekly_drift * b / weekly_sigma ** 2) if abs(weekly_drift) * b / weekly_sigma ** 2 < 300 else 0.0
    return min(1.0, _phi((-b - mu_t) / sd) + tail * _phi((-b + mu_t) / sd))


def _pct(x, nd=0):
    if x is None:
        return None
    return round(x * 100, nd) if nd else int(round(x * 100))


def build_waiting_evidence(price_hist: list, price: float, levels: Optional[list] = None) -> dict:
    """levels: [(label, price)] model price levels; those below today's price get their own row,
    alongside generic 5/10/20/30% dips."""
    wk = _weekly(price_hist)
    if len(wk) < MIN_WEEKS:
        return {"available": False, "reason": "less than about three years of price history, too little to say how often dips happen"}
    px = [p for _, p in wk]
    n = len(px)
    years = (wk[-1][0] - wk[0][0]).days / 365.25
    logrets = [math.log(px[i + 1] / px[i]) for i in range(n - 1)]
    mean = sum(logrets) / len(logrets)
    sigma_w = math.sqrt(sum((r - mean) ** 2 for r in logrets) / (len(logrets) - 1))

    targets = []          # (label, level price, drop fraction)
    seen = []
    for lab, lv in sorted(levels or [], key=lambda x: -(x[1] or 0)):
        if lv and lv < price * 0.985 and not any(abs(lv - s) / s < 0.015 for s in seen):
            seen.append(lv)
            targets.append((lab, float(lv), 1 - lv / price))
    for d in GENERIC_DIPS_PCT:
        targets.append((f"{d}% below today's price", price * (1 - d / 100), d / 100))

    rows = []
    for lab, level, drop in targets:
        touch, first_wait = {}, []
        for key, h in HORIZONS_WEEKS.items():
            hit = tot = 0
            for i in range(0, n - h):
                tot += 1
                thr = px[i] * (1 - drop)
                found = next((j for j in range(i + 1, i + h + 1) if px[j] <= thr), None)
                if found is not None:
                    hit += 1
                    if h == 52:
                        first_wait.append(found - i)
            touch[key] = _pct(hit / tot) if tot else None
        row = {
            "label": lab, "price": round(level, 0), "drop_pct": round(drop * 100, 1),
            "history_touch_pct": touch,
            "random_walk_12m_pct": _pct(touch_probability_random_walk(drop, 52, sigma_w, mean)),
            "median_weeks_to_reach_when_it_did": int(sorted(first_wait)[len(first_wait) // 2]) if first_wait else None,
        }
        if n - HOLD_WEEKS >= MIN_WAIT_WINDOWS:
            now_r, wait_r, filled, wins, missed = [], [], 0, 0, []
            for i in range(0, n - HOLD_WEEKS):
                thr = px[i] * (1 - drop)
                fill = next((j for j in range(i + 1, i + WAIT_LIMIT_WEEKS + 1) if px[j] <= thr), None)
                entry = thr if fill is not None else px[i + WAIT_LIMIT_WEEKS]
                nr, wr = px[i + HOLD_WEEKS] / px[i] - 1, px[i + HOLD_WEEKS] / entry - 1
                now_r.append(nr)
                wait_r.append(wr)
                wins += wr > nr
                if fill is not None:
                    filled += 1
                else:
                    missed.append(px[i + WAIT_LIMIT_WEEKS] / px[i] - 1)
            k = len(now_r)
            row["wait_vs_buy_now_2y"] = {
                "windows": k, "order_filled_pct": _pct(filled / k),
                "avg_price_return_buy_now_pct": _pct(sum(now_r) / k, 1), "avg_price_return_wait_pct": _pct(sum(wait_r) / k, 1),
                "waiting_beat_buying_now_pct": _pct(wins / k),
                "avg_rise_missed_when_order_never_filled_pct": _pct(sum(missed) / len(missed), 1) if missed else None,
            }
        rows.append(row)

    model_rows = [r for r in rows if "today's price" not in r["label"]]
    focus = model_rows[0] if model_rows else next(r for r in rows if r["drop_pct"] == 10.0)
    w = focus.get("wait_vs_buy_now_2y")
    text = (f"Across about {years:.0f} years of this stock's own weekly prices, it fell {focus['drop_pct']:.0f}% or more (to about Rs{focus['price']:,.0f}) "
            f"within 12 months of a given week in {focus['history_touch_pct']['12m']}% of cases; a random walk with the same volatility and drift would say {focus['random_walk_12m_pct']}%.")
    if w:
        text += (f" Waiting for that level (and buying anyway after a year if it never came) beat buying immediately in {w['waiting_beat_buying_now_pct']}% of starting weeks; "
                 f"the average 2-year price return was {w['avg_price_return_wait_pct']}% waiting versus {w['avg_price_return_buy_now_pct']}% buying at once.")
        if w["order_filled_pct"] is not None and w["order_filled_pct"] < 60 and w["avg_rise_missed_when_order_never_filled_pct"] is not None:
            text += (f" The order was only filled {w['order_filled_pct']}% of the time, and when it was not the stock rose {w['avg_rise_missed_when_order_never_filled_pct']}% on average "
                     "during the wait: the price of waiting is that a good stock may simply never come back.")
    verdict, verdict_label = "not_measured", "Not enough history to say whether waiting paid."
    if w:
        beat = w["waiting_beat_buying_now_pct"]
        if beat >= 55 and w["avg_price_return_wait_pct"] >= w["avg_price_return_buy_now_pct"]:
            verdict, verdict_label = "favours_waiting", f"History favours waiting for this level: it beat buying at once in {beat}% of starting weeks."
        elif beat < 45:
            verdict, verdict_label = "against_waiting", f"History does NOT favour waiting for this level: it beat buying at once in only {beat}% of starting weeks."
        else:
            verdict, verdict_label = "mixed", f"History is mixed on waiting for this level: it beat buying at once in {beat}% of starting weeks, roughly a coin flip."
    return {
        "available": True, "history_years": round(years, 1), "annual_volatility_pct": _pct(sigma_w * math.sqrt(52), 0), "rows": rows,
        "verdict": verdict, "verdict_label": verdict_label, "text": text,
        "caution": (f"Based on {years:.0f} years of one stock's history, so roughly {years:.0f} independent years even though there are many overlapping windows. "
                    "Prices are weekly closes (intraday dips are missed, so dips are somewhat undercounted). A stock that mostly rose over the sample shows few dips, and the stocks with long histories "
                    "here are ones that exist today, which flatters buying early (survivorship). Money left waiting is assumed to earn nothing. "
                    "This describes the past; it does not predict the next year."),
    }
