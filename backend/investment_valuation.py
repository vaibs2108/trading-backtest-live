"""
investment_valuation.py — the deterministic "should I invest now, wait, or at
what price" model behind Company Analysis's AI section.

Everything numeric lives here, not in the LLM: growth rates, the stock's own
historical P/E band, scenario returns, the entry-price ladder and the
baseline stance. The debate layer (investment_debate.py) only reasons over
these figures — same "compute deterministically, let the model explain"
split as Piotroski/Altman/yoy_trend, so the AI can't invent a fair value.

Method, in one paragraph: project EPS five years out under three growth
paths (bear/base/bull, all haircut from the company's own historical
growth), multiply by an exit P/E per path (anchored on the stock's own
multi-year P/E history and its industry P/E, crediting only HALF of any
upside re-rating since cheap stocks are often cheap for a reason, but
crediting full downside re-rating), add the dividend yield, and turn that
into the annual return an investor would get by buying at today's price. The
entry ladder is the same maths run backwards: the price at which the base
case still earns 15% / 12% / 9% a year.

This is a structured way of thinking about price vs. value with explicit,
visible assumptions — not a forecast, and not investment advice.

Isolation: imports only investment_scores (this feature's own module).
"""
import math
import re
from datetime import datetime, timedelta
from typing import Optional

from investment_scores import _real_fiscal_years
import investment_timing as timing

HORIZON_YEARS = 5
TARGET_ACCUMULATE = 0.15
TARGET_FAIR_START = 0.12
TARGET_UPPER_LIMIT = 0.09
BEAR_FLOOR_RETURN = 0.06

STANCES = ["accumulate_now", "start_small_and_stagger", "wait_for_better_price", "avoid_for_now"]
STANCE_LABELS = {
    "accumulate_now": "Attractive — accumulate now",
    "start_small_and_stagger": "Reasonable — start small, add on dips",
    "wait_for_better_price": "Fairly priced, thin margin of safety — wait for a dip",
    "avoid_for_now": "Expensive on these assumptions — stay away for now",
}

_NOISE_ANNOUNCEMENT_RE = re.compile(
    r"newspaper|loss of share|duplicate share|trading window|reg(ulation)?\.? ?74|certificate|"
    r"voting result|closure of trading|schedule of analyst|analyst / institutional|record date intimation|"
    r"copy of newspaper|proceedings of", re.I)


# ── small numeric helpers ────────────────────────────────────────────────

