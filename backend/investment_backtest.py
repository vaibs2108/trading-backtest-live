"""
investment_backtest.py — does the price-vs-value model actually work?

Run offline (never on a request):   python investment_backtest.py

What it does. For a universe of large Indian companies and a grid of past
dates, it rebuilds the model using ONLY what was knowable on each date —
annual results published at least 75 days earlier, prices up to that day —
then looks at what the stock actually returned afterwards (price + dividends,
annualised, over 1 / 2 / 3 / 5 years wherever the data allows). It asks:

  1. Rank test — did the stocks the model liked go on to beat the ones it
     didn't? (Spearman rank correlation per date, averaged.) Compared with a
     trivial rule, "buy the lowest P/E", so the model has to earn its keep.
  2. Calibration — of the cases where the base case promised >=15%, 12-15%,
     9-12%, <9% a year, what did they actually earn, and how often did they
     reach that target?
  3. Stance / fair-value / reverse-DCF buckets — does 'accumulate now' really
     do better than 'avoid'; does 'below the fair-value zone' beat 'above'?
  4. Waiting — across the whole universe and every starting week since ~2004,
     how often do 5/10/20/30% dips happen, and did waiting for one beat
     buying at once?

Honest limits, all written into the output:
  * Statements from our provider begin at FY2017, so the earliest usable date
    is mid-2020 and 5-year outcomes exist only for entries up to about
    mid-2021: the sample is essentially one market regime (post-COVID).
  * The universe is today's large companies (survivorship bias flatters
    absolute returns; comparisons WITHIN a date are less affected).
  * Trailing EPS is the latest published fiscal year (up to a year stale),
    and no industry P/E is available historically, so the exit multiple
    anchors on the stock's own P/E history only. That is a harder test than
    the live model faces, not an easier one.
  * Overlapping dates and a modest number of stocks: results are indicative,
    not proof.
"""
import bisect
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import investment_timing as timing
import investment_valuation as V
import tickertape_client as tt
from investment_scores import _real_fiscal_years

OUT_PATH = Path(__file__).parent / "data" / "investment" / "backtest_results.json"
REPORTING_LAG_DAYS = 75
HORIZONS = (1, 2, 3, 5)

UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "BAJFINANCE", "BAJAJFINSV",
    "WIPRO", "HCLTECH", "TECHM", "POWERGRID", "NTPC", "ONGC", "COALINDIA", "TATASTEEL", "JSWSTEEL", "M&M",
    "TATAMOTORS", "ADANIPORTS", "GRASIM", "HINDALCO", "DRREDDY", "CIPLA", "DIVISLAB", "EICHERMOT", "HEROMOTOCO", "BRITANNIA",
    "APOLLOHOSP", "INDUSINDBK", "BPCL", "SBILIFE", "HDFCLIFE", "TATACONSUMER", "BAJAJ-AUTO", "SHREECEM", "PIDILITIND", "DABUR",
    "GODREJCP", "HAVELLS", "SIEMENS", "AMBUJACEM", "BANKBARODA", "IOC", "GAIL", "DLF", "TATAPOWER", "MARICO",
    "COLPAL", "BERGEPAINT", "MUTHOOTFIN", "CHOLAFIN", "TVSMOTOR", "PERSISTENT", "LUPIN", "AUROPHARMA", "TORNTPHARM", "ABB",
]


# ── data ────────────────────────────────────────────────────────────────

