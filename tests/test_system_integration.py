r"""
tests/test_system_integration.py -- system checks for the strategies the app trades today.

Run:  .venv\Scripts\python.exe tests\test_system_integration.py      (no pytest needed)

  1. Every locked live strategy (config.LIVE_STRATEGY_IDS) is registered and loads.
  2. Each live strategy backtests cleanly on synthetic up / down / sideways data and returns well-formed trades.
  3. The lot size reaches backtest P&L (NIFTY 65 vs BANKNIFTY 30 must scale P&L by 65/30).
  4. SignalEvent serializes to the chart-marker / WebSocket dicts.

Rewritten 2026-10-03: the old version expected 4 kernels with only regime_trend_range live, and compared
against strategies/regime_strategy.py (superseded; stop-loss fills now use the real price on gaps).
"""
import sys
from pathlib import Path
import json
import logging
import numpy as np
import pandas as pd

backend_dir = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from config import LIVE_STRATEGY_IDS
from strategy_kernel import get_kernel, SignalEvent

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SystemTest")
logger.setLevel(logging.INFO)
REQUIRED_TRADE_KEYS = {"direction", "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "pnl"}


def generate_regime_frames(regime_type: str = "TRENDING_UP", num_days: int = 15) -> dict:
    """Generate synthetic data specifically designed for a target market regime."""
    np.random.seed(123 if regime_type == "TRENDING_UP" else 456 if regime_type == "TRENDING_DOWN" else 789)
    start_date = pd.Timestamp("2026-06-01 09:15:00")
    
    dates_5m = []
    curr = start_date
    for _ in range(num_days):
        day_start = curr.replace(hour=9, minute=15)
        for step in range(75):
            dates_5m.append(day_start + pd.Timedelta(minutes=5 * step))
        curr += pd.Timedelta(days=1)

    n = len(dates_5m)
    if regime_type == "TRENDING_UP":
        drift = np.linspace(0, 800, n)
        noise = np.cumsum(np.random.randn(n) * 15.0)
    elif regime_type == "TRENDING_DOWN":
        drift = np.linspace(0, -800, n)
        noise = np.cumsum(np.random.randn(n) * 15.0)
    else:  # SIDEWAYS / CHOP
        drift = np.sin(np.linspace(0, 10 * np.pi, n)) * 150.0
        noise = np.cumsum(np.random.randn(n) * 10.0)

    close = 50000.0 + drift + noise
    high = close + np.abs(np.random.randn(n) * 15.0) + 3.0
    low = close - np.abs(np.random.randn(n) * 15.0) - 3.0
    open_p = close + np.random.randn(n) * 5.0
    vol = np.random.randint(1000, 30000, size=n)

    df_5m = pd.DataFrame({
        "timestamp": dates_5m,
        "open": open_p, "high": high, "low": low, "close": close, "volume": vol
    })
    df_5m.set_index("timestamp", inplace=True)

    df_15m = df_5m.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    df_60m = df_5m.resample("60min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    df_1d = df_5m.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    df_5m.reset_index(inplace=True)

    return {"5": df_5m, "15": df_15m, "60": df_60m, "1D": df_1d}


def test_1_live_strategies_registered():
    logger.info("--- Test 1: live strategies registered ---")
    missing = [s for s in LIVE_STRATEGY_IDS if get_kernel(s) is None]
    assert not missing, f"Live strategies not registered: {missing}"
    logger.info(f"  OK: all {len(LIVE_STRATEGY_IDS)} live strategies load")
    return True


def test_2_live_backtests_all_regimes():
    logger.info("--- Test 2: live strategy backtests on up / down / sideways data ---")
    for regime in ("TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS"):
        frames = generate_regime_frames(regime, num_days=15)
        for sid in LIVE_STRATEGY_IDS:
            res = get_kernel(sid).run_backtest(frames, lot_size=30)
            assert isinstance(res, dict) and "trades" in res, f"{sid} {regime}: no trades list ({str(res)[:120]})"
            for tr in res["trades"]:
                missing = REQUIRED_TRADE_KEYS - set(tr)
                assert not missing, f"{sid} {regime}: trade missing {missing}"
                assert tr["direction"] in ("LONG", "SHORT"), f"{sid}: bad direction {tr['direction']}"
                assert str(tr["exit_time"]) >= str(tr["entry_time"]), f"{sid}: exit before entry {tr}"
            logger.info(f"  OK: {sid:36s} {regime:14s} {len(res['trades']):3d} trades")
    return True


def test_3_lot_size_scales_pnl():
    logger.info("--- Test 3: lot size reaches backtest P&L ---")
    frames = generate_regime_frames("TRENDING_UP", num_days=15)
    for sid in LIVE_STRATEGY_IDS:
        k = get_kernel(sid)
        p30 = sum(t["pnl"] for t in k.run_backtest(frames, lot_size=30)["trades"])
        p65 = sum(t["pnl"] for t in k.run_backtest(frames, lot_size=65)["trades"])
        if p30:
            assert abs(p65 / p30 - 65 / 30) < 0.01, f"{sid}: P&L ratio {p65 / p30:.3f}, expected {65 / 30:.3f}"
        logger.info(f"  OK: {sid:36s} P&L x{(p65 / p30 if p30 else 0):.3f} for lot 65 vs 30")
    return True


def test_4_signal_event_serialization():
    logger.info("--- Test 4: SignalEvent Serialization & Schema Integrity ---")
    sig = SignalEvent(
        signal="LONG",
        direction="LONG",
        timestamp="2026-07-25 10:15:00",
        strategy_id="regime_trend_range",
        entry_price=52500.50,
        sl=52350.00,
        target1=52700.00,
        target2=52950.00,
        reasons=["Regime TRENDING_UP", "Pullback entry confirmed"],
        regime="TRENDING_UP",
        trade_source="REGIME",
        atr=120.50
    )

    chart_marker = sig.to_chart_marker()
    ui_dict = sig.to_signal_dict()

    # Validate JSON serializability for WebSocket transmission
    json_marker = json.dumps(chart_marker)
    json_ui = json.dumps(ui_dict)

    assert chart_marker["signal"] == "LONG"
    assert chart_marker["strategy"] == "regime_trend_range"
    assert chart_marker["sl"] == 52350.00
    assert ui_dict["close"] == 52500.50

    logger.info("✅ Test 4 Passed: SignalEvent serializes cleanly to valid JSON schema.")
    return True


if __name__ == "__main__":
    tests = [test_1_live_strategies_registered, test_2_live_backtests_all_regimes,
             test_3_lot_size_scales_pnl, test_4_signal_event_serialization]
    failed = []
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append(f"{fn.__name__}: {e}")
            logger.error(f"FAILED {fn.__name__}: {e}")
    if failed:
        logger.error(f"{len(failed)} of {len(tests)} tests FAILED")
        sys.exit(1)
    logger.info(f"ALL {len(tests)} TESTS PASSED")
    sys.exit(0)
