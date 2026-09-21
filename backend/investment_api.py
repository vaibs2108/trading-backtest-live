"""
investment_api.py — Company Analysis REST endpoints for the Investment
Analysis page. Combines bse_client.py (primary: quotes, ratios, P&L,
shareholding, peers) and tickertape_client.py (the one gap-filler: full
10-year balance sheet/income/cash-flow) into frontend-ready responses, and
runs the Piotroski/Altman scoring in investment_scores.py on top.

Isolation, same as every other file in this feature: no imports from
broker.py / cas_broker.py / config.py / main.py. The ONLY place this file
is referenced from the rest of the app is a single additive
`app.include_router(...)` line in main.py's startup wiring — nothing
existing is modified to add that line, and removing it (plus this file)
fully removes the feature with zero trace elsewhere.
"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

import bse_client as bse
import tickertape_client as tt
import yahoo_client as yh
import investment_scores as scores
import investment_debate as debate
import investment_valuation as valuation
import investment_freshness as freshness
import investment_search as inv_search
import ipo_sentiment_client as ipo_sentiment
import rhp_extractor
import investment_backtest as backtest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/investment", tags=["investment"])

_symbol_index = None      # {SYMBOL: {scripcode, name, isin, mktcap_cr}}
_search_index = None      # investment_search index over the same universe
_universe_loaded_at = 0.0
_universe_forced_at = 0.0
_UNIVERSE_MAX_AGE = 6 * 3600     # matches bse_client's universe cache TTL
_FORCE_REFRESH_MIN_GAP = 600     # at most one forced re-fetch per 10 minutes


def _load_universe(force: bool = False) -> None:
    """(Re)build both lookup tables from the BSE universe. A company listed
    today is not in a cached universe, so a symbol/name that is not found
    triggers ONE forced re-fetch (rate-limited) before giving up — new
    listings become findable within minutes rather than after a cache expiry
    or a backend restart."""
    global _symbol_index, _search_index, _universe_loaded_at, _universe_forced_at
    rows = bse.get_universe("", force_refresh=force)
    idx = {}
    for row in rows:
        sym = str(row.get("scrip_id") or "").upper()
        if not sym:
            continue
        idx[sym] = {
            "scripcode": str(row.get("SCRIP_CD")),
            "name": row.get("Issuer_Name") or row.get("Scrip_Name"),
            "isin": row.get("ISIN_NUMBER"),
            "mktcap_cr": _to_float(row.get("Mktcap")),
        }
    _symbol_index = idx
    _search_index = inv_search.build_index(rows)
    _universe_loaded_at = time.time()
    if force:
        _universe_forced_at = time.time()


def _get_symbol_index() -> dict:
    if _symbol_index is None or time.time() - _universe_loaded_at > _UNIVERSE_MAX_AGE:
        _load_universe()
    return _symbol_index


def _get_search_index() -> list:
    if _search_index is None or time.time() - _universe_loaded_at > _UNIVERSE_MAX_AGE:
        _load_universe()
    return _search_index


def _refresh_universe_on_miss() -> bool:
    """True if a forced refresh actually ran (rate-limited)."""
    if time.time() - _universe_forced_at < _FORCE_REFRESH_MIN_GAP:
        return False
    try:
        _load_universe(force=True)
        return True
    except Exception as e:
        logger.warning(f"investment_api: forced universe refresh failed: {e}")
        return False


def _to_float(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


@router.get("/search")
def search_companies(q: str = "", limit: int = 8):
    """Company-name / symbol search for the "did you mean" box. Only ever
    suggests — the page loads an analysis for the exact symbol the user picks."""
    try:
        n = max(1, min(limit, 15))
        results = inv_search.search(_get_search_index(), q, n)
        if not results and len(q.strip()) >= 3 and _refresh_universe_on_miss():
            results = inv_search.search(_get_search_index(), q, n)     # maybe a brand-new listing
        return {"query": q, "results": results}
    except Exception as e:
        logger.error(f"investment_api: company search failed for {q!r}: {e}")
        raise HTTPException(status_code=502, detail=f"search failed: {e}")


def _iso(ts) -> Optional[str]:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") if ts else None


def _find_tt_match(symbol: str, name: Optional[str], isin: Optional[str]) -> dict:
    """Tickertape's search rejects very short queries outright (HTTP 400 for
    'LT' — L&T — which used to take down the whole Company Analysis with a
    500), and its fuzzy fallback can return a DIFFERENT company. So: try the
    symbol, then the company name; accept only an exact ticker match or a
    candidate whose ISIN equals BSE's. No match returns {} — the page then
    degrades to BSE-only data rather than showing another company's numbers."""
    tried = set()
    for query in (symbol, name):
        if not query or query in tried:
            continue
        tried.add(query)
        try:
            results = tt.search(query)
        except Exception as e:
            logger.info(f"investment_api: tickertape search '{query}' failed ({e}); trying next query")
            continue
        exact = [x for x in results if str(x.get("ticker", "")).upper() == symbol]
        if exact:
            return exact[0]
        if isin:
            for cand in results[:3]:
                try:
                    if (tt.get_info(cand.get("sid")) or {}).get("isin") == isin:
                        return cand
                except Exception:
                    continue
    return {}


