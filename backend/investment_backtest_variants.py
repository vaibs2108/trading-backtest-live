"""
investment_backtest_variants.py — would a different scoring rule have worked better?

Run offline:   python investment_backtest_variants.py

The back-test (investment_backtest.py) found that a plain lowest-P/E ranking
predicted returns better than the full model, and that past EPS growth on its
own carried no signal. This script tests the two obvious responses on exactly
the same stocks, dates and outcomes, WITHOUT touching the live model:

  * growth fade — shrink the historical growth rate toward a long-run 10% before
    the model's own haircut (50% fade, 75% fade, no growth at all = 'flat10',
    and a simple cap at 15%);
  * valuation views — lowest P/E, P/E relative to the stock's own history,
    distance to its own median P/E, a fair-value zone WITHOUT the growth-based
    method;
  * blends — average of a growth-based and a valuation-based rank.

Each rule is scored by (a) rank correlation with realised returns per date,
(b) the top-fifth minus bottom-fifth return spread per date, and (c) how often
the top fifth beat the typical stock. Then the robustness checks that guard
against fooling ourselves in one sample: early half versus late half of the
dates, financials versus everyone else, and paired date-by-date comparisons.
Finally a 2x2 table of the model's view against the cheap-versus-own-history
view, to show what each adds when they agree and when they disagree.

Same honest limits as the back-test: one market regime, today's large
companies, ~60 stocks, overlapping dates. Choosing a rule because it won here
is itself a form of overfitting, so the questions asked are "is the gap large,
consistent across horizons, halves and sectors" rather than "which number is
biggest".
"""
import json
import sys
from datetime import date

import investment_backtest as B

OUT = B.OUT_PATH.parent / "backtest_variants.json"
LONG_RUN_GROWTH_PCT = 10.0

GROWTH_RULES = {
    "live": None,
    "fade50": lambda g: 0.5 * g + 0.5 * LONG_RUN_GROWTH_PCT,
    "fade75": lambda g: 0.25 * g + 0.75 * LONG_RUN_GROWTH_PCT,
    "flat10": lambda g: LONG_RUN_GROWTH_PCT,
    "cap15": lambda g: min(g, 15.0),
}

# score name -> plain description
SCORES = {
    "e_live": "Model expected return (live rule)",
    "e_fade50": "Model expected return, growth faded 50% toward 10%",
    "e_fade75": "Model expected return, growth faded 75% toward 10%",
    "e_flat10": "Model expected return, growth ignored (flat 10%)",
    "e_cap15": "Model expected return, growth capped at 15%",
    "low_pe": "Lowest P/E",
    "pe_vs_own": "Cheapest vs its own P/E history",
    "rerate": "Furthest below its own median P/E",
    "fv_all": "Fair-value discount (all methods)",
    "fv_valuation_only": "Fair-value discount, valuation methods only (no growth method)",
    "blend_live_lowpe": "Blend: live model + lowest P/E",
    "blend_fade50_lowpe": "Blend: faded model + lowest P/E",
    "blend_lowpe_pe_vs_own": "Blend: lowest P/E + cheapest vs own history",
}


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def build(stocks, grid):
    """One row per (stock, date) with every score attached."""
    base = {}
    for name, fn in GROWTH_RULES.items():
        for s in stocks:
            for t in grid:
                m = B.model_asof(s, t, growth_fn=fn, fade=0.0)      # variants are defined on the pre-fade model
                if not m:
                    continue
                key = (s["symbol"], t.isoformat())
                if name == "live":
                    m.update(symbol=s["symbol"], date=t.isoformat(), **{f"ret_{h}y": B.forward_return(s, t, h) for h in B.HORIZONS})
                    base[key] = m
                if key in base:
                    base[key][f"e_{name}"] = m["e_base"]
    rows = list(base.values())
    for o in rows:
        o["low_pe"] = -o["pe"]
        o["pe_vs_own"] = -o["pe_percentile"] if o["pe_percentile"] is not None else None
        o["rerate"] = o["rerating_ratio"]
        o["fv_all"] = -o["fv_premium_pct"] if o["fv_premium_pct"] is not None else None
        mids = [v for k, v in (o.get("fv_mids") or {}).items() if k != "growth_model"]
        o["fv_valuation_only"] = -((o["price"] / _median(mids) - 1) * 100) if len(mids) >= 1 else None
    add_rank_blend(rows, "blend_live_lowpe", ["e_live", "low_pe"])
    add_rank_blend(rows, "blend_fade50_lowpe", ["e_fade50", "low_pe"])
    add_rank_blend(rows, "blend_lowpe_pe_vs_own", ["low_pe", "pe_vs_own"])
    return rows