def _f(v) -> Optional[float]:
    try:
        x = float(str(v).replace(",", ""))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _r(v, nd=1):
    return round(v, nd) if v is not None else None


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _percentile(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _cagr(first, last, years) -> Optional[float]:
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def _pct_change(cur, prev) -> Optional[float]:
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / abs(prev) * 100


def _parse_date(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


# ── fundamentals profile (growth, returns, cash quality, leverage) ───────

def fundamentals_profile(statements: dict) -> dict:
    statements = statements or {}
    inc = _real_fiscal_years(statements.get("income_statement_annual", []))
    bs = {r.get("displayPeriod"): r for r in _real_fiscal_years(statements.get("balance_sheet_annual", []))}
    cf = {r.get("displayPeriod"): r for r in _real_fiscal_years(statements.get("cash_flow_annual", []))}
    if len(inc) < 2:
        return {"available": False, "reason": "fewer than 2 fiscal years of statements"}

    history = []
    for r in inc[-6:]:
        fy = r.get("displayPeriod")
        rev, ni, eps = _f(r.get("incTrev")), _f(r.get("incNinc")), _f(r.get("incEps"))
        ebitda = _f(r.get("incEbi"))
        eq = _f((bs.get(fy) or {}).get("balTeq"))
        debt = _f((bs.get(fy) or {}).get("balTdeb"))
        history.append({
            "fy": fy, "revenue": rev, "net_income": ni, "eps": _r(eps, 2), "dps": _r(_f(r.get("incDps")), 2),
            "payout_ratio_pct": _r(_f(r.get("incDps")) / eps * 100, 0) if _f(r.get("incDps")) is not None and eps and eps > 0 else None,
            "ebitda_margin_pct": _r(ebitda / rev * 100) if ebitda is not None and rev else None,
            "net_margin_pct": _r(ni / rev * 100) if ni is not None and rev else None,
            "roe_pct": _r(ni / eq * 100) if ni is not None and eq else None,
            "debt_to_equity": _r(debt / eq, 2) if debt is not None and eq else None,
            "operating_cash_flow": _f((cf.get(fy) or {}).get("cafCfoa")),
            "free_cash_flow": _f((cf.get(fy) or {}).get("cafFcf")),
        })

    eps_series = [_f(r.get("incEps")) for r in inc]
    rev_series = [_f(r.get("incTrev")) for r in inc]

    def cagr_pct(series, n):
        return _r(_cagr(series[-n - 1], series[-1], n) * 100) if len(series) > n and _cagr(series[-n - 1], series[-1], n) is not None else None

    ups = sum(1 for a, b in zip(eps_series[-6:-1], eps_series[-5:]) if a is not None and b is not None and b > a)

    last3 = history[-3:]
    cfo_sum = sum(h["operating_cash_flow"] for h in last3 if h["operating_cash_flow"] is not None)
    ni_sum = sum(h["net_income"] for h in last3 if h["net_income"] is not None)

    q = sorted(statements.get("income_statement_quarterly", []), key=lambda r: r.get("endDate") or "")
    latest_q = None
    if len(q) >= 5:
        cur, prev = q[-1], q[-5]
        latest_q = {
            "quarter": cur.get("displayPeriod"), "vs_year_ago_quarter": prev.get("displayPeriod"),
            "revenue_yoy_pct": _r(_pct_change(_f(cur.get("qIncTrev")), _f(prev.get("qIncTrev")))),
            "net_income_yoy_pct": _r(_pct_change(_f(cur.get("qIncNinc")), _f(prev.get("qIncNinc")))),
            "eps_yoy_pct": _r(_pct_change(_f(cur.get("qIncEps")), _f(prev.get("qIncEps")))),
            "last_4_quarters_net_margin_pct": [
                _r(_f(x.get("qIncNinc")) / _f(x.get("qIncTrev")) * 100) if _f(x.get("qIncNinc")) is not None and _f(x.get("qIncTrev")) else None
                for x in q[-4:]],
        }

    latest = history[-1]
    return {
        "available": True,
        "unit": "₹ crore (consolidated, Tickertape)",
        "roe_definition": "roe_pct = net income / year-end equity (consolidated); the ROE tile on the page comes from BSE and can differ",
        "history_last_6_fy": history,
        "eps_cagr_3y_pct": cagr_pct(eps_series, 3), "eps_cagr_5y_pct": cagr_pct(eps_series, 5),
        "revenue_cagr_3y_pct": cagr_pct(rev_series, 3), "revenue_cagr_5y_pct": cagr_pct(rev_series, 5),
        "eps_up_years_of_last_5": ups,
        "cash_conversion_3y": _r(cfo_sum / ni_sum, 2) if ni_sum else None,  # operating cash flow ÷ net income
        "latest_roe_pct": latest.get("roe_pct"), "latest_debt_to_equity": latest.get("debt_to_equity"),
        "latest_quarter_yoy": latest_q,
    }


# ── the stock's own historical P/E ───────────────────────────────────────

def historical_pe(price_hist: list, statements: dict, current_pe: Optional[float]) -> dict:
    """P/E at each past fiscal-year end = weekly price nearest that date ÷
    that year's EPS. Coarse (one point a year), but it answers the question
    a bare P/E tile can't: is today's multiple high or low *for this
    stock*?"""
    weekly = []
    for p in price_hist or []:
        d, lp = _parse_date(p.get("ts")), _f(p.get("lp"))
        if d and lp:
            weekly.append((d, lp))
    weekly.sort()
    if not weekly:
        return {"available": False, "reason": "no price history"}

    points = []
    for r in _real_fiscal_years((statements or {}).get("income_statement_annual", []))[-9:]:
        end, eps = _parse_date(r.get("endDate")), _f(r.get("incEps"))
        if not end or not eps or eps <= 0:
            continue
        prior = [(d, lp) for d, lp in weekly if d <= end + timedelta(days=3)]
        if not prior or (end - prior[-1][0]).days > 10:
            continue
        pe = prior[-1][1] / eps
        if 3 < pe < 150:
            points.append({"fy": r.get("displayPeriod"), "pe": _r(pe)})
    if len(points) < 3:
        return {"available": False, "reason": "fewer than 3 usable fiscal-year P/E points"}

    pes = [p["pe"] for p in points]
    pct_rank = _r(sum(1 for x in pes if x <= current_pe) / len(pes) * 100, 0) if current_pe else None
    return {
        "available": True, "by_year": points, "median": _r(_median(pes)), "p25": _r(_percentile(pes, 0.25)),
        "p75": _r(_percentile(pes, 0.75)), "min": min(pes), "max": max(pes),
        "current_percentile_of_history": pct_rank,  # 0 = cheapest it has been, 100 = most expensive
    }


# ── how far the model can be trusted for THIS company ────────────────────

def assess_reliability(inc_rows: list, eps_ttm, growth_source, hist_pe, industry_pe, industry_pe_weak: bool = False) -> dict:
    """A projection built on erratic or loss-making earnings is worse than
    no projection (confirmed on a real loss-making stock: one profitable
    year produced a '+54% a year' base case). This grades the inputs so the
    model can cap its own stance and say so out loud."""
    eps = [_f(r.get("incEps")) for r in inc_rows][-6:]
    last5 = eps[-5:]
    reasons, level = [], "high"

    pos = sum(1 for x in last5 if x is not None and x > 0)
    if pos < 4:
        reasons.append(f"EPS was positive in only {pos} of the last {len(last5)} fiscal years")
        level = "low"
    if growth_source is None or "revenue" in growth_source or "generic" in growth_source:
        reasons.append("earnings history wasn't usable for growth, so revenue growth or a generic rate was substituted")
        level = "low"
    recent_pos = [x for x in eps[-3:] if x and x > 0]
    if recent_pos and eps_ttm:
        avg = sum(recent_pos) / len(recent_pos)
        if eps_ttm > 2.5 * avg or eps_ttm < 0.4 * avg:
            reasons.append("trailing EPS is far from its recent 3-year average — likely a one-off gain or a sharp swing")
            level = "low"
    yoy = [(b - a) / abs(a) for a, b in zip(eps[:-1], eps[1:]) if a and b is not None]
    if any(v < -0.30 for v in yoy[-5:]):
        reasons.append("earnings fell more than 30% in at least one recent year (cyclical/volatile), so extrapolating past growth is less reliable")
        level = "low" if level == "low" else "medium"
    has_hist = bool(hist_pe and hist_pe.get("available"))
    if not has_hist:
        reasons.append("no usable own-history P/E to anchor the exit multiple")
        level = "low" if level == "low" else "medium"
    if not industry_pe:
        reasons.append("no industry P/E available")
        level = "low" if level == "low" else "medium"
    # The exit multiple is what turns projected earnings into a price. With neither the
    # stock's own P/E history nor a REAL industry P/E behind it (a median over a handful
    # of BSE peers does not count), it is mostly assumption — not a valuation.
    if not has_hist and not (industry_pe and not industry_pe_weak):
        reasons.append("no solid P/E anchor (no usable own-history P/E and no reliable industry P/E), so the exit multiple is largely assumption")
        level = "low"
    return {"level": level, "reasons": reasons}


# ── scenarios, entry ladder, baseline stance ─────────────────────────────

def _scenario(eps, price, growth, exit_pe, div_yield):
    value_5y = eps * (1 + growth) ** HORIZON_YEARS * exit_pe
    price_cagr = (value_5y / price) ** (1 / HORIZON_YEARS) - 1
    return {
        "eps_growth_pct": _r(growth * 100), "exit_pe": _r(exit_pe), "eps_in_5y": _r(eps * (1 + growth) ** HORIZON_YEARS),
        "value_in_5y": _r(value_5y, 0), "price_return_pct_pa": _r(price_cagr * 100),
        "total_return_pct_pa": _r((price_cagr + div_yield) * 100),
    }


def _return_at_price(value_5y, price, div_yield):
    return _r(((value_5y / price) ** (1 / HORIZON_YEARS) - 1 + div_yield) * 100)


def _price_for_return(value_5y, target_total, div_yield):
    required_price_cagr = target_total - div_yield
    if required_price_cagr <= -0.5:
        return None
    return value_5y / (1 + required_price_cagr) ** HORIZON_YEARS


def _nice_price(x: float) -> float:
    """Round to a human-friendly price (multiples of 100/50/10/5/1 by magnitude)."""
    step = 100 if x >= 2000 else 50 if x >= 500 else 10 if x >= 100 else 5 if x >= 20 else 1
    return float(round(x / step) * step)


def _explain_growth(g_hist, g_base, source) -> str:
    if g_hist < 0:
        return (f"Historical EPS growth was negative ({g_hist * 100:.1f}%/yr), so the base case assumes only a modest "
                f"{g_base * 100:.0f}% a year recovery.")
    raw = 0.75 * g_hist
    if abs(raw - g_base) < 1e-9:
        return f"Base-case growth is {g_base * 100:.1f}% a year: 75% of the {g_hist * 100:.1f}% the company achieved historically ({source}), a deliberate haircut."
    return (f"75% of the {g_hist * 100:.1f}% historical growth ({source}) would be {raw * 100:.1f}%, held to {g_base * 100:.1f}% a year "
            f"so the projection stays within a plausible range.")


def _explain_exit_pe(hist_median, industry_pe, industry_used, anchor_pe, current_pe, exit_base) -> str:
    parts = []
    if hist_median:
        parts.append(f"the stock's own median P/E over recent years ({hist_median:.1f}x)")
    if industry_pe and industry_used:
        parts.append(f"the industry P/E ({industry_pe:.1f}x)")
    if not parts:
        return f"No P/E history or industry P/E was available, so the multiple is assumed to stay at today's {current_pe:.1f}x."
    anchor_txt = (f"the average of {parts[0]} and {parts[1]} = {anchor_pe:.1f}x" if len(parts) == 2 else f"{parts[0]}")
    if anchor_pe <= current_pe:
        return (f"The P/E the market is assumed to pay in 5 years is {exit_base:.1f}x, anchored on {anchor_txt}. Today's P/E is "
                f"{current_pe:.1f}x, above that, so the base case assumes it falls back — a fall in the multiple is counted in full.")
    return (f"The P/E the market is assumed to pay in 5 years is {exit_base:.1f}x. The anchor ({anchor_txt}) is above today's "
            f"{current_pe:.1f}x, but cheap stocks are often cheap for a reason, so only half of that gap is assumed to close.")


def _build_price_derivation(price, eps_ttm, g_base, g_hist, growth_source, scenarios, ladder, bear_safe_price,
                            div_yield, hist_median, industry_pe, industry_used, anchor_pe, current_pe, exit_base,
                            v_base, v_bear, v_bull) -> dict:
    """Shows the user exactly how each price level was reached, so a number like
    'Rs 989' is a traceable calculation, not an assertion: the base-case 5-year
    value, worked backwards to the price that earns each target return; why the
    targets are what they are; what buying at other prices would earn; and how
    much the number moves if the assumptions are wrong."""
    base = scenarios["base"]
    levels = []
    for lvl in ladder:
        target = lvl["target_return_pct_pa"] / 100
        req = target - div_yield
        levels.append({"name": lvl["name"], "scenario": "base", "target_pct": lvl["target_return_pct_pa"],
                       "required_price_growth_pct": _r(req * 100, 2), "growth_factor": _r((1 + req) ** HORIZON_YEARS, 3),
                       "value_in_5y": v_base, "price": lvl["price"]})
    if bear_safe_price:
        req = BEAR_FLOOR_RETURN - div_yield
        levels.append({"name": "Defensive floor (bear case earns 6% a year)", "scenario": "bear", "target_pct": 6,
                       "required_price_growth_pct": _r(req * 100, 2), "growth_factor": _r((1 + req) ** HORIZON_YEARS, 3),
                       "value_in_5y": v_bear, "price": _r(bear_safe_price, 0)})

    # the level the verdict most often refers to: the highest ladder price that is still below today's price
    below = [i for i, l in enumerate(levels) if l["scenario"] == "base" and l["price"] < price * 0.985]
    key_index = max(below, key=lambda i: levels[i]["price"]) if below else None
    ref = levels[key_index]["price"] if key_index is not None else (_r(bear_safe_price, 0) if bear_safe_price and bear_safe_price < price else None)

    nearby = []
    if ref:
        prices = {ref: "key"}
        for f in (0.8, 0.9, 1.1):
            prices.setdefault(_nice_price(ref * f), None)
        prices.setdefault(_r(price, 0), "today")
        for p_ in sorted(prices):
            if p_ and p_ > 0:
                nearby.append({"price": p_, "is_reference": p_ == ref, "is_today": prices[p_] == "today" or p_ == _r(price, 0),
                               "base_return_pct_pa": _return_at_price(v_base, p_, div_yield),
                               "bear_return_pct_pa": _return_at_price(v_bear, p_, div_yield)})

    target = levels[key_index]["target_pct"] / 100 if key_index is not None else TARGET_FAIR_START
    sens = {"target_pct": _r(target * 100, 0)}
    for label, v in (("bear", v_bear), ("base", v_base), ("bull", v_bull)):
        p_ = _price_for_return(v, target, div_yield)
        sens[label] = _r(p_, 0) if p_ else None

    return {
        "steps": {
            "eps_ttm": _r(eps_ttm, 2), "growth_pct": base["eps_growth_pct"], "growth_text": _explain_growth(g_hist, g_base, growth_source),
            "eps_in_5y": base["eps_in_5y"], "exit_pe": base["exit_pe"],
            "exit_pe_text": _explain_exit_pe(hist_median, industry_pe, industry_used, anchor_pe, current_pe, exit_base),
            "value_in_5y": v_base, "dividend_yield_pct": _r(div_yield * 100, 2), "horizon_years": HORIZON_YEARS,
        },
        "levels": levels, "key_level_index": key_index,
        "why_targets": ("The 9%, 12% and 15% targets are decision thresholds we chose, not numbers derived from the company: 9% is the "
                        "lowest yearly return we treat as worth the risk of owning a single stock, 12% is a comfortable return, and 15% is a "
                        "strongly attractive one. Change the target and every price level moves with it."),
        "nearby_prices": nearby, "assumption_sensitivity": sens,
    }


def _build_reverse_dcf(price, eps_ttm, current_pe, exit_base, div_yield, g_hist, growth_source, g_base) -> dict:
    """Reverse DCF / expectations investing (Mauboussin & Rappaport): instead of forecasting,
    start from today's price and solve for what it ALREADY assumes. The needed EPS growth is
    the g that makes buying at today's price earn the target return:
        price = EPS x (1+g)^5 x exit P/E / (1 + target - dividend yield)^5
    It contains no forecast, so it cannot be 'wrong' the way a projection can — the user
    judges for themselves whether that growth is plausible against the company's record."""
    def needed(target, exit_pe):
        return (price / (eps_ttm * exit_pe)) ** (1 / HORIZON_YEARS) * (1 + target - div_yield) - 1

    cases = [("pe_holds", f"the P/E stays at today's {current_pe:.1f}x", current_pe)]
    if abs(exit_base - current_pe) > 0.05:
        cases.append(("pe_base", f"the P/E moves to {exit_base:.1f}x (the base-case assumption)", exit_base))
    grid = [{"case": key, "label": label, "exit_pe": _r(pe),
             "needed_growth_pct": {str(int(t * 100)): _r(needed(t, pe) * 100)
                                    for t in (TARGET_UPPER_LIMIT, TARGET_FAIR_START, TARGET_ACCUMULATE)}}
            for key, label, pe in cases]

    g_need = needed(TARGET_FAIR_START, exit_base) * 100
    g_hist_pct = g_hist * 100 if g_hist is not None else None
    if g_need <= 0:
        level = "undemanding"
        text = (f"Buying at today's price would still earn 12% a year even if earnings shrank by {abs(g_need):.1f}% a year "
                f"(with the P/E at {exit_base:.1f}x). The price is low relative to earnings — worth asking why the market expects so little.")
    elif g_hist_pct is None:
        level = "unknown"
        text = f"To earn 12% a year from today's price, earnings must grow about {g_need:.1f}% a year for 5 years (P/E at {exit_base:.1f}x). The company has no usable growth record to compare against."
    else:
        gap = g_need - g_hist_pct
        if gap > 3:
            level = "demanding"
            text = (f"To earn 12% a year from today's price, earnings must grow about {g_need:.1f}% a year for 5 years (P/E at {exit_base:.1f}x) — "
                    f"more than the {g_hist_pct:.1f}% the company achieved ({growth_source}). The price already assumes better than its record.")
        elif gap < -3:
            level = "undemanding"
            text = (f"To earn 12% a year from today's price, earnings need to grow only about {g_need:.1f}% a year for 5 years (P/E at {exit_base:.1f}x) — "
                    f"less than the {g_hist_pct:.1f}% the company achieved ({growth_source}). The price does not need its past growth to continue in full.")
        else:
            level = "in_line"
            text = (f"To earn 12% a year from today's price, earnings must grow about {g_need:.1f}% a year for 5 years (P/E at {exit_base:.1f}x) — "
                    f"about what the company achieved ({g_hist_pct:.1f}%, {growth_source}). The price assumes its record repeats, leaving little room for slowing.")
    if g_need > 25:
        text += " That is above the 25% ceiling this model uses even for its bull case."
    return {
        "target_pct": 12, "exit_pe_used": _r(exit_base), "needed_growth_pct": _r(g_need),
        "historical_growth_pct": _r(g_hist_pct) if g_hist_pct is not None else None, "historical_source": growth_source,
        "model_base_growth_pct": _r(g_base * 100), "level": level, "text": text, "grid": grid,
        "caution": "Long-run earnings growth persists only weakly (Chan, Karceski & Lakonishok, 2003), so a company's own record is a generous yardstick, not a forecast.",
    }


FCF_REQUIRED_RETURN = 0.12
FCF_GROWTH_CAP = 0.06
INDUSTRY_PE_BAND = 0.15


def _build_fair_value(price, eps_ttm, ladder, hist_pe, industry_pe, industry_pe_weak, g_base, fcf_per_share, div_yield) -> dict:
    """What is this stock worth today? Answered four independent ways, each a
    zone (low - mid - high). One method is one opinion; when several
    unrelated methods land in the same place that is worth something, and when
    they scatter the honest answer is 'nobody knows within a wide range'.

      1. Growth-return model  - the prices at which our own 5-year model earns 15% / 12% / 9% a year.
      2. Own-history P/E      - trailing EPS x the stock's own 25th / median / 75th percentile P/E.
      3. Industry P/E         - trailing EPS x the industry P/E, +/-15% (a real industry P/E only).
      4. Cash-flow value      - 3-year average free cash flow per share, capitalised at a 12% required
                                return (11-13%), growth capped at 6%. Not used for banks/financials.
    The zone that summarises them is the median of the methods' lows, mids and highs, so one wild
    method cannot drag it."""
    methods = []

    def add(key, label, low, mid, high, basis):
        if None in (low, mid, high) or not (0 < low <= mid <= high):
            return
        methods.append({"key": key, "label": label, "low": _r(low, 0), "mid": _r(mid, 0), "high": _r(high, 0), "basis": basis,
                        "price_vs_zone": "above" if price > high else "below" if price < low else "inside"})

    by_target = {l["target_return_pct_pa"]: l["price"] for l in (ladder or [])}
    if all(k in by_target for k in (15.0, 12.0, 9.0)):
        add("growth_model", "Growth-return model", by_target[15.0], by_target[12.0], by_target[9.0],
            "prices at which the base case earns 15% / 12% / 9% a year over 5 years")
    if hist_pe and hist_pe.get("available"):
        add("own_history_pe", "Own-history P/E", eps_ttm * hist_pe["p25"], eps_ttm * hist_pe["median"], eps_ttm * hist_pe["p75"],
            f"trailing EPS Rs{eps_ttm:.1f} x its own P/E history (25th {hist_pe['p25']}, median {hist_pe['median']}, 75th {hist_pe['p75']})")
    if industry_pe and not industry_pe_weak:
        add("industry_pe", "Industry P/E", eps_ttm * industry_pe * (1 - INDUSTRY_PE_BAND), eps_ttm * industry_pe, eps_ttm * industry_pe * (1 + INDUSTRY_PE_BAND),
            f"trailing EPS Rs{eps_ttm:.1f} x industry P/E {industry_pe:.1f}, +/-{int(INDUSTRY_PE_BAND * 100)}%")
    if fcf_per_share and fcf_per_share > 0:
        g = _clamp(g_base, 0.0, FCF_GROWTH_CAP)

        def cap(r):
            return fcf_per_share * (1 + g) / (r - g)
        add("cash_flow", "Cash-flow value", cap(FCF_REQUIRED_RETURN + 0.01), cap(FCF_REQUIRED_RETURN), cap(FCF_REQUIRED_RETURN - 0.01),
            f"3-year average free cash flow Rs{fcf_per_share:.1f} a share, growing {g * 100:.0f}% a year, valued at a 12% required return (11-13%)")

    if len(methods) < 2:
        return {"available": False, "reason": "fewer than two independent valuation methods have enough data for this company"}

    mids = [m["mid"] for m in methods]
    centre = _median(mids)
    zone_low, zone_high = _median([m["low"] for m in methods]), _median([m["high"] for m in methods])
    spread = (max(mids) - min(mids)) / centre * 100
    n = len(methods)
    agreement = "limited" if n < 3 else "strong" if spread <= 25 else "moderate" if spread <= 50 else "weak"
    counts = {k: sum(1 for m in methods if m["price_vs_zone"] == k) for k in ("above", "inside", "below")}
    premium = (price - centre) / centre * 100
    where = "above" if price > zone_high else "below" if price < zone_low else "inside"
    verdict = {"above": "above the fair-value zone", "inside": "inside the fair-value zone", "below": "below the fair-value zone"}[where]
    agree_text = {"strong": "The methods largely agree, so the zone is reasonably firm.",
                  "moderate": "The methods only partly agree, so treat the zone as a wide range, not a point.",
                  "weak": "The methods disagree widely - nobody can say precisely what this stock is worth; do not lean on a single number.",
                  "limited": "Only two methods had enough data, so this is a thin cross-check."}[agreement]
    text = (f"Today's price of Rs{price:,.0f} is {verdict} (Rs{zone_low:,.0f} to Rs{zone_high:,.0f}, middle Rs{centre:,.0f}; the price is "
            f"{abs(premium):.0f}% {'above' if premium >= 0 else 'below'} the middle). {counts['above']} of {n} methods put the price above their fair range, "
            f"{counts['inside']} inside, {counts['below']} below. {agree_text}")
    return {"available": True, "methods": methods, "zone_low": _r(zone_low, 0), "zone_mid": _r(centre, 0), "zone_high": _r(zone_high, 0),
            "price_position": where, "price_vs_mid_pct": _r(premium), "spread_of_methods_pct": _r(spread, 0), "agreement": agreement,
            "methods_above": counts["above"], "methods_inside": counts["inside"], "methods_below": counts["below"], "method_count": n,
            "text": text,
            "caution": "Every method here is built from the past (trailing earnings, past multiples, past cash flow). Agreement between them "
                       "shows the past tells a consistent story; it does not make the future certain."}


def build_valuation_model(price, eps_ttm, industry_pe, hist_pe, growth_hist_pct, growth_source,
                          div_yield_pct, quality_flags, reliability=None, industry_pe_weak=False, fcf_per_share=None) -> dict:
    if not price or not eps_ttm or eps_ttm <= 0:
        return {"available": False, "reason_code": "no_positive_earnings",
                "reason": "This company has no positive trailing earnings (it is loss-making, or earnings data is missing), so a P/E-based price-vs-value analysis is not meaningful."}

    current_pe = price / eps_ttm
    div_yield = (div_yield_pct or 0) / 100

    g_hist = growth_hist_pct / 100 if growth_hist_pct is not None else None
    notes = []
    if g_hist is None:
        g_hist, growth_source = 0.10, "generic default (no usable earnings history)"
        notes.append("No usable earnings history, so a generic 10% growth was assumed as the starting point — treat this model as low reliability.")
    if g_hist < 0:
        g_bear, g_base, g_bull = -0.02, 0.02, 0.06
        notes.append("Historical EPS growth is negative; scenarios assume at best a modest recovery.")
    else:
        g_bear = _clamp(0.30 * g_hist, 0.0, 0.07)
        g_base = _clamp(0.75 * g_hist, 0.02, 0.18)
        g_bull = _clamp(1.00 * g_hist, g_base + 0.02, 0.25)

    hist_median = hist_pe.get("median") if hist_pe and hist_pe.get("available") else None
    # a median over a handful of BSE peers is too noisy to average in next to the stock's own
    # history (it swung one steel company's anchor from 15x to 34x) — it is the fallback only
    anchors = [x for x in (hist_median, industry_pe if (not industry_pe_weak or hist_median is None) else None) if x]
    anchor_pe = sum(anchors) / len(anchors) if anchors else None
    if anchor_pe is None:
        anchor_pe = current_pe
        notes.append("No P/E history or industry P/E available, so the multiple is assumed to stay where it is today.")

    p25 = hist_pe.get("p25") if hist_pe and hist_pe.get("available") else None
    exit_base = anchor_pe if anchor_pe <= current_pe else current_pe + 0.5 * (anchor_pe - current_pe)
    exit_bull = max(current_pe, anchor_pe)
    exit_bear = 0.85 * min(x for x in (current_pe, anchor_pe, p25) if x)

    scenarios = {
        "bear": _scenario(eps_ttm, price, g_bear, exit_bear, div_yield),
        "base": _scenario(eps_ttm, price, g_base, exit_base, div_yield),
        "bull": _scenario(eps_ttm, price, g_bull, exit_bull, div_yield),
    }
    v_bear, v_base, v_bull = (scenarios[k]["value_in_5y"] for k in ("bear", "base", "bull"))

    def level(name, target):
        p = _price_for_return(v_base, target, div_yield)
        if p is None:
            return None
        return {"name": name, "target_return_pct_pa": _r(target * 100, 0), "price": _r(p, 0),
                "vs_current_pct": _r((p - price) / price * 100),
                "return_if_bought_here_pct_pa": {"bear": _return_at_price(v_bear, p, div_yield),
                                                   "base": _return_at_price(v_base, p, div_yield),
                                                   "bull": _return_at_price(v_bull, p, div_yield)}}

    ladder = [x for x in (level("Accumulate zone (base case earns 15% a year)", TARGET_ACCUMULATE),
                          level("Fair entry (base case earns 12% a year)", TARGET_FAIR_START),
                          level("Upper limit (base case earns only 9% a year)", TARGET_UPPER_LIMIT)) if x]
    bear_safe = _price_for_return(v_bear, BEAR_FLOOR_RETURN, div_yield)

    e_base = scenarios["base"]["total_return_pct_pa"] / 100
    e_bear = scenarios["bear"]["total_return_pct_pa"] / 100
    if e_base >= TARGET_ACCUMULATE and e_bear >= BEAR_FLOOR_RETURN:
        stance = 0
    elif e_base >= TARGET_FAIR_START:
        stance = 1
    elif e_base >= TARGET_UPPER_LIMIT:
        stance = 2
    else:
        stance = 3
    downgraded_for = []
    if quality_flags.get("weak_piotroski"):
        downgraded_for.append("weak Piotroski F-Score")
    if quality_flags.get("altman_distress"):
        downgraded_for.append("Altman Z-Score in the distress zone")
    if g_hist < 0:
        downgraded_for.append("shrinking historical EPS")
    if downgraded_for:
        stance = min(3, stance + 1)
    capped_for_reliability = False
    if reliability and reliability.get("level") == "low" and stance < 2:
        stance, capped_for_reliability = 2, True

    industry_used = bool(industry_pe) and (not industry_pe_weak or hist_median is None)
    price_derivation = _build_price_derivation(price, eps_ttm, g_base, g_hist, growth_source, scenarios, ladder,
                                               bear_safe, div_yield, hist_median, industry_pe, industry_used, anchor_pe,
                                               current_pe, exit_base, v_base, v_bear, v_bull)
    reverse_dcf = _build_reverse_dcf(price, eps_ttm, current_pe, exit_base, div_yield, g_hist, growth_source, g_base)
    fair_value = _build_fair_value(price, eps_ttm, ladder, hist_pe, industry_pe, industry_pe_weak, g_base, fcf_per_share, div_yield)
    return {
        "available": True,
        "reliability": reliability,
        "reverse_dcf": reverse_dcf,
        "fair_value": fair_value,
        "price_derivation": price_derivation,
        "stance_capped_for_low_reliability": capped_for_reliability,
        "horizon_years": HORIZON_YEARS,
        "current_price": _r(price, 2), "eps_ttm": _r(eps_ttm, 2), "current_pe": _r(current_pe),
        "industry_pe": _r(industry_pe), "own_history_median_pe": _r(hist_pe.get("median")) if hist_pe and hist_pe.get("available") else None,
        "anchor_pe": _r(anchor_pe), "dividend_yield_pct": _r(div_yield_pct, 2),
        "growth_basis": {"historical_eps_growth_pct": _r(g_hist * 100), "source": growth_source},
        "scenarios": scenarios,
        "entry_ladder": ladder,
        "bear_case_safe_price": {"price": _r(bear_safe, 0), "meaning": "price at which even the bear scenario still earns ~6% a year"} if bear_safe else None,
        "baseline_stance": STANCES[stance],
        "baseline_stance_label": STANCE_LABELS[STANCES[stance]],
        "stance_downgraded_for": downgraded_for,
        "tranche_plan": _tranche_plan(STANCES[stance], price, ladder, bear_safe),
        "assumptions": [
            f"{HORIZON_YEARS}-year horizon; returns are annualised and include the current dividend yield (approximate).",
            "Growth paths are haircuts of the company's own historical EPS growth (bear ≈30%, base ≈75%, bull ≈100%).",
            "Exit P/E anchors on the stock's own multi-year P/E history and its industry P/E; only half of any upside re-rating is credited in the base case, all of any downside re-rating.",
            *notes,
        ],
    }


def redact_for_low_reliability(model: dict):
    """For a company whose inputs are too unreliable to support a valuation,
    NO price level or return projection may reach the user or the AI — a
    'fair entry of Rs X' built on erratic earnings looks authoritative and is
    not. Returns (public_model, withheld_prices): the model stripped to what is
    safe to show (reliability + the plain facts), and the set of model-derived
    price numbers (ladder, floor, tranches, projected 5-year values) so that any
    stray mention in generated text can be scrubbed. The current price is never
    withheld — it is a fact, not a model output."""
    current = (model or {}).get("current_price")
    withheld = set()

    def add(v):
        if isinstance(v, (int, float)) and not (current is not None and abs(v - current) < 0.6):
            withheld.add(round(float(v), 2))

    for lvl in (model or {}).get("entry_ladder") or []:
        add(lvl.get("price"))
    add(((model or {}).get("bear_case_safe_price") or {}).get("price"))
    for t in (model or {}).get("tranche_plan") or []:
        add(t.get("price"))
    for sc in ((model or {}).get("scenarios") or {}).values():
        add(sc.get("value_in_5y"))
    fv = (model or {}).get("fair_value") or {}
    for k in ("zone_low", "zone_mid", "zone_high"):
        add(fv.get(k))
    for mth in fv.get("methods") or []:
        for k in ("low", "mid", "high"):
            add(mth.get(k))

    public = {
        "available": True, "levels_withheld": True,
        "reliability": (model or {}).get("reliability"),
        "current_price": current, "eps_ttm": (model or {}).get("eps_ttm"), "current_pe": (model or {}).get("current_pe"),
        "growth_basis": (model or {}).get("growth_basis"),
        "note": "Price levels and return projections are withheld: this company's data is too unreliable to support them.",
    }
    return public, withheld


def scrub_withheld_prices(obj, withheld: set):
    """Recursively replace any rupee amount that equals a withheld model price
    (a safety net: the AI is never shown them, so this should rarely fire)."""
    if not withheld:
        return obj
    rx = re.compile(r"(₹|Rs\.?)\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")

    def fix(m):
        try:
            v = float(m.group(2).replace(",", ""))
        except ValueError:
            return m.group(0)
        return "[price level withheld]" if any(abs(v - w) < 0.6 for w in withheld) else m.group(0)

    if isinstance(obj, str):
        return rx.sub(fix, obj)
    if isinstance(obj, list):
        return [scrub_withheld_prices(x, withheld) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub_withheld_prices(v, withheld) for k, v in obj.items()}
    return obj


def _tranche_plan(stance: str, price: float, ladder: list, bear_safe: Optional[float]) -> list:
    """Staggered entry anchored on the model's own price levels that sit
    BELOW today's price (a level above today's price is one the stock has
    already passed, so it can't be a future entry trigger). Falls back to a
    plain percentage dip only when the model gives no level to anchor on."""
    levels = sorted({x for x in [l["price"] for l in ladder] + [bear_safe] if x and x < price * 0.985}, reverse=True)

    def pick(i, fallback_pct, prev):
        p = levels[i] if i < len(levels) else price * (1 - fallback_pct)
        if prev is not None and p >= prev * 0.985:
            p = prev * 0.95
        return _r(p, 0)

    def step(share, when_now, p):
        return {"share_pct": share, "when": "now" if when_now else f"if price falls to ~₹{p:.0f}", "price": _r(price if when_now else p, 0)}

    if stance == "accumulate_now":
        t2 = pick(0, 0.05, None); t3 = pick(1, 0.10, t2)
        return [step(40, True, price), step(30, False, t2), step(30, False, t3)]
    if stance == "start_small_and_stagger":
        t2 = pick(0, 0.05, None); t3 = pick(1, 0.12, t2)
        return [step(25, True, price), step(35, False, t2), step(40, False, t3)]
    if stance == "wait_for_better_price":
        t2 = pick(0, 0.06, None); t3 = pick(1, 0.12, t2)
        return [{"share_pct": 0, "when": "not now", "price": _r(price, 0)}, step(40, False, t2), step(60, False, t3)]
    t = pick(0, 0.20, None)
    return [{"share_pct": 0, "when": "not now", "price": _r(price, 0)},
            {"share_pct": 100, "when": f"re-evaluate only if price falls to ~₹{t:.0f} or the earnings outlook improves", "price": t}]


# ── evidence dossier for the LLM ─────────────────────────────────────────

def _shareholding_trend(holdings: list) -> dict:
    rows = []
    for h in (holdings or [])[-6:]:
        d = h.get("data") or {}
        rows.append({"date": str(h.get("date"))[:10], "promoter_pct": _r(_f(d.get("pmPctT")), 2), "promoter_pledged_pct": _r(_f(d.get("plPctT")), 2),
                     "fii_pct": _r(_f(d.get("fiPctT")), 2), "dii_pct": _r(_f(d.get("diPctT")), 2),
                     "mutual_funds_pct": _r(_f(d.get("mfPctT")), 2), "retail_pct": _r(_f(d.get("rhPctT")), 2)})
    change = None
    if len(rows) >= 2:
        first, last = rows[0], rows[-1]
        change = {k: _r((last[k] or 0) - (first[k] or 0), 2) for k in ("promoter_pct", "fii_pct", "dii_pct", "mutual_funds_pct", "retail_pct")}
    return {"quarters": rows, "change_first_to_last_pct_points": change}


def _recent_announcements(anns: list, limit: int = 14) -> list:
    out = []
    for a in anns or []:
        headline = (a.get("HEADLINE") or a.get("NEWSSUB") or "").strip()
        if not headline or _NOISE_ANNOUNCEMENT_RE.search(headline) or _NOISE_ANNOUNCEMENT_RE.search(a.get("NEWSSUB") or ""):
            continue
        out.append({"date": str(a.get("NEWS_DT"))[:10], "category": a.get("CATEGORYNAME"), "headline": headline[:220]})
        if len(out) >= limit:
            break
    return out


def _fcf_per_share(profile: dict) -> Optional[float]:
    """Average free cash flow per share over the last 3 fiscal years. Shares are not in the
    statements, so they are recovered as net income / EPS for each year (both in the same unit)."""
    vals = []
    for h in (profile or {}).get("history_last_6_fy", [])[-3:]:
        ni, eps, fcf = h.get("net_income"), h.get("eps"), h.get("free_cash_flow")
        if ni and eps and fcf is not None and eps > 0 and ni > 0:
            vals.append(fcf / (ni / eps))
    return sum(vals) / len(vals) if len(vals) >= 2 else None


def compute_all(overview: dict, financials: dict, shareholding: dict, price_summary: dict,
                tt_info: dict, price_hist: list, announcements: list, peers: list) -> dict:
    """Returns {"model": ..., "dossier": ...}. `model` is shown to the user as
    numbers; `dossier` is the compact evidence pack handed to the LLM."""
    statements = (financials or {}).get("statements_10yr") or {}
    ratios = (tt_info or {}).get("ratios") or {}
    price = _f(((overview or {}).get("quote") or {}).get("ltp")) or _f(ratios.get("lastPrice"))

    inc_rows = _real_fiscal_years(statements.get("income_statement_annual", []))
    ttm = next((r for r in statements.get("income_statement_annual", []) if r.get("displayPeriod") == "TTM"), None)
    eps_ttm = _f((ttm or {}).get("incEps")) or _f(ratios.get("eps")) or _f(((overview or {}).get("ratios") or {}).get("eps"))
    current_pe = price / eps_ttm if price and eps_ttm and eps_ttm > 0 else None

    profile = fundamentals_profile(statements)
    hist_pe = historical_pe(price_hist, statements, current_pe)

    g_pct, g_src = None, None
    if profile.get("available"):
        for key, label in (("eps_cagr_5y_pct", "5-year EPS CAGR"), ("eps_cagr_3y_pct", "3-year EPS CAGR"),
                           ("revenue_cagr_5y_pct", "5-year revenue CAGR (EPS history unusable)")):
            if profile.get(key) is not None:
                g_pct, g_src = profile[key], label
                break

    industry_pe, industry_pe_source = _f(ratios.get("indpe")), "Tickertape industry P/E"
    if industry_pe is None:
        target = ((overview or {}).get("name") or "").strip().lower()
        symbol = ((overview or {}).get("symbol") or "").strip().upper()
        peer_pes = sorted(x for x in (_f(p.get("PE")) for p in (peers or [])
                                       if str(p.get("Name") or "").strip().upper() != symbol and str(p.get("Name") or "").strip().lower() != target)
                          if x and x > 0)
        if len(peer_pes) >= 3:
            industry_pe = _median(peer_pes)
            industry_pe_source = f"median P/E of {len(peer_pes)} BSE peers (a small comparison set)"
        else:
            industry_pe_source = None

    piotroski = (financials or {}).get("piotroski_f_score") or {}
    altman = (financials or {}).get("altman_z_score") or {}
    # Altman Z was built for manufacturers: a bank's or NBFC's balance sheet
    # is structurally leveraged (deposits/borrowings ARE the business), so
    # it reads "distress" for perfectly healthy lenders — confirmed on HDFC
    # Bank. Not used as a quality flag for financials.
    sector_text = " ".join(str(x or "") for x in (
        ((overview or {}).get("classification") or {}).get("sector"),
        ((overview or {}).get("classification") or {}).get("industry"),
        ((tt_info or {}).get("info") or {}).get("sector"))).lower()
    is_financial = any(k in sector_text for k in ("financ", "bank", "insurance", "nbfc"))
    quality_flags = {
        "weak_piotroski": piotroski.get("score") is not None and piotroski["score"] <= 3,
        "altman_distress": altman.get("zone") == "distress" and not is_financial,
    }

    if not profile.get("available"):
        model = {"available": False, "reason_code": "no_multi_year_data",
                 "reason": "No multi-year financial statements are available for this company from our data providers (typical of very small and SME-listed stocks, and of newly listed companies that have not yet built a reporting history), so a price-vs-value analysis cannot be built. The ratios, latest results, shareholding and peer table above are still shown."}
        return {"model": model, "dossier": None}
    industry_pe_weak = bool(industry_pe_source and "BSE peers" in industry_pe_source)
    reliability = assess_reliability(inc_rows, eps_ttm, g_src, hist_pe, industry_pe, industry_pe_weak)
    fresh = (financials or {}).get("data_freshness") or {}
    if fresh.get("statements_source") == "yahoo":
        reliability["reasons"].append("financials come from a backup source (Yahoo Finance): about 4 fiscal years, not cross-checked against filings")
        if reliability["level"] == "high":
            reliability["level"] = "medium"
    if industry_pe_source and "BSE peers" in industry_pe_source:
        reliability["reasons"].append(f"industry P/E is the {industry_pe_source}")
    if fresh.get("status") == "tickertape_behind":
        reliability["reasons"].append(
            f"the statements behind growth, trailing EPS and valuation only run through {fresh.get('financials_through')} "
            f"while BSE already shows {fresh.get('bse_latest_quarter')} — one quarter out of date")
        if reliability["level"] == "high":
            reliability["level"] = "medium"
    fcf_ps = None if is_financial else _fcf_per_share(profile)
    model = build_valuation_model(price, eps_ttm, industry_pe, hist_pe, g_pct, g_src,
                                  _f(ratios.get("divYield")), quality_flags, reliability, industry_pe_weak, fcf_ps)

    if model.get("available"):
        levels = [(l["name"], l["price"]) for l in model.get("entry_ladder") or []]
        if model.get("bear_case_safe_price"):
            levels.append(("Bear-case safe price", model["bear_case_safe_price"]["price"]))
        fvz = model.get("fair_value") or {}
        if fvz.get("available"):
            levels.append(("Bottom of the fair-value zone", fvz["zone_low"]))
        try:
            model["waiting_evidence"] = timing.build_waiting_evidence(price_hist, price, levels)
        except Exception:
            model["waiting_evidence"] = {"available": False, "reason": "could not be computed"}

    failed_checks = [k for k, v in (piotroski.get("checks") or {}).items() if v is False]
    high, low = _f(ratios.get("52wHigh")), _f(ratios.get("52wLow"))
    dossier = {
        "company": {
            "name": (overview or {}).get("name"), "symbol": (overview or {}).get("symbol"),
            "sector": ((overview or {}).get("classification") or {}).get("sector"),
            "sub_sector": ((tt_info or {}).get("info") or {}).get("sector"),
            "business": (((tt_info or {}).get("info") or {}).get("description") or "")[:500],
            "market_cap_class": ratios.get("marketCapLabel"), "beta": _r(_f(ratios.get("beta")), 2),
        },
        "price": {
            "current": _r(price, 2), "week52_high": high, "week52_low": low,
            "pct_below_52w_high": _r((price - high) / high * 100) if price and high else None,
            "pct_above_52w_low": _r((price - low) / low * 100) if price and low else None,
            "momentum_1m_3m_6m_12m_pct": [price_summary.get(k) for k in ("pct_change_1m", "pct_change_3m", "pct_change_6m", "pct_change_12m")] if price_summary else None,
        },
        "valuation_snapshot": {
            "pe_ttm": _r(current_pe), "industry_pe": _r(industry_pe), "industry_pe_source": industry_pe_source,
            "pe_premium_to_industry_pct": _r((current_pe / industry_pe - 1) * 100) if current_pe and industry_pe else None,
            "pb": _r(_f(ratios.get("pb")), 2), "industry_pb": _r(_f(ratios.get("indpb")), 2),
            "dividend_yield_pct": _r(_f(ratios.get("divYield")), 2), "industry_dividend_yield_pct": _r(_f(ratios.get("inddy")), 2),
            "own_history_pe": hist_pe,
            "bse_peers": [{"name": p.get("Name"), "pe": p.get("PE"), "opm": p.get("OPM"), "npm": p.get("NPM")} for p in (peers or [])][:6],
        },
        "fundamentals": profile,
        "quality_scores": {"piotroski": piotroski.get("score"), "piotroski_failed_checks": failed_checks,
                            "altman_z": _r(_f(altman.get("z_score")), 2), "altman_zone": altman.get("zone"),
                            "note": "Altman Z-Score is not meaningful for banks/financial companies (structural leverage) — ignore it for this company." if is_financial else None},
        "shareholding_trend": _shareholding_trend((shareholding or {}).get("quarterly_trend")),
        "recent_corporate_announcements": _recent_announcements(announcements),
        "data_freshness": {k: fresh.get(k) for k in ("financials_through", "bse_latest_quarter", "status", "message", "statements_source")},
        "valuation_model": {k: v for k, v in model.items() if k != "price_derivation"},
    }
    return {"model": model, "dossier": dossier}
