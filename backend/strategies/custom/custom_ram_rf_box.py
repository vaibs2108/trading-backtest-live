"""
custom_ram_rf_box.py -- "Ram + Range Filter + S/R Box (Option C)" -- BankNifty, 1 lot, BACKTEST PAGE ONLY.

Researched 2026-09-30..10-01 in scratch/research_14L/ (see FINDINGS.md rounds 12-19 there for every test).
live_capable = False: not wired into live trading; Range Filter / Ram are recursive indicators whose live
values on the app's rolling 300-bar window would need their own parity check before any live use.

Two signal engines, ports of the user's own TradingView scripts, run independently on 5-min bars:
  1. Ram_strategy  -- fast/slow ATR trails + SuperTrend(2,10) + UT Bot(2,6) + Range Filter(20,3.5) + ALMA(50,0.85,6).
                      BUY = (RF buy or UT buy) and (SuperTrend-up crosses slow trail or close > fast trail) and
                      green bar and close > ALMA; SELL mirrored. Entry at NEXT bar open (TradingView default);
                      exit = UT Bot opposite signal via limit order at that bar's close; opposite entry reverses.
  2. Range Filter  -- Range Filter B&S (20, 3.5), always has a direction, flips at bar close.
One lot only, priority Ram > Range Filter:
  - Ram opens positions. Range Filter is HOLD-ONLY: when Ram exits, the lot stays open only if Range Filter
    points the same way (it then rides until Range Filter flips); Range Filter never opens a new position.
  - No-progress exit (both engines): if a position has not been +10 pts in profit within 4 bars, exit at that bar's close.
  - S/R box sideways filter: Ram may NOT open a new position while the last 24 bars' range is <= 7 x ATR(14)
    AND price is in the middle 60% of that range (entries at box edges / breakouts are still allowed).

CORRECTION 2026-10-01: the first version of this file evaluated the S/R box on the FILL bar (look-ahead -- Ram
fills at that bar's open) and showed 79,395 pts / +10.55L. Fixed: the box is now evaluated on the signal bar.
Honest results after the fix (CARRY_FORWARD, index points, 1 lot):
  Dhan 5-yr 2022-01..2026-09: 50,682 pts, +27,912 after the app's cost formula, PF 1.37, worst DD -5,789 pts, 1,410 trades.
  2022 +6,169 | 2023 +6,953 | 2024 +6,776 | 2025 +7,405 | 2026 Jan-Sep +23,379
  Backtest page 2025-07-01..2026-09-30: 26,761 pts (+Rs 8.03L), PF 1.83, DD -2,009 pts.
With the look-ahead removed the box filter does NOT beat plain Ram > Range Filter (no box) -- see
scratch/research_14L/FINDINGS.md round 20. Kept on the Backtest page only as a record; not a promotion candidate.
Only CARRY_FORWARD was validated. INTRADAY mode flattens at 15:20 and is untested.
"""
import os
import sys

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import numpy as np
import pandas as pd

from strategy_kernel import StrategyKernel
from config import get_settings


# ------------------------------------------------------------------ indicator helpers (Pine semantics)
def _rma(x, n):
    return pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().values


def _ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def _atr(h, l, c, n):
    pc = np.r_[c[0], c[:-1]]
    return _rma(np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))), n)


def _atr_trail(src, loss):
    """Trail1 / Trail2 / UT Bot xATRTrailingStop recursion."""
    t = np.zeros(len(src))
    for i in range(len(src)):
        p = t[i - 1] if i else 0.0
        s1 = src[i - 1] if i else src[i]
        if src[i] > p and s1 > p:
            t[i] = max(p, src[i] - loss[i])
        elif src[i] < p and s1 < p:
            t[i] = min(p, src[i] + loss[i])
        else:
            t[i] = src[i] - loss[i] if src[i] > p else src[i] + loss[i]
    return t


def _alma(x, n=50, offset=0.85, sigma=6):
    m, s = offset * (n - 1), n / sigma
    w = np.exp(-((np.arange(n) - m) ** 2) / (2 * s * s))
    w /= w.sum()
    return pd.Series(x).rolling(n).apply(lambda v: (v * w).sum(), raw=True).values