def _d(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def load_stock(symbol: str):
    m = tt.find_sid(symbol)
    if not m:
        return None
    st = tt.get_financial_statements(m["slug"])
    ph = tt.get_price_history(m["sid"], "max")
    info = (tt.get_info(m["sid"]) or {}).get("info") or {}
    if not st or not ph:
        return None
    sector = str(info.get("sector") or "").lower()
    pts = sorted((d, float(p["lp"])) for p in ph for d in [_d(p.get("ts"))] if d and p.get("lp"))
    dps = {}
    for r in st.get("income_statement_annual", []):
        end = _d(r.get("endDate"))
        if end and r.get("displayPeriod") != "TTM" and V._f(r.get("incDps")) is not None:
            dps[end] = V._f(r.get("incDps"))
    return {"symbol": symbol, "name": m.get("name"), "statements": st, "prices": pts, "dates": [d for d, _ in pts],
            "is_financial": any(k in sector for k in ("financ", "bank", "insurance", "nbfc")), "dps": dps}


def price_on(stock, t: date, max_gap_days=10):
    i = bisect.bisect_right(stock["dates"], t) - 1
    if i < 0 or (t - stock["dates"][i]).days > max_gap_days:
        return None
    return stock["prices"][i][1]


def forward_return(stock, t: date, years: int):
    """Annualised total return (price + dividends of the fiscal years ending inside the window,
    shifted 4 months for the payment lag) over `years` from t; None if the window runs past our data."""
    end = t + timedelta(days=round(365.25 * years))
    if end > stock["dates"][-1]:
        return None
    p0, p1 = price_on(stock, t), price_on(stock, end)
    if not p0 or not p1:
        return None
    lo, hi = t - timedelta(days=122), end - timedelta(days=122)
    divs = sum(v for d, v in stock["dps"].items() if lo < d <= hi)
    total = (p1 + divs) / p0
    return (total ** (1 / years) - 1) * 100 if total > 0 else None


# ── the model as it would have been on date t ───────────────────────────

def asof_statements(st, t: date):
    cutoff = t - timedelta(days=REPORTING_LAG_DAYS)

    def keep(rows):
        return [r for r in rows if r.get("displayPeriod") != "TTM" and _d(r.get("endDate")) and _d(r.get("endDate")) <= cutoff]
    return {"income_statement_annual": keep(st.get("income_statement_annual", [])),
            "balance_sheet_annual": keep(st.get("balance_sheet_annual", [])),
            "cash_flow_annual": keep(st.get("cash_flow_annual", [])),
            "income_statement_quarterly": []}


def model_asof(stock, t: date):
    price = price_on(stock, t)
    if not price:
        return None
    tr = asof_statements(stock["statements"], t)
    prof = V.fundamentals_profile(tr)
    if not prof.get("available"):
        return None
    inc = _real_fiscal_years(tr["income_statement_annual"])
    eps = V._f(inc[-1].get("incEps")) if inc else None
    if not eps or eps <= 0:
        return None
    g_pct = g_src = None
    for key, label in (("eps_cagr_5y_pct", "5-year EPS CAGR"), ("eps_cagr_3y_pct", "3-year EPS CAGR")):
        if prof.get(key) is not None:
            g_pct, g_src = prof[key], label
            break
    if g_pct is None:
        return None
    ph = [{"ts": d.isoformat(), "lp": p} for d, p in stock["prices"] if d <= t]
    hist = V.historical_pe(ph, tr, price / eps)
    if not hist.get("available"):
        return None
    rel = V.assess_reliability(inc, eps, g_src, hist, None)
    if rel["level"] == "low":
        return None                      # the live model withholds levels for these, so there is nothing to test
    dps = prof["history_last_6_fy"][-1].get("dps") or 0
    fcfps = None if stock["is_financial"] else V._fcf_per_share(prof)
    m = V.build_valuation_model(price, eps, None, hist, g_pct, g_src, dps / price * 100, {}, rel, False, fcfps)
    if not m.get("available"):
        return None
    fv = m.get("fair_value") or {}
    return {
        "price": price, "eps": eps, "pe": price / eps, "pe_percentile": hist.get("current_percentile_of_history"),
        "e_base": m["scenarios"]["base"]["total_return_pct_pa"], "stance": m["baseline_stance"],
        "fv_available": bool(fv.get("available")), "fv_premium_pct": fv.get("price_vs_mid_pct"), "fv_position": fv.get("price_position"),
        "fv_agreement": fv.get("agreement"), "fv_methods_above": fv.get("methods_above"), "fv_method_count": fv.get("method_count"),
        "rdcf_level": (m.get("reverse_dcf") or {}).get("level"), "rdcf_needed": (m.get("reverse_dcf") or {}).get("needed_growth_pct"),
        "past_growth_pct": g_pct, "rerating_ratio": m["anchor_pe"] / (price / eps) if m.get("anchor_pe") else None,
    }


def build_observations(stocks, as_of_dates):
    obs = []
    for s in stocks:
        for t in as_of_dates:
            m = model_asof(s, t)
            if not m:
                continue
            m.update(symbol=s["symbol"], date=t.isoformat(), **{f"ret_{h}y": forward_return(s, t, h) for h in HORIZONS})
            obs.append(m)
    return obs


# ── statistics ──────────────────────────────────────────────────────────

def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 8:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx, vy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return cov / math.sqrt(vx * vy) if vx and vy else None


def mean_ic(obs, score_fn, ret_key):
    by_date = {}
    for o in obs:
        sc, rt = score_fn(o), o.get(ret_key)
        if sc is not None and rt is not None:
            by_date.setdefault(o["date"], []).append((sc, rt))
    ics = [ic for ic in (spearman([a for a, _ in v], [b for _, b in v]) for v in by_date.values()) if ic is not None]
    if not ics:
        return None
    m = sum(ics) / len(ics)
    return {"mean_ic": round(m, 3), "dates": len(ics), "share_of_dates_positive_pct": round(sum(1 for i in ics if i > 0) / len(ics) * 100)}


def _avg(xs):
    return sum(xs) / len(xs) if xs else None


def bucket_table(obs, key_fn, order, ret_key, target_fn=None):
    """For each bucket: n, mean and median realised annualised return, share beating the median stock
    of the same date, and (optionally) share reaching that bucket's target."""
    med_by_date = {}
    for o in obs:
        if o.get(ret_key) is not None:
            med_by_date.setdefault(o["date"], []).append(o[ret_key])
    med_by_date = {d: sorted(v)[len(v) // 2] for d, v in med_by_date.items()}
    rows = []
    for b in order:
        rs = [(o[ret_key], o["date"]) for o in obs if key_fn(o) == b and o.get(ret_key) is not None]
        if not rs:
            continue
        vals = sorted(r for r, _ in rs)
        row = {"bucket": b, "n": len(rs), "mean_return_pct": round(_avg(vals), 1), "median_return_pct": round(vals[len(vals) // 2], 1),
               "beat_median_stock_pct": round(sum(1 for r, d in rs if r > med_by_date[d]) / len(rs) * 100)}
        tgt = target_fn(b) if target_fn else None
        if tgt is not None:
            row["reached_target_pct"] = round(sum(1 for r, _ in rs if r >= tgt) / len(rs) * 100)
            row["target_pct"] = tgt
        rows.append(row)
    return rows


def e_bucket(o):
    e = o["e_base"]
    return "15%+" if e >= 15 else "12-15%" if e >= 12 else "9-12%" if e >= 9 else "under 9%"


E_TARGET = {"15%+": 15, "12-15%": 12, "9-12%": 9, "under 9%": 9}


def analyse(obs):
    out = {}
    for h in HORIZONS:
        key = f"ret_{h}y"
        have = [o for o in obs if o.get(key) is not None]
        if len(have) < 40:
            continue
        allr = sorted(o[key] for o in have)
        out[f"{h}y"] = {
            "observations": len(have), "dates": len({o["date"] for o in have}), "stocks": len({o["symbol"] for o in have}),
            "all_stocks_mean_return_pct": round(_avg(allr), 1), "all_stocks_median_return_pct": round(allr[len(allr) // 2], 1),
            "rank_test": {
                "model_expected_return": mean_ic(have, lambda o: o["e_base"], key),
                "fair_value_discount": mean_ic(have, lambda o: -o["fv_premium_pct"] if o["fv_premium_pct"] is not None else None, key),
                "reverse_dcf_easier_is_better": mean_ic(have, lambda o: -o["rdcf_needed"] if o["rdcf_needed"] is not None else None, key),
                "past_eps_growth_alone": mean_ic(have, lambda o: o["past_growth_pct"], key),
                "rerating_to_own_median_pe_alone": mean_ic(have, lambda o: o["rerating_ratio"], key),
                "baseline_low_pe": mean_ic(have, lambda o: -o["pe"], key),
                "baseline_low_pe_vs_own_history": mean_ic(have, lambda o: -o["pe_percentile"] if o["pe_percentile"] is not None else None, key),
            },
            "by_expected_return": bucket_table(have, e_bucket, ["15%+", "12-15%", "9-12%", "under 9%"], key, E_TARGET.get),
            "by_stance": bucket_table(have, lambda o: o["stance"], V.STANCES, key),
            "by_fair_value_position": bucket_table([o for o in have if o["fv_available"]], lambda o: o["fv_position"], ["below", "inside", "above"], key),
            "by_fair_value_agreement": bucket_table([o for o in have if o["fv_available"]], lambda o: o["fv_agreement"], ["strong", "moderate", "weak", "limited"], key),
            "by_reverse_dcf": bucket_table([o for o in have if o["rdcf_level"]], lambda o: o["rdcf_level"], ["undemanding", "in_line", "demanding"], key),
        }
    return out


def waiting_pooled(stocks):
    """Pool the per-stock waiting study across the whole universe (each stock's full weekly history)."""
    acc = {}
    for s in stocks:
        ph = [{"ts": d.isoformat(), "lp": p} for d, p in s["prices"]]
        w = timing.build_waiting_evidence(ph, s["prices"][-1][1], None)
        if not w.get("available"):
            continue
        for r in w["rows"]:
            a = acc.setdefault(r["drop_pct"], {"touch12": [], "rw12": [], "filled": [], "beat": [], "now": [], "wait": [], "missed": []})
            a["touch12"].append(r["history_touch_pct"]["12m"]); a["rw12"].append(r["random_walk_12m_pct"])
            x = r.get("wait_vs_buy_now_2y")
            if x:
                a["filled"].append(x["order_filled_pct"]); a["beat"].append(x["waiting_beat_buying_now_pct"])
                a["now"].append(x["avg_price_return_buy_now_pct"]); a["wait"].append(x["avg_price_return_wait_pct"])
                if x["avg_rise_missed_when_order_never_filled_pct"] is not None:
                    a["missed"].append(x["avg_rise_missed_when_order_never_filled_pct"])
    rows = []
    for drop in sorted(acc):
        a = acc[drop]
        rows.append({"dip_pct": drop, "stocks": len(a["touch12"]),
                     "reached_within_12m_pct": round(_avg(a["touch12"])), "random_walk_says_pct": round(_avg(a["rw12"])),
                     "order_filled_pct": round(_avg(a["filled"])), "waiting_beat_buying_now_pct": round(_avg(a["beat"])),
                     "avg_2y_price_return_buy_now_pct": round(_avg(a["now"]), 1), "avg_2y_price_return_wait_pct": round(_avg(a["wait"]), 1),
                     "avg_rise_missed_when_never_filled_pct": round(_avg(a["missed"]), 1) if a["missed"] else None})
    return rows


def _row(rows, bucket):
    return next((r for r in rows or [] if r["bucket"] == bucket), None)


def plain_findings(res: dict) -> list:
    """The results in plain English, every figure taken from the numbers themselves so the text can
    never drift from the tables. Headline horizon = 3 years (the longest with a useful sample)."""
    h = (res.get("horizons") or {}).get("3y")
    if not h:
        return []
    out = []
    acc, avoid = _row(h["by_stance"], "accumulate_now"), _row(h["by_stance"], "avoid_for_now")
    if acc and avoid:
        out.append(f"In this sample the direction was right. Stocks rated 'accumulate now' went on to beat the typical stock {acc['beat_median_stock_pct']}% of the time over 3 years "
                   f"(average {acc['mean_return_pct']}% a year, {acc['n']} cases); stocks rated 'avoid for now' did so {avoid['beat_median_stock_pct']}% of the time.")
        out.append(f"'Avoid' does not mean 'will fall'. Those stocks still earned {avoid['mean_return_pct']}% a year on average, because the market rose strongly "
                   f"(the average stock returned {h['all_stocks_mean_return_pct']}% a year). Read the rating as relative attractiveness, not a prediction of loss.")
    low = _row(h["by_expected_return"], "under 9%")
    if low:
        out.append(f"The 9% / 12% / 15% levels are not calibrated forecasts. Even the group the model expected to earn under 9% a year earned 9% or more in {low['reached_target_pct']}% of cases. "
                   "Use them as a ladder for comparing entry prices, not as promised returns.")
    below, above = _row(h["by_fair_value_position"], "below"), _row(h["by_fair_value_position"], "above")
    if below and above:
        out.append(f"The fair-value zone separates cheap from dear. Stocks priced below it beat the typical stock {below['beat_median_stock_pct']}% of the time over 3 years; stocks above it, {above['beat_median_stock_pct']}%.")
    rt = h["rank_test"]
    m, pe, g = (rt.get("model_expected_return") or {}).get("mean_ic"), (rt.get("baseline_low_pe") or {}).get("mean_ic"), (rt.get("past_eps_growth_alone") or {}).get("mean_ic")
    if m is not None and pe is not None:
        out.append(f"A weakness, stated plainly: simply ranking stocks by lowest P/E predicted returns better than the full model (rank correlation {pe} versus {m} at 3 years"
                   + (f"; past earnings growth on its own scored {g}, i.e. nothing" if g is not None else "")
                   + "). Most of the model's signal comes from how cheap a stock is against its own history; its growth projection adds little.")
    w = {r["dip_pct"]: r for r in res.get("waiting") or []}
    if 10.0 in w:
        r = w[10.0]
        out.append(f"Waiting for a 10% dip is close to a coin flip. Across {r['stocks']} stocks since about 2004, the dip arrived within a year {r['reached_within_12m_pct']}% of the time and waiting beat buying at once "
                   f"{r['waiting_beat_buying_now_pct']}% of the time. But when the dip never came the stock had usually run away (up {r['avg_rise_missed_when_never_filled_pct']}% on average), so the average 2-year return was "
                   f"{r['avg_2y_price_return_wait_pct']}% when waiting versus {r['avg_2y_price_return_buy_now_pct']}% buying at once (these are stocks that survived to today, which flatters buying early).")
    return out


def track_record_for_ai(res: dict) -> dict:
    """A short, factual digest of the back-test for the AI's dossier, so it can calibrate its confidence
    against how this very model has actually performed instead of presenting its levels as forecasts."""
    h = (res.get("horizons") or {}).get("3y")
    if not h:
        return {}
    pick = lambda rows, b: {k: v for k, v in (_row(rows, b) or {}).items() if k in ("n", "mean_return_pct", "beat_median_stock_pct")}
    w = {r["dip_pct"]: r for r in res.get("waiting") or []}
    rt = h["rank_test"]
    return {
        "what_this_is": f"Back-test of this same model on {res['universe']['stocks']} large Indian companies, entry dates mid-2020 to 2025, using only data known at the time; 3-year outcomes shown",
        "average_stock_return_pct_pa": h["all_stocks_mean_return_pct"],
        "accumulate_now_stances": pick(h["by_stance"], "accumulate_now"), "avoid_for_now_stances": pick(h["by_stance"], "avoid_for_now"),
        "priced_below_fair_value_zone": pick(h["by_fair_value_position"], "below"), "priced_above_fair_value_zone": pick(h["by_fair_value_position"], "above"),
        "rank_correlation_with_returns": {"this_model": (rt.get("model_expected_return") or {}).get("mean_ic"), "plain_lowest_pe_rule": (rt.get("baseline_low_pe") or {}).get("mean_ic"),
                                          "past_eps_growth_alone": (rt.get("past_eps_growth_alone") or {}).get("mean_ic")},
        "waiting_for_10pct_dip": {k: w[10.0][k] for k in ("reached_within_12m_pct", "waiting_beat_buying_now_pct", "avg_2y_price_return_wait_pct", "avg_2y_price_return_buy_now_pct")} if 10.0 in w else None,
        "how_to_use": "The 9/12/15% levels rank stocks; they are not forecasts (every group, even 'under 9%', usually earned more than 9% a year in this rising market). 'Avoid' stocks still earned positive returns. "
                      "A plain low-P/E ranking beat this model's ranking, and past growth alone had no predictive value, so do not lean on the growth projection. Sample: one market regime, survivors only.",
    }


def as_of_grid(first=date(2020, 6, 30), last=date(2025, 9, 30)):
    out, y, m = [], first.year, first.month
    while True:
        # last day of month m
        t = (date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1))
        if t > last:
            break
        out.append(t)
        m += 3
        if m > 12:
            m -= 12
            y += 1
    return out


def run(symbols=None, progress=print):
    stocks, failed = [], []
    for sym in symbols or UNIVERSE:
        try:
            s = load_stock(sym)
        except Exception as e:
            s, _ = None, progress(f"  {sym}: fetch error {e}")
        if s and len(s["prices"]) > 300:
            stocks.append(s)
        else:
            failed.append(sym)
        time.sleep(0.3)
    progress(f"loaded {len(stocks)} stocks; skipped {failed}")
    grid = as_of_grid()
    obs = build_observations(stocks, grid)
    progress(f"{len(obs)} observations over {len(grid)} dates")
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe": {"stocks": len(stocks), "symbols": [s["symbol"] for s in stocks], "skipped": failed},
        "as_of_dates": [grid[0].isoformat(), grid[-1].isoformat()], "observations": len(obs),
        "horizons": analyse(obs), "waiting": waiting_pooled(stocks),
        "limits": [
            "Our statements begin at FY2017, so the earliest testable date is mid-2020 and 5-year outcomes exist only for entries up to about mid-2021 - essentially one market regime (the post-COVID recovery).",
            "The universe is today's large companies. Companies that failed or were delisted are absent, which flatters absolute returns (comparisons within a date are less affected).",
            "Trailing EPS is the latest published fiscal year (up to a year stale) and the exit multiple uses the stock's own P/E history only, since no historical industry P/E is available. The live model has fresher earnings and an industry P/E, so this is a harder test than it faces.",
            "Neighbouring dates share most of their information and there are only about 60 stocks: treat every figure as indicative, not proof.",
        ],
    }
    result["findings"] = plain_findings(result)
    return result, obs


if __name__ == "__main__":
    res, observations = run()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(res, indent=1), encoding="utf-8")
    (OUT_PATH.parent / "backtest_observations.json").write_text(json.dumps(observations), encoding="utf-8")
    print(f"saved {OUT_PATH}")
