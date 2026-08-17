"""
custom_regime_v1_trend_range_final.py — "Regime T/R V1 Final (Research)"

Live-capable (live_capable = True) as of the promotion to Live Trading.
Packages the final,
validated result of the scratch/research_v1/ research session: the
Regime Trend/Range V2 kernel chain (Donchian breakout + CUSUM change-point
filter + HTF-hold exit + SuperTrend-adaptive-oscillator entry on the
trend side; VWAP session-anchored mean reversion + Ornstein-Uhlenbeck
half-life time-stop on the range side).

Full 13-month validated result (see scratch/research_v1/SESSION_STATUS.md):
  Combined: +17,815.3 pts (12.1% capture), net +534,458 rs,
            PF 1.94, max drawdown -6.94% (within the 7% risk budget)

The SuperTrend-adaptive entry (ported from the user's own PineScript
research, jsr 2 strategy.txt) was added as one more fallback trend-entry
path on top of the original V1 Final chain -- a small, clean, validated
improvement over the prior +17,614.2 pts / PF 1.93 / maxDD -6.98% result
(more points, better PF, slightly LOWER drawdown, no downside found).

A separate grid-architecture attempt at improving the range side was
tried and rejected (every sizing/tier calibration that controlled
drawdown also captured fewer points than this single-position config) --
see scratch/research_v2_grid/README_V2.md.

Two other PineScript-derived ideas were tested and NOT folded in here:
  - HalfTrend+Hull confluence: strong standalone (PF 2.11) but makes this
    blended chain WORSE when stacked in (its signals compete with/preempt
    CUSUM/Donchian/Burst rather than complementing them) -- see the
    separate standalone strategy instead.
  - Gate loosening (lower confidence/score thresholds, shorter cooldown):
    every lever that increases trade count also breaches the 7% DD cap,
    several by 2x+ -- rejected across the board.

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

from strategies.regime_trend_range_v1_research import RegimeTrendRangeV2SuperTrendAddOnKernel
from strategies.regime_agents.vwap_reversion_entry import VWAPReversionAgent


class RegimeV1TrendRangeFinalKernel(RegimeTrendRangeV2SuperTrendAddOnKernel):
    strategy_id = "custom_regime_v1_trend_range_final"
    display_name = "Regime T/R V1 Final (Research)"
    live_capable = True

    # Trend side: Donchian breakout lookback, CUSUM threshold, HTF-hold exit
    # (inherited), retuned trail/breakeven, shorter cooldown, SuperTrend-
    # adaptive-oscillator entry (inherited from RegimeTrendRangeV2SuperTrendAddOnKernel).
    BREAKOUT_MIN_ER = 0.30
    BREAKOUT_MIN_CONFIDENCE = 0.80
    DONCHIAN_LOOKBACK = 70
    TRAIL_MULT = 3.5
    BE_TRIGGER = 2.0
    COOLDOWN_BARS = 4
    CUSUM_THRESHOLD_ATR = 2.0

    # Range side: half-life timeout multiplier (validated optimum on the
    # full 13-month history, not the smaller Jul-Aug sample which
    # preferred 3.0 due to a single outlier trade).
    HALFLIFE_MAX_MULT = 1.5

    def __init__(self):
        super().__init__()
        # Swap the default percentile-range agent for session-anchored
        # VWAP + std-dev mean reversion -- the strongest range-side result
        # of the research session (2x the half-life-only baseline).
        self._range_agent = VWAPReversionAgent(entry_std=1.75, min_std_atr=0.5)


# strategy_kernel.py's custom-strategy auto-discovery instantiates EVERY
# StrategyKernel subclass it finds bound at module level (via dir(mod)),
# not just ones defined in this file -- so the imported base class above
# would otherwise also get registered as its own live_capable=True
# strategy. Delete the imported name so only RegimeV1TrendRangeFinalKernel
# (defined in this file, live_capable=False) is discovered.
del RegimeTrendRangeV2SuperTrendAddOnKernel
