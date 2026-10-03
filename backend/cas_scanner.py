"""
cas_scanner.py — CAS-window and "undercurrent" option spike scanner.

Background daemon thread (mirrors order_update_feed.py's shape), using its
own dedicated Dhan connection (cas_broker.py) fully isolated from the live
trading engine. Alert-only -- never places orders, never touches
trade_manager state. See the plan this was built from (CAS Spike Scanner)
for the full research/reasoning behind the design.

Two independent detection modes, sharing one periodic full-universe sweep:

  Mode A ("CAS Window"): stock options only, shortlists near-worthless
  strikes expiring soon (days-to-expiry is the load-bearing signal, not
  moneyness -- verified against a real 0-DTE, at-the-money case where a
  "deep OTM" filter would have missed it entirely). Reactive spike
  detection only runs 15:05-15:28 IST, per explicit scope.

  Mode B ("Undercurrent"): stocks AND indices, all day. Flags contracts on
  Volume/OI ratio (primary), building near-ATM OI concentration over time
  (a genuine gamma/pin-risk precursor, not just an instantaneous trigger),
  and IV-skew as a corroborating (not independent) signal.

The scoring functions below are pure (data in, results out) so they can be
unit-tested against synthetic option-chain snapshots without any network
or threading involved -- see scratch/ for the test harness.
"""

import json
import logging
import threading
import time as _time
from datetime import datetime, timedelta, date, time as _dtime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_IST_OFFSET = timedelta(hours=5, minutes=30)


def _now_ist() -> datetime:
    return datetime.utcnow() + _IST_OFFSET


# Found live (2026-08-28, ~20 min after open): the scanner had been running
# since pre-market (09:00, well before NSE/BSE F&O continuous trading opens
# at 09:15 -- MARKET_HOURS["NFO"] in config.py). Once 30 minutes of wall
# clock had passed, compute_concentration_trend()'s baseline lookup started
# reaching back into those pre-open samples -- pre-open-auction OI/price
# data is structurally different from real continuous-trading data, so
# comparing against it produced a spurious "rising concentration" read
# across ~20 unrelated stocks simultaneously (all firing within the same
# ~14ms), not genuine buildup in any of them. Gate history recording on
# the market actually being open so a pre-open sample can never become a
# trend baseline again.
def _in_continuous_session(now: datetime) -> bool:
    return _dtime(9, 15) <= now.time() <= _dtime(15, 30)


# Found live (2026-08-28, 09:20 IST): letting Mode C track through the open
# meant dozens of zero-prior-day-OI strikes cleared the 5-min confirm window
# simultaneously on ordinary opening-range price discovery -- not genuine
# bursts. Suppressing just the ALERT during an opening window wouldn't have
# fixed it: the same strikes would still all become "confirmed but held"
# together underneath, and fire in one batch the instant suppression lifted.
# Skipping the sweep entirely for the first cas_index_opening_range_minutes
# means tracking only ever starts from a genuinely settled baseline once
# the window ends.
def _market_traded_today(now: datetime) -> bool:
    """Weekday and NSE actually traded today (NIFTY has 1-minute bars dated today --
    no bars on an exchange holiday). A "yes" is kept for the day; a "no" is re-checked
    every 3 minutes (bars appear a minute or so after 09:15). If the check itself fails,
    scanning goes ahead (a Dhan hiccup must not silently stop the scanner)."""
    import cas_broker
    if now.weekday() >= 5:
        return False
    today = now.date().isoformat()
    td = _trading_day
    if td["date"] == today and td["traded"] is True:
        return True
    if td["date"] == today and td["traded"] is False and _time.time() - td["checked_at"] < 180:
        return False
    traded = cas_broker.traded_today(today)
    if traded is None:
        return True
    td.update(date=today, traded=traded, checked_at=_time.time())
    if not traded:
        logger.info(f"CAS scanner: no NSE trading today so far ({today}) -- paused")
    return traded


def _market_open(now: datetime) -> bool:
    """Scanner works only while the F&O market is really open: 09:15-15:30 IST on a day
    NSE traded. It used to run around the clock -- 18,261 failed option-chain calls on
    the 2 Oct holiday, digests at 19:52/20:10 repeating end-of-day data."""
    if now.weekday() >= 5:
        reason = "Weekend"
    elif not _in_continuous_session(now):
        reason = "Outside market hours (09:15-15:30 IST)"
    elif not _market_traded_today(now):
        reason = "Market not trading today (holiday)"
    else:
        reason = None
    _status["market_open"] = reason is None
    _status["paused_reason"] = reason
    return reason is None


def _index_tracking_allowed(now: datetime, cfg) -> bool:
    if not _market_open(now):
        return False
    open_dt = now.replace(hour=9, minute=15, second=0, microsecond=0)
    return now >= open_dt + timedelta(minutes=cfg.cas_index_opening_range_minutes)


# ── Tracer — same TRACE[id] convention as main.py's live-signal tracer, so
# `grep TRACE\[` in app.log surfaces both systems the same way and a full
# sweep/alert lifecycle can be reconstructed after the fact. Deliberately not
# imported from main.py (keeps cas_scanner.py standalone, no circular import,
# same reasoning as cas_broker.py's isolation) -- just the same convention. ──
def _trace_id(*parts) -> str:
    import hashlib
    key = "|".join(str(p) for p in parts)
    return hashlib.md5(key.encode()).hexdigest()[:8]


def _trace(trace_id: str, stage: str, detail: str = ""):
    logger.info(f"TRACE[{trace_id}] CAS_{stage}" + (f": {detail}" if detail else ""))


# ── Module state ───────────────────────────────────────────────────────────
_running = False
_thread: Optional[threading.Thread] = None
_index_thread: Optional[threading.Thread] = None
_state_lock = threading.Lock()

# What the CAS page shows about the scanner itself (so "nothing flagged" can be told
# apart from "not running" / "market closed").
_status = {
    "market_open": False, "paused_reason": None,
    "last_sweep_at": None, "last_sweep_seconds": None, "last_sweep_stocks": 0, "last_sweep_failures": 0,
    "last_index_sweep_at": None,
}
_trading_day = {"date": None, "traded": None, "checked_at": 0.0}

_at_risk: list = []          # Mode A shortlist
_undercurrent: list = []     # Mode B flagged list
_alerts_a: list = []         # Mode A fired alerts, today
_alerts_b: list = []         # Mode B fired alerts, today
_alert_day: Optional[date] = None

_heatmap: list = []          # per-underlying aggregate view, ALL F&O underlyings every sweep
# symbol -> {"first_seen", "last_seen", "sweep_count", "dominant"} -- carries
# streak continuity across sweeps for the heatmap; NOT reset on every sweep
# (only on day boundary), unlike _undercurrent which is rebuilt from scratch.
_heat_streak_history: dict = {}

# Per-contract rolling premium history for Mode A spike detection:
# key -> list[(timestamp, price)]
_premium_history: dict = {}
# Per-underlying near-ATM OI concentration history for Mode B's building signal:
# symbol -> list[(timestamp, concentration_pct, otm_bias_pct)]
_oi_concentration_history: dict = {}
# Per-strike rolling (timestamp, volume, oi) samples for Mode B's accumulation
# gate (2026-08-26 redesign) -- "symbol:strike:side:expiry" -> [(ts, vol, oi), ...],
# trimmed to the confirm window. Persisted to disk each sweep and reloaded on
# restart (same day only) -- an in-memory-only version of this is exactly what
# caused the 12k+ alert flood after today's many redeploys, since every
# restart re-treated every already-elevated strike as brand new.
_strike_activity_state: dict = {}
_UNDERCURRENT_STATE_PATH = Path(__file__).parent / "data" / "cas_undercurrent_state.json"
# Zero-baseline-OI candidates (real volume against literally no starting OI --
# the strongest possible version of this signal) rank as maximal without using
# a real Python inf, which json.dumps can't serialize for the WS broadcast/API.
_INF_RATIO_SENTINEL = 999999.0
# Throttle: don't re-fire the same contract/signal within the same day
_last_alerted: dict = {}  # key -> datetime
# Last time the consolidated Mode B digest (top-N-per-side, one Telegram
# message) was sent -- deliberately NOT persisted across a restart, since at
# worst a restart triggers one extra digest, nowhere near the per-candidate
# flood this replaced.
_last_digest_sent: Optional[datetime] = None

