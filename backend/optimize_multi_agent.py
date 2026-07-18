"""
optimize_multi_agent.py — Grid search for Multi-Agent strategy.
Loads data from CSV files, sweeps parameters offline.
"""
import sys, os, json, time, itertools, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("optimizer")
logger.setLevel(logging.INFO)

import pandas as pd
import numpy as np

# ── PARAMETER GRID ────────────────────────────────────────────────────────────
PARAM_GRID = {
    "ml_threshold": [0.0, 0.35, 0.50, 0.65],
    "htf_conflict": [
        (2.0, 1.5),    # current (tight)
        (4.0, 3.0),    # relaxed
        (8.0, 6.0),    # very relaxed
        (999, 999),     # disabled
    ],
    "min_score": [0.30, 0.40, 0.50],
    "sl_target": [
        (1.2, 2.5, 4.0),   # current
        (1.5, 2.0, 3.5),   # tighter targets
        (1.0, 1.5, 3.0),   # tight SL, quick T1
        (1.8, 3.0, 5.0),   # wider SL, bigger targets
    ],
}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
BT_FROM = "2026-05-20"
BT_TO = "2026-06-12"

def load_frames():
    """Load BANKNIFTY data from CSV files."""
    frames = {}
    tf_map = {"1": "1", "5": "5", "15": "15", "60": "60", "1D": "1D"}
    for tf_key, file_suffix in tf_map.items():
        path = os.path.join(DATA_DIR, f"Banknf_historical_data_{file_suffix}.csv")
        if os.path.exists(path):
            df = pd.read_csv(path)
            df = df.dropna(subset=["timestamp"])
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            frames[tf_key] = df
            logger.info(f"  Loaded {tf_key}: {len(df)} rows")
    return frames


def run_backtest_with_params(frames, params):
    """Monkey-patch params, run backtest, restore."""
    import strategy
    import agents.structure_agent as struct_mod
    from config import get_settings

    cfg = get_settings()

    # Save originals
    orig = {
        "ml": cfg.ml_threshold,
        "sl": cfg.atr_sl_mult,
        "t1": cfg.atr_t1_mult,
        "t2": cfg.atr_t2_mult,
        "min_score": cfg.min_orchestrator_score,
        "w_dist": getattr(cfg, 'htf_w_conflict_dist', 2.0),
        "d1_dist": getattr(cfg, 'htf_d1_conflict_dist', 1.5),
        "model_cache": strategy._model_cache,
    }

    try:
        # Apply params
        cfg.ml_threshold = params["ml_threshold"]
        cfg.atr_sl_mult = params["sl_target"][0]
        cfg.atr_t1_mult = params["sl_target"][1]
        cfg.atr_t2_mult = params["sl_target"][2]
        cfg.min_orchestrator_score = params["min_score"]
        cfg.htf_w_conflict_dist = params["htf_conflict"][0]
        cfg.htf_d1_conflict_dist = params["htf_conflict"][1]

        # Disable ML if threshold is 0
        if params["ml_threshold"] == 0.0:
            strategy._model_cache = "__DISABLED__"

        result = strategy.run_backtest(
            frames,
            initial_capital=500_000,
            lot_size=30,
            lot_multiplier=1,
            start_date=BT_FROM,
            end_date=BT_TO,
        )

        if "error" in result and not result.get("trades"):
            return None
        return result.get("stats", {})

    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        import traceback; traceback.print_exc()
        return None
    finally:
        cfg.ml_threshold = orig["ml"]
        cfg.atr_sl_mult = orig["sl"]
        cfg.atr_t1_mult = orig["t1"]
        cfg.atr_t2_mult = orig["t2"]
        cfg.min_orchestrator_score = orig["min_score"]
        cfg.htf_w_conflict_dist = orig["w_dist"]
        cfg.htf_d1_conflict_dist = orig["d1_dist"]
        strategy._model_cache = orig["model_cache"]