def _resolve(symbol: str) -> dict:
    """Resolve a trading symbol (e.g. 'RELIANCE') to everything downstream
    endpoints need: BSE scripcode + Tickertape sid/slug. Raises 404 if the
    symbol isn't found on BSE at all (Tickertape not finding a match is
    handled gracefully per-endpoint instead, since that's just the
    balance-sheet gap-filler, not the primary source)."""
    symbol = symbol.upper().strip()
    idx = _get_symbol_index()
    bse_match = idx.get(symbol)
    if not bse_match and _refresh_universe_on_miss():
        bse_match = _get_symbol_index().get(symbol)     # maybe a brand-new listing
    if not bse_match:
        raise HTTPException(status_code=404, detail=f"'{symbol}' isn't a BSE symbol. Try searching by company name.")
    tt_match = _find_tt_match(symbol, bse_match["name"], bse_match["isin"])
    return {
        "symbol": symbol,
        "scripcode": bse_match["scripcode"],
        "name": bse_match["name"],
        "isin": bse_match["isin"],
        "mktcap_cr": bse_match["mktcap_cr"],
        "tt_sid": tt_match.get("sid"),
        "tt_slug": tt_match.get("slug"),
    }


@router.get("/equity/{symbol}/overview")
def get_overview(symbol: str):
    """Quote + key ratio tiles (P/E, P/B, ROE, EPS, dividend yield) +
    sector/industry classification."""
    r = _resolve(symbol)
    try:
        quote = bse.get_quote(r["scripcode"])
        info = bse.get_company_info(r["scripcode"])
    except Exception as e:
        logger.error(f"investment_api: overview fetch failed for {symbol}: {e}")
        raise HTTPException(status_code=502, detail=f"upstream data fetch failed: {e}")
    return {
        "symbol": r["symbol"], "name": r["name"], "isin": r["isin"], "mktcap_cr": r["mktcap_cr"],
        "quote": quote,
        "quote_as_of": _iso(bse.cache_fetched_at("quote", r["scripcode"])),
        "ratios": {
            "pe": _to_float(info.get("PE")), "pb": _to_float(info.get("PB")), "roe": _to_float(info.get("ROE")),
            "eps": _to_float(info.get("EPS")), "ceps": _to_float(info.get("CEPS")),
            "opm": _to_float(info.get("OPM")), "npm": _to_float(info.get("NPM")),
            "consolidated_pe": _to_float(info.get("ConPE")), "consolidated_eps": _to_float(info.get("ConEPS")),
        },
        "classification": {
            "sector": info.get("Sector"), "industry": info.get("IndustryNew"),
            "group": info.get("IGroup"), "sub_group": info.get("ISubGroup"),
        },
    }


def _has_history(statements) -> bool:
    return bool(statements) and len(scores._real_fiscal_years(statements.get("income_statement_annual", []))) >= 2


def _yahoo_symbol(r: dict) -> Optional[str]:
    """The Yahoo ticker for this company, verified by name AND price against BSE
    (see yahoo_client) — or None. Never guesses."""
    try:
        ltp = (bse.get_quote(r["scripcode"]) or {}).get("ltp")
        return yh.resolve_symbol(r["symbol"], r["scripcode"], r["name"], ltp)
    except Exception as e:
        logger.info(f"investment_api: yahoo symbol resolution failed for {r['symbol']}: {e}")
        return None