# Mode C -- Index Burst (2026-08-27): the 3 indices get their own rolling
# accumulation state, kept apart from _strike_activity_state so the index
# fast loop (cas_index_sweep_seconds cadence) and the stock sweep (5 min
# cadence) never stomp each other's per-strike history mid-window.
_index_strike_activity_state: dict = {}
# Latest confirmed candidates from each source. Two independent
# loops/cadences feed the same final _undercurrent list, so each keeps its
# own raw output here and _recompute_undercurrent_and_heatmap() merges them
# every time either refreshes -- otherwise whichever loop runs last would
# silently wipe out the other's contribution.
_stock_undercurrent_raw: list = []
_index_undercurrent_raw: list = []
# Mode C alerts fire immediately per-event (unlike Mode B's digest-only
# stock side -- a fast index burst can complete before the next digest), but
# the count still gets folded into the next digest's summary line so the
# rollup the user asked for isn't just the stock side.
_index_alerts_since_digest: int = 0

# Set by main.py at startup so this module can broadcast without importing it
# (avoids a circular import -- main.py imports cas_scanner, not vice versa).
_ws_broadcast_fn = None
_telegram_fn = None


def set_broadcast_hooks(ws_broadcast_fn, telegram_fn):
    """main.py calls this once at startup to wire up WS broadcast and Telegram
    send without cas_scanner.py importing main.py directly."""
    global _ws_broadcast_fn, _telegram_fn
    _ws_broadcast_fn = ws_broadcast_fn
    _telegram_fn = telegram_fn


def _broadcast(msg: dict):
    if _ws_broadcast_fn is not None:
        try:
            _ws_broadcast_fn(msg)
        except Exception as e:
            logger.debug(f"CAS scanner: WS broadcast failed: {e}")


def _send_telegram(message: str):
    if _telegram_fn is not None:
        try:
            _telegram_fn(message)
        except Exception as e:
            logger.warning(f"CAS scanner: Telegram send failed: {e}")


def _save_day_state():
    """Persist all day-scoped in-memory state to disk. Without this, every
    restart wiped it clean -- confirmed live (2026-08-26) as the direct cause
    of a 12k+ alert flood: ~15 redeploys in one session each made every
    already-elevated candidate and already-sent alert look brand new again."""
    try:
        payload = {
            "date": _alert_day.isoformat() if _alert_day else None,
            "alerts_a": _alerts_a,
            "alerts_b": _alerts_b,
            "last_alerted": {k: v.isoformat() for k, v in _last_alerted.items()},
            "oi_concentration_history": {
                k: [[ts.isoformat(), pct, bias] for ts, pct, bias in v]
                for k, v in _oi_concentration_history.items()
            },
            "heat_streak_history": {
                k: {**v, "first_seen": v["first_seen"].isoformat(), "last_seen": v["last_seen"].isoformat()}
                for k, v in _heat_streak_history.items()
            },
            "strike_activity_state": {
                k: [[ts.isoformat(), vol, oi] for ts, vol, oi in v]
                for k, v in _strike_activity_state.items()
            },
            "index_strike_activity_state": {
                k: [[ts.isoformat(), vol, oi] for ts, vol, oi in v]
                for k, v in _index_strike_activity_state.items()
            },
        }
        _UNDERCURRENT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _UNDERCURRENT_STATE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logger.warning(f"CAS scanner: failed to save day state: {e}")


def _load_day_state_if_today() -> bool:
    """Restore today's day-scoped state from disk on a restart (not a real
    day boundary). Returns True if state was restored, False if there's
    nothing usable (no file, or it's from a different day)."""
    global _alert_day, _alerts_a, _alerts_b, _last_alerted, _oi_concentration_history, _heat_streak_history, _strike_activity_state, _index_strike_activity_state
    try:
        if not _UNDERCURRENT_STATE_PATH.exists():
            return False
        payload = json.loads(_UNDERCURRENT_STATE_PATH.read_text(encoding="utf-8"))
        today = _now_ist().date()
        if payload.get("date") != today.isoformat():
            return False
        _alert_day = today
        _alerts_a = payload.get("alerts_a", [])
        _alerts_b = payload.get("alerts_b", [])
        _last_alerted = {k: datetime.fromisoformat(v) for k, v in payload.get("last_alerted", {}).items()}
        _oi_concentration_history = {
            k: [(datetime.fromisoformat(ts), pct, bias) for ts, pct, bias in v]
            for k, v in payload.get("oi_concentration_history", {}).items()
        }
        _heat_streak_history = {
            k: {**v, "first_seen": datetime.fromisoformat(v["first_seen"]), "last_seen": datetime.fromisoformat(v["last_seen"])}
            for k, v in payload.get("heat_streak_history", {}).items()
        }
        _strike_activity_state = {
            k: [(datetime.fromisoformat(ts), vol, oi) for ts, vol, oi in v]
            for k, v in payload.get("strike_activity_state", {}).items()
        }
        _index_strike_activity_state = {
            k: [(datetime.fromisoformat(ts), vol, oi) for ts, vol, oi in v]
            for k, v in payload.get("index_strike_activity_state", {}).items()
        }
        logger.info(
            f"CAS scanner: restored day state from disk ({len(_alerts_a)} A-alerts, "
            f"{len(_alerts_b)} B-alerts, {len(_strike_activity_state)} tracked strikes)"
        )
        return True
    except Exception as e:
        logger.warning(f"CAS scanner: failed to load day state: {e}")
        return False


def _reset_if_new_day():
    global _alert_day, _alerts_a, _alerts_b, _premium_history, _oi_concentration_history, _last_alerted, _heat_streak_history, _strike_activity_state, _index_strike_activity_state
    today = _now_ist().date()
    if _alert_day == today:
        return  # already initialized for today
    if _alert_day is None and _load_day_state_if_today():
        return  # first call this process, and disk had today's state -- restart, not a real day boundary
    _alert_day = today
    _alerts_a = []
    _alerts_b = []
    _premium_history = {}
    _oi_concentration_history = {}
    _last_alerted = {}
    _heat_streak_history = {}
    _strike_activity_state = {}
    _index_strike_activity_state = {}
    logger.info("CAS scanner: day boundary reset")


# ══════════════════════════════════════════════════════════════════════════
# PURE SCORING FUNCTIONS — no I/O, unit-testable directly
# ══════════════════════════════════════════════════════════════════════════

def _extract_oc(chain_resp: dict):
    """Normalize the two response shapes seen from the dhanhq SDK
    ({"data": {"last_price":..., "oc":{...}}} vs the doubly-nested
    {"data": {"data": {...}}}) into (spot, oc_dict)."""
    if not isinstance(chain_resp, dict):
        return None, None
    data = chain_resp.get("data", {})
    if isinstance(data, dict) and "oc" in data:
        inner = data
    elif isinstance(data, dict) and isinstance(data.get("data"), dict):
        inner = data["data"]
    else:
        return None, None
    return inner.get("last_price"), inner.get("oc", {})


