"""
custom_time_gated_alpha_combo.py -- "Time-Gated Alpha Combo" (BankNifty)

LIVE-CAPABLE (promoted to live trading 2026-08-23, backtest-page addition was
2026-08-23 same day) -- promoted from
scratch/research_banknifty/strategy_time_gated_alpha.py after a full
train/validate/full discipline check plus a deep trade-level audit (see
that scratch session for the full methodology): chronological/overlap
check, zero entries inside the trap windows (confirmed empirically, not
just read from the code), independent stats recompute matching the
reported numbers, and a signal-reversal falsification test (PF 2.24 ->
0.45, net flips sign) confirming the edge is real and directional.

BankNifty-only validation: the time-gate windows and CUSUM/HalfTrend params
here were tuned and validated exclusively on BankNifty (see
STRATEGY_REGISTRY.md at the repo root) -- do not assume this transfers to
other instruments without re-running discipline there first.

Same as Alpha Combo (custom_alpha_combo_cusum125), with ONE addition: new
entries are blocked during two "trap" windows --
  10:00-10:45 AM (morning exhaustion trap)
  13:00-13:45 PM (midday lunch liquidity slump)
Existing open positions continue to be managed (trailing stop / targets)
normally through these windows -- only NEW entries are gated. Everything
else -- CUSUM threshold, HalfTrend params, trailing/breakeven settings,
cooldown -- is identical to Alpha Combo.

Validated via train/validate/full discipline (local CSV snapshot refreshed
to 2026-08-18, CARRY_FORWARD hold mode, 1 lot = 30 qty) against the Alpha
Combo baseline -- beats it on profit factor and net P&L in EVERY window,
including the untouched validate slice:

  TRAIN (2025-07-01..2026-01-31):
    baseline: PF 1.39, net +134,415, maxDD 5.02%
    time-gated: PF 1.83, net +209,631, maxDD 5.96%
  VALIDATE (2026-02-01..2026-08-18, untouched):
    baseline: PF 2.05, net +562,359, maxDD 6.87%
    time-gated: PF 2.41, net +610,521, maxDD 5.34%
  FULL (2025-07-01..2026-08-18):
    baseline: PF 1.79, net +696,773, maxDD 5.77%
    time-gated: PF 2.20, net +820,152, maxDD 5.96%

Note: max drawdown is slightly worse than baseline on TRAIN and FULL
(by <1 percentage point) -- a tighter-trailing-stop variant (TRAIL_MULT
3.0, BE_TRIGGER 1.5) closes most of that gap and cleanly beats baseline on
FULL, but trades away ~10-14% of VALIDATE's net profit to get there. This
file keeps the original (higher-profit) risk parameters; the DD-tuned
variant was evaluated but not adopted -- see scratch/research_banknifty/
if that trade-off is revisited later.
"""
import sys
import os

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pandas as pd

from strategies.custom.custom_alpha_combo_cusum125 import AlphaComboCUSUM125Kernel


class TimeGatedAlphaComboKernel(AlphaComboCUSUM125Kernel):
    strategy_id = "custom_time_gated_alpha_combo"
    display_name = "Time-Gated Alpha Combo (Anti-Trap)"
    live_capable = True

    # Anti-Trap Time Gates (HHMM format) -- blocks NEW entries only
    TRAP_WINDOW_1_START = 1000  # 10:00 AM
    TRAP_WINDOW_1_END = 1045    # 10:45 AM
    TRAP_WINDOW_2_START = 1300  # 01:00 PM
    TRAP_WINDOW_2_END = 1345    # 01:45 PM

    def on_bar(self, bar_idx, base_df, row, position, context):
        if position == "NONE":
            ts = pd.Timestamp(row["timestamp"])
            time_hm = ts.hour * 100 + ts.minute
            in_morning_trap = self.TRAP_WINDOW_1_START <= time_hm <= self.TRAP_WINDOW_1_END
            in_midday_slump = self.TRAP_WINDOW_2_START <= time_hm <= self.TRAP_WINDOW_2_END
            if in_morning_trap or in_midday_slump:
                return None
        return super().on_bar(bar_idx, base_df, row, position, context)


# strategy_kernel.py's custom-strategy auto-discovery instantiates EVERY
# StrategyKernel subclass it finds bound at module level (via dir(mod)),
# not just ones defined in this file -- delete the imported base class
# name so only TimeGatedAlphaComboKernel is discovered.
del AlphaComboCUSUM125Kernel