def _load_statements(r: dict, force: bool = False):
    """(statements, source, yahoo_symbol). Tickertape is primary. Yahoo Finance
    is the backup, used only when Tickertape has no usable multi-year statements
    for the company (many small/SME stocks, some renamed ones)."""
    statements = None
    if r["tt_slug"]:
        try:
            statements = tt.get_financial_statements(r["tt_slug"], force_refresh=force)
        except Exception as e:
            logger.warning(f"investment_api: tickertape financials failed for {r['symbol']}: {e}")
    if _has_history(statements):
        return statements, "tickertape", None
    ysym = _yahoo_symbol(r)
    if ysym:
        try:
            backup = yh.get_financial_statements(ysym, force_refresh=force)
            if _has_history(backup):
                return backup, "yahoo", ysym
        except Exception as e:
            logger.warning(f"investment_api: yahoo financials failed for {r['symbol']} ({ysym}): {e}")
    return (statements, "tickertape", None) if statements else (None, None, None)


def _statements_fetched_at(r: dict, source: Optional[str], ysym: Optional[str]):
    if source == "tickertape" and r["tt_slug"]:
        return tt.cache_fetched_at("tt_financials", r["tt_slug"])
    if source == "yahoo" and ysym:
        return yh.cache_fetched_at("yh_statements", ysym)
    return None


@router.get("/equity/{symbol}/financials")
def get_financials(symbol: str):
    """Recent quarterly P&L (BSE) + 10-year balance sheet/income/cash flow
    (Tickertape) + Piotroski F-Score + Altman Z-Score computed on top."""
    r = _resolve(symbol)
    try:
        recent_results = bse.get_financial_results(r["scripcode"])
    except Exception as e:
        logger.error(f"investment_api: BSE results fetch failed for {symbol}: {e}")
        recent_results = None

    statements, source, ysym = _load_statements(r)

    # The two providers each report the latest quarter a company has published.
    # If BSE disagrees with the statements, a cache is stale: re-fetch both once
    # (unless the statements were fetched in the last 30 min — then re-fetching
    # can't help, the provider simply hasn't caught up) and, if they still
    # disagree, say so.
    fresh = freshness.assess(recent_results, statements)
    fresh["refreshed_to_catch_up"] = False
    if fresh["status"] in ("tickertape_behind", "bse_behind") and source:
        age = time.time() - (_statements_fetched_at(r, source, ysym) or 0)
        if age > 1800:
            try:
                recent_results = bse.get_financial_results(r["scripcode"], force_refresh=True)
                statements, source, ysym = _load_statements(r, force=True)
                fresh = freshness.assess(recent_results, statements)
                fresh["refreshed_to_catch_up"] = True
                logger.info(f"investment_api: {symbol} sources disagreed on latest quarter; force-refreshed -> {fresh['status']}")
            except Exception as e:
                logger.warning(f"investment_api: forced refresh for {symbol} failed: {e}")
    fresh["statements_source"] = source          # 'tickertape' | 'yahoo' | None
    fresh["yahoo_symbol"] = ysym
    fresh["statements_fetched_at"] = _iso(_statements_fetched_at(r, source, ysym))

    piotroski = {"score": None, "complete": False, "reason": "no multi-year statements found from any source"}
    altman = {"z_score": None, "reason": "no multi-year statements found from any source"}
    if statements:
        try:
            piotroski = scores.compute_piotroski(statements)
            altman = scores.compute_altman_z(statements, r["mktcap_cr"])
        except Exception as e:
            logger.warning(f"investment_api: score computation failed for {symbol}: {e}")
            piotroski = {"score": None, "complete": False, "reason": str(e)}
            altman = {"z_score": None, "reason": str(e)}

    return {
        "symbol": r["symbol"],
        "recent_results_bse": recent_results,   # last quarter/prior quarter/latest FY, from BSE
        "statements_10yr": statements,           # multi-year history: Tickertape (10y), or Yahoo Finance (~4y) as the backup
        "piotroski_f_score": piotroski,
        "altman_z_score": altman,
        "data_freshness": fresh,
    }