def score_mode_a(chain_resp: dict, symbol: str, expiry_date_str: str, today: date,
                  premium_floor: float, max_days_to_expiry: int, atm_band_pct: float) -> list:
    """Mode A scoring: near-worthless + expiring soon (the load-bearing
    signal), with near-ATM OI concentration as a ranking boost, not a filter.
    Returns a list of candidate dicts, highest-priority first."""
    spot, oc = _extract_oc(chain_resp)
    if not spot or not oc:
        return []

    try:
        expiry_dt = datetime.strptime(expiry_date_str[:10], "%Y-%m-%d").date()
    except Exception:
        return []
    days_to_expiry = (expiry_dt - today).days
    if days_to_expiry < 0 or days_to_expiry > max_days_to_expiry:
        return []  # not near-term enough -- Mode A doesn't apply to this expiry at all

    total_oi = sum(
        (sides.get(side, {}).get("oi") or 0)
        for sides in oc.values() for side in ("ce", "pe")
    )

    candidates = []
    for strike_str, sides in oc.items():
        try:
            strike = float(strike_str)
        except (TypeError, ValueError):
            continue
        near_atm = abs(strike - spot) / spot * 100.0 <= atm_band_pct
        for side in ("ce", "pe"):
            d = sides.get(side, {})
            ltp = d.get("last_price")
            if ltp is None or ltp <= 0 or ltp > premium_floor:
                continue
            oi = d.get("oi") or 0
            oi_share = (oi / total_oi * 100.0) if total_oi > 0 else 0.0
            # Ranking score: cheaper + sooner + more near-ATM OI concentration
            # all push a contract higher, matching the DIXON shape exactly.
            score = (
                (max_days_to_expiry + 1 - days_to_expiry) * 10
                + (premium_floor - ltp)
                + (oi_share if near_atm else 0.0) * 2
            )
            candidates.append({
                "symbol": symbol, "strike": strike, "option_type": side.upper(),
                "expiry": expiry_date_str[:10], "days_to_expiry": days_to_expiry,
                "premium": ltp, "spot": spot, "oi": oi, "oi_share_pct": round(oi_share, 2),
                "near_atm": near_atm, "volume": d.get("volume") or 0,
                "score": score,
            })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def _classify_flow(side: str, oi: float, previous_oi, last_price, previous_close_price):
    """Classify one option leg's OI-vs-price move into the standard
    Long Buildup / Short Buildup / Short Covering / Long Unwinding quadrant
    (comparing today's OI/price to the prior close, the same convention used
    by every options-flow screener), then translate that into a plain
    buying-vs-writing read on the underlying direction. Returns (flow, bias);
    both None if oi/price change can't be determined."""
    if previous_oi is None or last_price is None or previous_close_price is None:
        return None, None
    oi_change = oi - previous_oi
    price_change = last_price - previous_close_price
    if oi_change == 0:
        return "flat", None
    if oi_change > 0:
        flow = "long_buildup" if price_change > 0 else "short_buildup"
    else:
        flow = "short_covering" if price_change > 0 else "long_unwinding"

    # CE buyers/coverers bet the underlying rises; PE buyers/coverers bet it
    # falls. Writers/unwinders lean the opposite way. This mirrors the same
    # OTM-side-OI proxy caveat already used for Mode B's concentration signal
    # -- a lean read off positioning, not a certainty about who's on the
    # other side of the trade.
    bullish_flows_ce = {"long_buildup", "short_covering"}
    bullish = (side == "ce") == (flow in bullish_flows_ce)
    bias = "bullish" if bullish else "bearish"
    return flow, bias


def score_mode_b(chain_resp: dict, symbol: str, expiry_date_str: str,
                  undercurrent_ratio: float, atm_band_pct: float,
                  strike_activity_state: dict, now: datetime,
                  entry_ratio: float = 1.5, confirm_minutes: float = 30.0,
                  oi_concentration_trend: Optional[float] = None,
                  otm_oi_bias: Optional[float] = None,
                  otm_side_lean: Optional[str] = None,
                  min_accumulated_volume: float = 0.0) -> tuple:
    """Mode B scoring: a per-strike accumulation gate (primary trigger) OR a
    rising OI-concentration trend (second, independent trigger path), with
    IV-skew used only to adjust confidence on either. Applies to stocks and
    indices alike -- no expiry-proximity requirement, unlike Mode A.

    Redesigned 2026-08-26 after a single-snapshot Volume/OI ratio flooded
    12k+ alerts in one live session -- researched real practice (VPIN,
    gamma/dealer-positioning strike-clustering literature) rather than just
    raising the threshold. A single instantaneous ratio can't distinguish a
    one-off print from genuine building pressure; VPIN-style volume
    accumulation over a real window, required to persist, can:
      1. entry_ratio is a loose bar (instantaneous vol/oi) to START tracking
         a strike's rolling history at all -- cheap, keeps memory bounded to
         only strikes that showed at least some initial elevation.
      2. Once tracked, accumulated volume since the OLDEST sample still
         inside the rolling confirm_minutes window (not "since first ever
         seen" -- that would only monotonically increase and could never
         genuinely go quiet) is compared against that sample's OI as a
         baseline -- undercurrent_ratio is now this accumulated ratio, not
         an instantaneous snapshot.
      3. A strike only becomes an actual candidate once it has persisted for
         the full confirm_minutes AND cleared the accumulation ratio -- both,
         not either.
      4. Strike clustering: neighboring strikes (same side) also showing
         elevated *instantaneous* activity right now is a real, named
         pattern in gamma/dealer-positioning research (OI/volume
         concentrating and spreading to adjacent strikes signals genuine
         institutional buildup, not an isolated fluke) -- boosts confidence,
         doesn't gate.

    min_accumulated_volume (2026-08-28, Mode C only -- default 0.0 is a
    no-op, preserving Mode B's stock-side behavior exactly): the
    zero-baseline-OI ("infinite ratio") path has no natural signal-strength
    floor otherwise -- a handful of contracts trading against a strike with
    zero prior OI confirms just as strongly as a real burst. Caught live:
    at real market open, dozens of deep-OTM zero-OI strikes get their first
    opening-range volume near-simultaneously (ordinary price discovery, not
    genuine unusual activity), and without this floor they all confirm
    together. Gates the zero-baseline-OI path specifically, not the regular
    ratio path (which already has an implicit strength dimension via the
    ratio itself).

    Returns (candidates, updated_strike_activity_state) -- still pure/no I/O,
    unit-testable by feeding synthetic chains across successive calls."""
    spot, oc = _extract_oc(chain_resp)
    if not spot or not oc:
        return [], strike_activity_state

    window = timedelta(minutes=confirm_minutes * 1.2)
    new_state = dict(strike_activity_state)

    # Pre-compute instantaneous ratio per (strike, side) once -- used both for
    # the entry gate and, below, for the clustering neighbor lookup.
    ratio_by_strike_side = {}
    for strike_str, sides in oc.items():
        try:
            strike = float(strike_str)
        except (TypeError, ValueError):
            continue
        for side in ("ce", "pe"):
            d = sides.get(side, {})
            oi = d.get("oi") or 0
            vol = d.get("volume") or 0
            ratio_by_strike_side[(strike, side)] = (vol / oi) if oi > 0 else (float("inf") if vol > 0 else 0.0)
    sorted_strikes = sorted({s for s, _ in ratio_by_strike_side.keys()})

    expiry_short = expiry_date_str[:10] if expiry_date_str else ""
    ivs_near_atm = []
    candidates = []
    for strike_str, sides in oc.items():
        try:
            strike = float(strike_str)
        except (TypeError, ValueError):
            continue
        near_atm = abs(strike - spot) / spot * 100.0 <= atm_band_pct
        for side in ("ce", "pe"):
            d = sides.get(side, {})
            oi = d.get("oi") or 0
            vol = d.get("volume") or 0
            iv = d.get("implied_volatility") or 0
            if iv > 0 and near_atm:
                ivs_near_atm.append(iv)

            key = f"{symbol}:{strike}:{side}:{expiry_short}"
            current_ratio = ratio_by_strike_side[(strike, side)]
            already_tracked = key in new_state
            if not already_tracked and current_ratio < entry_ratio:
                continue  # never crossed the loose entry bar -- nothing to track

            history = list(new_state.get(key, []))
            history.append((now, vol, oi))
            cutoff = now - window
            history = [h for h in history if h[0] >= cutoff]
            if not history:
                new_state.pop(key, None)
                continue
            new_state[key] = history

            baseline_ts, baseline_vol, baseline_oi = history[0]
            minutes_tracked = (now - baseline_ts).total_seconds() / 60.0
            accumulated_volume = vol - baseline_vol
            zero_baseline_oi = baseline_oi <= 0 and accumulated_volume > 0
            if zero_baseline_oi:
                accumulation_ratio = _INF_RATIO_SENTINEL
            elif baseline_oi > 0:
                accumulation_ratio = accumulated_volume / baseline_oi
            else:
                accumulation_ratio = 0.0

            # zero_baseline_oi and the regular ratio aren't independent paths
            # here -- accumulation_ratio is itself set to the inf sentinel
            # for the zero-OI case, so a plain "ratio >= threshold" OR would
            # let the sentinel satisfy the ratio check regardless of
            # min_accumulated_volume, defeating the floor entirely (caught
            # by a test expecting a thin 70-contract print NOT to confirm --
            # it still did, via this exact loophole).
            if zero_baseline_oi:
                confirmed = minutes_tracked >= confirm_minutes and accumulated_volume >= min_accumulated_volume
            else:
                confirmed = minutes_tracked >= confirm_minutes and accumulation_ratio >= undercurrent_ratio
            if not confirmed:
                continue  # still building -- not yet a candidate

            # Strike clustering (see docstring point 4): check the 2 nearest
            # strikes on either side, same option side, for elevated
            # *instantaneous* activity right now.
            idx = sorted_strikes.index(strike)
            neighbor_strikes = sorted_strikes[max(0, idx - 2):idx] + sorted_strikes[idx + 1:idx + 3]
            cluster_neighbors = sum(
                1 for ns in neighbor_strikes
                if ratio_by_strike_side.get((ns, side), 0) >= entry_ratio
            )

            confidence = "moderate"
            if cluster_neighbors >= 2:
                confidence = "elevated (strike clustering)"
            elif otm_oi_bias is not None and otm_oi_bias > 60:
                confidence = "elevated (OTM-side OI bias)"

            flow, bias = _classify_flow(side, oi, d.get("previous_oi"), d.get("last_price"), d.get("previous_close_price"))

            candidates.append({
                "symbol": symbol, "strike": strike, "option_type": side.upper(),
                "expiry": expiry_short,
                "volume": vol, "oi": oi,
                "accumulated_volume": round(accumulated_volume, 0),
                "accumulation_ratio": round(accumulation_ratio, 2),
                "zero_baseline_oi": zero_baseline_oi,
                "minutes_tracked": round(minutes_tracked, 1),
                "cluster_neighbors": cluster_neighbors,
                "premium": d.get("last_price"), "spot": spot,
                "trigger": "volume_oi_ratio", "confidence": confidence,
                "flow": flow, "bias": bias,
            })

    # Second, independent trigger path: a rising OI-concentration trend for
    # this underlying as a whole (not per-strike) -- fires once per
    # underlying, distinct from the per-strike candidates above. Already has
    # its own persistence via compute_concentration_trend's lookback window,
    # so it's untouched by this redesign.
    if oi_concentration_trend is not None and oi_concentration_trend > 0:
        confidence = "elevated (OTM-side OI bias)" if (otm_oi_bias or 0) > 60 else "moderate"
        # calls-heavy OTM buildup reads as retail bullish speculation, puts-heavy
        # as bearish -- a lean off positioning, not a certainty (see docstring
        # on compute_near_atm_oi_concentration).
        bias = {"calls": "bullish", "puts": "bearish"}.get(otm_side_lean)
        candidates.append({
            "symbol": symbol, "strike": None, "option_type": None,
            "expiry": expiry_short,
            "trigger": "oi_concentration_trend",
            "concentration_trend_pct": round(oi_concentration_trend, 2),
            "confidence": confidence,
            "otm_side_lean": otm_side_lean, "bias": bias,
        })

    return candidates, new_state