def range_filter_signals(src, per=20, qty=3.5):
    """Range Filter B&S: +1 on the BUY label bar, -1 on the SELL label bar, else 0."""
    d = np.abs(np.diff(src, prepend=src[0]))
    rng = _ema(_ema(d, per), per * 2 - 1) * qty
    filt = np.empty(len(src))
    filt[0] = src[0]
    for i in range(1, len(src)):
        f = filt[i - 1]
        if src[i] - rng[i] > f:
            f = src[i] - rng[i]
        if src[i] + rng[i] < f:
            f = src[i] + rng[i]
        filt[i] = f
    fdir = np.zeros(len(src))
    for i in range(1, len(src)):
        fdir[i] = 1 if filt[i] > filt[i - 1] else -1 if filt[i] < filt[i - 1] else fdir[i - 1]
    prev = np.r_[src[0], src[:-1]]
    long_c = (src > filt) & (src != prev) & (fdir == 1)
    short_c = (src < filt) & (src != prev) & (fdir == -1)
    cond = np.zeros(len(src))
    sig = np.zeros(len(src), int)
    for i in range(1, len(src)):
        cond[i] = 1 if long_c[i] else -1 if short_c[i] else cond[i - 1]
        if long_c[i] and cond[i - 1] == -1:
            sig[i] = 1
        elif short_c[i] and cond[i - 1] == 1:
            sig[i] = -1
    return sig


def ram_signals(o, h, l, c):
    tr1 = _atr_trail(c, 0.5 * _atr(h, l, c, 5))
    tr2 = _atr_trail(c, 3.0 * _atr(h, l, c, 10))
    green = (tr1 > tr2) & (c > tr2) & (l > tr2)
    red = (tr2 > tr1) & (c < tr2) & (h < tr2)
    a10 = _atr(h, l, c, 10)
    hl2 = (h + l) / 2
    up, dn = hl2 - 2 * a10, hl2 + 2 * a10
    tu, td = np.zeros(len(c)), np.zeros(len(c))
    for i in range(len(c)):
        tu[i] = max(up[i], tu[i - 1]) if i and c[i - 1] > tu[i - 1] else up[i]
        td[i] = min(dn[i], td[i - 1]) if i and c[i - 1] < td[i - 1] else dn[i]
    ut = _atr_trail(c, 2 * _atr(h, l, c, 6))
    prev_c, prev_ut = np.r_[c[0], c[:-1]], np.r_[ut[0], ut[:-1]]
    ut_buy = (c > ut) & (prev_c <= prev_ut)
    ut_sell = (c < ut) & (prev_c >= prev_ut)
    rf = range_filter_signals(c, 20, 3.5)
    al = _alma(c)
    prev_tu, prev_td, prev_tr2 = np.r_[tu[0], tu[:-1]], np.r_[td[0], td[:-1]], np.r_[tr2[0], tr2[:-1]]
    x_up = (tu > tr2) & (prev_tu <= prev_tr2)
    x_dn = (td < tr2) & (prev_td >= prev_tr2)
    buy = ((rf == 1) | ut_buy) & (x_up | (c > tr1)) & green & (c > al)
    sell = ((rf == -1) | ut_sell) & (x_dn | (c < tr1)) & red & (c < al)
    return buy, sell, ut_buy, ut_sell


# ------------------------------------------------------------------ engine tracks
class _Track:
    """dir_end[i] = engine position after bar i; entry_px / exit_px[i] = the engine's own fills during bar i (nan if none)."""

    def __init__(self, n):
        self.dir_end = np.zeros(n)
        self.entry_px = np.full(n, np.nan)
        self.exit_px = np.full(n, np.nan)


