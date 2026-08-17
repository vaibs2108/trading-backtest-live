"""
custom_cusum15_nodonchian_cd8.py — "CUSUM 1.5 + No Donchian + CD8 (Research)"

Live-capable (live_capable = True) as of the promotion to Live Trading.
The best-performing config found across the whole research session,
ranked on points captured (the user's stated primary goal -- "everything
else will fall in line with it").

KNOWN RISK, carried into live trading deliberately per the user's own
choice: maxDD -7.92% sits ~0.9 percentage points over their stated 7%
risk budget (see below) -- this was flagged explicitly before promoting
this strategy to Live Trading.

Full 13-month validated result (see scratch/research_v1/SESSION_STATUS.md
and monthly_points_comparison.py):
  +21,096.3 pts (14.3% capture), PF 2.37, net +632,889 rs, maxDD -7.92%

Compared to the other two research strategies on the backtest page:
  - Regime T/R V1 Final:        +17,815.3 pts, PF 1.94, net +534,458, maxDD -6.94%
  - HalfTrend + Hull Standalone: +15,117.4 pts, PF 2.21, net +453,519, maxDD -5.27%

This one wins on points in 9 of the last 14 months, and is the ONLY one
of the three that turns July 2026 -- the month whose negative P&L started
this entire research project -- net POSITIVE (+413.0 pts / +12,391 rs).

Known trade-off: maxDD -7.92% sits ~0.9 percentage points over the 7%
risk budget. A cooldown sweep (2 through 16 bars) found this is the
closest any config gets without giving back most of the extra return --
see RegimeTrendRangeV2CUSUMCappedSLKernel's docstring in
regime_trend_range_v1_research.py for the full diagnosis (the drawdown
was traced to Donchian entries turning net-negative at this looser CUSUM
threshold, not diffuse risk -- disabling Donchian was the real fix).

This strategy is wired into the live polling loop (main.py's _proc_list)
and is selectable from the Live Trading page's strategy dropdown as of
the promotion -- it participates in real-time signal generation,
Telegram alerts, and (subject to the global Auto Trade switch) real
order placement, the same as any other live strategy. Its
COOLDOWN_BARS=8 is load-bearing for the validated drawdown control above
and must be respected by any live cooldown-after-loss logic (see
main.py's _COOLDOWN_BARS lookup).
"""
import sys
import os

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from strategies.regime_trend_range_v1_research import RegimeTrendRangeV2CUSUMCappedSLKernel
from strategies.regime_agents.vwap_reversion_entry import VWAPReversionAgent


class CUSUM15NoDonchianCd8Kernel(RegimeTrendRangeV2CUSUMCappedSLKernel):
    strategy_id = "custom_cusum15_nodonchian_cd8"
    display_name = "CUSUM 1.5 + No Donchian + CD8 (Research)"
    live_capable = True

    BREAKOUT_MIN_ER = 0.30
    BREAKOUT_MIN_CONFIDENCE = 0.80
    DONCHIAN_LOOKBACK = 70   # unused -- Donchian entries disabled below
    TRAIL_MULT = 3.5
    BE_TRIGGER = 2.0
    COOLDOWN_BARS = 8        # validated: closest to the 7% DD cap of any cooldown swept
    CUSUM_THRESHOLD_ATR = 1.5
    DISABLE_DONCHIAN = True
    HALFLIFE_MAX_MULT = 1.5

    def __init__(self):
        super().__init__()
        self._range_agent = VWAPReversionAgent(entry_std=1.75, min_std_atr=0.5)


# strategy_kernel.py's custom-strategy auto-discovery instantiates EVERY
# StrategyKernel subclass it finds bound at module level (via dir(mod)),
# not just ones defined in this file -- delete the imported base class
# name so only CUSUM15NoDonchianCd8Kernel is discovered.
del RegimeTrendRangeV2CUSUMCappedSLKernel