def compute_near_atm_oi_concentration(chain_resp: dict, atm_band_pct: float):
    """Returns (concentration_pct, otm_side_bias_pct, otm_side_lean) for one
    underlying's chain: what fraction of total OI sits within the band of
    spot, what fraction of that near-ATM OI sits on the OTM side (calls above
    spot, puts below) as a directional-bias proxy, and which side (calls vs
    puts) that OTM OI actually leans toward -- calls-heavy reads as retail
    bullish speculative buildup, puts-heavy as bearish, per the same
    OTM-side-OI proxy documented for this signal (a lean, not a certainty,
    since true dealer positioning isn't observable from this data). All None
    if data is missing."""
    spot, oc = _extract_oc(chain_resp)
    if not spot or not oc:
        return None, None, None

    total_oi = 0.0
    near_atm_oi = 0.0
    near_atm_otm_oi = 0.0
    near_atm_otm_ce_oi = 0.0
    near_atm_otm_pe_oi = 0.0
    for strike_str, sides in oc.items():
        try:
            strike = float(strike_str)
        except (TypeError, ValueError):
            continue
        near_atm = abs(strike - spot) / spot * 100.0 <= atm_band_pct
        for side in ("ce", "pe"):
            oi = sides.get(side, {}).get("oi") or 0
            total_oi += oi
            if near_atm:
                near_atm_oi += oi
                is_otm = (side == "ce" and strike > spot) or (side == "pe" and strike < spot)
                if is_otm:
                    near_atm_otm_oi += oi
                    if side == "ce":
                        near_atm_otm_ce_oi += oi
                    else:
                        near_atm_otm_pe_oi += oi

    if total_oi <= 0:
        return None, None, None
    concentration_pct = near_atm_oi / total_oi * 100.0
    otm_bias_pct = (near_atm_otm_oi / near_atm_oi * 100.0) if near_atm_oi > 0 else None
    otm_side_lean = None
    if near_atm_otm_oi > 0:
        ce_share = near_atm_otm_ce_oi / near_atm_otm_oi * 100.0
        if ce_share >= 60:
            otm_side_lean = "calls"
        elif ce_share <= 40:
            otm_side_lean = "puts"
        else:
            otm_side_lean = "balanced"
    return concentration_pct, otm_bias_pct, otm_side_lean


def compute_concentration_trend(history: list, lookback_minutes: int = 30) -> Optional[float]:
    """Given a per-underlying concentration history [(timestamp, pct, bias), ...],
    return the change in concentration over the lookback window, or None if
    there isn't enough history yet. Positive = rising (the precursor signal)."""
    if len(history) < 2:
        return None
    now = history[-1][0]
    cutoff = now - timedelta(minutes=lookback_minutes)
    older = [h for h in history if h[0] <= cutoff]
    if not older:
        return None
    baseline = older[-1][1]
    latest = history[-1][1]
    return latest - baseline


def build_heatmap(candidates: list, all_symbols: list, streak_history: dict,
                   now: datetime, sweep_interval_minutes: float = 5.0) -> tuple:
    """Aggregate this sweep's flat Mode B candidate list into one entry per
    underlying, for the CAS page's heatmap tab -- the Telegram alert stream
    is a log of point-in-time triggers, not a state view, so there's no way
    to tell from it alone whether SYMBOL is still flagged right now or
    stopped 20 minutes ago. This gives that state view directly, over the
    FULL F&O universe (not just currently-flagged names), with a streak
    (first_seen/sweep_count) carried across sweeps via streak_history so
    continuity survives from one sweep to the next.

    Per underlying: net bias = count(bullish legs) - count(bearish legs)
    across all its flagged strikes/trend-triggers this sweep (a stock can
    have both a bullish CE leg and a bearish PE leg flagged simultaneously;
    net score is the honest aggregate, not "pick one"). A streak continues
    only while the dominant bias stays the same sweep-over-sweep and the gap
    since last seen doesn't exceed 1.5x the sweep interval (a genuine flip
    or a real gap both honestly restart it, not silently extend it).

    Returns (heatmap_list, updated_streak_history) -- pure, no I/O, so this
    is unit-testable against synthetic candidate lists directly."""
    by_symbol: dict = {}
    for c in candidates:
        by_symbol.setdefault(c["symbol"], []).append(c)

    grace = timedelta(minutes=sweep_interval_minutes * 1.5)
    new_streak_history: dict = {}
    heatmap = []
    for symbol in all_symbols:
        legs = by_symbol.get(symbol, [])
        bullish = sum(1 for c in legs if c.get("bias") == "bullish")
        bearish = sum(1 for c in legs if c.get("bias") == "bearish")
        net = bullish - bearish
        if not legs:
            dominant = None
        elif net > 0:
            dominant = "bullish"
        elif net < 0:
            dominant = "bearish"
        else:
            dominant = "neutral"

        streak_minutes = 0.0
        sweep_count = 0
        if legs:
            prev = streak_history.get(symbol)
            if prev and prev.get("dominant") == dominant and (now - prev["last_seen"]) <= grace:
                first_seen = prev["first_seen"]
                sweep_count = prev["sweep_count"] + 1
            else:
                first_seen = now
                sweep_count = 1
            new_streak_history[symbol] = {
                "first_seen": first_seen, "last_seen": now,
                "sweep_count": sweep_count, "dominant": dominant,
            }
            streak_minutes = round((now - first_seen).total_seconds() / 60.0, 1)

        heatmap.append({
            "symbol": symbol, "active": bool(legs), "dominant_bias": dominant,
            "bullish_count": bullish, "bearish_count": bearish, "net_score": net,
            "streak_minutes": streak_minutes, "sweep_count": sweep_count,
            "strikes": [
                {
                    "strike": c.get("strike"), "option_type": c.get("option_type"),
                    "trigger": c["trigger"], "bias": c.get("bias"), "flow": c.get("flow"),
                    "accumulation_ratio": c.get("accumulation_ratio"), "zero_baseline_oi": c.get("zero_baseline_oi"),
                    "minutes_tracked": c.get("minutes_tracked"), "cluster_neighbors": c.get("cluster_neighbors"),
                    "concentration_trend_pct": c.get("concentration_trend_pct"),
                    "confidence": c["confidence"],
                }
                for c in legs
            ],
        })

    return heatmap, new_streak_history


