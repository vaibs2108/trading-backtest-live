"""
custom_cusum15_plus_raw_halftrend.py — "CUSUM 1.5 + Raw HalfTrend (Research)"

BACKTEST-PAGE ONLY. live_capable = False deliberately -- this is the
result of an extensive research thread (see
scratch/research_v1/diagnose_final_combo.py and the surrounding scripts
in that directory for the full derivation) being promoted to the
backtest page specifically so results can be checked across timeframes
before any further step. It has NOT been wired into the live polling
loop, the Live Trading dropdown, or Telegram alerts.

CUSUM 1.5 runs exactly as the already-live custom_cusum15_nodonchian_cd8
strategy (same params, same SL, same everything) -- the only addition is
raw, completely unfiltered HalfTrend as an extra entry trigger: no Hull
confirmation, no regime gate, no orchestrator quality gate, checked
first each bar. HalfTrend's own stop is a swing-based formula (15-bar
low/high -/+ 1.5xATR(14)), independently validated against several
alternatives. (A flat 300pt SL cap was tried and REMOVED -- it tightened
recoverable drawdowns into permanent losses, hurting net P&L substantially
in CARRY_FORWARD mode by cutting off trades before they could recover into
large multi-day winners; see git history / conversation for the
train/validate numbers that led to reverting it.)

Full 13-month validated result (tuned on Jul'25-Jan'26, confirmed on the
untouched Feb'26-Aug'26 holdout before being kept):
  n=601, PF 1.84, net +710,379 rs, maxDD -6.16%, +23,679.3 pts
  (vs CUSUM 1.5 alone: n=325, PF 2.37, net +632,889 rs, maxDD -7.92%)
  NOTE: these figures predate the position_hold_mode toggle (INTRADAY is
  now the default -- see config.py) and should be re-verified under the
  current settings before being treated as current.

See RegimeTrendRangeV2CUSUMPlusRawHalfTrendKernel's own docstring in
regime_trend_range_v1_research.py for the full list of things tried and
rejected along the way (regime-gating HalfTrend, various quality
filters, a SuperTrend-based exit, applying the same SL formula to CUSUM
too), and the transaction-cost caveat (the shared cost estimator is
futures-style and has not been reconciled against real options trading
costs).
"""
import sys
import os

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from strategies.regime_trend_range_v1_research import RegimeTrendRangeV2CUSUMPlusRawHalfTrendKernel
from strategies.regime_agents.vwap_reversion_entry import VWAPReversionAgent


class CUSUM15PlusRawHalfTrendKernel(RegimeTrendRangeV2CUSUMPlusRawHalfTrendKernel):
    strategy_id = "custom_cusum15_plus_raw_halftrend"
    display_name = "CUSUM + HalfTrend Combo (Backtest Only)"
    live_capable = False

    BREAKOUT_MIN_ER = 0.30
    BREAKOUT_MIN_CONFIDENCE = 0.80
    DONCHIAN_LOOKBACK = 70   # unused -- Donchian entries disabled below
    TRAIL_MULT = 3.5
    BE_TRIGGER = 2.0
    COOLDOWN_BARS = 8
    CUSUM_THRESHOLD_ATR = 1.5
    DISABLE_DONCHIAN = True
    HALFLIFE_MAX_MULT = 1.5

    HT_AMPLITUDE = 4
    HT_CHANNEL_DEVIATION = 2
    HT_ATR_LENGTH = 40
    HT_SL_LOOKBACK = 15
    HT_SL_ATR_MULT = 1.5

    def __init__(self):
        super().__init__()
        self._range_agent = VWAPReversionAgent(entry_std=1.75, min_std_atr=0.5)


# strategy_kernel.py's custom-strategy auto-discovery instantiates EVERY
# StrategyKernel subclass it finds bound at module level (via dir(mod)),
# not just ones defined in this file -- delete the imported base class
# name so only CUSUM15PlusRawHalfTrendKernel is discovered.
del RegimeTrendRangeV2CUSUMPlusRawHalfTrendKernel
