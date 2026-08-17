"""
custom_halftrend_hull_standalone.py — "HalfTrend + Hull (Standalone, Research)"

Live-capable (live_capable = True) as of the promotion to Live Trading.
A genuinely separate system from "Regime T/R V1 Final" -- ported from the user's own
PineScript research (scratch/research_v1/pine scripts/All bank atm.txt):
HalfTrend (Everget) trend-reversal flip, confirmed by a Hull-momentum
flip within a short lookback window.

Why this is its OWN strategy rather than folded into V1 Final: tested
both ways. Standalone (pullback + HalfTrend+Hull only, no CUSUM/Donchian/
Burst) it's genuinely strong -- best PF of anything tried this whole
session. Stacked as an extra entry path on top of V1 Final's full chain,
it made the blend WORSE (its signals compete with/preempt CUSUM/Donchian/
Burst rather than complementing them) -- see
scratch/research_v1/SESSION_STATUS.md for the full comparison.

Full 13-month validated result at HT_AMPLITUDE=4 (best of the sweep --
2/3/4 were tested, 4 won on every metric):
  +15,117.4 pts (10.3% capture), PF 2.21, net +453,519 rs,
  maxDD -5.27% (comfortably inside the 7% risk budget)

Parameters below are ordinary class attributes -- change them directly
to try other configurations and re-run the backtest page. Documented
sweep results for each are in
strategies/regime_trend_range_v1_research.py's class docstring.

This strategy is wired into the live polling loop (main.py's _proc_list)
and is selectable from the Live Trading page's strategy dropdown as of
the promotion -- it participates in real-time signal generation,
Telegram alerts, and (subject to the global Auto Trade switch) real
order placement, the same as any other live strategy.
"""
import sys
import os

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from strategies.regime_trend_range_v1_research import RegimeTrendRangeV2HalfTrendStandaloneKernel
from strategies.regime_agents.vwap_reversion_entry import VWAPReversionAgent


class HalfTrendHullStandaloneKernel(RegimeTrendRangeV2HalfTrendStandaloneKernel):
    strategy_id = "custom_halftrend_hull_standalone"
    display_name = "HalfTrend + Hull (Standalone, Research)"
    live_capable = True

    # HalfTrend + Hull confluence params (see class docstring in
    # regime_trend_range_v1_research.py for the sweep that picked these).
    HT_AMPLITUDE = 4
    HT_CHANNEL_DEVIATION = 2
    HT_ATR_LENGTH = 40
    HULL_LENGTH = 11
    HULL_LOOKBACK_BARS = 3

    # Exit engine + range side -- reuse the same validated pieces as V1
    # Final (Donchian/CUSUM/Burst are NOT used here at all, unlike V1
    # Final -- those class attributes below only affect the RANGE-side
    # half-life/VWAP logic and the trend TRAIL/BE exit management, which
    # this kernel inherits from the same base chain).
    TRAIL_MULT = 3.5
    BE_TRIGGER = 2.0
    COOLDOWN_BARS = 4
    HALFLIFE_MAX_MULT = 1.5

    def __init__(self):
        super().__init__()
        self._range_agent = VWAPReversionAgent(entry_std=1.75, min_std_atr=0.5)


# strategy_kernel.py's custom-strategy auto-discovery instantiates EVERY
# StrategyKernel subclass it finds bound at module level (via dir(mod)),
# not just ones defined in this file -- delete the imported base class
# name so only HalfTrendHullStandaloneKernel is discovered.
del RegimeTrendRangeV2HalfTrendStandaloneKernel
