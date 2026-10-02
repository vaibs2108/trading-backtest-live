"""
backtest_worker.py — runs Backtest-page backtests in a separate process.

The Backtest page and the live trading loop used to share one process: the same
strategy kernel objects (each holding a lock for a whole run, so a 5-minute backtest
of a live strategy stalled that strategy's live evaluation), the same CPU/GIL (live
API responses slowed ~3x during a backtest), and the same global settings. Running
the backtest in a child process gives it its own kernels, its own settings copy and
its own interpreter, so nothing it does can reach the live loop.

Data is still fetched in the main process (through broker.py's shared Dhan rate
limiter, since the API limits are per account); only the computation runs here.
"""
import asyncio
import logging
import multiprocessing
import os
import sys
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

logger = logging.getLogger(__name__)

_MAX_WORKERS = 2
_pool = None
_pool_lock = threading.Lock()

# ── Child-process side ─────────────────────────────────────────────────────────

_custom_signature = None


def _worker_init():
    # A spawned child on Windows has no console handles when the parent's output is
    # redirected; give it harmless ones so stray prints/warnings can't fail.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(os.path.join(log_dir, "backtest_worker.log"), encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [bt-worker %(process)d] %(name)s: %(message)s",
        handlers=[handler],
        force=True,
    )


def _custom_files_signature():
    custom_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategies", "custom")
    try:
        return tuple(sorted(
            (f, os.path.getmtime(os.path.join(custom_dir, f)))
            for f in os.listdir(custom_dir) if f.endswith(".py")
        ))
    except OSError:
        return None


def _run_in_child(settings, frames, initial_capital, lot_size, lot_multiplier, from_d, to_d):
    """Executed in the worker process. `settings` is the caller's settings snapshot with
    the backtest's strategy/instrument/hold mode already applied."""
    global _custom_signature
    import strategy_kernel
    import strategy_router
    from config import settings_override

    # Pick up custom strategies saved/edited in Research Studio since the last run
    # (the main process reloads them on save; this process has its own registry).
    sig = _custom_files_signature()
    if _custom_signature is None:
        strategy_kernel.init_kernels()  # first run: registers builtins + custom files
    elif sig != _custom_signature:
        strategy_kernel.reload_custom_kernels()
    _custom_signature = sig

    with settings_override(base=settings):
        return strategy_router.run_backtest(frames, initial_capital, lot_size, lot_multiplier, from_d, to_d)


# ── Main-process side ──────────────────────────────────────────────────────────

def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(
                max_workers=_MAX_WORKERS,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_worker_init,
            )
        return _pool


def _discard_pool(pool):
    global _pool
    with _pool_lock:
        if _pool is pool:
            _pool = None
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


async def run_backtest(settings, frames, initial_capital, lot_size, lot_multiplier, from_d, to_d) -> dict:
    """Run strategy_router.run_backtest in the worker process and return its result."""
    pool = _get_pool()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            pool, _run_in_child, settings, frames, initial_capital, lot_size, lot_multiplier, from_d, to_d
        )
    except BrokenProcessPool:
        # The worker died (crash / killed). Start a fresh one on the next request.
        logger.error("Backtest worker process died; it will be restarted on the next backtest")
        _discard_pool(pool)
        raise RuntimeError("Backtest worker process stopped unexpectedly -- please run the backtest again")


def shutdown():
    """Stop the worker processes (called on app shutdown)."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is None:
        return
    procs = list(getattr(pool, "_processes", {}).values())
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