def _ram_track(o, h, l, c):
    buy, sell, ut_buy, ut_sell = ram_signals(o, h, l, c)
    tr = _Track(len(c))
    pos, e, ei, lim = 0, 0.0, 0, None

    def close_at(i, px):
        tr.dir_end[ei:i] = pos
        tr.entry_px[ei] = e
        tr.exit_px[i] = px

    for i in range(1, len(c)):
        if pos and lim is not None and ((pos > 0 and h[i] >= lim) or (pos < 0 and l[i] <= lim)):
            close_at(i, max(lim, o[i]) if pos > 0 else min(lim, o[i]))
            pos, lim = 0, None
        want = 1 if buy[i - 1] else -1 if sell[i - 1] else 0
        if want and ((want > 0 and pos <= 0) or (want < 0 and pos >= 0)):
            if pos:
                close_at(i, o[i])
            pos, e, ei, lim = want, o[i], i, None
        if pos > 0 and ut_sell[i] and lim is None:
            lim = c[i]
        if pos < 0 and ut_buy[i] and lim is None:
            lim = c[i]
    if pos:                                     # still-open position at the latest bar must stay visible
        tr.dir_end[ei:] = pos
        tr.entry_px[ei] = e
    # Live use only (never read by the backtest): the UT limit still armed after the last bar, so
    # the live app can exit when the index touches it instead of a bar later (W12, 07 Oct).
    tr.lim_last = lim if pos else None
    return tr


def _rf_track(c):
    d = pd.Series(range_filter_signals(c, 20, 3.5)).replace(0, np.nan).ffill().fillna(0).values
    tr = _Track(len(c))
    last_d = 0
    for i in range(len(c)):
        if d[i] != last_d:
            if last_d != 0:
                tr.exit_px[i] = c[i]
            tr.entry_px[i] = c[i]
            last_d = d[i]
        tr.dir_end[i] = last_d
    return tr


def _segments(tr):
    out, n, i = [], len(tr.dir_end), 0
    while i < n:
        if not np.isnan(tr.entry_px[i]) and tr.dir_end[i] != 0:
            d, j = tr.dir_end[i], i + 1
            while j < n and tr.dir_end[j] == d and np.isnan(tr.entry_px[j]):
                j += 1
            out.append((i, j, d))
            i = j
        else:
            i += 1
    return out


def _no_progress(tr, h, l, c, bars, pts):
    for a, z, d in _segments(tr):
        k = a + bars
        if k >= z or k >= len(c):
            continue
        e = tr.entry_px[a]
        mfe = (h[a + 1:k + 1].max() - e) if d > 0 else (e - l[a + 1:k + 1].min())
        if mfe < pts:
            tr.dir_end[k:z] = 0
            tr.exit_px[k] = c[k]
            if z < len(c) and np.isnan(tr.entry_px[z]):
                tr.exit_px[z] = np.nan


def _drop_entries(tr, mask):
    for a, z, d in _segments(tr):
        if mask[a]:
            tr.dir_end[a:z] = 0
            tr.entry_px[a] = np.nan
            if z < len(tr.dir_end) and np.isnan(tr.entry_px[z]):
                tr.exit_px[z] = np.nan


def _sr_box_mask(h, l, c, bars, atr_mult, edge):
    atr = _atr(h, l, c, 14)
    hh = pd.Series(h).rolling(bars).max().values
    ll = pd.Series(l).rolling(bars).min().values
    p = (c - ll) / np.maximum(hh - ll, 1e-9)
    m = np.nan_to_num((((hh - ll) <= atr_mult * atr) & (p > edge) & (p < 1 - edge)).astype(float)).astype(bool)
    # Ram fills at the OPEN of bar a, so the decision may only use bars up to a-1 (the signal bar).
    return np.r_[False, m[:-1]]


def _track_from_trades(ts, trades):
    """Engine track from another kernel's trade list (entry/exit fills at that kernel's own prices)."""
    tr = _Track(len(ts))
    pos_of = pd.Series(np.arange(len(ts)), index=pd.to_datetime(ts))
    for t in trades:
        a = pos_of.get(pd.Timestamp(t["entry_time"]))
        if a is None:
            continue
        d = 1 if str(t["direction"]).upper() == "LONG" else -1
        tr.entry_px[a] = float(t["entry_price"])
        z = None
        if t.get("exit_reason") != "OPEN" and t.get("exit_time") not in (None, "", "-"):
            z = pos_of.get(pd.Timestamp(t["exit_time"]))
        if z is None:
            tr.dir_end[a:] = d
        else:
            tr.dir_end[a:z] = d
            tr.exit_px[z] = float(t["exit_price"])
    return tr