@router.get("/equity/{symbol}/shareholding")
def get_shareholding(symbol: str):
    """BSE's own latest XBRL-filed shareholding (authoritative) plus
    Tickertape's multi-quarter trend (for the chart) — BSE stays the
    source of truth for the current figure, Tickertape fills the history
    the same way it does for financials."""
    r = _resolve(symbol)
    bse_latest = None
    try:
        bse_latest = bse.get_shareholding_pattern(r["scripcode"])
    except Exception as e:
        logger.warning(f"investment_api: BSE shareholding fetch failed for {symbol}: {e}")

    trend = []
    if r["tt_sid"]:
        try:
            trend = tt.get_holdings(r["tt_sid"])
        except Exception as e:
            logger.warning(f"investment_api: tickertape holdings fetch failed for {symbol}: {e}")

    return {"symbol": r["symbol"], "bse_latest": bse_latest, "quarterly_trend": trend}


@router.get("/equity/{symbol}/price")
def get_price_history(symbol: str, months: str = "12M"):
    """OHLCV-ish price/volume history for charting."""
    r = _resolve(symbol)
    try:
        return {"symbol": r["symbol"], **bse.get_historical_price(r["scripcode"], months)}
    except Exception as e:
        logger.error(f"investment_api: price history fetch failed for {symbol}: {e}")
        raise HTTPException(status_code=502, detail=f"upstream data fetch failed: {e}")


@router.get("/equity/{symbol}/peers")
def get_peers(symbol: str):
    """BSE's own peer-group table (its own sector classification, not
    computed by us) — target company plus real comparables with Revenue,
    PAT, OPM/NPM/RONW, EPS, P/E for direct comparison."""
    r = _resolve(symbol)
    try:
        return {"symbol": r["symbol"], "peers": bse.get_peer_comparison(r["scripcode"])}
    except Exception as e:
        logger.error(f"investment_api: peer comparison fetch failed for {symbol}: {e}")
        raise HTTPException(status_code=502, detail=f"upstream data fetch failed: {e}")


def _optional(symbol: str, label: str, fn, default):
    """Every extra evidence source is best-effort: a failed fetch thins the
    dossier (the model is told what is missing) but never blocks the analysis."""
    try:
        return fn()
    except Exception as e:
        logger.warning(f"investment_api: input '{label}' unavailable for {symbol}: {e}")
        return default


def _valuation_bundle(symbol: str, r: dict, overview: dict, financials: dict, shareholding: dict, full: bool = True) -> dict:
    """Gathers every input the price-vs-value model needs and runs it. `full`
    False skips the announcements fetch (not needed for the reliability grade
    shown on every company page)."""
    price_history = _optional(symbol, "12M price", lambda: bse.get_historical_price(r["scripcode"], "12M"), {"rows": []})
    peers = _optional(symbol, "peers", lambda: bse.get_peer_comparison(r["scripcode"]), [])
    announcements = _optional(symbol, "announcements", lambda: bse.get_announcements(r["scripcode"]), []) if full else []
    fresh = financials.get("data_freshness") or {}
    if fresh.get("statements_source") == "yahoo" and fresh.get("yahoo_symbol"):
        ysym = fresh["yahoo_symbol"]
        tt_info = _optional(symbol, "yahoo profile", lambda: yh.get_info(ysym), {})
        long_history = _optional(symbol, "yahoo multi-year prices", lambda: yh.get_price_history(ysym), [])
    else:
        tt_info = _optional(symbol, "tickertape info", lambda: tt.get_info(r["tt_sid"]), {}) if r["tt_sid"] else {}
        long_history = _optional(symbol, "multi-year prices", lambda: tt.get_price_history(r["tt_sid"], "max"), []) if r["tt_sid"] else []
    return valuation.compute_all(overview, financials, shareholding, debate.summarize_price_momentum(price_history),
                                 tt_info, long_history, announcements, peers)


def _is_fund(overview: dict) -> bool:
    """Mutual funds / ETFs carry ISINs starting 'INF' (companies: 'INE')."""
    return str((overview or {}).get("isin") or "").upper().startswith("INF")