def _rank_key(c: dict) -> float:
    """Strength score for ranking Mode B candidates: accumulated volume-vs-OI
    ratio for per-strike candidates, concentration-trend magnitude for the
    underlying-level trend trigger."""
    if c.get("trigger") == "oi_concentration_trend":
        return c.get("concentration_trend_pct") or 0
    return c.get("accumulation_ratio") or 0


def _cap_top_n_per_side(candidates: list, top_n: int) -> list:
    """Cap Mode B's confirmed candidates to the strongest top_n bullish and
    top_n bearish (ranked separately, per side, per the user's explicit ask)
    -- replaces "alert everything that crossed the bar" with "alert the
    strongest N," which is the whole point of the accumulation-gate redesign:
    filter down to where genuine activity is concentrated, not just visible."""
    bullish = sorted((c for c in candidates if c.get("bias") == "bullish"), key=_rank_key, reverse=True)[:top_n]
    bearish = sorted((c for c in candidates if c.get("bias") == "bearish"), key=_rank_key, reverse=True)[:top_n]
    neutral = sorted((c for c in candidates if c.get("bias") not in ("bullish", "bearish")), key=_rank_key, reverse=True)[:top_n]
    return bullish + bearish + neutral


def spike_detected(price_history: list, current_price: float, spike_multiple: float,
                    lookback_minutes: int) -> bool:
    """Mode A's reactive detection: has current_price risen to >= spike_multiple
    times the recent rolling minimum over the lookback window?"""
    if not price_history or current_price is None or current_price <= 0:
        return False
    now = price_history[-1][0] if price_history else None
    if now is None:
        return False
    cutoff = now - timedelta(minutes=lookback_minutes)
    recent = [p for ts, p in price_history if ts >= cutoff and p > 0]
    if not recent:
        return False
    recent_min = min(recent)
    if recent_min <= 0:
        return False
    return current_price >= spike_multiple * recent_min


def in_cas_window(now_ist: datetime) -> bool:
    """True only during 15:05-15:28 IST -- Mode A's explicit, user-scoped
    active window for reactive alerting (the shortlist itself stays visible
    all day; this only gates whether alerts can fire)."""
    t = now_ist.time()
    return (t.hour == 15 and 5 <= t.minute <= 28)


# ══════════════════════════════════════════════════════════════════════════
# ORCHESTRATION — the background thread, all I/O lives here
# ══════════════════════════════════════════════════════════════════════════

def _sweep_once(cfg):
    """Stock-only sweep (~208 F&O stocks), Mode A + Mode B. Indices used to be
    tacked onto the end of this same sweep, but that meant a fast index burst
    could only ever be seen at this sweep's 5-min cadence, ordered last --
    confirmed live (2026-08-27) that's structurally too slow for a genuine
    fast move (SENSEX 77300/77400 PE spiked 500%+ and mostly reversed within
    ~7-9 minutes). Indices now run on their own fast, independent cadence --
    see _index_sweep_once() -- and this function no longer touches them at
    all."""
    global _heat_streak_history, _strike_activity_state, _at_risk, _stock_undercurrent_raw
    import cas_broker
    sweep_start = _time.time()
    sweep_id = _trace_id("sweep", int(sweep_start))
    today = _now_ist().date()
    sweep_now = _now_ist()
    new_at_risk = []
    new_undercurrent = []
    sweep_strike_state = dict(_strike_activity_state)  # threaded through every _score() call this sweep, written back once at the end
    universe = cas_broker.get_fo_stock_universe()
    _trace(sweep_id, "SWEEP_START", f"stocks={len(universe)}")

    failure_reasons = {}  # reason -> count, so the summary shows WHY, not just how many

    def _current_failure_reason():
        # str() defensively -- this tally must never crash the sweep loop
        # over an unhashable reason type (already bit us once: Dhan can
        # return remarks as a nested dict, not a string).
        return str(cas_broker.last_option_chain_error or "unknown")

    def _record_failure(bucket, label, reason):
        bucket.append(label)
        failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    def _score(symbol, expiry, chain):
        nonlocal sweep_strike_state
        conc_pct, otm_bias, otm_side_lean = compute_near_atm_oi_concentration(chain, cfg.cas_atm_band_pct)
        if conc_pct is not None and _in_continuous_session(sweep_now):
            hist = _oi_concentration_history.setdefault(symbol, [])
            hist.append((sweep_now, conc_pct, otm_bias))
            if len(hist) > 200:
                del hist[:-200]
        trend = compute_concentration_trend(_oi_concentration_history.get(symbol, []))
        new_at_risk.extend(score_mode_a(
            chain, symbol, expiry, today,
            cfg.cas_premium_floor, cfg.cas_max_days_to_expiry, cfg.cas_atm_band_pct,
        ))
        b_candidates, sweep_strike_state = score_mode_b(
            chain, symbol, expiry, cfg.cas_undercurrent_ratio, cfg.cas_atm_band_pct,
            sweep_strike_state, sweep_now,
            entry_ratio=cfg.cas_undercurrent_entry_ratio, confirm_minutes=cfg.cas_undercurrent_confirm_minutes,
            oi_concentration_trend=trend, otm_oi_bias=otm_bias, otm_side_lean=otm_side_lean,
        )
        new_undercurrent.extend(b_candidates)

    # Found live (2026-08-26): after any gap since cas_broker's last call
    # (there's a ~79s idle gap between 5-min sweeps outside the 15:05-15:28
    # window, since neither reactive loop touches cas_broker then), Dhan's
    # option_chain returns this same empty failure for roughly the first ~85
    # calls of the next burst before it reliably starts succeeding -- verified
    # directly: the same handful of symbols retried moments later on an
    # already-warm connection succeed every time. Rather than guess at Dhan's
    # internal warm-up behavior further, retry failed underlyings once at the
    # end of the sweep, by which point the connection is warm from the calls
    # that already succeeded.
    # Found live (2026-08-26, second look): a single retry cut failures from
    # 84 to 35/208, but that residual 35 turned out to be the *same* 35 stocks
    # across independent sweeps 7 minutes apart -- not randomly-shifting
    # transient noise, so a second retry round is worth the extra ~35 calls
    # (bounded, not unbounded) to see how much further it clears. A second
    # round brought it to a stable 14/208; a third round took it to 7/208.
    # The pattern (84 -> 35 -> 14 -> 7, roughly halving each round) looks like
    # each still-failing call has a persistent ~50% per-attempt success
    # chance rather than a fixed count that clears outright -- more rounds
    # keep helping but likely approach, not reach, zero. Bounded cost either
    # way (each round only retries what's still failing), so still worth it.
    MAX_RETRY_ROUNDS = 4

    stock_fetch_failures = []
    retry_stocks = []
    for stock in universe:
        chain = cas_broker.option_chain(stock["security_id"], stock["exchange_segment"], stock["nearest_expiry"])
        if chain is None:
            retry_stocks.append(stock)
            continue
        _score(stock["symbol"], stock["nearest_expiry"], chain)

    last_reason = {}  # symbol -> most recent failure reason, kept fresh every attempt
    for round_num in range(MAX_RETRY_ROUNDS):
        still_failing_stocks = []
        for stock in retry_stocks:
            chain = cas_broker.option_chain(stock["security_id"], stock["exchange_segment"], stock["nearest_expiry"])
            if chain is None:
                still_failing_stocks.append(stock)
                last_reason[stock["symbol"]] = _current_failure_reason()
                continue
            _score(stock["symbol"], stock["nearest_expiry"], chain)
        retry_stocks = still_failing_stocks

        if not retry_stocks:
            break

    for stock in retry_stocks:
        _record_failure(stock_fetch_failures, stock["symbol"], last_reason.get(stock["symbol"], "unknown"))

    n_confirmed = len(new_undercurrent)
    # Tagged so _reactive_mode_b can tell stock-sourced candidates apart from
    # Mode C's index-sourced ones once both are merged into the same
    # _undercurrent list below -- without this, the merged list would look
    # identical regardless of source, and _reactive_mode_b would re-fire
    # index candidates under the wrong ("undercurrent") mode, duplicating
    # Mode C's own immediate alert.
    for c in new_undercurrent:
        c["_source"] = "stock"
    # Mode A shortlist: the top cas_mode_a_top_n contracts by score across all stocks
    # (cheaper + sooner + more near-ATM OI rank higher -- see score_mode_a)
    new_at_risk.sort(key=lambda c: c["score"], reverse=True)
    new_at_risk = new_at_risk[:cfg.cas_mode_a_top_n]
    with _state_lock:
        _at_risk = new_at_risk
        _strike_activity_state = sweep_strike_state
        _stock_undercurrent_raw = new_undercurrent
    _broadcast({"type": "cas_at_risk_update", "data": new_at_risk})
    final_undercurrent = _recompute_undercurrent_and_heatmap(cfg)
    _save_day_state()

    duration = _time.time() - sweep_start
    n_fail = len(stock_fetch_failures)
    _status.update(last_sweep_at=_now_ist().isoformat(timespec="seconds"), last_sweep_seconds=round(duration),
                   last_sweep_stocks=len(universe), last_sweep_failures=n_fail)
    _trace(sweep_id, "SWEEP_DONE",
           f"duration={duration:.1f}s at_risk={len(new_at_risk)} undercurrent={len(final_undercurrent)} "
           f"(stock_confirmed={n_confirmed}, tracked={len(sweep_strike_state)}) "
           f"fetch_failures={n_fail}/{len(universe)}")
    if n_fail > 0:
        # Explicit so "0 results" is never silently ambiguous between "nothing
        # qualified this sweep" and "most of the universe failed to fetch" --
        # the latter needs attention, the former is a normal quiet sweep.
        # failure_reasons breaks down WHY, not just how many -- found the hard
        # way (2026-08-26, first live sweep) that the count alone doesn't say
        # whether it's rate-limiting, a bad response, or something else.
        logger.warning(
            f"CAS scanner sweep {sweep_id}: {n_fail} underlyings failed to fetch. "
            f"Reasons: {failure_reasons}. "
            f"stocks: {stock_fetch_failures[:10]}{'...' if len(stock_fetch_failures) > 10 else ''}"
        )
    logger.info(f"CAS scanner sweep: {len(new_at_risk)} at-risk (Mode A), {len(final_undercurrent)} undercurrent (Mode B, stock-sourced={n_confirmed})")


