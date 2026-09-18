"""
investment_freshness.py — tells the user (and the AI) which reporting period
an analysis is actually based on, and whether the two financial sources agree.

BSE (primary, quick to update) and Tickertape (10-year statements, the
gap-filler) each report the latest quarter a company has published. Right
after a company reports, one can be a day or more ahead of the other. This
compares them so the analysis can (a) force a re-fetch when they disagree,
and (b) say so plainly if one source still hasn't caught up, instead of
quietly presenting last quarter's numbers as current.

Pure functions, no I/O — isolation as with every file in this feature.
"""
from datetime import datetime
from typing import Optional


def _parse_bse_period(label) -> Optional[datetime]:
    try:
        return datetime.strptime(str(label).strip(), "%b-%y")      # 'Jun-26'
    except (TypeError, ValueError):
        return None


def _parse_tt_period(label) -> Optional[datetime]:
    try:
        return datetime.strptime(str(label).strip().title(), "%b %Y")  # 'JUN 2026'
    except (TypeError, ValueError):
        return None


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%b-%y") if dt else None


def _latest_tt_quarter(statements: Optional[dict]) -> Optional[datetime]:
    quarters = [_parse_tt_period(r.get("displayPeriod")) for r in (statements or {}).get("income_statement_quarterly", [])]
    quarters = [q for q in quarters if q]
    return max(quarters) if quarters else None


def assess(bse_results: Optional[dict], statements: Optional[dict]) -> dict:
    """status: in_sync | tickertape_behind | bse_behind | unknown.
    'financials_through' is the quarter the analysis' fundamentals come from
    (Tickertape's statements drive the growth rates, TTM EPS and valuation)."""
    periods = (bse_results or {}).get("periods") or []
    bse_q = _parse_bse_period(periods[0]) if periods else None
    tt_q = _latest_tt_quarter(statements)

    out = {"bse_latest_quarter": _fmt(bse_q), "tickertape_latest_quarter": _fmt(tt_q),
           "financials_through": _fmt(tt_q), "status": "unknown", "message": None}
    if bse_q and tt_q:
        if bse_q == tt_q:
            out["status"] = "in_sync"
        elif bse_q > tt_q:
            out["status"] = "tickertape_behind"
            out["message"] = (f"BSE already shows {_fmt(bse_q)} results, but the multi-year statements behind the growth rates, "
                              f"trailing EPS and valuation model only run through {_fmt(tt_q)} — the data provider hasn't loaded "
                              f"the new quarter yet. Treat those figures as one quarter out of date and re-run in a few hours.")
        else:
            out["status"] = "bse_behind"
            out["message"] = f"BSE's results table ({_fmt(bse_q)}) is one quarter behind the statements ({_fmt(tt_q)}); the statements are the newer source."
    return out
