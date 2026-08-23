"""
custom_alpha_combo_cusum125.py -- "Alpha Combo" (CUSUM 1.25 + Raw HalfTrend)

LIVE-CAPABLE (promoted 2026-08-22) -- the current best strategy under
train/validate/full discipline out of everything tried this research
session, including all 4 dual-engine pyramid variants (removed after
failing discipline in every window). Default live strategy going forward;
see backend/live_bar_processor.py's AlphaComboLiveProcessor and
backend/main.py's _proc_list / _TRAIL_PARAMS / _COOLDOWN_BARS for the live
wiring, which mirrors the 3 prior promotions exactly (same trail/cooldown
shape, inherited unchanged from CUSUM15PlusRawHalfTrendKernel below).

Same as "CUSUM + HalfTrend Combo (Backtest Only)"
(custom_cusum15_plus_raw_halftrend), with ONE change: CUSUM_THRESHOLD_ATR
lowered from 1.5 to 1.25 (a slightly looser change-point trigger, so
CUSUM fires a bit more often). Everything else -- HalfTrend params,
trailing/cooldown/breakeven settings, Range/VWAP agent -- is identical.

Validated via train/validate/full discipline (Research/data/clean parquet,
CARRY_FORWARD hold mode, 1 lot = 30 qty) against the CUSUM 1.5 baseline --
beats it on EVERY metric in EVERY window, including the untouched
validate slice, not just a favorable full-period number:

  TRAIN (2025-07-01..2026-01-31):
    baseline: PF 1.48, net +167,754, maxDD -5.21%
    alpha:    PF 1.49, net +172,013, maxDD -5.18%
  VALIDATE (2026-02-01..2026-08-20, untouched):
    baseline: PF 2.54, net +581,643, maxDD -7.03%
    alpha:    PF 2.60, net +601,954, maxDD -6.82%
  FULL (2025-07-01..2026-08-20):
    baseline: n=535, PF 2.03, net +749,396, maxDD -5.65%
    alpha:    n=539, PF 2.06, net +773,968, maxDD -5.49%

Win rate is identical to baseline (52.9%) in every window -- the edge
comes from a handful of extra CUSUM entries landing at the same hit
rate as the existing ones, not a shift in trade quality.
"""
import sys
import os

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from strategies.custom.custom_cusum15_plus_raw_halftrend import CUSUM15PlusRawHalfTrendKernel


class AlphaComboCUSUM125Kernel(CUSUM15PlusRawHalfTrendKernel):
    strategy_id = "custom_alpha_combo_cusum125"
    display_name = "Alpha Combo (CUSUM 1.25 Tuned)"
    live_capable = True

    CUSUM_THRESHOLD_ATR = 1.25


# strategy_kernel.py's custom-strategy auto-discovery instantiates EVERY
# StrategyKernel subclass it finds bound at module level (via dir(mod)),
# not just ones defined in this file -- delete the imported base class
# name so only AlphaComboCUSUM125Kernel is discovered.
del CUSUM15PlusRawHalfTrendKernel