def _recompute_undercurrent_and_heatmap(cfg) -> list:
    """Merge the stock sweep's and Mode C's index loop's latest raw Mode B
    output into one capped/ranked _undercurrent list, then rebuild the
    heatmap from it. Two independent cadences (5-min stock sweep,
    cas_index_sweep_seconds index loop) both feed the same final list, so
    this is called after EITHER one refreshes its raw output -- otherwise
    whichever ran last would silently overwrite the other's contribution
    instead of merging with it."""
    global _undercurrent, _heatmap, _heat_streak_history
    import cas_broker
    with _state_lock:
        combined = list(_stock_undercurrent_raw) + list(_index_undercurrent_raw)
    new_undercurrent = _cap_top_n_per_side(combined, cfg.cas_undercurrent_top_n)
    universe = cas_broker.get_fo_stock_universe()  # cached after the first call -- cheap to call from the fast index loop too
    all_symbols = [s["symbol"] for s in universe] + [idx["symbol"] for idx in cas_broker.INDEX_UNIVERSE]
    new_heatmap, updated_streak_history = build_heatmap(new_undercurrent, all_symbols, _heat_streak_history, _now_ist())
    with _state_lock:
        _undercurrent = new_undercurrent
        _heatmap = new_heatmap
        _heat_streak_history = updated_streak_history
    _broadcast({"type": "cas_undercurrent_update", "data": new_undercurrent})
    _broadcast({"type": "cas_heatmap_update", "data": new_heatmap})
    return new_undercurrent


def _reactive_mode_a(cfg):
    import cas_broker
    if not in_cas_window(_now_ist()):
        return
    with _state_lock:
        shortlist = list(_at_risk)
    seen_symbols = {c["symbol"] for c in shortlist}
    logger.debug(f"CAS_A_TICK: window active, checking {len(shortlist)} shortlisted contracts across {len(seen_symbols)} underlyings")
    for symbol in seen_symbols:
        stock = next((s for s in cas_broker.get_fo_stock_universe() if s["symbol"] == symbol), None)
        if not stock:
            continue
        chain = cas_broker.option_chain(stock["security_id"], stock["exchange_segment"], stock["nearest_expiry"])
        if chain is None:
            continue
        spot, oc = _extract_oc(chain)
        if not oc:
            continue
        for c in [x for x in shortlist if x["symbol"] == symbol]:
            side_key = "ce" if c["option_type"] == "CE" else "pe"
            d = oc.get(str(c["strike"]), oc.get(f"{c['strike']:.6f}", {})).get(side_key, {})
            ltp = d.get("last_price")
            if ltp is None:
                continue
            key = (c["symbol"], c["strike"], c["option_type"], c["expiry"])
            hist = _premium_history.setdefault(key, [])
            hist.append((_now_ist(), ltp))
            if len(hist) > 200:
                del hist[:-200]
            if spike_detected(hist, ltp, cfg.cas_spike_multiple, cfg.cas_lookback_minutes):
                _fire_alert_a(c, hist, ltp, cfg)


def _fire_alert_a(candidate: dict, hist: list, current_price: float, cfg):
    tid = _trace_id("A", candidate["symbol"], candidate["strike"], candidate["option_type"], candidate["expiry"])
    key = f"a:{candidate['symbol']}:{candidate['strike']}:{candidate['option_type']}:{candidate['expiry']}"
    now = _now_ist()
    last = _last_alerted.get(key)
    if last and (now - last).total_seconds() < 900:
        _trace(tid, "A_THROTTLED", f"last_sent={last.strftime('%H:%M:%S')}")
        return
    _last_alerted[key] = now

    recent_min = min((p for _, p in hist if p > 0), default=current_price)
    pct_move = ((current_price - recent_min) / recent_min * 100.0) if recent_min > 0 else 0.0
    _trace(tid, "A_DETECTED",
           f"{candidate['symbol']} {candidate['strike']}{candidate['option_type']} "
           f"premium={recent_min:.2f}->{current_price:.2f} ({pct_move:+.0f}%)")
    alert = {
        "time": now.strftime("%H:%M:%S"), "symbol": candidate["symbol"],
        "strike": candidate["strike"], "option_type": candidate["option_type"],
        "expiry": candidate["expiry"], "premium_before": recent_min, "premium_peak": current_price,
        "pct_move": round(pct_move, 1), "mode": "cas_window",
    }
    with _state_lock:
        _alerts_a.append(alert)
    _broadcast({"type": "cas_alert", "data": alert})
    msg = (
        f"\U0001F6A8 CAS SPIKE — {candidate['symbol']} {candidate['strike']:.0f} {candidate['option_type']}\n"
        f"Expiry: {candidate['expiry']}\n"
        f"Premium: Rs.{recent_min:.2f} -> Rs.{current_price:.2f} ({pct_move:+.0f}%)\n"
        f"Awareness only — not an auto-trade signal."
    )
    _trace(tid, "A_TELEGRAM", "sending")
    _send_telegram(msg)
    _save_day_state()


def _burst_only(candidates: list) -> list:
    """Only volume_oi_ratio candidates are genuinely time-boxed to
    cas_index_confirm_minutes and built for "burst" framing --
    oi_concentration_trend uses compute_concentration_trend's hardcoded
    30-min lookback, a slow building signal wholly unrelated to Mode C's
    fast window. Caught live (2026-08-28): the trend trigger fired
    immediate Mode C Telegram alerts for trivial moves (+2.5pts, +0.7pts,
    no directional bias) that weren't remotely burst-like. Used to filter
    what reaches _fire_index_alerts -- trend-trigger candidates are still
    tracked and visible via the merged undercurrent/heatmap/digest, just
    excluded from the immediate per-event alert."""
    return [c for c in candidates if c.get("trigger") == "volume_oi_ratio"]


