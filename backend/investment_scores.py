"""
investment_scores.py — Piotroski F-Score and Altman Z-Score, computed from
tickertape_client's financial-statement data. Pure functions, no I/O, no
dependency on bse_client/tickertape_client themselves (they're handed
already-fetched data) — kept separate so the scoring logic is testable in
isolation and easy to audit against the formulas.

Field names below are Tickertape's own short codes (see
tickertape_client.get_financial_statements docstring): balTota=Total
Assets, balTeq=Total Equity, balTotl=Total Liabilities, balTca/balTcl=
Total Current Assets/Liabilities, balRtne=Retained Earnings,
balTdeb=Total Debt, balTltd=Total Long-Term Debt, balTcso=shares
outstanding; incTrev=Total Revenue, incNinc=Net Income, incPbi=Profit
Before Interest (i.e. EBIT — after depreciation, before interest expense,
inferred from field ordering: incEbi(EBITDA) -> less incDep -> incPbi);
cafCfoa=Cash Flow from Operating Activities.

Only real fiscal years are used for comparisons — Tickertape's annual
arrays include a trailing "TTM" (trailing-twelve-month) entry that is NOT
a real filed year, so callers must pass already-filtered FY-only lists
(see _real_fiscal_years below, used internally).
"""
from typing import Optional


def _real_fiscal_years(annual_rows: list) -> list:
    """Filter out TTM/interim rows, keep only genuine 'FY nnnn' periods,
    oldest first (Tickertape already returns oldest-first, but don't
    assume — sort explicitly by endDate where present)."""
    rows = [r for r in annual_rows if str(r.get("displayPeriod", "")).startswith("FY")]
    rows.sort(key=lambda r: r.get("endDate") or r.get("displayPeriod", ""))
    return rows


def compute_piotroski(financials: dict) -> dict:
    """9-point Piotroski F-Score. Needs at least 2 real fiscal years of
    balance sheet + income + cash flow to compute the year-over-year
    checks (6 of the 9 points); returns whatever subset is computable if
    less history is available, with 'complete': False so callers know not
    to treat a partial score as the real 0-9 scale."""
    bs = _real_fiscal_years(financials.get("balance_sheet_annual", []))
    inc = _real_fiscal_years(financials.get("income_statement_annual", []))
    cf = _real_fiscal_years(financials.get("cash_flow_annual", []))

    if len(bs) < 2 or len(inc) < 2 or len(cf) < 1:
        return {"score": None, "max": 9, "complete": False, "reason": "insufficient history", "checks": {}}

    cur_bs, prev_bs = bs[-1], bs[-2]
    cur_inc, prev_inc = inc[-1], inc[-2]
    cur_cf = cf[-1]

    def safe_div(a, b):
        return a / b if a is not None and b not in (None, 0) else None

    roa_cur = safe_div(cur_inc.get("incNinc"), cur_bs.get("balTota"))
    roa_prev = safe_div(prev_inc.get("incNinc"), prev_bs.get("balTota"))
    leverage_cur = safe_div(cur_bs.get("balTltd"), cur_bs.get("balTota"))
    leverage_prev = safe_div(prev_bs.get("balTltd"), prev_bs.get("balTota"))
    current_ratio_cur = safe_div(cur_bs.get("balTca"), cur_bs.get("balTcl"))
    current_ratio_prev = safe_div(prev_bs.get("balTca"), prev_bs.get("balTcl"))
    gross_margin_cur = safe_div(cur_inc.get("incTrev", 0) - cur_inc.get("incOpe", 0), cur_inc.get("incTrev"))
    gross_margin_prev = safe_div(prev_inc.get("incTrev", 0) - prev_inc.get("incOpe", 0), prev_inc.get("incTrev"))
    asset_turnover_cur = safe_div(cur_inc.get("incTrev"), cur_bs.get("balTota"))
    asset_turnover_prev = safe_div(prev_inc.get("incTrev"), prev_bs.get("balTota"))

    checks = {
        "positive_net_income": cur_inc.get("incNinc", 0) > 0,
        "positive_operating_cashflow": cur_cf.get("cafCfoa", 0) > 0,
        "roa_improved": (roa_cur is not None and roa_prev is not None and roa_cur > roa_prev),
        "cfo_exceeds_net_income": (cur_cf.get("cafCfoa") or 0) > (cur_inc.get("incNinc") or 0),
        "leverage_decreased": (leverage_cur is not None and leverage_prev is not None and leverage_cur < leverage_prev),
        "current_ratio_improved": (current_ratio_cur is not None and current_ratio_prev is not None and current_ratio_cur > current_ratio_prev),
        "no_new_shares_issued": (cur_bs.get("balTcso") or 0) <= (prev_bs.get("balTcso") or float("inf")),
        "gross_margin_improved": (gross_margin_cur is not None and gross_margin_prev is not None and gross_margin_cur > gross_margin_prev),
        "asset_turnover_improved": (asset_turnover_cur is not None and asset_turnover_prev is not None and asset_turnover_cur > asset_turnover_prev),
    }
    score = sum(1 for v in checks.values() if v is True)
    return {
        "score": score,
        "max": 9,
        "complete": True,
        "compared_years": [prev_bs.get("displayPeriod"), cur_bs.get("displayPeriod")],
        "checks": checks,
    }


