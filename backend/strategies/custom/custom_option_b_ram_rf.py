"""
custom_option_b_ram_rf.py -- "Option B: Ram > Range Filter (hold-only + no-progress)" -- BankNifty, 1 lot, BACKTEST PAGE ONLY.

Same engine as custom_ram_rf_box.py (Option C) with the S/R box filter switched OFF:
  - Ram_strategy opens positions (next-bar-open entry, UT Bot limit exit, opposite entry reverses).
  - Range Filter (20, 3.5) is HOLD-ONLY: keeps the lot when Ram exits only if it points the same way; never opens.
  - No-progress exit on both engines: not +10 pts in profit within 4 bars -> exit at that bar's close.
Researched in scratch/research_14L/ (FINDINGS.md rounds 16-20). live_capable = False.
Verified 2026-10-01 through the app's backtest engine (CARRY_FORWARD, index points, 1 lot):
  Backtest page 2025-07-01..2026-09-30: 33,466 pts (+Rs 10.04L), 455 trades, PF 1.81, worst DD -1,862 pts,
    +25,032 pts after the app's cost formula.
  Dhan 5-yr 2022-01..2026-09: 62,217 pts, +32,386 after costs, PF 1.35, worst DD -5,477 pts.
    By year: 2022 +5,948 | 2023 +12,056 | 2024 +8,273 | 2025 +6,084 | 2026 Jan-Sep +29,856.
  Reversal test (all Ram + Range Filter signals flipped): PF 0.70, -11,022 pts -> the edge is directional, not luck.
Only CARRY_FORWARD was validated.

HONEST RESULTS 2026-10-08 (D2, your decision: the Backtest page must show the honest view). Two look-aheads
were removed from the shared engine (custom_ram_rf_box.py): the Range Filter "keep holding?" decision at a
Ram limit fill used RF at the END of the fill bar, and a limit fill in the same bar as a reversal was booked
at the limit instead of the open. Same data, index points, 1 lot:
  2025-07-01..2026-09-30: 32,234 pts, PF 1.77, worst DD -1,923 (was 33,947 / 1.83 / -1,862)
  Dhan 5-yr 2022-01..2026-09: 53,798 pts, PF 1.29, worst DD -6,648 (was 62,217 / 1.35 / -5,477)
The live app follows exactly these rules.
"""
import os
import sys

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from strategies.custom.custom_ram_rf_box import RamRangeFilterBoxKernel


class OptionBRamRangeFilterKernel(RamRangeFilterBoxKernel):
    strategy_id = "custom_option_b_ram_rf"
    display_name = "Option B: Ram > Range Filter (hold-only + no-progress)"
    live_capable = False

    ORDER = ("RAM", "RF")
    HOLD_ONLY = ("RF",)
    NP_ENGINES = ("RAM", "RF")
    USE_BOX = False

    # Live SL/target (2026-10-01, cross-checked between two independent passes --
    # see custom_ram_rf_box.py's class docstring for the mechanism/sourcing).
    # B's worst-ever adverse move before its own exit was ~1,343 pts over 5
    # years -- 1,500 never fires (tighter than Option C's 2,000 since B has no
    # box filter pulling in a wider trade mix). T1 off for the same reason as
    # A/C: reaching target1 moves the live SL to breakeven, and testing showed
    # it costs this engine points at every level tried rather than helping.
    SL_PTS = 1500.0
    TARGET1_PCT = 0.0      # off -- touching target1 would move live SL to breakeven
    TARGET2_PCT = 0.03     # informational only (_INFO_ONLY_TARGET2) -- never auto-exits


# Custom-strategy auto-discovery registers StrategyKernel subclasses found in this module --
# remove the imported base so only Option B is discovered here (same pattern as the other custom files).
del RamRangeFilterBoxKernel