# Live-only metadata, NOT used by the backtest's own exit logic (that logic is
# entirely track-driven -- see book()/the main loop below, neither of which
# ever reads sl/target1/target2). Attached to each trade purely for the live
# engine and UI/Telegram/journal display. Per-strategy values live as class
# attributes on RamRangeFilterBoxKernel (SL_PTS/TARGET1_PCT/TARGET2_PCT), not
# here, so A/B/C can each override them -- see that class for the sourcing
# and the per-option numbers (scratch/research_14L/FINDINGS.md round 21,
# live_limits.py, cross-checked against this session's own MAE analysis).
#
# IMPORTANT: a TARGET1_PCT/TARGET2_PCT of 0 must produce a target of exactly
# 0.0 (not entry +/- 0%, which equals entry price -- a truthy, immediately-
# true `pos.target1 > 0` / `ltp >= pos.target1` the instant the position
# opens). 0.0 is the sentinel position_monitor treats as "off".
def _target_px(entry, direction, pct):
    return entry + direction * (entry * pct) if pct > 0 else 0.0


def _combine(ts, c, tracks, order, hold_only, i0, qty, sl_pts, t1_pct, t2_pct):
    """One lot, priority = order. An engine in hold_only may keep an already-open same-direction
    position but never opens one. Same direction across a handover = keep holding (no exit/re-entry)."""
    trades, cur_dir, cur_ctrl, e_px, e_i = [], 0, None, 0.0, 0
    cur_sl, cur_t1, cur_t2 = 0.0, 0.0, 0.0

    def book(i, px, reason):
        if e_i >= i0:
            pts = (px - e_px) * cur_dir
            trades.append({"entry_time": str(pd.Timestamp(ts[e_i])), "exit_time": str(pd.Timestamp(ts[i])),
                           "direction": "LONG" if cur_dir > 0 else "SHORT", "entry_price": round(e_px, 2),
                           "exit_price": round(px, 2), "exit_reason": reason, "pnl": round(pts * qty, 2),
                           "pnl_pts": round(pts, 2), "sl": round(cur_sl, 2),
                           "target1": round(cur_t1, 2), "target2": round(cur_t2, 2)})

    for i in range(len(c)):
        ctrl, d = None, 0
        for k in order:
            v = tracks[k].dir_end[i]
            if v != 0 and (k not in hold_only or v == cur_dir):
                ctrl, d = k, v
                break
        if d == cur_dir and d != 0:
            cur_ctrl = ctrl
            continue
        if cur_dir != 0:
            own_exit = tracks[cur_ctrl].exit_px[i]
            new_entry = tracks[ctrl].entry_px[i] if ctrl else np.nan
            px = own_exit if not np.isnan(own_exit) else (new_entry if not np.isnan(new_entry) else c[i])
            book(i, px, f"{cur_ctrl}_EXIT" if not np.isnan(own_exit) else f"PREEMPT_BY_{ctrl}" if ctrl else "FLAT")
        if d != 0:
            ne = tracks[ctrl].entry_px[i]
            e_px, e_i = (ne if not np.isnan(ne) else c[i]), i
            cur_sl = e_px - d * sl_pts
            cur_t1 = _target_px(e_px, d, t1_pct)
            cur_t2 = _target_px(e_px, d, t2_pct)
        cur_dir, cur_ctrl = d, ctrl
    if cur_dir != 0 and e_i >= i0:
        trades.append({"entry_time": str(pd.Timestamp(ts[e_i])), "exit_time": "", "direction": "LONG" if cur_dir > 0 else "SHORT",
                       "entry_price": round(e_px, 2), "exit_price": None, "exit_reason": "OPEN", "pnl": 0, "pnl_pts": 0,
                       "sl": round(cur_sl, 2), "target1": round(cur_t1, 2), "target2": round(cur_t2, 2)})
    return trades