def compute_altman_z(financials: dict, market_cap_cr: Optional[float]) -> dict:
    """Altman Z-Score: Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5.
    market_cap_cr: market capitalisation in Rs. Crore (same unit as the
    balance sheet figures) — pass None if unavailable; X4 (and the final
    score) will be null in that case rather than silently wrong."""
    bs = _real_fiscal_years(financials.get("balance_sheet_annual", []))
    inc = _real_fiscal_years(financials.get("income_statement_annual", []))
    if not bs or not inc:
        return {"z_score": None, "zone": None, "reason": "no fiscal-year data", "components": {}}

    cur_bs, cur_inc = bs[-1], inc[-1]
    total_assets = cur_bs.get("balTota")
    if not total_assets:
        return {"z_score": None, "zone": None, "reason": "missing total assets", "components": {}}

    working_capital = (cur_bs.get("balTca") or 0) - (cur_bs.get("balTcl") or 0)
    x1 = working_capital / total_assets
    x2 = (cur_bs.get("balRtne") or 0) / total_assets
    x3 = (cur_inc.get("incPbi") or 0) / total_assets  # EBIT proxy — see module docstring
    x5 = (cur_inc.get("incTrev") or 0) / total_assets

    x4 = None
    z = None
    if market_cap_cr and cur_bs.get("balTotl"):
        x4 = market_cap_cr / cur_bs["balTotl"]
        z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5

    zone = None
    if z is not None:
        zone = "safe" if z > 2.6 else ("distress" if z < 1.1 else "grey")

    return {
        "z_score": z,
        "zone": zone,
        "fiscal_year": cur_bs.get("displayPeriod"),
        "components": {"x1_working_capital_ratio": x1, "x2_retained_earnings_ratio": x2,
                        "x3_ebit_ratio": x3, "x4_market_value_to_liabilities": x4, "x5_asset_turnover": x5},
    }