def add_rank_blend(rows, name, keys):
    """Average of the per-date percentile ranks of the given scores."""
    by_date = {}
    for o in rows:
        by_date.setdefault(o["date"], []).append(o)
    for group in by_date.values():
        ok = [o for o in group if all(o.get(k) is not None for k in keys)]
        ranks = {k: dict(zip([id(o) for o in ok], B._ranks([o[k] for o in ok]))) for k in keys}
        for o in ok:
            o[name] = sum(ranks[k][id(o)] for k in keys) / (len(keys) * max(len(ok), 1))


def ic_by_date(rows, score, ret_key):
    by_date = {}
    for o in rows:
        sc, rt = o.get(score), o.get(ret_key)
        if sc is not None and rt is not None:
            by_date.setdefault(o["date"], []).append((sc, rt))
    out = {}
    for d, v in by_date.items():
        ic = B.spearman([a for a, _ in v], [b for _, b in v])
        if ic is not None:
            out[d] = ic
    return out


def quintile_stats(rows, score, ret_key):
    """Per date: mean return of the top fifth minus the bottom fifth by score, and the share of the top
    fifth that beat that date's median stock. Dates with fewer than 20 stocks are skipped."""
    by_date = {}
    for o in rows:
        if o.get(score) is not None and o.get(ret_key) is not None:
            by_date.setdefault(o["date"], []).append(o)
    spreads, tops, beats, bottoms = [], [], [], []
    for d, g in by_date.items():
        if len(g) < 20:
            continue
        med = _median([o[ret_key] for o in g])
        g = sorted(g, key=lambda o: o[score], reverse=True)
        k = max(1, len(g) // 5)
        top, bot = g[:k], g[-k:]
        tm, bm = sum(o[ret_key] for o in top) / k, sum(o[ret_key] for o in bot) / k
        spreads.append(tm - bm); tops.append(tm); bottoms.append(bm)
        beats.append(sum(1 for o in top if o[ret_key] > med) / k)
    if not spreads:
        return None
    n = len(spreads)
    return {"dates": n, "top_fifth_return_pct": round(sum(tops) / n, 1), "bottom_fifth_return_pct": round(sum(bottoms) / n, 1),
            "spread_pct": round(sum(spreads) / n, 1), "top_fifth_beat_typical_pct": round(sum(beats) / n * 100)}


def summarise(rows, ret_key, subset=None):
    rs = [o for o in rows if subset(o)] if subset else rows
    out = {}
    for sc in SCORES:
        ics = list(ic_by_date(rs, sc, ret_key).values())
        if not ics:
            continue
        out[sc] = {"mean_ic": round(sum(ics) / len(ics), 3), "dates": len(ics), "dates_positive_pct": round(sum(1 for i in ics if i > 0) / len(ics) * 100),
                   **(quintile_stats(rs, sc, ret_key) or {})}
    return out


def paired(rows, a, b, ret_key):
    """Date-by-date: is score `a` better than `b`? (difference in rank correlation)"""
    ia, ib = ic_by_date(rows, a, ret_key), ic_by_date(rows, b, ret_key)
    common = [d for d in ia if d in ib]
    if not common:
        return None
    diffs = [ia[d] - ib[d] for d in common]
    return {"dates": len(common), "mean_ic_difference": round(sum(diffs) / len(diffs), 3),
            "dates_where_first_better_pct": round(sum(1 for x in diffs if x > 0) / len(diffs) * 100)}


def two_by_two(rows, ret_key, model_score="e_live"):
    """Model view (expected return >= 12%) against cheap-versus-own-history (P/E in the cheapest 40% of its history)."""
    med = {}
    by_date = {}
    for o in rows:
        if o.get(ret_key) is not None:
            by_date.setdefault(o["date"], []).append(o[ret_key])
    med = {d: _median(v) for d, v in by_date.items()}
    cells = {}
    for o in rows:
        if o.get(ret_key) is None or o.get("pe_percentile") is None:
            continue
        a = "model attractive" if o[model_score] >= 12 else "model not attractive"
        b = "cheap vs own history" if o["pe_percentile"] <= 40 else "not cheap vs own history"
        cells.setdefault((a, b), []).append(o)
    out = []
    for (a, b), g in sorted(cells.items()):
        out.append({"model_view": a, "own_history_view": b, "n": len(g), "mean_return_pct": round(sum(o[ret_key] for o in g) / len(g), 1),
                    "beat_typical_pct": round(sum(1 for o in g if o[ret_key] > med[o["date"]]) / len(g) * 100)})
    return out


def extended_low_pe(stocks, progress=print):
    """The lowest-P/E rule needs only the latest published EPS, not three-plus years of history, so it can be
    tested from mid-2017 (statements start at FY2017) - through the 2017-2019 stretch when high-quality,
    high-P/E companies led the market. That is the out-of-regime check on the finding that low P/E won."""
    from investment_scores import _real_fiscal_years
    grid = B.as_of_grid(first=date(2017, 6, 30))
    rows = []
    for s in stocks:
        for t in grid:
            price = B.price_on(s, t)
            inc = _real_fiscal_years(B.asof_statements(s["statements"], t)["income_statement_annual"])
            eps = B.V._f(inc[-1].get("incEps")) if inc else None
            if not price or not eps or eps <= 0 or price / eps > 200:
                continue
            rows.append({"symbol": s["symbol"], "date": t.isoformat(), "year": t.year, "low_pe": -(price / eps), "pe": price / eps,
                         **{f"ret_{h}y": B.forward_return(s, t, h) for h in B.HORIZONS}})
    progress(f"extended low-P/E set: {len(rows)} observations, {len(grid)} dates from {grid[0]}")
    out = {"observations": len(rows), "first_date": grid[0].isoformat(), "horizons": {}, "by_entry_year": {}}
    for h in (1, 2, 3, 5):
        key = f"ret_{h}y"
        ics = list(ic_by_date(rows, "low_pe", key).values())
        if ics:
            out["horizons"][f"{h}y"] = {"mean_ic": round(sum(ics) / len(ics), 3), "dates": len(ics), "dates_positive_pct": round(sum(1 for i in ics if i > 0) / len(ics) * 100),
                                        **(quintile_stats(rows, "low_pe", key) or {})}
    for y in sorted({o["year"] for o in rows}):
        yr = [o for o in rows if o["year"] == y]
        cell = {}
        for h in (1, 3):
            key = f"ret_{h}y"
            ics = list(ic_by_date(yr, "low_pe", key).values())
            q = quintile_stats(yr, "low_pe", key)
            if ics:
                cell[f"{h}y"] = {"mean_ic": round(sum(ics) / len(ics), 3), "dates": len(ics), "spread_pct": (q or {}).get("spread_pct")}
        out["by_entry_year"][str(y)] = cell
    return out


def regime_check(stocks, progress=print):
    """Does a rule that weighs BOTH cheapness and growth hold up across regimes better than either alone?
    Growth here is the 2-year EPS CAGR (the longest that exists for entries in mid-2019), so this is a coarser
    growth measure than the live model's 3-5 year one - it tests the idea, not the exact live rule."""
    from investment_scores import _real_fiscal_years
    grid = B.as_of_grid(first=date(2019, 6, 30))
    rows = []
    for s in stocks:
        for t in grid:
            price = B.price_on(s, t)
            inc = _real_fiscal_years(B.asof_statements(s["statements"], t)["income_statement_annual"])
            eps = [B.V._f(r.get("incEps")) for r in inc]
            if not price or len(eps) < 3 or not eps[-1] or eps[-1] <= 0 or not eps[-3] or eps[-3] <= 0 or price / eps[-1] > 200:
                continue
            g2 = ((eps[-1] / eps[-3]) ** 0.5 - 1) * 100
            rows.append({"symbol": s["symbol"], "date": t.isoformat(), "year": t.year, "low_pe": -(price / eps[-1]), "growth2y": g2,
                         **{f"ret_{h}y": B.forward_return(s, t, h) for h in B.HORIZONS}})
    add_rank_blend(rows, "cheap_and_growing", ["low_pe", "growth2y"])
    out = {"observations": len(rows), "first_date": grid[0].isoformat(), "by_entry_year": {}}
    for y in sorted({o["year"] for o in rows}):
        yr = [o for o in rows if o["year"] == y]
        cell = {}
        for h in (1, 3):
            key = f"ret_{h}y"
            per = {}
            for sc in ("low_pe", "growth2y", "cheap_and_growing"):
                ics = list(ic_by_date(yr, sc, key).values())
                q = quintile_stats(yr, sc, key)
                if ics:
                    per[sc] = {"mean_ic": round(sum(ics) / len(ics), 3), "spread_pct": (q or {}).get("spread_pct")}
            if per:
                cell[f"{h}y"] = per
        out["by_entry_year"][str(y)] = cell
    out["all"] = {}
    for h in (1, 3):
        key = f"ret_{h}y"
        out["all"][f"{h}y"] = {sc: {"mean_ic": round(sum(v := list(ic_by_date(rows, sc, key).values())) / len(v), 3), "dates_positive_pct": round(sum(1 for i in v if i > 0) / len(v) * 100),
                                    "spread_pct": (quintile_stats(rows, sc, key) or {}).get("spread_pct")}
                               for sc in ("low_pe", "growth2y", "cheap_and_growing") if ic_by_date(rows, sc, key)}
    return out


def run(progress=print):
    stocks = []
    for sym in B.UNIVERSE:
        try:
            s = B.load_stock(sym)
        except Exception as e:
            progress(f"  {sym}: {e}")
            continue
        if s and len(s["prices"]) > 300:
            stocks.append(s)
    grid = B.as_of_grid()
    progress(f"{len(stocks)} stocks, {len(grid)} dates")
    rows = build(stocks, grid)
    progress(f"{len(rows)} observations")
    cut = date(2021, 12, 31).isoformat()
    out = {"scores": SCORES, "observations": len(rows), "horizons": {}}
    for h in (1, 2, 3):
        key = f"ret_{h}y"
        out["horizons"][f"{h}y"] = {
            "all": summarise(rows, key),
            "early_dates": summarise(rows, key, lambda o: o["date"] <= cut),
            "late_dates": summarise(rows, key, lambda o: o["date"] > cut),
            "financials_only": summarise(rows, key, lambda o: o["is_financial"]),
            "non_financials": summarise(rows, key, lambda o: not o["is_financial"]),
            "paired": {
                "low_pe_vs_live": paired(rows, "low_pe", "e_live", key),
                "fade50_vs_live": paired(rows, "e_fade50", "e_live", key),
                "flat10_vs_live": paired(rows, "e_flat10", "e_live", key),
                "blend_live_lowpe_vs_live": paired(rows, "blend_live_lowpe", "e_live", key),
                "blend_live_lowpe_vs_lowpe": paired(rows, "blend_live_lowpe", "low_pe", key),
                "blend_fade50_lowpe_vs_lowpe": paired(rows, "blend_fade50_lowpe", "low_pe", key),
            },
            "two_by_two": two_by_two(rows, key),
        }
    out["low_pe_extended"] = extended_low_pe(stocks, progress)
    out["regime_check"] = regime_check(stocks, progress)
    return out


if __name__ == "__main__":
    res = run()
    OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"saved {OUT}")
