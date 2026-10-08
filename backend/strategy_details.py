"""
strategy_details.py -- data for the Strategy Details page (TODO B-8, built 08 Oct 2026).

Each Backtest-page strategy: a plain-English description and its backtest results on BANKNIFTY,
NIFTY, SENSEX and CRUDEOIL. Your decisions (08 Oct): all 16 strategies, grouped "Live now" /
"Backtest only"; period 1 Jul 2025 to the latest completed day; only the LAST run is kept, with
its date; a Refresh button PER STRATEGY re-runs only that strategy on the 4 instruments, after market
hours (main.py: same data download + backtest worker as the Backtest page; results merged row by row).
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import pytz

logger = logging.getLogger(__name__)
_IST = pytz.timezone("Asia/Kolkata")

DATA_DIR = Path(__file__).parent / "data" / "strategy_details"
SNAPSHOT_PATH = DATA_DIR / "latest.json"
INSTRUMENTS = ["BANKNIFTY", "NIFTY", "SENSEX", "CRUDEOIL"]
PERIOD_FROM = "2025-07-01"
CAPITAL = 500_000

_VOL_NOTE = ("Uses volume (VWAP): live signals are decided on Dhan's official candle, usually 1-3 min after "
             "the close (live index candles carry no volume).")

STRATEGIES = [
    # ── Live now ──────────────────────────────────────────────────────────────────────────────────
    {"id": "custom_option_b_ram_rf", "group": "live", "name": "Option B: Ram > Range Filter",
     "summary": "Trend-following on 5-min candles, built from your own TradingView scripts (Ram_strategy + Range "
                "Filter). One position at a time; holds overnight (carry-forward).",
     "entry": "Ram opens at the next candle's open when a fresh trigger (Range Filter flip or UT Bot cross) appears "
              "on the same candle as its trend filters (ATR trails, SuperTrend, candle colour, price vs ALMA). "
              "The live app acts at the signal candle's close.",
     "exit": "Ram exits via a limit at its UT Bot opposite-signal level; if the Range Filter still points the same "
             "way it keeps holding until the Range Filter flips. No-progress exit: not +10 pts in profit within 4 "
             "candles -> out at that candle's close. An opposite Ram signal reverses.",
     "risk": "1,500-pt index stop (never reached in 5 years of backtest). Live option entries have no broker "
             "stop-loss; the app exits them.",
     "notes": "Honest rules since 08 Oct (two backtest look-aheads removed; the live app follows the same rules). "
              "About half of its 5-year result came in 2026."},
    {"id": "custom_alpha_combo_cusum125", "group": "live", "name": "Alpha Combo (CUSUM 1.25 Tuned)",
     "summary": "CUSUM change-point breakouts (threshold 1.25) combined with raw HalfTrend reversal flips.",
     "entry": "A CUSUM break of the recent trend, or a HalfTrend flip, accepted by the regime / trend filters.",
     "exit": "ATR stop with breakeven lock and trailing stop; regime exits; cooldown after a loss.",
     "risk": "ATR-based stop on every trade.", "notes": _VOL_NOTE},
    {"id": "custom_time_gated_alpha_combo", "group": "live", "name": "Time-Gated Alpha Combo (Anti-Trap)",
     "summary": "Alpha Combo with no NEW entries in two trap windows: 10:00-10:45 and 13:00-13:45.",
     "entry": "As Alpha Combo, except no new entries inside the two windows.",
     "exit": "As Alpha Combo (open trades are managed normally through the windows).",
     "risk": "ATR-based stop on every trade.",
     "notes": "Windows and parameters were tuned on BankNifty only. " + _VOL_NOTE},
    {"id": "custom_cusum15_nodonchian_cd8", "group": "live", "name": "CUSUM 1.5 + No Donchian + CD8",
     "summary": "CUSUM change-point entries at threshold 1.5, Donchian entries switched off, 8-candle cooldown "
                "after a loss.",
     "entry": "A CUSUM break of the recent trend accepted by the regime filters.",
     "exit": "ATR stop with breakeven / trailing; cooldown after a loss.",
     "risk": "Known max drawdown ~7.9%, above the 7% budget (accepted when it was promoted).",
     "notes": _VOL_NOTE},
    {"id": "custom_regime_v1_trend_range_final", "group": "live", "name": "Regime T/R V1 Final",
     "summary": "Switches playbook by regime: trend days use breakouts, range days use mean reversion.",
     "entry": "Trend side: Donchian breakout + CUSUM filter + SuperTrend-adaptive entries. Range side: VWAP "
              "session-anchored mean reversion.",
     "exit": "Trend side: higher-timeframe hold exit. Range side: Ornstein-Uhlenbeck half-life time stop.",
     "risk": "ATR-based stops; validated drawdown ~6.9%.", "notes": _VOL_NOTE},
    # ── Backtest only ─────────────────────────────────────────────────────────────────────────────
    {"id": "custom_option_a_tg_ram_rf", "group": "backtest", "name": "Option A: Time-Gated > Ram > Range Filter",
     "summary": "One lot by priority: Time-Gated Alpha Combo first, then Ram, then Range Filter fills any "
                "remaining time.",
     "entry": "The first engine holding a position controls the lot; Range Filter may open trades here.",
     "exit": "Each engine's own exits; same direction across a hand-over keeps holding.",
     "risk": "1,500-pt index stop.",
     "notes": "Slow (runs the full Time-Gated backtest inside). Depends on Time-Gated, which was flat to "
              "negative after costs before mid-2025."},
    {"id": "custom_ram_rf_box", "group": "backtest", "name": "Option C: Ram + Range Filter + S/R Box",
     "summary": "Option B plus a sideways filter: no new Ram entries inside a tight box (24 candles within "
                "7 ATR, middle 60%).",
     "entry": "As Option B, unless price sits in the middle of a tight recent range.",
     "exit": "As Option B.", "risk": "2,000-pt index stop.",
     "notes": "After its look-ahead fix (01 Oct) it does not beat Option B."},
    {"id": "custom_cusum15_plus_raw_halftrend", "group": "backtest", "name": "CUSUM + HalfTrend Combo",
     "summary": "CUSUM 1.5 (as the live CUSUM strategy) plus raw, unfiltered HalfTrend flips.",
     "entry": "CUSUM break, or a HalfTrend flip.", "exit": "ATR stop with breakeven / trailing.",
     "risk": "ATR-based stop.", "notes": "Research step towards Alpha Combo."},
    {"id": "custom_halftrend_hull_standalone", "group": "backtest", "name": "HalfTrend + Hull (Standalone)",
     "summary": "HalfTrend reversal flip confirmed by a Hull-momentum flip within a short lookback (your "
                "PineScript research).",
     "entry": "HalfTrend flip + Hull momentum flip in the same direction.", "exit": "ATR stop / trailing.",
     "risk": "ATR-based stop.", "notes": "Was on Live Trading until Option B replaced it (01 Oct)."},
    {"id": "custom_multi_agent_v2", "group": "backtest", "name": "Multi-Agent V2 (Loss Reduction)",
     "summary": "The Multi-Agent V3 engine with loss-reduction settings.",
     "entry": "Multi-Agent signals; short entries vetoed when StochRSI < 20.",
     "exit": "Breakeven at +0.8 ATR; 4-candle pause after a loss.",
     "risk": "Daily loss cap Rs 3,500.", "notes": ""},
    {"id": "multi_agent", "group": "backtest", "name": "Multi-Agent V3",
     "summary": "The original multi-agent strategy: several analysis agents combined by an orchestrator.",
     "entry": "Orchestrator score from the agents.", "exit": "Trailing stop / breakeven.",
     "risk": "ATR-based stop.", "notes": "Retired from live trading."},
    {"id": "regime_trend_range", "group": "backtest", "name": "Regime Trend/Range (Optimized)",
     "summary": "Regime agents (detection, trend entry, HTF structure, momentum/volume) with tight trade "
                "management for 20-30 pt moves.",
     "entry": "Trend entry agent signals in the detected regime.",
     "exit": "Trailing 1.5 ATR, breakeven at +0.4 ATR, end-of-day exit.", "risk": "ATR-based stop.",
     "notes": "Retired from live trading."},
    {"id": "regime_trend_v2", "group": "backtest", "name": "Regime Trend V2 (Selective)",
     "summary": "Regime Trend with the exit asymmetry fixed: winners run (4 ATR trail) instead of being banked "
                "at ~0.5 ATR. Selective profile (fewer trades).",
     "entry": "As Regime Trend, stricter.", "exit": "Wider ATR trail.", "risk": "ATR-based stop.", "notes": ""},
    {"id": "regime_trend_v2b", "group": "backtest", "name": "Regime Trend V2-B (Balanced)",
     "summary": "Regime Trend V2 balanced profile: more trades than Selective.",
     "entry": "As Regime Trend V2, looser.", "exit": "Wider ATR trail.", "risk": "ATR-based stop.", "notes": ""},
    {"id": "donchian_5m_swing", "group": "backtest", "name": "Donchian 5m Swing (overnight)",
     "summary": "5-min Donchian channel breakout, 36 candles (3 hours); may hold overnight.",
     "entry": "Close beyond the 3-hour channel.", "exit": "Trailing stop 5 ATR.", "risk": "Stop 3 ATR.",
     "notes": ""},
    {"id": "donchian_5m_intraday", "group": "backtest", "name": "Donchian 5m Intraday",
     "summary": "5-min Donchian channel breakout, closed the same day.",
     "entry": "Close beyond the channel.", "exit": "Trailing stop; flat before the close.",
     "risk": "ATR-based stop.", "notes": ""},
]
STRATEGY_IDS = [s["id"] for s in STRATEGIES]

STAT_KEYS = ("total_trades", "wins", "losses", "win_rate_pct", "profit_factor", "total_pnl", "avg_win",
             "avg_loss", "max_drawdown_pct", "expectancy", "exit_distribution")


def load_snapshot():
    try:
        if SNAPSHOT_PATH.exists():
            return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Strategy details: could not read {SNAPSHOT_PATH.name}: {e}")
    return None


def save_snapshot(snap: dict) -> None:
    """Replace the last snapshot (written to a temp file first, so a failure never leaves half a file)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, indent=1, default=str), encoding="utf-8")
    tmp.replace(SNAPSHOT_PATH)


def slim_stats(stats: dict, lot_qty: int) -> dict:
    """The page's numbers for one run (gross, as the backtest engine reports them)."""
    out = {k: (stats or {}).get(k) for k in STAT_KEYS}
    try:
        out["points"] = round(float(out["total_pnl"] or 0) / float(lot_qty), 1) if lot_qty else None
    except Exception:
        out["points"] = None
    return out


def refresh_allowed(now=None):
    """(ok, reason): refreshes run only outside market hours (they share Dhan's history rate limit with live)."""
    now = now or datetime.now(_IST)
    if now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) < (15, 40):
        return False, "Refresh runs only outside market hours (before 09:00 or after 15:40 on weekdays)."
    return True, ""