def _index_sweep_once(cfg):
    """Mode C -- Index Burst: its own fast sweep over just the 3 indices
    (NIFTY/BANKNIFTY/SENSEX), on cas_index_sweep_seconds cadence, fully
    decoupled from the 208-stock sweep's 5-min cadence. Reuses score_mode_b()
    -- the same accumulation-gate scoring as the stock side -- but with
    index-specific params (looser entry_ratio since indices carry naturally
    huge absolute volume, much shorter confirm_minutes since a genuine index
    burst can complete in minutes) and its own rolling state
    (_index_strike_activity_state), kept apart from the stock sweep's
    _strike_activity_state so the two cadences never stomp each other's
    per-strike history mid-window.

    Only 3 underlyings, so a single retry pass is enough -- no need for the
    stock sweep's multi-round cold-start handling (that exists to amortize
    Dhan's ~85-call warm-up cost across 208 calls; irrelevant at this scale)."""
    global _index_strike_activity_state, _stock_undercurrent_raw, _index_undercurrent_raw
    import cas_broker
    sweep_id = _trace_id("csweep", int(_time.time()))
    now = _now_ist()
    index_state = dict(_index_strike_activity_state)
    new_index_undercurrent = []
    fetch_failures = []

    def _score_index(symbol, expiry, chain):
        nonlocal index_state
        conc_pct, otm_bias, otm_side_lean = compute_near_atm_oi_concentration(chain, cfg.cas_atm_band_pct)
        if conc_pct is not None and _in_continuous_session(now):
            hist = _oi_concentration_history.setdefault(symbol, [])
            hist.append((now, conc_pct, otm_bias))
            if len(hist) > 200:
                del hist[:-200]
        trend = compute_concentration_trend(_oi_concentration_history.get(symbol, []))
        candidates, index_state = score_mode_b(
            chain, symbol, expiry, cfg.cas_undercurrent_ratio, cfg.cas_atm_band_pct,
            index_state, now,
            entry_ratio=cfg.cas_index_entry_ratio, confirm_minutes=cfg.cas_index_confirm_minutes,
            oi_concentration_trend=trend, otm_oi_bias=otm_bias, otm_side_lean=otm_side_lean,
            min_accumulated_volume=cfg.cas_index_min_accumulated_volume,
        )
        new_index_undercurrent.extend(candidates)

    pending = []
    for idx in cas_broker.INDEX_UNIVERSE:
        expiry = cas_broker.nearest_expiry_for_index(idx["security_id"], idx["exchange_segment"])
        if not expiry:
            fetch_failures.append(f"{idx['symbol']}(no expiry)")
            continue
        chain = cas_broker.option_chain(idx["security_id"], idx["exchange_segment"], expiry)
        if chain is None:
            pending.append((idx, expiry))
            continue
        _score_index(idx["symbol"], expiry, chain)

    for idx, expiry in pending:
        chain = cas_broker.option_chain(idx["security_id"], idx["exchange_segment"], expiry)
        if chain is None:
            fetch_failures.append(idx["symbol"])
            continue
        _score_index(idx["symbol"], expiry, chain)

    for c in new_index_undercurrent:  # see _sweep_once's matching tag -- keeps _reactive_mode_b from re-firing these under the wrong mode
        c["_source"] = "index"
    with _state_lock:
        _index_strike_activity_state = index_state
        _index_undercurrent_raw = new_index_undercurrent
    _recompute_undercurrent_and_heatmap(cfg)
    _fire_index_alerts(_burst_only(new_index_undercurrent), cfg)

    if fetch_failures:
        logger.warning(f"CAS index sweep {sweep_id}: fetch failed for {fetch_failures}")
    _trace(sweep_id, "CSWEEP_DONE", f"undercurrent={len(new_index_undercurrent)} tracked={len(index_state)}")
    _status["last_index_sweep_at"] = _now_ist().isoformat(timespec="seconds")


# Human-readable flow labels for the Telegram message -- same OI-price
# quadrant _classify_flow() computes, just spelled out so "bias: bullish"
# doesn't collapse two different mechanisms (fresh buying vs. short
# covering, fresh writing vs. long unwinding) into one word. Added
# 2026-08-28 per explicit user request, after having to look up the raw
# `flow` field via the API to answer "is this fresh buying or writers
# covering?" for a real alert.
_FLOW_LABELS = {
    "long_buildup": "fresh buying (OI+price up)",
    "short_buildup": "fresh writing (OI up, price down)",
    "short_covering": "writers covering (OI down, price up)",
    "long_unwinding": "longs exiting (OI+price down)",
    "flat": "flat OI",
}


def _index_alert_message(c: dict) -> str:
    sym = c["symbol"]
    flow_line = ""
    if c.get("flow"):
        flow_line = f"Flow: {_FLOW_LABELS.get(c['flow'], c['flow'])}\n"
    if c.get("strike") is None:
        return (
            f"⚡ CAS INDEX BURST — {sym} (OI-buildup, {c['concentration_trend_pct']:+.1f}pts)\n"
            f"Bias: {c.get('bias', 'n/a')}\n"
            f"{flow_line}"
            f"Awareness only — not an auto-trade signal."
        )
    strike, opt = c["strike"], c["option_type"]
    r = "inf" if c.get("zero_baseline_oi") else f"{c.get('accumulation_ratio'):.1f}x"
    return (
        f"⚡ CAS INDEX BURST — {sym} {strike:.0f}{opt}\n"
        f"Expiry: {c.get('expiry')}\n"
        f"Vol/OI accumulation: {r} over {c.get('minutes_tracked'):.0f}m\n"
        f"Bias: {c.get('bias', 'n/a')}\n"
        f"{flow_line}"
        f"Awareness only — not an auto-trade signal."
    )


def _fire_index_alerts(candidates: list, cfg):
    """Mode C's immediate per-event Telegram -- unlike Mode B's digest-only
    stock side, a fast index burst can complete and reverse before the next
    cas_digest_interval_minutes digest (confirmed live: SENSEX 77300/77400 PE
    did exactly this in ~7-9 minutes), so waiting for the digest would mean
    reporting it after it's already over. Throttled via the same
    _last_alerted dict as Modes A/B, with a distinct "c:" key prefix so it
    can't collide with a stock-sourced candidate for the same symbol."""
    global _index_alerts_since_digest
    now = _now_ist()
    fired_any = False
    for c in candidates:
        if c.get("strike") is None:
            key = f"c:{c['symbol']}:trend"
            tid = _trace_id("C", c["symbol"], "trend")
        else:
            key = f"c:{c['symbol']}:{c['strike']}:{c['option_type']}:{c['expiry']}"
            tid = _trace_id("C", c["symbol"], c["strike"], c["option_type"], c["expiry"])
        last = _last_alerted.get(key)
        if last and (now - last).total_seconds() < 900:
            _trace(tid, "C_THROTTLED", f"last_sent={last.strftime('%H:%M:%S')}")
            continue
        _last_alerted[key] = now
        fired_any = True
        _index_alerts_since_digest += 1
        _trace(tid, "C_DETECTED", f"{c['symbol']} trigger={c.get('trigger')} confidence={c.get('confidence')}")
        # "mode" lives on the stored alert dict itself, not just the WS
        # broadcast wrapper -- otherwise a REST fallback poll (or a client
        # that missed the WS push) can't tell a Mode C index alert apart from
        # a Mode B stock one once both land in the same alerts_b list.
        alert = {"time": now.strftime("%H:%M:%S"), **c, "mode": "index_burst"}
        with _state_lock:
            _alerts_b.append(alert)
        _broadcast({"type": "cas_alert", "data": alert})
        _trace(tid, "C_TELEGRAM", "sending")
        _send_telegram(_index_alert_message(c))
    if fired_any:
        _save_day_state()


def _reactive_mode_b(cfg):
    """Logs and broadcasts each newly-confirmed STOCK candidate (for the UI's
    Alerts tab and heatmap) but no longer sends a per-candidate Telegram
    message -- with the ranked list running up to 45 candidates (15 bull + 15
    bear + 15 neutral) once the market's been open a while, per-candidate
    Telegram pings meant 45 separate messages ("so many messages which is not
    making sense"). Telegram now gets one consolidated digest instead -- see
    _maybe_send_undercurrent_digest, on its own cadence in _run().

    Filters out index-sourced candidates (Mode C, tagged _source="index" in
    _index_sweep_once) even though they're present in the merged
    _undercurrent list -- Mode C already fires its own immediate alert for
    those via _fire_index_alerts; without this filter this loop would also
    pick them up on its own 20s cadence and re-broadcast/re-store them under
    the wrong ("undercurrent") mode, duplicating Mode C's alert."""
    with _state_lock:
        flagged = [c for c in _undercurrent if c.get("_source") != "index"]
    now = _now_ist()
    logger.debug(f"CAS_B_TICK: checking {len(flagged)} flagged candidates")
    fired_any = False
    for c in flagged:
        if c.get("strike") is None:
            key = f"b:{c['symbol']}:trend"
            tid = _trace_id("B", c["symbol"], "trend")
        else:
            key = f"b:{c['symbol']}:{c['strike']}:{c['option_type']}:{c['expiry']}"
            tid = _trace_id("B", c["symbol"], c["strike"], c["option_type"], c["expiry"])
        last = _last_alerted.get(key)
        if last and (now - last).total_seconds() < 900:
            _trace(tid, "B_THROTTLED", f"last_sent={last.strftime('%H:%M:%S')}")
            continue
        _last_alerted[key] = now
        fired_any = True
        _trace(tid, "B_DETECTED", f"{c['symbol']} trigger={c.get('trigger')} confidence={c.get('confidence')}")
        alert = {"time": now.strftime("%H:%M:%S"), **c, "mode": "undercurrent"}
        with _state_lock:
            _alerts_b.append(alert)
        _broadcast({"type": "cas_alert", "data": alert})
    if fired_any:
        _save_day_state()


