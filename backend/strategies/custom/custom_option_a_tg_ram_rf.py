"""
custom_option_a_tg_ram_rf.py -- "Option A: Time-Gated > Ram > Range Filter" -- BankNifty, 1 lot, BACKTEST PAGE ONLY.

One lot, priority order (the first engine holding a position controls the lot; same direction across a
handover = keep holding):
  1. Time-Gated Alpha Combo -- the app's own custom_time_gated_alpha_combo kernel, unchanged (its trades are
     taken from its own run_backtest, including its stops/trailing/regime exits).
  2. Ram_strategy  -- user's Pine port (see custom_ram_rf_box.py).
  3. Range Filter (20, 3.5) -- always has a direction, fills any remaining time.
No no-progress exit, no hold-only rule, no box filter (the configuration tested as "Option A").
Researched in scratch/research_14L/ (FINDINGS.md rounds 15-18). live_capable = False.
Verified 2026-10-01 through the app's backtest engine (CARRY_FORWARD, index points, 1 lot):
  Backtest page 2025-07-01..2026-09-30: 38,257 pts (+Rs 11.48L), 839 trades, PF 1.65, worst DD -2,967 pts,
    +22,681 pts after the app's cost formula.
  Dhan 5-yr 2022-01..2026-09: 68,358 pts but only +15,128 after costs (3,277 trades), PF 1.27, worst DD -7,458 pts.
    By year: 2022 +10,229 | 2023 +7,727 | 2024 +8,411 | 2025 +12,202 | 2026 Jan-Sep +29,788.
  Depends on Time-Gated, which is flat-to-negative after costs in 2022-2025H1 (regime-dependent).
Only CARRY_FORWARD was validated. Slow: runs the full Time-Gated backtest internally (~90 s for 15 months).
"""
import os
import sys

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from strategies.custom.custom_ram_rf_box import RamRangeFilterBoxKernel


class OptionATGRamRangeFilterKernel(RamRangeFilterBoxKernel):
    strategy_id = "custom_option_a_tg_ram_rf"
    display_name = "Option A: Time-Gated > Ram > Range Filter"
    live_capable = False

    ORDER = ("TG", "RAM", "RF")
    HOLD_ONLY = ()
    NP_ENGINES = ()
    USE_BOX = False

    # Live SL/target (2026-10-01, cross-checked between two independent passes --
    # see custom_ram_rf_box.py's class docstring for the mechanism/sourcing).
    # A's worst-ever adverse move before its own exit was ~1,346 pts over 5
    # years -- 1,500 never fires. T1 off: reaching target1 moves the live SL
    # to breakeven (not purely informational), and even a wide 2% T1 showed
    # no benefit for A in testing, so there's no reason to risk it firing.
    SL_PTS = 1500.0
    TARGET1_PCT = 0.0      # off -- touching target1 would move live SL to breakeven
    TARGET2_PCT = 0.03     # informational only (_INFO_ONLY_TARGET2) -- never auto-exits


del RamRangeFilterBoxKernel