def main():
    total = 1
    for v in PARAM_GRID.values():
        total *= len(v)
    logger.info(f"Multi-Agent Optimizer: {total} combinations")

    logger.info("Loading CSV data...")
    frames = load_frames()
    if "5" not in frames:
        logger.error("No 5-min data!")
        sys.exit(1)

    results = []
    keys = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())

    t0 = time.time()
    for idx, combo in enumerate(itertools.product(*values)):
        params = dict(zip(keys, combo))
        label = (
            f"ML={params['ml_threshold']:.2f} | "
            f"HTF=W{params['htf_conflict'][0]}/D{params['htf_conflict'][1]} | "
            f"MinScr={params['min_score']:.2f} | "
            f"SL/T1/T2={params['sl_target'][0]}/{params['sl_target'][1]}/{params['sl_target'][2]}"
        )
        if idx % 10 == 0:
            elapsed = time.time() - t0
            rate = (idx / elapsed) if elapsed > 0 and idx > 0 else 0
            eta = ((total - idx) / rate / 60) if rate > 0 else 0
            logger.info(f"[{idx+1}/{total}] ETA: {eta:.1f}min | {label}")
        
        stats = run_backtest_with_params(frames, params)
        
        if stats and stats.get("total_trades", 0) > 0:
            row = {
                "ml_threshold": params["ml_threshold"],
                "htf_w_dist": params["htf_conflict"][0],
                "htf_d1_dist": params["htf_conflict"][1],
                "min_score": params["min_score"],
                "sl_mult": params["sl_target"][0],
                "t1_mult": params["sl_target"][1],
                "t2_mult": params["sl_target"][2],
                "total_trades": stats.get("total_trades", 0),
                "wins": stats.get("wins", 0),
                "losses": stats.get("losses", 0),
                "win_rate": stats.get("win_rate_pct", 0),
                "profit_factor": stats.get("profit_factor", 0),
                "total_pnl": stats.get("total_pnl", 0),
                "avg_win": stats.get("avg_win", 0),
                "avg_loss": stats.get("avg_loss", 0),
                "max_dd_pct": stats.get("max_drawdown_pct", 0),
                "expectancy": stats.get("expectancy", 0),
                "exit_dist": json.dumps(stats.get("exit_distribution", {})),
            }
            results.append(row)

    elapsed = time.time() - t0
    logger.info(f"Grid search done in {elapsed:.0f}s ({total} combos)")

    if not results:
        print("\nNo parameter combination produced trades!")
        sys.exit(1)

    df = pd.DataFrame(results)
    df_valid = df[df["total_trades"] >= 3].copy()
    if df_valid.empty:
        df_valid = df.copy()

    # Composite score
    for col in ["total_trades", "win_rate", "profit_factor", "total_pnl"]:
        mn, mx = df_valid[col].min(), df_valid[col].max()
        df_valid[f"{col}_n"] = ((df_valid[col] - mn) / (mx - mn)) if mx > mn else 0.5

    dd_min, dd_max = df_valid["max_dd_pct"].min(), df_valid["max_dd_pct"].max()
    df_valid["dd_n"] = (1 - (df_valid["max_dd_pct"] - dd_min) / (dd_max - dd_min)) if dd_max > dd_min else 0.5

    df_valid["composite"] = (
        0.20 * df_valid["total_trades_n"] +
        0.25 * df_valid["win_rate_n"] +
        0.25 * df_valid["profit_factor_n"] +
        0.20 * df_valid["total_pnl_n"] +
        0.10 * df_valid["dd_n"]
    )
    df_valid = df_valid.sort_values("composite", ascending=False)

    print("\n" + "=" * 100)
    print(f"TOP 15 RESULTS (out of {len(df)} with trades, {total} tested)")
    print("=" * 100)
    for rank, (_, row) in enumerate(df_valid.head(15).iterrows(), 1):
        print(
            f"\n#{rank} [Score={row['composite']:.3f}]"
            f"\n  ML={row['ml_threshold']:.2f} | HTF=W{row['htf_w_dist']}/D{row['htf_d1_dist']} | "
            f"MinScr={row['min_score']:.2f} | SL/T1/T2={row['sl_mult']}/{row['t1_mult']}/{row['t2_mult']}"
            f"\n  Trades={int(row['total_trades'])}, WR={row['win_rate']:.1f}%, "
            f"PF={row['profit_factor']:.2f}, PnL=Rs.{row['total_pnl']:,.0f}, "
            f"MaxDD={row['max_dd_pct']:.1f}%, Exp=Rs.{row['expectancy']:,.0f}"
            f"\n  Exits: {row['exit_dist']}"
        )

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total tested: {total}")
    print(f"With trades: {len(df)}")
    print(f"With >= 3 trades: {len(df[df['total_trades'] >= 3])}")
    print(f"Best PnL: Rs.{df['total_pnl'].max():,.0f}")
    print(f"Best WR: {df['win_rate'].max():.1f}%")
    print(f"Most trades: {int(df['total_trades'].max())}")
    print(f"Best PF: {df['profit_factor'].max():.2f}")

    out_path = os.path.join(os.path.dirname(__file__), "data", "optimization_results.csv")
    df_valid.to_csv(out_path, index=False)
    print(f"\nFull results: {out_path}")

    best = df_valid.iloc[0]
    print("\n" + "=" * 60)
    print("RECOMMENDED PARAMETERS")
    print("=" * 60)
    print(f"  ml_threshold:          {best['ml_threshold']:.2f}")
    print(f"  htf_w_conflict_dist:   {best['htf_w_dist']}")
    print(f"  htf_d1_conflict_dist:  {best['htf_d1_dist']}")
    print(f"  min_orchestrator_score:{best['min_score']:.2f}")
    print(f"  atr_sl_mult:           {best['sl_mult']}")
    print(f"  atr_t1_mult:           {best['t1_mult']}")
    print(f"  atr_t2_mult:           {best['t2_mult']}")


if __name__ == "__main__":
    main()