def _digest_label(c: dict) -> str:
    """One-line label for a single Mode B candidate in the consolidated digest."""
    sym = c["symbol"]
    if c.get("strike") is None:
        return f"{sym} (OI-buildup, {c['concentration_trend_pct']:+.1f}pts)"
    strike, opt = c["strike"], c["option_type"]
    if c.get("trigger") == "volume_oi_ratio":
        r = "inf" if c.get("zero_baseline_oi") else f"{c.get('accumulation_ratio'):.1f}x"
        return f"{sym} {strike:.0f}{opt} ({r}, {c.get('minutes_tracked'):.0f}m)"
    return f"{sym} {strike:.0f}{opt} (OI-buildup)"


def build_undercurrent_digest(undercurrent: list, top_n: int = 10) -> str:
    """Pure: the consolidated Mode B Telegram digest -- top_n bullish + top_n
    bearish, ranked, one message instead of one per candidate (2026-08-27,
    replacing per-candidate Telegram alerts that hit 45 separate messages
    once the ranked list filled up: "so many messages which is not making
    sense"). Returns "" when there's nothing to report (caller should skip
    sending rather than push an empty digest)."""
    bulls = sorted((c for c in undercurrent if c.get("bias") == "bullish"), key=_rank_key, reverse=True)[:top_n]
    bears = sorted((c for c in undercurrent if c.get("bias") == "bearish"), key=_rank_key, reverse=True)[:top_n]
    if not bulls and not bears:
        return ""

    lines = [f"\U0001F4CA CAS UNDERCURRENT DIGEST — {len(bulls)} bullish, {len(bears)} bearish"]
    if bulls:
        lines.append("\nBULLISH:")
        lines.extend(f"  {_digest_label(c)}" for c in bulls)
    if bears:
        lines.append("\nBEARISH:")
        lines.extend(f"  {_digest_label(c)}" for c in bears)
    lines.append("\nAwareness only — not an auto-trade signal.")
    return "\n".join(lines)


def _maybe_send_undercurrent_digest(cfg):
    global _last_digest_sent, _index_alerts_since_digest
    now = _now_ist()
    if _last_digest_sent and (now - _last_digest_sent).total_seconds() < cfg.cas_digest_interval_minutes * 60:
        return
    with _state_lock:
        current = list(_undercurrent)
    msg = build_undercurrent_digest(current, top_n=cfg.cas_digest_top_n)
    c_count = _index_alerts_since_digest
    if c_count > 0:
        # Mode C already sent its own immediate per-event Telegram for each of
        # these (too fast for a digest to be the first notice), but the user
        # still wants the rollup to reflect it, not just the stock side.
        c_line = f"\n⚡ Index Burst (Mode C): {c_count} alert{'s' if c_count != 1 else ''} sent since last digest."
        msg = (msg + c_line) if msg else (
            f"\U0001F4CA CAS UNDERCURRENT DIGEST — nothing on the stock side.{c_line}"
        )
    if not msg:
        return  # nothing to report -- skip rather than send an empty digest
    _last_digest_sent = now
    _index_alerts_since_digest = 0
    tid = _trace_id("digest", int(_time.time()))
    _trace(tid, "B_DIGEST", f"sending ({len(current)} candidates in the ranked pool, {c_count} Mode C alerts)")
    _send_telegram(msg)


def _index_run():
    """Mode C's own loop, on its OWN daemon thread -- not just its own cadence
    check inside the main loop. Found live (2026-08-27, first deploy): a
    same-thread cadence check still blocks on _sweep_once()'s full duration
    (211s typical, 366s observed on a post-idle-gap cold start), so indices
    only actually got polled in the gap between stock sweeps -- exactly the
    "too slow for a fast burst" gap Mode C exists to close. A real second
    thread fixes this because cas_broker.dhan_cas_api_call's rate limiter is
    a single shared lock: this thread's calls interleave with the stock
    sweep's roughly every ~1s (Dhan's own budget), instead of waiting behind
    the entire sweep."""
    from config import get_settings
    last_index_sweep = 0.0
    while _running:
        try:
            cfg = get_settings()
            if cfg.cas_scanner_enabled and cfg.cas_mode_c_enabled:
                now_wall = _time.time()
                if now_wall - last_index_sweep >= cfg.cas_index_sweep_seconds:
                    # Still advances last_index_sweep even when skipped by
                    # the opening-range check, so this just quietly no-ops
                    # on the normal 30s cadence rather than busy-checking
                    # every 2s until the window lifts.
                    if _index_tracking_allowed(_now_ist(), cfg):
                        _index_sweep_once(cfg)
                    last_index_sweep = now_wall
        except Exception as e:
            logger.error(f"CAS index scanner loop error: {e}", exc_info=True)
        _time.sleep(2)


def _run():
    global _running, _index_thread
    from config import get_settings
    import cas_broker

    cfg = get_settings()
    if not cas_broker.connect(cfg.dhan_cas_client_code, cfg.dhan_cas_access_token):
        if not cfg.dhan_cas_client_code or not cfg.dhan_cas_access_token:
            logger.info("CAS scanner: no dedicated credentials in .env (DHAN_CAS_CLIENT_CODE/DHAN_CAS_TOKEN_ID) — staying idle")
        else:
            # Credentials ARE present but the connection itself failed -- distinct
            # from "not configured" so a stale/expired token doesn't look identical
            # to "never set up" in the log (the daily-refresh case this matters for).
            logger.warning(f"CAS scanner: dedicated credentials present but connection failed "
                            f"(likely expired token, needs today's refresh): {cas_broker._connection_error}")
        _running = False
        return

    _index_thread = threading.Thread(target=_index_run, daemon=True, name="CasIndexScanner")
    _index_thread.start()
    logger.info("CAS index (Mode C) scanner thread started")

    last_sweep = 0.0
    last_reactive_b = 0.0
    while _running:
        try:
            _reset_if_new_day()
            cfg = get_settings()
            if not cfg.cas_scanner_enabled:
                _time.sleep(15)
                continue

            if not _market_open(_now_ist()):
                _time.sleep(30)
                continue

            now = _time.time()
            if now - last_sweep >= 300:  # 5 min (a sweep itself takes ~15 min at Dhan's 1-per-3s limit)
                _sweep_once(cfg)
                last_sweep = now

            _reactive_mode_a(cfg)  # no-ops itself outside 15:05-15:28

            if now - last_reactive_b >= 20:
                _reactive_mode_b(cfg)
                last_reactive_b = now

            _maybe_send_undercurrent_digest(cfg)  # no-ops itself until cas_digest_interval_minutes has elapsed

        except Exception as e:
            logger.error(f"CAS scanner loop error: {e}", exc_info=True)

        _time.sleep(5)


def start():
    global _running, _thread
    if _running:
        logger.warning("CAS scanner already running")
        return
    _running = True
    _thread = threading.Thread(target=_run, daemon=True, name="CasScanner")
    _thread.start()
    logger.info("CAS scanner thread started")


def stop():
    global _running
    _running = False
    _save_day_state()
    logger.info("CAS scanner stopped")


def get_state() -> dict:
    with _state_lock:
        return {
            "at_risk": list(_at_risk),
            "undercurrent": list(_undercurrent),
            "alerts_a": list(_alerts_a),
            "alerts_b": list(_alerts_b),
            "heatmap": list(_heatmap),
            "running": _running,
            "status": dict(_status),
        }