def compute_piotroski_from_rhp(summary_financials: dict) -> dict:
    """Same 9-point Piotroski F-Score, computed from rhp_extractor's
    3-year restated summary (latest/prior/prior2) instead of Tickertape's
    field names — this is what makes IPO Review a genuine quality read on
    a company that isn't listed yet, straight from its own prospectus."""
    bs = (summary_financials or {}).get("balance_sheet", {})
    pl = (summary_financials or {}).get("profit_and_loss", {})
    cf = (summary_financials or {}).get("cash_flow", {})
    cur_bs, prev_bs = bs.get("latest", {}), bs.get("prior", {})
    cur_pl, prev_pl = pl.get("latest", {}), pl.get("prior", {})
    cur_cf = cf.get("latest", {})

    required = [cur_bs.get("total_assets"), prev_bs.get("total_assets"),
                cur_pl.get("net_profit"), prev_pl.get("net_profit")]
    if any(v is None for v in required):
        return {"score": None, "max": 9, "complete": False, "reason": "insufficient RHP financial data", "checks": {}}

    def safe_div(a, b):
        return a / b if a is not None and b not in (None, 0) else None

    roa_cur = safe_div(cur_pl.get("net_profit"), cur_bs.get("total_assets"))
    roa_prev = safe_div(prev_pl.get("net_profit"), prev_bs.get("total_assets"))
    leverage_cur = safe_div(cur_bs.get("total_noncurrent_liabilities"), cur_bs.get("total_assets"))
    leverage_prev = safe_div(prev_bs.get("total_noncurrent_liabilities"), prev_bs.get("total_assets"))
    current_ratio_cur = safe_div(cur_bs.get("total_current_assets"), cur_bs.get("total_current_liabilities"))
    current_ratio_prev = safe_div(prev_bs.get("total_current_assets"), prev_bs.get("total_current_liabilities"))
    op_margin_cur = safe_div((cur_pl.get("revenue") or 0) - (cur_pl.get("total_expenses") or 0), cur_pl.get("revenue"))
    op_margin_prev = safe_div((prev_pl.get("revenue") or 0) - (prev_pl.get("total_expenses") or 0), prev_pl.get("revenue"))
    asset_turnover_cur = safe_div(cur_pl.get("revenue"), cur_bs.get("total_assets"))
    asset_turnover_prev = safe_div(prev_pl.get("revenue"), prev_bs.get("total_assets"))

    checks = {
        "positive_net_income": (cur_pl.get("net_profit") or 0) > 0,
        "positive_operating_cashflow": (cur_cf.get("operating_cash_flow") or 0) > 0,
        "roa_improved": (roa_cur is not None and roa_prev is not None and roa_cur > roa_prev),
        "cfo_exceeds_net_income": (cur_cf.get("operating_cash_flow") or 0) > (cur_pl.get("net_profit") or 0),
        "leverage_decreased": (leverage_cur is not None and leverage_prev is not None and leverage_cur < leverage_prev),
        "current_ratio_improved": (current_ratio_cur is not None and current_ratio_prev is not None and current_ratio_cur > current_ratio_prev),
        "no_new_shares_issued": (cur_bs.get("equity_share_capital") or 0) <= (prev_bs.get("equity_share_capital") or float("inf")),
        "operating_margin_improved": (op_margin_cur is not None and op_margin_prev is not None and op_margin_cur > op_margin_prev),
        "asset_turnover_improved": (asset_turnover_cur is not None and asset_turnover_prev is not None and asset_turnover_cur > asset_turnover_prev),
    }
    score = sum(1 for v in checks.values() if v is True)
    return {"score": score, "max": 9, "complete": True, "compared_years": "latest vs prior restated FY", "checks": checks}