class RamRangeFilterBoxKernel(StrategyKernel):
    strategy_id = "custom_ram_rf_box"
    display_name = "Ram + Range Filter + S/R Box (Option C)"
    live_capable = False

    # Shared engine for Options A / B / C (custom_option_a_*.py and custom_option_b_*.py subclass this).
    ORDER = ("RAM", "RF")                   # priority: first engine holding a position controls the single lot
    HOLD_ONLY = ("RF",)                     # these engines may keep a same-direction position, never open one
    NP_ENGINES = ("RAM", "RF")              # engines that get the no-progress exit
    USE_BOX = True                          # S/R box sideways filter on Ram's new entries
    NP_BARS, NP_PTS = 4, 10.0               # no-progress exit
    BOX_BARS, BOX_ATR, BOX_EDGE = 24, 7.0, 0.2   # S/R box: 24 bars, <= 7 ATR, block entries in middle 60%

    # Live SL/target settings, per strategy_id (2026-10-01, cross-checked between
    # this session's own MAE analysis and a second independent pass --
    # scratch/research_14L/FINDINGS.md round 21, live_limits.py). Option C's
    # box filter changes its trade mix enough to need a wider SL (worst-ever
    # adverse move 1,918 pts vs ~1,340 for A/B) -- these values are Option C's
    # own (A/B override in their own files). IMPORTANT: reaching target1 moves
    # the live SL to breakeven (trade_manager.py's mark_t1_hit()) -- it is NOT
    # purely informational like target2. T1=0 (off) here because that
    # breakeven-jump cost Option C -271 to -915 pts across the levels tested;
    # target2 stays informational-only regardless of value (see
    # main.py's _INFO_ONLY_TARGET2), so its number is a safe no-op either way.
    SL_PTS = 2000.0
    TARGET1_PCT = 0.0      # off -- touching target1 would move live SL to breakeven, costly for this engine
    TARGET2_PCT = 0.03     # informational only (_INFO_ONLY_TARGET2) -- never auto-exits

    def on_bar(self, bar_idx, base_df, row, position, context):
        return None                          # backtest-only strategy (see module docstring)

    def run_backtest(self, frames, initial_capital=500_000, lot_size=30, lot_multiplier=1, start_date=None, end_date=None):
        b = frames["5"].copy()
        b["timestamp"] = pd.to_datetime(b["timestamp"])
        t = b.timestamp.dt.time
        b = b[(t >= pd.Timestamp("09:15").time()) & (t <= pd.Timestamp("15:25").time())]
        if end_date:
            b = b[b.timestamp.dt.date <= pd.to_datetime(end_date).date()]
        b = b.reset_index(drop=True)
        if len(b) < 300:
            return {"error": "Not enough 5-minute data (need at least 300 bars)", "trades": []}

        o, h, l, c = (b[k].values.astype(float) for k in ("open", "high", "low", "close"))
        ts = b.timestamp.values
        tracks = {"RAM": _ram_track(o, h, l, c), "RF": _rf_track(c)}
        if "TG" in self.ORDER:
            from strategy_kernel import get_kernel
            tg = get_kernel("custom_time_gated_alpha_combo")
            if tg is None:
                return {"error": "Time-Gated Alpha Combo strategy is not registered", "trades": []}
            tg_res = tg.safe_run_backtest(frames, initial_capital, lot_size, lot_multiplier, start_date, end_date)
            tracks["TG"] = _track_from_trades(ts, tg_res.get("trades", []))
        for k in self.NP_ENGINES:
            _no_progress(tracks[k], h, l, c, self.NP_BARS, self.NP_PTS)
        if self.USE_BOX:
            _drop_entries(tracks["RAM"], _sr_box_mask(h, l, c, self.BOX_BARS, self.BOX_ATR, self.BOX_EDGE))
        if getattr(get_settings(), "position_hold_mode", "INTRADAY") != "CARRY_FORWARD":
            tod = b.timestamp.dt.hour.values * 100 + b.timestamp.dt.minute.values
            for k in ("RAM", "RF"):              # Time-Gated already applies its own INTRADAY rules
                tracks[k].dir_end[tod >= 1520] = 0

        i0 = int(np.searchsorted(ts, np.datetime64(pd.Timestamp(start_date)))) if start_date else 0
        trades = _combine(ts, c, tracks, self.ORDER, self.HOLD_ONLY, i0, lot_size * lot_multiplier,
                           self.SL_PTS, self.TARGET1_PCT, self.TARGET2_PCT)
        return {"trades": trades}