def _data_quality(overview: dict, built: Optional[dict]) -> Optional[dict]:
    """One plain-language verdict on how much the analysis for this company can
    be trusted, shown at the top of the page the moment it loads — so a
    low-reliability company is never presented like a well-covered one.
    None = nothing to flag (high reliability)."""
    if _is_fund(overview):
        return {"level": "not_applicable", "headline": "This is an ETF / fund, not a company",
                "reasons": ["Company fundamentals, quality scores and price-vs-value analysis do not apply to funds. The price and ratios shown are for information only."]}
    if not built:
        return None
    model = built["model"]
    if not model.get("available"):
        return {"level": "unavailable", "headline": "No valuation analysis is possible for this company",
                "reasons": [model.get("reason")], "code": model.get("reason_code")}
    rel = model.get("reliability") or {}
    if rel.get("level") == "low":
        return {"level": "low", "headline": "Low-reliability company — the data behind any valuation is too weak to trust",
                "reasons": rel.get("reasons") or []}
    if rel.get("level") == "medium":
        return {"level": "medium", "headline": "Some data limitations — treat the valuation with extra caution",
                "reasons": rel.get("reasons") or []}
    return None


_BACKTEST_PATH = Path(__file__).parent / "data" / "investment" / "backtest_results.json"


def _backtest_results() -> Optional[dict]:
    try:
        return json.loads(_BACKTEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@router.get("/model-track-record")
def get_model_track_record():
    """Results of the offline back-test of the price-vs-value model (investment_backtest.py).
    Static until the back-test is re-run; a missing file just means it has not been run yet."""
    res = _backtest_results()
    return {"available": True, **res} if res else {"available": False}


@router.get("/equity/{symbol}/debate")
def get_debate(symbol: str):
    """On-demand AI analyst debate — deliberately NOT included in /full,
    since it's slower (a few real LLM calls) and costs money per call;
    the frontend triggers this explicitly rather than loading it with
    everything else. Always returns 200 with 'available': False rather
    than erroring if no LLM provider is currently usable — the
    quantitative page must never break because of this optional layer."""
    r = _resolve(symbol)
    overview = get_overview(symbol)
    if _is_fund(overview):
        return {"available": False, "ai_skipped": True, "valuation_model": {"available": False},
                "reason": "This is an ETF / fund, not a company, so a company price-vs-value analysis does not apply.",
                "disclaimer": debate.DISCLAIMER}
    financials = get_financials(symbol)
    shareholding = get_shareholding(symbol)
    built = _valuation_bundle(symbol, r, overview, financials, shareholding, full=True)
    # gpt-4.1 ("openai_deep") for this on-demand, single-stock path — a multi-round
    # debate needs a much stronger model than the cheap default. Groq stays the
    # module default, reserved for the Scanner's future bulk shortlist runs.
    dossier = built["dossier"]
    if dossier and (built["model"].get("reliability") or {}).get("level") != "low":
        res = _backtest_results()      # lets the AI calibrate its confidence against how this model has really performed
        digest = backtest.track_record_for_ai(res) if res else None
        if digest:
            dossier["model_track_record"] = digest
    return debate.run_deep_debate(dossier, built["model"], provider="openai_deep")


@router.get("/equity/{symbol}/full")
def get_full_profile(symbol: str):
    """Convenience: everything above in one call, for a single page load."""
    overview = get_overview(symbol)
    financials = get_financials(symbol)
    shareholding = get_shareholding(symbol)
    # Data-quality verdict, computed on every load (all inputs are cached), so a
    # low-reliability company is flagged the moment its page appears. Never allowed
    # to break the page: on any failure the page simply shows no banner.
    data_quality = None
    try:
        built = None if _is_fund(overview) else _valuation_bundle(symbol, _resolve(symbol), overview, financials, shareholding, full=False)
        data_quality = _data_quality(overview, built)
    except Exception as e:
        logger.warning(f"investment_api: data-quality check failed for {symbol}: {e}")
    return {
        "overview": overview,
        "financials": financials,
        "shareholding": shareholding,
        "peers": get_peers(symbol),
        "data_quality": data_quality,
    }


# ── IPO Review ───────────────────────────────────────────────────────────

# Rights Issues, Buybacks, and Offer-to-Buy (OTB) entries share BSE's IPO
# list endpoint but are legally different instruments from a fresh equity
# offering — confirmed by checking every currently-open issue: every single
# IPO/FPO has an RHP URL, every single RI/OTB/BuyBack/CMN/DPI does not (DPI
# is a debt issue — bonds, not equity — so it's excluded from "equity offer"
# even though it does have an RHP-style document), because those file a
# "Letter of Offer" (or nothing analogous) instead of a Red Herring
# Prospectus. A Rights Issue is also only open to existing shareholders, and
# a Buyback is the company repurchasing its own shares — neither fits "is
# this worth investing in" the way a fresh IPO does. Still shown (the user
# wants full visibility of what's open, not just IPOs), but flagged and
# grouped separately so it's clear up front which ones have RHP-based
# analysis and which don't — labels are BSE's own, from Get_issuetype_
# dropdown/w, not guessed.
_EQUITY_OFFER_TYPES = {"IPO", "FPO"}
_ISSUE_TYPE_LABELS = {
    "IPO": "IPO", "FPO": "FPO", "OFS": "Offer for Sale", "IPP": "Institutional Placement",
    "BuyBack": "Buyback", "RI": "Rights Issue", "OTB": "Offer to Buy", "DPI": "Debt Issue",
    "InvITs": "InvIT", "REITS": "REIT", "SMREITS": "SM-REIT", "CMN": "Call Money Notice", "ZCZP": "ZCZP",
}


@router.get("/ipo/list")
def get_ipo_list():
    """All issues currently open for bidding (BSE Status 'L'), each tagged
    with is_equity_offer and a spelled-out type_full — see the module-level
    comment above for why that distinction matters (only IPO/FPO get
    RHP-based fundamentals). GMP means nothing once an issue has closed, so
    closed/historical issues don't belong here regardless of type. Each row
    is enriched with a best-effort GMP/subscription sentiment match, sorted
    equity offers first, then by closing date (soonest first)."""
    rows = bse.get_live_ipo_list()
    open_rows = [r for r in rows if r.get("Status") == "L"]
    open_rows.sort(key=lambda r: (r.get("IR_flag") not in _EQUITY_OFFER_TYPES, r.get("End_Dt") or ""))
    out = []
    for row in open_rows:
        name = row.get("Scrip_Name") or row.get("LONG_NAME") or ""
        sentiment = None
        try:
            sentiment = ipo_sentiment.get_sentiment_for(name)
        except Exception as e:
            logger.warning(f"investment_api: sentiment lookup failed for {name}: {e}")
        ir_flag = row.get("IR_flag")
        out.append({
            "ipo_no": row.get("IPO_NO"),
            "scrip_code": row.get("Scrip_cd"),
            "name": name,
            "start_date": row.get("Start_Dt"),
            "end_date": row.get("End_Dt"),
            "price_band": row.get("Price_Band"),
            "type": ir_flag,
            "type_full": _ISSUE_TYPE_LABELS.get(ir_flag, ir_flag),
            "is_equity_offer": ir_flag in _EQUITY_OFFER_TYPES,
            "platform": row.get("eXCHANGE_PLATFORM"),
            "status": row.get("Status"),
            "sentiment": sentiment,
        })
    return {"ipos": out}


def _tidy_mechanics(mechanics: dict) -> dict:
    """BSE's Price_Band field is free text: '1700.00-1785.00|/A discount of Rs 170/- ... is being offered to Eligible
    Employees...|'. Split it into the actual band (also formatted for display) and the footnote, so the page and the
    AI both get a clean band instead of a paragraph."""
    raw = str((mechanics or {}).get("price_band") or "")
    parts = [p.strip(" /") for p in raw.split("|") if p.strip(" /")]
    if not parts:
        return mechanics
    band = parts[0]
    nums = re.findall(r"\d[\d,]*\.?\d*", band)
    display = f"\u20b9{float(nums[0].replace(',', '')):,.0f} \u2013 \u20b9{float(nums[-1].replace(',', '')):,.0f}" if len(nums) >= 2 else (f"\u20b9{band}" if nums else band)
    note = " ".join(parts[1:]).strip()
    return {**mechanics, "price_band": band, "price_band_display": display, "price_band_note": note[:1].upper() + note[1:] if note else None}


@router.get("/ipo/{ipo_no}")
def get_ipo_detail(ipo_no: str):
    """Issue mechanics + BSE's raw bid-demand data + best-effort GMP/
    category-wise subscription sentiment."""
    try:
        mechanics = _tidy_mechanics(bse.get_ipo_details(ipo_no))
    except Exception as e:
        logger.error(f"investment_api: IPO details fetch failed for {ipo_no}: {e}")
        raise HTTPException(status_code=502, detail=f"upstream data fetch failed: {e}")
    sentiment = None
    if mechanics.get("scrip_name"):
        try:
            sentiment = ipo_sentiment.get_sentiment_for(mechanics["scrip_name"])
        except Exception as e:
            logger.warning(f"investment_api: sentiment lookup failed for {ipo_no}: {e}")
    return {"ipo_no": ipo_no, "mechanics": mechanics, "sentiment": sentiment}


@router.get("/ipo/{ipo_no}/analysis")
def get_ipo_analysis(ipo_no: str):
    """The heavy one: downloads (cached, permanent) and parses the RHP for
    risk factors + restated 3-year financials + objects of the offer,
    computes Piotroski/Altman-Z' straight from those RHP financials, and
    runs the decisive AI verdict debate on top of all of it. Genuinely
    slow on a cold cache (RHP PDFs run 300-600+ pages) — cached after the
    first request since a filed RHP never changes."""
    try:
        mechanics = _tidy_mechanics(bse.get_ipo_details(ipo_no))
    except Exception as e:
        logger.error(f"investment_api: IPO details fetch failed for {ipo_no}: {e}")
        raise HTTPException(status_code=502, detail=f"upstream data fetch failed: {e}")

    rhp_url = mechanics.get("rhp_url")
    rhp_data = {"risk_factors": [], "summary_financials": None, "objects_of_offer": [], "errors": []}
    if rhp_url:
        try:
            rhp_data = rhp_extractor.extract(rhp_url)
        except Exception as e:
            logger.error(f"investment_api: RHP extraction failed for {ipo_no}: {e}")
            rhp_data["errors"] = [str(e)]
    elif mechanics.get("scrip_name") is not None:
        rhp_data["errors"] = [
            "no RHP available — this issue type doesn't file a Red Herring Prospectus "
            "(only fresh IPOs/FPOs do; Rights Issues, Buybacks, and Offer-to-Buy issues use "
            "a different disclosure document and aren't a fit for this fundamentals-based analysis)"
        ]

    piotroski = scores.compute_piotroski_from_rhp(rhp_data.get("summary_financials"))
    altman_zprime = scores.compute_altman_zprime(rhp_data.get("summary_financials"))
    trend = scores.compute_yoy_trend(rhp_data.get("summary_financials"))

    sentiment = None
    if mechanics.get("scrip_name"):
        try:
            sentiment = ipo_sentiment.get_sentiment_for(mechanics["scrip_name"])
        except Exception as e:
            logger.warning(f"investment_api: sentiment lookup failed for {ipo_no}: {e}")

    investor_interest = scores.classify_investor_interest((sentiment or {}).get("subscription"))

    if rhp_url:
        verdict = debate.run_ipo_verdict(
            mechanics, sentiment, rhp_data,
            {"piotroski": piotroski, "altman_zprime": altman_zprime},
            trend, investor_interest,
            provider="openai",
        )
    else:
        # No RHP at all (Rights Issue/Buyback/OTB/etc.) — a verdict built on
        # essentially no fundamentals data would just restate "insufficient
        # data" in verdict-shaped clothing, contradicting the clear banner
        # already shown above this. Skip the LLM call entirely rather than
        # dress up a non-answer as a decisive-looking star rating.
        verdict = {"available": False, "reason": rhp_data["errors"][0] if rhp_data.get("errors") else
                   "not applicable — this issue type has no RHP-based fundamentals to analyze",
                   "disclaimer": debate.DISCLAIMER}

    return {
        "ipo_no": ipo_no,
        "mechanics": mechanics,
        "sentiment": sentiment,
        "rhp": rhp_data,
        "piotroski_f_score": piotroski,
        "altman_zprime_score": altman_zprime,
        "yoy_trend": trend,
        "investor_interest": investor_interest,
        "verdict": verdict,
    }