def compute_altman_zprime(summary_financials: dict) -> dict:
    """Altman Z'-Score — the standard private-company variant (Z' = 0.717X1
    + 0.847X2 + 3.107X3 + 0.420X4 + 0.998X5), used here instead of the
    listed-company Z-Score because a pre-IPO company has no market
    capitalisation yet: X4 uses BOOK value of equity over total
    liabilities rather than market value, per the published methodology
    for exactly this situation. Thresholds also differ from the listed
    variant: Z' > 2.9 safe, 1.23-2.9 grey, < 1.23 distress."""
    bs = (summary_financials or {}).get("balance_sheet", {}).get("latest", {})
    pl = (summary_financials or {}).get("profit_and_loss", {}).get("latest", {})
    total_assets = bs.get("total_assets")
    if not total_assets:
        return {"z_prime_score": None, "zone": None, "reason": "missing total assets", "components": {}}

    total_liabilities = (bs.get("total_noncurrent_liabilities") or 0) + (bs.get("total_current_liabilities") or 0)
    working_capital = (bs.get("total_current_assets") or 0) - (bs.get("total_current_liabilities") or 0)
    retained_earnings_proxy = (bs.get("total_equity") or 0) - (bs.get("equity_share_capital") or 0)
    ebit = (pl.get("profit_before_tax") or 0) + (pl.get("finance_costs") or 0)

    x1 = working_capital / total_assets
    x2 = retained_earnings_proxy / total_assets
    x3 = ebit / total_assets
    x4 = (bs.get("total_equity") / total_liabilities) if total_liabilities else None
    x5 = (pl.get("revenue") or 0) / total_assets

    z = None
    if x4 is not None:
        z = 0.717 * x1 + 0.847 * x2 + 3.107 * x3 + 0.420 * x4 + 0.998 * x5

    zone = None
    if z is not None:
        zone = "safe" if z > 2.9 else ("distress" if z < 1.23 else "grey")

    return {
        "z_prime_score": z,
        "zone": zone,
        "variant": "private-company (book value of equity)",
        "components": {"x1_working_capital_ratio": x1, "x2_retained_earnings_ratio": x2,
                        "x3_ebit_ratio": x3, "x4_book_equity_to_liabilities": x4, "x5_asset_turnover": x5},
    }


def compute_yoy_trend(summary_financials: dict) -> dict:
    """Revenue/net-profit YoY % change and direction, computed directly
    from the RHP's own restated financials — not left to the LLM to
    eyeball, so the 'Revenue UP / PAT UP' style read is a real number, not
    a guess. Growth-stage pre-IPO companies routinely show weak Piotroski/
    Altman scores despite genuinely growing revenue and profit (heavy
    capex, negative working capital) — this is what actually separates
    'financially distressed' from 'investing in growth', so it's surfaced
    as its own signal rather than folded into the quality scores above."""
    pl = (summary_financials or {}).get("profit_and_loss", {})
    cur, prev = pl.get("latest", {}), pl.get("prior", {})

    def pct_change(cur_v, prev_v):
        if cur_v is None or prev_v in (None, 0):
            return None
        return round(((cur_v - prev_v) / abs(prev_v)) * 100, 1)

    def direction(pct):
        if pct is None:
            return None
        return "up" if pct > 1 else ("down" if pct < -1 else "flat")

    revenue_yoy = pct_change(cur.get("revenue"), prev.get("revenue"))
    profit_yoy = pct_change(cur.get("net_profit"), prev.get("net_profit"))

    # Cross-check: revenue and total_income (revenue + other income) should
    # move in roughly the same direction/magnitude for a real business
    # change. A wild divergence between them (one crashes, the other
    # doesn't) is a strong signal the 'latest'/'prior' columns picked up
    # mismatched periods for one of the two lines — e.g. a stub/interim
    # period got read as a full fiscal year (confirmed happening on at
    # least one real filing with an unusual 4+ period table) — rather than
    # a genuine result. Flagged instead of shown as a confident number.
    income_yoy = pct_change(cur.get("total_income"), prev.get("total_income"))
    reliable = True
    if revenue_yoy is not None and income_yoy is not None:
        if abs(revenue_yoy - income_yoy) > 40 and (abs(revenue_yoy) > 50 or abs(income_yoy) > 50):
            reliable = False

    return {
        "revenue_yoy_pct": revenue_yoy if reliable else None,
        "revenue_direction": direction(revenue_yoy) if reliable else None,
        "net_profit_yoy_pct": profit_yoy,
        "net_profit_direction": direction(profit_yoy),
        "reliable": reliable,
    }


def classify_investor_interest(subscription: dict) -> Optional[str]:
    """Subscription multiple is a direct, real demand signal — no LLM
    guessing needed. Thresholds match common retail-IPO-commentary
    convention (10x+ genuinely hot, under 2x genuinely soft)."""
    if not subscription:
        return None
    total_x = subscription.get("total_x")
    if total_x is None:
        return None
    if total_x >= 10:
        return "high"
    if total_x >= 2:
        return "moderate"
    return "low"
