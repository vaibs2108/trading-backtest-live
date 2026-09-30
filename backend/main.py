"""
main.py — FastAPI application: REST API + WebSocket for live signals & chart data.
"""
import os
import sys
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import asyncio
import logging
import json
import pytz
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_IST = pytz.timezone("Asia/Kolkata")

import math
def _sanitise_floats(obj):
    """Replace NaN/Inf floats with None so json.dumps doesn't crash."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitise_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitise_floats(v) for v in obj]
    return obj

# ── Data health tracking ─────────────────────────────────────────────────────
_data_health_state = {
    "is_stale": False,
    "last_candle_time": None,
    "staleness_seconds": 0,
    "broker_failures": 0,
    "last_check_time": None,
    "stale_poll_count": 0,  # consecutive polls over threshold (hysteresis, avoids flapping alerts)
}
_STALE_CONFIRM_POLLS = 3  # require this many consecutive stale polls before alerting (mirrors broker_failures pattern)

# ── Application-level start/stop control ────────────────────────────────────
_app_running = True  # When False, polling loop and data fetching are paused

import pandas as pd

def _format_ist_timestamp(ts_val) -> str:
    """
    Normalize and convert any candle or system timestamp into an ISO 8601 string 
    localized to Asia/Kolkata with the +05:30 suffix.
    """
    if ts_val is None:
        dt = datetime.now(_IST)
    elif isinstance(ts_val, (int, float)):
        dt = datetime.fromtimestamp(ts_val, tz=_IST)
    elif isinstance(ts_val, datetime):
        if ts_val.tzinfo is None:
            dt = _IST.localize(ts_val)
        else:
            dt = ts_val.astimezone(_IST)
    elif isinstance(ts_val, pd.Timestamp):
        dt = ts_val.to_pydatetime()
        if dt.tzinfo is None:
            dt = _IST.localize(dt)
        else:
            dt = dt.astimezone(_IST)
    else:
        try:
            parsed = pd.to_datetime(ts_val)
            dt = parsed.to_pydatetime()
            if dt.tzinfo is None:
                dt = _IST.localize(dt)
            else:
                dt = dt.astimezone(_IST)
        except Exception:
            dt = datetime.now(_IST)
    return dt.isoformat()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import broker
import strategy
from live_feed import get_live_feed
import strategy_router
import signal_journal_manager
from config import get_settings, save_settings, INSTRUMENT_META, TIMEFRAME_LABELS, MARKET_HOURS
from trade_manager import get_trade_manager, ActivePosition
from capital_tracker import get_capital_tracker
import slippage_tracker
import performance_tracker
from watchdog import write_heartbeat, save_app_state

# ── Logging ───────────────────────────────────────────────────────────────────
_log_dir = Path(__file__).parent / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "app.log"

console_handler = logging.StreamHandler()
console_handler.encoding = "utf-8"

from logging.handlers import TimedRotatingFileHandler

class AutoFlushingFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

class AutoFlushingTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Rotates app.log at midnight, keeps 30 days of history, flushes every
    write so entries are durably on disk even if the process is killed or
    the terminal crashes."""
    def emit(self, record):
        super().emit(record)
        self.flush()

# app.log always holds "today" (or the current run); on rotation the previous
# day's log is renamed to app.log.YYYY-MM-DD and kept for 30 days so a crash
# always leaves something to look back at, even across restarts/days.
file_handler = AutoFlushingTimedRotatingFileHandler(
    str(_log_file), when="midnight", backupCount=30, encoding="utf-8", utc=False
)
file_handler.suffix = "%Y-%m-%d"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        console_handler,
        file_handler,
    ],
    force=True,
)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Dhan ML Trading Engine", version="1.0.0")

# Investment Analysis page (Company Analysis / Stock Scanner / IPO Review) —
# fully isolated: its own data clients (bse_client.py, tickertape_client.py),
# no imports from or edits to broker.py/cas_broker.py/config.py/the live
# trading loop. Guarded so a problem in this new, separate feature can never
# take down the trading engine's own startup — worst case this page is
# simply unavailable, nothing else here is affected. Rollback: delete this
# try block and the three investment_*.py files.
try:
    import investment_api
    app.include_router(investment_api.router)
    logger.info("Investment Analysis API mounted at /api/investment")
except Exception as _inv_api_err:
    logger.error(f"Investment Analysis API failed to mount (feature disabled, trading engine unaffected): {_inv_api_err}")

@app.on_event("shutdown")
def on_app_shutdown():
    logger.info("Shutdown event triggered — force exiting process cleanly.")
    import threading, os
    threading.Timer(0.15, lambda: os._exit(0)).start()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve React frontend
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

# ── WebSocket connection manager ──────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws) if hasattr(self.active, 'discard') else None
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        disconnected = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

ws_manager = ConnectionManager()

# ── In-memory state ───────────────────────────────────────────────────────────
_live_frames: dict = {}          # cached multi-TF data
_live_frames_instrument: str = ""   # which instrument _live_frames belongs to
_last_signal: dict = {}          # last computed signal
_all_strat_sigs_cache: dict = {}  # cached signals from ALL strategies (set by polling loop)
_signal_history: list = []       # all signals this session
_SIGNAL_HISTORY_PATH = Path(__file__).parent / "data" / "signal_history.json"

# Cached frames from the polling loop — avoids re-fetching for chart_signals
_cached_frames: dict = {}
_cached_frames_ts: float = 0.0  # time.time() of last cache update
_cached_frames_instrument: str = ""

# Cached chart_signals backtest result — avoids re-running backtest on every page load
_chart_signals_cache: dict = {}  # {strategy: {"signals": [...], "ts": float}}

# In-flight chart_signals computations, keyed the same as the cache above.
# The backtest here can take 30-95s (full-history recompute, worse for
# instruments with longer sessions like MCX CrudeOil), while the frontend's
# safety-net poll fires every 10s -- without this, each poll that lands
# before the previous computation finishes starts its OWN redundant 30-95s
# recompute on top of the ones already running. This only changes whether a
# duplicate concurrent request waits for the existing computation instead of
# starting a new one -- the computation itself, its inputs, and its result
# are completely unchanged, and no live-trading/signal-detection code path
# is touched.
_chart_signals_inflight: dict = {}  # {cache_key: asyncio.Task}

import math

def _clean_nan_values(obj):
    """Recursively replaces float('nan'), float('inf'), and -float('inf') with None so JSON serialization never fails."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: _clean_nan_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_nan_values(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(_clean_nan_values(v) for v in obj)
    elif hasattr(obj, 'item') and callable(getattr(obj, 'item')):
        try:
            return _clean_nan_values(obj.item())
        except Exception:
            return None
    return obj

def _load_signal_history():
    global _signal_history
    try:
        path = _SIGNAL_HISTORY_PATH
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                _signal_history = _clean_nan_values(raw) if isinstance(raw, list) else []
            logger.info(f"Loaded {len(_signal_history)} signals from history log.")
        else:
            _signal_history = []
    except Exception as e:
        logger.error(f"Failed to load signal history: {e}")
        _signal_history = []

def _save_signal_history():
    try:
        path = _SIGNAL_HISTORY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        clean_hist = _clean_nan_values(_signal_history)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(clean_hist, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save signal history: {e}")

def _add_signal_to_history(sig: dict, source: str = "strategy"):
    global _signal_history
    if not sig or sig.get("signal") in ("HOLD", None):
        return
    # Tag with instrument, strategy, and source so signals don't bleed
    cfg = get_settings()
    strat_id = sig.get("strategy", cfg.strategy)
    direction = sig.get("signal")
    # Ensure 'time' is always set — strategies should provide it from the candle
    # timestamp, but if empty fall back to wall-clock IST.
    if not sig.get("time"):
        sig["time"] = datetime.now(_IST).isoformat()
    entry = _clean_nan_values({**sig, "instrument": cfg.instrument, "strategy": strat_id, "source": source, "recorded_at": datetime.now(_IST).isoformat()})

    # DEDUP 1: exact same candle time + direction + instrument + strategy → skip
    exists = any(
        s.get("time") == sig.get("time") and s.get("signal") == direction
        and s.get("instrument") == cfg.instrument and s.get("strategy") == strat_id
        for s in _signal_history
    )
    if exists:
        return

    # DEDUP 2: same direction as the last signal for this strategy+instrument → skip
    # Only log when direction CHANGES (e.g. HOLD→SHORT, SHORT→LONG, SHORT→SHORT_EXIT)
    # This prevents 5 consecutive M:S markers when the signal persists across bars
    last_for_strat = None
    for s in reversed(_signal_history):
        if s.get("strategy") == strat_id and s.get("instrument") == cfg.instrument:
            last_for_strat = s
            break
    if last_for_strat and last_for_strat.get("signal") == direction:
        return  # same direction already logged — skip

    _signal_history.append(entry)
    _signal_history = _signal_history[-100:]  # keep last 100
    _save_signal_history()

_chart_data: dict = {}           # OHLCV for charting
_last_telegram_signal_key: tuple = ("", "")  # (direction, candle_ts) of last sent Telegram alert
_pending_telegram_signal: dict = {}  # Signal that needs confirmation on next poll cycle
_last_telegram_signal_alerts: dict = {}  # {strategy_id: (signal, candle_ts)} — dedup signal notifications
_active_trade_signal: dict = {}  # The signal that triggered the current open trade (persists until position is closed)
_recently_closed_symbols: dict = {}  # {symbol: datetime} — prevents Dhan sync from re-importing a position that was just closed locally
_last_loss_direction: str = "NONE"   # direction of last losing trade
_last_loss_time: Optional[datetime] = None  # timezone-aware datetime of last loss

# ── Signal deduplication & cooldown state (mirrors backtest behavior) ────────
# Fix A: Track last processed candle timestamp per strategy to avoid re-evaluating
#        the same 5m candle across multiple 60s polling cycles.
_last_processed_candle_ts: dict = {}  # {strat_id: candle_timestamp_str}

# Fix B: Cooldown after exit for ALL strategies (not just active auto-trade).
#        Mirrors backtest's block_long_until / block_short_until per strategy.
_virtual_cooldown: dict = {}  # {strat_id: {"direction": str, "until": datetime}}

# Fix D: Track last date for daily regime state reset
_last_regime_reset_date: Optional[object] = None  # date object

_activity_logs: list = []

def add_activity_log(msg: str):
    try:
        t_str = datetime.now(_IST).strftime("%H:%M:%S")
        log_entry = f"[{t_str}] {msg}"
        _activity_logs.append(log_entry)
        if len(_activity_logs) > 50:
            _activity_logs.pop(0)
    except Exception:
        pass

def record_cooldown_loss(direction: str):
    global _last_loss_direction, _last_loss_time
    _last_loss_direction = direction
    _last_loss_time = datetime.now(_IST)
    logger.info(f"Cooldown registered: {direction} blocked for {getattr(get_settings(), 'cooldown_bars', 0) * 5} mins")

def _safe_pnl_exit_price(pos, index_ltp: float) -> float:
    """
    Return the correct exit price for PnL calculation.
    For OPTIONS positions: fetch option premium, with sanity check.
    For INDEX positions: return index_ltp as-is.
    
    CRITICAL: entry_price for OPTIONS is the option premium (~500-2000).
    Using index LTP (~57000) as exit_price would produce absurd PnL.
    """
    if pos.trade_mode != "OPTIONS" or not pos.symbol:
        return index_ltp
    
    # Try to fetch option premium
    try:
        if broker.is_connected():
            fetched = broker.get_option_ltp(pos.symbol)
            if fetched > 0:
                return fetched
    except Exception as e:
        logger.warning(f"_safe_pnl_exit_price: could not fetch option LTP: {e}")
    
    # Fallback: if we can't get option premium, DON'T use index LTP.
    # Use entry_price as conservative estimate (PnL = 0) rather than
    # a wildly wrong number that could show crores of fake P&L.
    logger.warning(
        f"_safe_pnl_exit_price: using entry_price as fallback for {pos.symbol} "
        f"(entry={pos.entry_price}, index_ltp={index_ltp})"
    )
    return pos.entry_price

_exit_signal_logged_for_position: str = ""  # order_id for which we already logged an exit signal this position lifetime
_exit_telegram_sent_for_position: str = ""  # order_id for which we already sent an exit alert this position lifetime
_manual_exit_alert_last_sent: dict = {}  # order_id -> datetime of last "MANUAL EXIT NEEDED" reminder (DHAN_SYNC + auto_trade=OFF), capped to one reminder per hour


# ── Pydantic models ───────────────────────────────────────────────────────────
class ConnectRequest(BaseModel):
    client_code: str
    access_token: str

class SettingsUpdate(BaseModel):
    strategy: Optional[str] = None
    instrument: Optional[str] = None
    trade_mode: Optional[str] = None
    index_expiry: Optional[int] = None
    options_expiry: Optional[int] = None
    strike_type: Optional[str] = None
    strike_offset: Optional[int] = None
    lot_multiplier: Optional[int] = None
    max_daily_loss: Optional[float] = None
    max_daily_profit: Optional[float] = None
    auto_trade: Optional[bool] = None
    ml_threshold: Optional[float] = None
    atr_sl_mult: Optional[float] = None
    atr_t1_mult: Optional[float] = None
    atr_t2_mult: Optional[float] = None
    chart_timeframe: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    # Agent Engine settings
    agent_weight_macro: Optional[float] = None
    agent_weight_structure: Optional[float] = None
    agent_weight_momentum: Optional[float] = None
    agent_weight_trigger: Optional[float] = None
    agent_weight_volume: Optional[float] = None
    agent_weight_memory: Optional[float] = None
    min_orchestrator_score: Optional[float] = None
    chart_patterns_enabled: Optional[bool] = None
    pattern_memory_enabled: Optional[bool] = None
    # Auto-trade settings
    auto_square_off_minutes: Optional[int] = None
    auto_kill_switch: Optional[bool] = None
    auto_kill_switch_max_failures: Optional[int] = None
    starting_capital: Optional[float] = None
    data_stale_threshold_min: Optional[int] = None
    # Regime Strategy settings
    regime_trail_mult: Optional[float] = None
    regime_trail_activation: Optional[float] = None
    regime_be_trigger: Optional[float] = None
    regime_be_buffer: Optional[float] = None

class BacktestRequest(BaseModel):
    instrument: str = "BANKNIFTY"
    from_date: str = ""
    to_date: str = ""
    initial_capital: float = 500_000
    lot_multiplier: int = 1
    strategy: Optional[str] = None  # override active strategy for this backtest
    hold_mode: str = "INTRADAY"  # INTRADAY | CARRY_FORWARD — position EOD handling

class ManualTradeRequest(BaseModel):
    action: str   # LONG | SHORT | EXIT


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    if FRONTEND_DIST.exists():
        return FileResponse(str(FRONTEND_DIST / "index.html"))
    return {"status": "Dhan ML Trading Engine running", "docs": "/docs"}


@app.post("/api/connect")
async def connect_broker(req: ConnectRequest):
    cfg = get_settings()
    success, msg = broker.connect(req.client_code, req.access_token)
    # Credentials are NOT saved to settings.json — they come from .env only
    return {"success": success, "message": msg, "connected": broker.is_connected()}


def _get_active_entries(cfg, tm) -> dict:
    """Find active (OPEN) signal journal entries, overlaid with real live position if applicable."""
    active_entries = {}
    try:
        from signal_journal_manager import _load
        entries = _load()
        for e in entries:
            if e.get("status") == "OPEN" and e.get("instrument") == cfg.instrument:
                strat = e.get("strategy")
                if strat:
                    active_entries[strat] = {
                        "signal": e.get("direction"),
                        "entry": e.get("entry_price"),
                        "sl": e.get("sl"),
                        "target1": e.get("target1"),
                        "target2": e.get("target2"),
                        "time": e.get("entry_time"),
                        "strategy": strat,
                        "instrument": e.get("instrument"),
                        "reasons": e.get("reasons", []),
                        "regime": e.get("regime", ""),
                        "regime_confidence": e.get("regime_confidence", 0.0),
                        "playbook": e.get("playbook", ""),
                        "macro_bias": e.get("macro_bias", ""),
                        "atr_5m": e.get("atr_5m", 0.0),
                        "weighted_score": e.get("weighted_score", 0.0),
                        "ml_prob": e.get("ml_prob", 0.0),
                        "adx_1h": e.get("adx_1h", 0.0),
                        "rr_t1": e.get("rr_t1", 0.0),
                        "entry_quality": e.get("entry_quality", 0.0),
                    }
    except Exception as e_journal:
        logger.warning(f"Failed to load active entries from journal: {e_journal}")

    # Only overlay tm.position onto the active strategy's tile when it's
    # ACTUALLY that strategy's own position. Previously this ran unconditionally
    # on tm.position.instrument alone, so a broker-synced position (strategy=
    # "broker_sync", e.g. a carry position imported from Dhan that's unrelated
    # to any live strategy's own signal) got mislabeled under whichever
    # strategy happened to be active -- a real incident (2026-08-24): the
    # Time-Gated Alpha Combo tile showed a stuck DHAN_SYNC SHORT position as
    # if it were that strategy's own active signal, while the strategy's real
    # signal (LONG, correctly on the chart) was a completely different trade.
    if tm.position and tm.position.instrument == cfg.instrument and tm.position.strategy == cfg.strategy:
        active_entries[cfg.strategy] = {
            "signal": tm.position.direction,
            "entry": getattr(tm.position, "index_entry_price", 0.0) or tm.position.entry_price,
            "sl": tm.position.sl,
            "target1": tm.position.target1,
            "target2": tm.position.target2,
            "time": tm.position.entry_time,
            "strategy": cfg.strategy,
            "instrument": tm.position.instrument,
            "reasons": _active_trade_signal.get("reasons", []) if _active_trade_signal else ["Active live position"],
            "regime": _active_trade_signal.get("regime", "") if _active_trade_signal else "",
            "regime_confidence": _active_trade_signal.get("regime_confidence", 0.0) if _active_trade_signal else 0.0,
            "playbook": _active_trade_signal.get("playbook", "") if _active_trade_signal else "",
            "macro_bias": _active_trade_signal.get("macro_bias", "") if _active_trade_signal else "",
            "atr_5m": _active_trade_signal.get("atr_5m", 0.0) if _active_trade_signal else tm.position.entry_atr,
            "weighted_score": _active_trade_signal.get("weighted_score", 0.0) if _active_trade_signal else 0.0,
            "ml_prob": _active_trade_signal.get("ml_prob", 0.0) if _active_trade_signal else 0.0,
            "adx_1h": _active_trade_signal.get("adx_1h", 0.0) if _active_trade_signal else 0.0,
            "rr_t1": _active_trade_signal.get("rr_t1", 0.0) if _active_trade_signal else 0.0,
            "entry_quality": _active_trade_signal.get("entry_quality", 0.0) if _active_trade_signal else 0.0,
        }
    else:
        active_entries.pop(cfg.strategy, None)

    return active_entries


@app.get("/api/status")
async def get_status():
    cfg = get_settings()
    tm  = get_trade_manager()

    # Synchronize positions from Dhan first!
    if broker.is_connected():
        await asyncio.to_thread(_sync_dhan_positions, cfg, tm)

    bal = 0.0
    if broker.is_connected():
        try:
            bal = await asyncio.to_thread(broker.get_balance)
            if bal > 0 and cfg.auto_trade:
                ct = get_capital_tracker()
                ct.current_equity = bal
                if ct.current_equity > ct.peak_equity:
                    ct.peak_equity = ct.current_equity
                ct._check_breach()
                ct._save()
        except Exception as e:
            logger.warning(f"Failed to sync broker balance in status: {e}")
            
    lot = broker.get_lot_size(cfg.instrument)
    ltp = (await asyncio.to_thread(broker.get_ltp, cfg.instrument)) if broker.is_connected() else None

    # Compute Live P&L from TradeManager's tracked position.
    # "Position P&L" / "Today's P&L" are meant to reflect the real broker
    # account only (2026-08-24 fix) -- journal_stats.gross_pnl below already
    # excludes paper trades (journal_manager.py filters trade_id startswith
    # "PAPER_"), but this open-position component didn't, so a paper trade
    # could show as live P&L on the dashboard even with a real, unrelated
    # broker loss sitting unshown. Excluding PAPER_ positions here makes both
    # tiles real-broker-only, consistent with the journal-derived figure.
    live_pnl = 0.0
    if tm.position and tm.position.instrument == cfg.instrument and not tm.position.order_id.startswith("PAPER_"):
        pos = tm.position
        if pos.order_id.startswith("PAPER_"):
            # Paper mode: use index LTP for P&L
            if ltp:
                tm.update_pnl(ltp)
            live_pnl = round(pos.current_pnl, 2)
        elif pos.trade_mode == "OPTIONS" and broker.is_connected() and pos.symbol:
            # Live OPTIONS: compute from actual option premium change
            # Options are always BOUGHT (BUY PUT for SHORT, BUY CE for LONG)
            # so P&L = (current_option_ltp - entry_option_price) * qty
            try:
                opt_ltp = await asyncio.to_thread(broker.get_option_ltp, pos.symbol)
                if opt_ltp and opt_ltp > 0 and pos.entry_price > 0:
                    live_pnl = round((opt_ltp - pos.entry_price) * pos.qty, 2)
                    pos.current_pnl = live_pnl  # keep in sync
                else:
                    live_pnl = round(pos.current_pnl, 2)  # fallback to sync value
            except Exception as _opt_err:
                logger.debug(f"Option LTP fetch for P&L failed: {_opt_err}")
                live_pnl = round(pos.current_pnl, 2)
        else:
            # Live INDEX: use index LTP for P&L
            if ltp:
                tm.update_pnl(ltp)
            live_pnl = round(pos.current_pnl, 2)

    # Today's P&L: use journal-derived stats as authoritative source
    try:
        from journal_manager import get_today_journal_stats
        journal_stats = get_today_journal_stats(cfg.instrument)
    except Exception as e:
        logger.warning(f"Failed to get journal stats: {e}")
        journal_stats = {"total_trades": 0, "wins": 0, "losses": 0, "gross_pnl": 0.0, "trade_log": []}

    # Today's total P&L = journal closed trades P&L + current unrealized P&L
    today_pnl = round(journal_stats["gross_pnl"] + live_pnl, 2)

    # Build trade_state — journal is authoritative for real broker trades only
    trade_state = tm.get_state()
    jds = trade_state["day_stats"]
    # Use journal stats (already filtered to exclude paper trades)
    jds["total_trades"] = journal_stats["total_trades"]
    jds["wins"] = journal_stats["wins"]
    jds["losses"] = journal_stats["losses"]
    jds["gross_pnl"] = today_pnl
    if journal_stats["trade_log"]:
        jds["trade_log"] = journal_stats["trade_log"]

    return {
        "connected":    broker.is_connected(),
        "instrument":   cfg.instrument,
        "trade_mode":   cfg.trade_mode,
        "auto_trade":   cfg.auto_trade,
        "balance":      bal,
        "live_pnl":     live_pnl,
        "today_pnl":    today_pnl,
        "lot_size":     lot,
        "ltp":          ltp,
        "trade_state":  trade_state,
        "last_signal":  _last_signal,
        "active_trade_signal": _active_trade_signal,
        "active_entries": _get_active_entries(cfg, tm),
        "capital_state": get_capital_tracker().get_state(),
        "data_health":  _data_health_state,
        "app_running":  _app_running,
        "max_daily_loss": cfg.max_daily_loss,
        "max_daily_profit": cfg.max_daily_profit,
    }


@app.get("/api/sparkline")
async def get_sparkline():
    cfg = get_settings()
    instrument = cfg.instrument
    inst_map = {
        "NIFTY": {"id": 13, "seg": "IDX_I"},
        "BANKNIFTY": {"id": 25, "seg": "IDX_I"},
        "FINNIFTY": {"id": 27, "seg": "IDX_I"},
        "MIDCPNIFTY": {"id": 442, "seg": "IDX_I"},
        "SENSEX": {"id": 51, "seg": "IDX_I"}
    }
    
    meta = inst_map.get(instrument.upper(), {"id": 25, "seg": "IDX_I"})
    
    if not broker.is_connected():
        return {"instrument": instrument, "closes": [], "pct_change": 0.0}
        
    try:
        from datetime import datetime, timedelta
        import pytz
        _IST = pytz.timezone("Asia/Kolkata")
        now_ist = datetime.now(_IST)
        from_date = (now_ist - timedelta(days=30)).strftime("%Y-%m-%d")
        to_date = now_ist.strftime("%Y-%m-%d")
        
        res = await asyncio.to_thread(
            broker._dhan_client.historical_daily_data,
            security_id=meta["id"],
            exchange_segment=meta["seg"],
            instrument_type="INDEX",
            from_date=from_date,
            to_date=to_date,
            expiry_code=0
        )
        if isinstance(res, dict) and res.get("status") == "success" and "data" in res and isinstance(res["data"], dict) and "close" in res["data"]:
            closes = [float(val) for val in res["data"]["close"] if val is not None]
            closes = closes[-20:]
            
            pct_change = 0.0
            if len(closes) >= 2:
                last_c = closes[-1]
                first_c = closes[0]
                if first_c > 0:
                    pct_change = round(((last_c - first_c) / first_c) * 100, 2)
                    
            return {"instrument": instrument, "closes": closes, "pct_change": pct_change}
    except Exception as e:
        logger.error(f"Error fetching sparkline: {e}")
        
    return {"instrument": instrument, "closes": [], "pct_change": 0.0}


@app.get("/api/activity_logs")
async def get_activity_logs():
    return {"logs": _activity_logs}


@app.get("/api/settings")
async def get_settings_endpoint():
    cfg = get_settings()
    return cfg.model_dump(exclude={"dhan_access_token", "dhan_client_code"})


@app.post("/api/settings")
async def update_settings(req: SettingsUpdate):
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    cfg = save_settings(data)
    tm = get_trade_manager()
    tm.update_limits(cfg.max_daily_loss, cfg.max_daily_profit)
    # Sync capital tracker settings
    ct = get_capital_tracker()
    ct.update_config(cfg.starting_capital)
    
    log_msgs = []
    if "instrument" in data:
        log_msgs.append(f"Changed target instrument to {data['instrument']}")
    if "strategy" in data:
        strat_lbl = "Multi-Agent" if data["strategy"] == "multi_agent" else "Regime T/R"
        log_msgs.append(f"Changed strategy to {strat_lbl}")
    if "auto_trade" in data:
        status_lbl = "ENABLED" if data["auto_trade"] else "DISABLED"
        log_msgs.append(f"Auto-trading {status_lbl}")
    if "max_daily_loss" in data:
        log_msgs.append(f"Daily risk limit updated to ₹{float(data['max_daily_loss']):,.2f}")
    
    if log_msgs:
        for msg in log_msgs:
            add_activity_log(msg)
    else:
        add_activity_log(f"Settings updated: {list(data.keys())}")
        
    return {"success": True, "settings": cfg.model_dump(exclude={"dhan_access_token"})}


@app.get("/api/chart/{timeframe}")
async def get_chart_data(timeframe: str, instrument: Optional[str] = None):
    """Get OHLCV data for charting the specified instrument + timeframe."""
    if not broker.is_connected():
        raise HTTPException(status_code=400, detail="Not connected to broker")
    cfg = get_settings()
    inst = instrument or cfg.instrument
    days_map = {"1": 3, "5": 15, "15": 30, "25": 40, "60": 90, "DAY": 365}
    days = days_map.get(timeframe, 30)
    import pytz
    kolkata_tz = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(kolkata_tz)
    from_d = (now_ist - timedelta(days=days)).strftime("%Y-%m-%d")
    to_d   = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
    df = await asyncio.to_thread(broker.get_historical_data, inst, timeframe, from_d, to_d, use_index=True)
    if df is None or df.empty:
        raise HTTPException(status_code=500, detail="Failed to fetch chart data")

    # Compute indicators for overlay
    try:
        edf = await asyncio.to_thread(strategy_router.add_indicators, df.copy())
        df["supertrend"]     = edf["supertrend"]
        df["supertrend_dir"] = edf["supertrend_dir"]
        df["ema7"]           = edf["ema7"]
        df["ema21"]          = edf["ema21"]
    except Exception:
        pass

    records = []
    _IST_OFFSET = 19800  # 5h30m in seconds — TradingView displays UTC; add offset so x-axis reads IST
    for _, row in df.iterrows():
        ts_val = pd.Timestamp(row["timestamp"])
        if ts_val.tz is None:
            ts_val = ts_val.tz_localize("Asia/Kolkata")
        else:
            ts_val = ts_val.tz_convert("Asia/Kolkata")

        r = {
            "time":  int(ts_val.timestamp()) + _IST_OFFSET,
            "open":  round(float(row["open"]),2),
            "high":  round(float(row["high"]),2),
            "low":   round(float(row["low"]),2),
            "close": round(float(row["close"]),2),
            "volume": int(row.get("volume",0) or 0),
        }
        for col in ["supertrend","ema7","ema21","supertrend_dir"]:
            if col in row and not pd.isna(row[col]):
                r[col] = round(float(row[col]),2)
        records.append(r)

    return {"instrument": inst, "timeframe": timeframe,
            "label": TIMEFRAME_LABELS.get(timeframe, timeframe),
            "candles": records, "count": len(records)}


@app.post("/api/app/start")
async def app_start():
    """Resume the application — re-enable polling loop and data fetching."""
    global _app_running
    _app_running = True
    logger.info("Application STARTED by user")
    try:
        save_app_state("RUNNING", "Started by user")
    except Exception:
        pass
    return {"success": True, "app_running": True}


@app.post("/api/app/stop")
async def app_stop():
    """Pause the application — disable polling loop and data fetching. Does NOT close positions."""
    global _app_running
    _app_running = False
    logger.info("Application STOPPED by user")
    try:
        save_app_state("STOPPED", "Stopped by user")
    except Exception:
        pass
    return {"success": True, "app_running": False}


@app.get("/api/signal")
async def get_signal():
    """Compute and return the latest signal."""
    if not broker.is_connected():
        raise HTTPException(status_code=400, detail="Not connected to broker")
    cfg = get_settings()
    tm  = get_trade_manager()

    frames = await asyncio.to_thread(_fetch_all_frames, cfg.instrument)
    if not frames:
        raise HTTPException(status_code=500, detail="Failed to fetch market data")

    if tm.position and tm.position.instrument == cfg.instrument:
        strat_pos = tm.position.direction
    else:
        strat_pos = _get_strategy_position(cfg.strategy, cfg.instrument)

    sig = await asyncio.to_thread(strategy_router.get_current_signal, frames, position=strat_pos)
    sig["instrument"] = cfg.instrument
    global _last_signal, _live_frames, _live_frames_instrument
    _last_signal = sig
    _live_frames = frames
    _live_frames_instrument = cfg.instrument

    await ws_manager.broadcast({"type": "signal", "data": sig})
    return sig




@app.get("/api/signals_all")
async def get_signals_all():
    """Return cached signals from ALL strategies as computed by the polling loop.
    This is the SINGLE source of truth — the same evaluation that drives chart
    markers, signal journal, and Telegram alerts.  We never re-evaluate
    strategies here; doing so caused panels to diverge from the journal/chart."""
    cfg = get_settings()
    tm  = get_trade_manager()

    # Return the cache populated by _signal_polling_loop
    results = dict(_all_strat_sigs_cache)  # shallow copy

    # If the cache is empty (engine hasn't run yet), return minimal HOLD stubs
    if not results:
        for strat_id in strategy_router.STRATEGY_OPTIONS:
            results[strat_id] = {"signal": "HOLD", "strategy": strat_id, "instrument": cfg.instrument}

    active_entries = _get_active_entries(cfg, tm)
    return {"active_strategy": cfg.strategy, "signals": results, "active_entries": active_entries}

@app.post("/api/trade/manual")
async def manual_trade(req: ManualTradeRequest):
    """Manually trigger a trade (for testing/override)."""
    if not broker.is_connected():
        raise HTTPException(status_code=400, detail="Not connected to broker")
    cfg = get_settings()
    tm  = get_trade_manager()
    add_activity_log(f"Manual action triggered: {req.action}")

    if req.action == "EXIT":
        if tm.position is None:
            return {"success": False, "error": "No open position"}
        pos = tm.position
        
        # Always capture index LTP for journal/display
        index_exit_ltp = (await asyncio.to_thread(broker.get_ltp, pos.instrument or cfg.instrument)) or pos.index_entry_price or 0.0

        # Determine option premium LTP for PnL calc (only for imported Dhan sync OPTIONS positions)
        use_option_ltp = pos.trade_mode == "OPTIONS" and pos.order_id.startswith("DHAN_SYNC_")
        if use_option_ltp:
            opt_premium = 0.0
            try:
                opt_premium = await asyncio.to_thread(broker.get_option_ltp, pos.symbol)
            except Exception as e:
                logger.warning(f"Could not fetch option LTP for manual exit: {e}")
            ltp = opt_premium if opt_premium > 0 else pos.entry_price
        else:
            _opt_idx_ltp = await asyncio.to_thread(broker.get_ltp, cfg.instrument) if broker.is_connected() else None
            ltp = index_exit_ltp or _opt_idx_ltp or pos.entry_price

        if not pos.order_id.startswith("PAPER_"):
            if getattr(pos, "sl_order_id", None):
                try:
                    await asyncio.to_thread(broker.cancel_broker_sl, pos.sl_order_id)
                except Exception as sl_cancel_err:
                    logger.warning(f"Could not cancel broker SL during manual exit: {sl_cancel_err}")
                pos.sl_order_id = None
            result = await asyncio.to_thread(broker.place_exit_order, pos.symbol, pos.exchange, pos.direction, pos.qty)
        else:
            result = {"success": True, "order_id": pos.order_id}
            
        # Try to fetch actual realized P&L from Dhan after exit order is placed
        realized_pnl = None
        if not pos.order_id.startswith("PAPER_"):
            try:
                await asyncio.sleep(1.0) # sleep to allow fill
                df = await asyncio.to_thread(broker.get_positions)
                if df is not None and not df.empty:
                    matched_rows = df[df['tradingSymbol'] == pos.symbol].to_dict(orient="records")
                    if matched_rows:
                        matched_row = matched_rows[0]
                        realized_pnl = float(matched_row.get('realizedProfit', 0.0) or matched_row.get('realisedProfit', 0.0) or 0.0)
            except Exception as e:
                logger.warning(f"Failed to fetch realized profit for manual exit P&L: {e}")

        global _active_trade_signal
        rec = tm.close_position(ltp, "MANUAL_EXIT", pnl_override=realized_pnl)
        _active_trade_signal = {}
        
        # Log exit signal to history log
        try:
            exit_sig = {
                "signal": "LONG_EXIT" if pos.direction == "LONG" else "SHORT_EXIT",
                "time": datetime.now(pytz.timezone("Asia/Kolkata")).isoformat(),
                "entry": ltp,
                "close": ltp,
                "strategy": getattr(pos, "strategy", cfg.strategy),
                "reason": "MANUAL_EXIT",
                "reasons": ["Manual exit triggered by user"]
            }
            _add_signal_to_history(exit_sig)
            signal_journal_manager.close_entry(
                exit_sig, cfg.instrument, "MANUAL_EXIT", index_price=index_exit_ltp
            )
        except Exception as ex_err:
            logger.error(f"Failed to log manual exit signal: {ex_err}")

        # Update Trading Journal (use index LTP for display consistency)
        try:
            import pytz
            from journal_manager import close_journal_entry
            close_journal_entry(
                symbol=pos.symbol,
                exit_price=index_exit_ltp if index_exit_ltp > 0 else ltp,
                exit_time=datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S"),
                exit_reason="MANUAL_EXIT",
                pnl=rec.get("pnl", 0.0),
                order_id=pos.order_id
            )
        except Exception as e:
            logger.error(f"Failed to close journal entry: {e}")

        # Send Telegram exit alert (option premium ltp for PnL, index_exit_ltp for price display)
        try:
            send_telegram_exit_alert(pos, ltp, "MANUAL_EXIT", rec.get("pnl", 0.0), index_exit_price=index_exit_ltp or ltp)
        except Exception as e:
            logger.warning(f"Telegram exit alert failed: {e}")
            
        return {"success": True, "message": "Position closed manually", "order": result}

    # Entry
    ok = True
    reason = "OK"
    if cfg.auto_trade:
        ok, reason = tm.can_trade
        if ok:
            ct_ok, ct_reason = get_capital_tracker().can_trade()
            if not ct_ok:
                ok, reason = False, ct_reason
    if not ok:
        return {"success": False, "error": reason}

    if not _last_signal or _last_signal.get("signal") == "HOLD":
        return {"success": False, "error": "No active signal to trade"}

    sig = _last_signal
    result = await asyncio.to_thread(_execute_order, sig, cfg, req.action)
    return result


@app.get("/api/positions")
async def get_positions():
    if not broker.is_connected():
        return {"positions": [], "connected": False}
    try:
        df = await asyncio.to_thread(broker.get_positions)
        if df is None or df.empty:
            return {"positions": [], "pnl": 0}
        live_pnl = await asyncio.to_thread(broker.get_live_pnl)
        return {"positions": df.to_dict(orient="records"), "pnl": live_pnl}
    except Exception as e:
        return {"positions": [], "error": str(e)}


@app.post("/api/backtest")
async def run_backtest(req: BacktestRequest):
    """Run backtest on Dhan live data for specified instrument and date range."""
    if not broker.is_connected():
        raise HTTPException(status_code=400, detail="Not connected to broker")

    import pytz
    kolkata_tz = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(kolkata_tz)

    to_d = req.to_date or now_ist.strftime("%Y-%m-%d")
    from_d = req.from_date or (now_ist - timedelta(days=365)).strftime("%Y-%m-%d")

    # Add 100 calendar days of warm-up data before the requested start date
    from_dt = datetime.strptime(from_d, "%Y-%m-%d")
    warmup_from_d = (from_dt - timedelta(days=100)).strftime("%Y-%m-%d")

    logger.info(f"Backtest: {req.instrument} from {from_d} to {to_d} (with warm-up from {warmup_from_d})")
    frames = await asyncio.to_thread(
        _fetch_frames_range, req.instrument, warmup_from_d, to_d
    )
    if not frames:
        raise HTTPException(status_code=500, detail="Failed to fetch backtest data")
    # Use dynamic lot size from Dhan API / INSTRUMENT_META
    lot_size = broker.get_lot_size(req.instrument)
    # Temporarily override strategy and instrument for this backtest
    cfg = get_settings()
    bt_strategy = req.strategy or cfg.strategy
    original_strategy = cfg.strategy
    original_instrument = cfg.instrument
    original_hold_mode = cfg.position_hold_mode
    if bt_strategy != original_strategy:
        cfg.strategy = bt_strategy
    if req.instrument != original_instrument:
        cfg.instrument = req.instrument
    bt_hold_mode = req.hold_mode if req.hold_mode in ("INTRADAY", "CARRY_FORWARD") else "INTRADAY"
    cfg.position_hold_mode = bt_hold_mode

    try:
        result = await asyncio.to_thread(
            strategy_router.run_backtest,
            frames,
            req.initial_capital,
            lot_size,
            req.lot_multiplier,
            from_d,
            to_d,
        )
    except Exception as e:
        logger.error(f"Backtest execution error for {req.instrument} ({bt_strategy}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Backtest error: {str(e)}")
    finally:
        cfg.strategy = original_strategy
        cfg.instrument = original_instrument
        cfg.position_hold_mode = original_hold_mode

    if not isinstance(result, dict):
        result = {"error": "Invalid backtest result"}

    result["strategy_used"] = bt_strategy
    result["hold_mode_used"] = bt_hold_mode
    return _sanitise_floats(result)



@app.get("/api/strategies")
async def get_strategies():
    """Return list of available strategies."""
    from strategy_router import get_strategy_list
    from strategy_kernel import init_kernels
    init_kernels()
    cfg = get_settings()
    return {"strategies": get_strategy_list(), "active": cfg.strategy}


# ── Research Studio Endpoints ───────────────────────────────────────────────

from pydantic import BaseModel

class ConvertRequest(BaseModel):
    pinescript: str
    mode: str = "local"
    strategy_id: str = ""

class PromptRequest(BaseModel):
    prompt: str

class DryRunRequest(BaseModel):
    python_code: str
    instrument: str = "BANKNIFTY"

class SaveStrategyRequest(BaseModel):
    strategy_id: str
    python_code: str


@app.get("/api/research/templates")
async def get_research_templates():
    from strategy_sandbox import SAMPLE_ALL_BANK_ATM, SAMPLE_TREND_REVERSAL
    return {
        "templates": [
            {"id": "all_bank_atm", "name": "All Bank ATM Strategy (v5)", "pinescript": SAMPLE_ALL_BANK_ATM},
            {"id": "trend_reversal", "name": "Trend Reversal Strategy (v5)", "pinescript": SAMPLE_TREND_REVERSAL}
        ]
    }


@app.post("/api/research/convert")
async def convert_pinescript(req: ConvertRequest):
    from strategy_sandbox import PineScriptToPythonConverter, AIStrategyAssistant, StrategyValidator, PineSyntaxChecker

    # Fast syntax-only pre-check (pynescript) before spending an AI call or
    # running the local transpiler on input that's simply malformed. No-op if
    # pynescript isn't installed. Returning an "error" key (not raising) here
    # matches the existing frontend contract in ResearchStudio.jsx, which
    # already displays res.error when res.python_code is absent.
    syntax_check = PineSyntaxChecker.check(req.pinescript)
    if not syntax_check.get("ok", True):
        return {"error": syntax_check["message"], "syntax_check": syntax_check}

    comp_info = PineScriptToPythonConverter.check_complexity(req.pinescript)

    # 1. Prefer AI Assistant conversion if OPENAI_API_KEY is configured and mode is not local
    if req.mode == "ai" and not AIStrategyAssistant.is_configured():
        raise HTTPException(
            status_code=400,
            detail="OPENAI_API_KEY is not configured in .env file or environment. Please add OPENAI_API_KEY=sk-... to .env file."
        )

    if req.mode != "force_local" and req.mode != "local" and AIStrategyAssistant.is_configured():
        try:
            ai_res = await asyncio.to_thread(AIStrategyAssistant.generate_strategy, req.pinescript, True)
            if ai_res.get("success") and ai_res.get("python_code"):
                val = StrategyValidator.validate_code(ai_res["python_code"])
                if val.get("valid"):
                    return {
                        "python_code": ai_res["python_code"],
                        "validation": val,
                        "mode": "ai",
                        "warning": "",
                        "is_complex": comp_info["is_complex"]
                    }
                else:
                    logger.warning(f"AI PineScript code failed AST validation: {val.get('errors')}")
                    if req.mode == "ai":
                        err_str = "; ".join(val.get("errors", []))
                        raise HTTPException(status_code=400, detail=f"AI generated code failed AST validation: {err_str}")
            elif not ai_res.get("success"):
                if req.mode == "ai":
                    raise HTTPException(status_code=400, detail=ai_res.get("error", "AI conversion failed."))
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"AI PineScript conversion attempt failed: {e}")
            if req.mode == "ai":
                raise HTTPException(status_code=500, detail=f"AI Conversion failed: {str(e)}")
    
    # 2. Fallback to Local Rule-Based Converter
    py_code = PineScriptToPythonConverter.convert(req.pinescript, req.strategy_id)
    val = StrategyValidator.validate_code(py_code)
    return {
        "python_code": py_code,
        "validation": val,
        "mode": "local",
        "warning": comp_info["warning"],
        "is_complex": comp_info["is_complex"]
    }


@app.post("/api/research/generate-prompt")
async def generate_prompt(req: PromptRequest):
    from strategy_sandbox import AIStrategyAssistant, StrategyValidator
    if not AIStrategyAssistant.is_configured():
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY is not configured in .env")
    ai_res = await asyncio.to_thread(AIStrategyAssistant.generate_strategy, req.prompt, False)
    if not ai_res.get("success"):
        raise HTTPException(status_code=500, detail=ai_res.get("error", "AI generation failed"))
    val = StrategyValidator.validate_code(ai_res["python_code"])
    return {"python_code": ai_res["python_code"], "validation": val}


@app.post("/api/research/dry-run")
async def dry_run_strategy(req: DryRunRequest):
    from strategy_sandbox import SignalVerifier
    res = SignalVerifier.generate_report(req.python_code, instrument=req.instrument)
    if "error" in res and not res.get("verification_table"):
        raise HTTPException(status_code=400, detail=str(res["error"]))
    return res


@app.post("/api/research/save")
async def save_custom_strategy(req: SaveStrategyRequest):
    from strategy_sandbox import CustomStrategyManager
    from strategy_kernel import init_kernels
    res = CustomStrategyManager.save_strategy(req.strategy_id, req.python_code)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=", ".join(res.get("errors", ["Save failed"])))
    init_kernels()
    return res


@app.get("/api/research/list")
async def list_custom_strategies():
    from strategy_sandbox import CustomStrategyManager
    return {"strategies": CustomStrategyManager.list_custom_strategies()}


@app.get("/api/backtest/strategies")
async def get_backtest_strategies():
    from strategy_sandbox import CustomStrategyManager
    from strategy_kernel import init_kernels
    init_kernels()

    builtins = [
        {"id": "regime_trend_range", "label": "Regime Trend/Range Optimized"},
        {"id": "regime_reversal", "label": "Regime + Reversal Combined"},
        {"id": "regime_trend_v2", "label": "Regime Trend V2 — Selective (optimized)"},
        {"id": "regime_trend_v2b", "label": "Regime Trend V2-B — Balanced"},
        {"id": "multi_agent", "label": "Multi-Agent V3 Kernel"},
        {"id": "donchian_5m_swing", "label": "Donchian 5m Swing (overnight)"},
        {"id": "donchian_5m_intraday", "label": "Donchian 5m Intraday"}
    ]
    builtin_ids = {b["id"] for b in builtins}

    customs = []
    for s in CustomStrategyManager.list_custom_strategies():
        s_id = s.get("strategy_id")
        if s_id and s_id not in builtin_ids:
            customs.append({
                "id": s_id,
                "label": f"✨ Custom: {s.get('display_name') or s_id}"
            })

    return {"strategies": builtins + customs}


@app.get("/api/research/strategy/{strategy_id}")
async def get_custom_strategy(strategy_id: str):
    from strategy_sandbox import CustomStrategyManager
    res = CustomStrategyManager.get_strategy(strategy_id)
    if not res:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return res


@app.delete("/api/research/strategy/{strategy_id}")
async def delete_custom_strategy(strategy_id: str):
    from strategy_sandbox import CustomStrategyManager
    from strategy_kernel import init_kernels
    ok = CustomStrategyManager.delete_strategy(strategy_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Strategy file not found or could not be deleted")
    init_kernels()
    return {"success": True, "message": f"Strategy {strategy_id} deleted"}


@app.get("/api/signal_history")
async def get_signal_history(instrument: Optional[str] = None):
    cfg = get_settings()
    inst = instrument or cfg.instrument
    filtered = []
    for s in _signal_history:
        if s.get("instrument", "") != inst:
            continue
        is_strategy = (s.get("source", "strategy") == "strategy" and s.get("reason", "") not in ("DHAN_SYNC_EXIT", "DHAN_SYNC"))
        is_sync_exit = (
            (s.get("source") == "broker_sync" or s.get("reason") == "DHAN_SYNC_EXIT")
            and s.get("signal") in ("LONG_EXIT", "SHORT_EXIT")
        )
        if is_strategy or is_sync_exit:
            filtered.append(s)
    return _clean_nan_values({"signals": filtered[-80:]})



@app.delete("/api/signal_history")
async def clear_signal_history():
    global _signal_history, _exit_signal_logged_for_position, _exit_telegram_sent_for_position
    global _last_processed_candle_ts, _virtual_cooldown
    _signal_history = []
    _exit_signal_logged_for_position = ""
    _exit_telegram_sent_for_position = ""
    _last_processed_candle_ts = {}
    _virtual_cooldown = {}
    _save_signal_history()
    _chart_signals_cache.clear()  # Invalidate backtest cache too
    # Reset processors so they re-initialise from fresh backtest state
    from live_bar_processor import reset_all_processors
    reset_all_processors()
    logger.info("Signal history + processors cleared by user request")
    return {"success": True, "message": "Signal history and processors cleared"}


@app.get("/api/live-feed-status")
async def get_live_feed_status():
    """Return live market feed connection status and candle counts."""
    return get_live_feed().get_status()


def _build_chart_signals_from_result(result: dict, strat: str, inst: str, days: int) -> list:
    """Convert a raw backtest result's trade list into chart entry/exit markers,
    filtered to the last `days` days. Extracted verbatim from get_chart_signals
    so it can be shared between the cache-hit and in-flight-compute paths."""
    signals = []
    raw_trades = result.get("trades", [])
    cutoff_date = (datetime.now(_IST) - timedelta(days=days)).date()
    for t in raw_trades:
        entry_time = str(t.get("entry_time", ""))
        exit_time = str(t.get("exit_time", ""))
        direction = t.get("direction", "")
        try:
            if pd.to_datetime(entry_time).date() < cutoff_date:
                continue
        except Exception:
            continue
        signals.append({
            "signal": direction, "time": entry_time,
            "entry": t.get("entry_price", 0), "sl": t.get("sl", 0),
            "target1": t.get("target1", 0), "target2": t.get("target2", 0),
            "strategy": strat,
        })
        if exit_time:
            signals.append({
                "signal": f"{direction}_EXIT", "time": exit_time,
                "entry": t.get("exit_price", 0), "close": t.get("exit_price", 0),
                "exit_price": t.get("exit_price", 0), "reason": t.get("exit_reason", ""),
                "pnl": t.get("pnl", 0), "pnl_pts": t.get("pnl_pts", 0),
                "strategy": strat,
            })
    return signals


@app.get("/api/chart_signals")
async def get_chart_signals(strategy: Optional[str] = None, instrument: Optional[str] = None, days: int = 10):
    """Run backtest for the selected strategy and return entry/exit markers for chart overlay.

    This mirrors TradingView behaviour: applying a strategy shows signals
    over the entire available history, strictly matching the backtest trade log.
    """
    cfg = get_settings()
    strat = strategy or cfg.strategy
    inst = instrument or cfg.instrument

    # Check backtest result cache first (valid for 10s so chart stays in live sync with backtest)
    import time as _t
    _now = _t.time()
    cache_key = f"{strat}_{inst}"
    _bt_cache = _chart_signals_cache.get(cache_key)

    # When the market is closed, the underlying candles can't change until the next
    # session opens — reuse whatever we last computed instead of re-fetching historical
    # data and re-running a full multi-hundred-bar backtest on every dashboard poll.
    import pytz as _pytz
    _now_ist = datetime.now(_pytz.timezone("Asia/Kolkata"))
    _inst_exch = INSTRUMENT_META.get(inst, {}).get("exchange_index", "INDEX")
    if _inst_exch == "MCX":
        _mkt_open = (_now_ist.hour >= 9) and (_now_ist.hour < 23 or (_now_ist.hour == 23 and _now_ist.minute <= 30))
    else:
        _mkt_open = (_now_ist.hour > 9 or (_now_ist.hour == 9 and _now_ist.minute >= 15)) and \
                    (_now_ist.hour < 15 or (_now_ist.hour == 15 and _now_ist.minute <= 30))
    _mkt_open = _mkt_open and _now_ist.weekday() < 5
    _cache_ttl = 5 if _mkt_open else 1800  # post-close: reuse last result for 30 min instead of 5s

    signals = []
    if _bt_cache and (_now - _bt_cache["ts"]) < _cache_ttl:
        signals = list(_bt_cache["result"].get("signals", []))
    else:
        # Reuse an already-running computation for this exact strategy+instrument
        # instead of starting a duplicate. The backtest below can take 30-95s;
        # the frontend's safety-net poll fires every 10s, so without this a slow
        # instrument (e.g. MCX CrudeOil's longer session) could end up with
        # several redundant full recomputes stacked up concurrently. This only
        # dedupes concurrent requests for the identical (strat, inst) pair -- the
        # computation itself, its inputs, and its result are unchanged.
        _inflight = _chart_signals_inflight.get(cache_key)
        if _inflight is not None and not _inflight.done():
            try:
                signals = list(await _inflight)
            except Exception:
                signals = []
        else:
            async def _compute_chart_signals():
                # Use cached frames from the polling loop if available and fresh (< 3 min)
                _cache_age = _t.time() - _cached_frames_ts
                if _cached_frames and _cached_frames_instrument == inst and _cache_age < 180:
                    frames = _cached_frames
                else:
                    frames = await asyncio.to_thread(_fetch_all_frames, inst)

                _sigs = []
                if frames:
                    def _run_bt():
                        try:
                            import strategy_router
                            # Run for the REQUESTED strategy and instrument — override cfg
                            # for the duration of this call so router executes correctly.
                            _orig_strat = cfg.strategy
                            _orig_inst = cfg.instrument
                            if strat != _orig_strat:
                                cfg.strategy = strat
                            if inst != _orig_inst:
                                cfg.instrument = inst
                            try:
                                return strategy_router.run_backtest(frames)
                            finally:
                                cfg.strategy = _orig_strat
                                cfg.instrument = _orig_inst
                        except Exception as e:
                            logger.error(f"chart_signals backtest error ({strat} for {inst}): {e}")
                            return None

                    result = await asyncio.to_thread(_run_bt)
                    if result:
                        _sigs = _build_chart_signals_from_result(result, strat, inst, days)
                        _chart_signals_cache[cache_key] = {
                            "result": {"signals": _sigs, "strategy": strat, "instrument": inst},
                            "ts": _t.time(),
                        }
                return _sigs

            _task = asyncio.ensure_future(_compute_chart_signals())
            _chart_signals_inflight[cache_key] = _task
            try:
                signals = list(await _task)
            except Exception:
                signals = []
            finally:
                if _chart_signals_inflight.get(cache_key) is _task:
                    del _chart_signals_inflight[cache_key]

    # Sort all markers chronologically by timestamp
    def _norm_ts(ts_val) -> str:
        if not ts_val:
            return ""
        s = str(ts_val).replace("T", " ").split("+")[0].split(".")[0].strip()
        return s[:16] if len(s) >= 16 else s

    try:
        signals.sort(key=lambda s: _norm_ts(s.get("time", "")))
    except Exception:
        pass

    return _sanitise_floats({"signals": signals, "strategy": strat, "count": len(signals)})


@app.get("/api/signal_journal")
async def get_signal_journal_endpoint():
    """Fetch strategy signal journal entries (theoretical P&L, all instruments)."""
    entries = signal_journal_manager.get_journal(None)
    return {"entries": entries}


@app.get("/api/cas_alerts")
async def get_cas_alerts_endpoint(mode: Optional[str] = None):
    """CAS scanner fired alerts, today. mode: 'cas_window' | 'undercurrent' | omitted for both."""
    import cas_scanner
    state = cas_scanner.get_state()
    if mode == "cas_window":
        return {"alerts": state["alerts_a"]}
    if mode == "undercurrent":
        return {"alerts": state["alerts_b"]}
    return {"alerts_cas_window": state["alerts_a"], "alerts_undercurrent": state["alerts_b"]}


@app.get("/api/cas_at_risk")
async def get_cas_at_risk_endpoint():
    """CAS scanner Mode A: current at-risk shortlist (stock options only)."""
    import cas_scanner
    return {"at_risk": cas_scanner.get_state()["at_risk"]}


@app.get("/api/cas_undercurrent")
async def get_cas_undercurrent_endpoint():
    """CAS scanner Mode B: current undercurrent-flagged list (stocks + indices)."""
    import cas_scanner
    return {"undercurrent": cas_scanner.get_state()["undercurrent"]}


@app.get("/api/cas_heatmap")
async def get_cas_heatmap_endpoint():
    """CAS scanner Mode B: per-underlying heatmap over the full F&O universe,
    with streak continuity across sweeps -- see cas_scanner.build_heatmap()."""
    import cas_scanner
    return {"heatmap": cas_scanner.get_state()["heatmap"]}


@app.delete("/api/signal_journal")
async def clear_signal_journal_endpoint():
    signal_journal_manager.clear_journal()
    logger.info("Signal journal cleared by user request")
    return {"success": True}


def reconstruct_dhan_trades(trades: list) -> list:
    """
    Reconstruct open/closed trades from raw Dhan executions using a FIFO/matching algorithm.
    Correctly maps PUT buying as SHORT market exposure and CALL buying as LONG market exposure.
    """
    import re

    def get_time(t):
        for key in ["createTime", "exchangeTime", "updateTime"]:
            val = t.get(key)
            if val and val != "NA":
                return val
        return ""

    trades_sorted = sorted(trades, key=get_time)

    from collections import defaultdict
    by_symbol = defaultdict(list)
    for t in trades_sorted:
        sym = t.get("customSymbol") or t.get("tradingSymbol")
        if sym:
            by_symbol[sym].append(t)

    reconstructed = []

    def guess_instrument(symbol: str) -> str:
        s = symbol.upper()
        if "BANKNIFTY" in s:
            return "BANKNIFTY"
        if "FINNIFTY" in s:
            return "FINNIFTY"
        if "MIDCPNIFTY" in s:
            return "MIDCPNIFTY"
        if "CRUDEOIL" in s:
            return "CRUDEOIL"
        if "SENSEX" in s:
            return "SENSEX"
        if "NIFTY" in s:
            return "NIFTY"
        return "INDEX"

    def parse_contract(symbol: str, tx_type: str):
        sym_u = symbol.upper()
        is_put = "PUT" in sym_u or " PE" in sym_u
        is_call = "CALL" in sym_u or " CE" in sym_u

        opt_type = "PUT" if is_put else ("CALL" if is_call else None)
        m = re.search(r'\b(\d{4,6})\b', symbol)
        opt_strike = m.group(1) if m else None

        if is_put:
            direction = "SHORT" if tx_type == "BUY" else "LONG"
        elif is_call:
            direction = "LONG" if tx_type == "BUY" else "SHORT"
        else:
            direction = "LONG" if tx_type == "BUY" else "SHORT"

        return direction, opt_type, opt_strike

    for symbol, sym_trades in by_symbol.items():
        active = None

        for t_exec in sym_trades:
            qty = int(t_exec.get("tradedQuantity", 0) or t_exec.get("quantity", 0) or 0)
            if qty <= 0:
                continue
            price = float(t_exec.get("tradedPrice", 0.0) or t_exec.get("price", 0.0) or 0.0)
            tx_type = (t_exec.get("transactionType") or t_exec.get("type") or "").upper()
            if not tx_type:
                continue

            time_str = get_time(t_exec)
            parts = time_str.split("T") if "T" in time_str else time_str.split(" ")
            date_part = parts[0] if len(parts) > 0 else ""
            time_part = parts[1] if len(parts) > 1 else ""

            etid = t_exec.get("exchangeTradeId") or t_exec.get("tradeId")
            trade_id = etid if etid and etid != "0" else f"DHAN_{t_exec.get('orderId')}_{tx_type}"

            direction, opt_type, opt_strike = parse_contract(symbol, tx_type)

            if active is None:
                active = {
                    "trade_id": trade_id,
                    "status": "OPEN",
                    "instrument": guess_instrument(symbol),
                    "symbol": symbol,
                    "direction": direction,
                    "open_tx_type": tx_type,
                    "option_type": opt_type,
                    "option_strike": opt_strike,
                    "qty": qty,
                    "entry_date": date_part,
                    "entry_time": time_part,
                    "entry_price": price,
                    "exit_price": None,
                    "exit_time": None,
                    "exit_reason": None,
                    "pnl": None,
                    "reasons": []
                }
            else:
                # Active position exists. Check if this execution closes or adds to the position.
                if active["open_tx_type"] == tx_type:
                    # Same order type: scale up position
                    total_qty = active["qty"] + qty
                    active["entry_price"] = round(((active["entry_price"] * active["qty"]) + (price * qty)) / total_qty, 2)
                    active["qty"] = total_qty
                else:
                    # Opposite order type: close or reduce position
                    closed_qty = min(qty, active["qty"])
                    if active["open_tx_type"] == "BUY":
                        pnl = round((price - active["entry_price"]) * closed_qty, 2)
                    else:
                        pnl = round((active["entry_price"] - price) * closed_qty, 2)

                    if qty >= active["qty"]:
                        active["status"] = "CLOSED"
                        active["exit_price"] = price
                        active["exit_time"] = time_part
                        active["pnl"] = pnl
                        active["exit_reason"] = "DHAN_CLOSED"
                        reconstructed.append(active)

                        rem_qty = qty - closed_qty
                        if rem_qty > 0:
                            # Flipped position
                            active = {
                                "trade_id": trade_id,
                                "status": "OPEN",
                                "instrument": guess_instrument(symbol),
                                "symbol": symbol,
                                "direction": direction,
                                "open_tx_type": tx_type,
                                "option_type": opt_type,
                                "option_strike": opt_strike,
                                "qty": rem_qty,
                                "entry_date": date_part,
                                "entry_time": time_part,
                                "entry_price": price,
                                "exit_price": None,
                                "exit_time": None,
                                "exit_reason": None,
                                "pnl": None,
                                "reasons": []
                            }
                        else:
                            active = None
                    else:
                        closed_part = active.copy()
                        closed_part["qty"] = closed_qty
                        closed_part["status"] = "CLOSED"
                        closed_part["exit_price"] = price
                        closed_part["exit_time"] = time_part
                        closed_part["pnl"] = pnl
                        closed_part["exit_reason"] = "DHAN_PARTIAL_CLOSE"
                        reconstructed.append(closed_part)
                        active["qty"] -= closed_qty

        if active is not None:
            reconstructed.append(active)

    return reconstructed



def align_with_strategy_journal(reconstructed_trades: list) -> list:
    try:
        from journal_manager import load_journal
        strategy_journal = load_journal()
        if not strategy_journal:
            return reconstructed_trades
            
        for rt in reconstructed_trades:
            # Try to find a match in the strategy journal
            match = None
            rt_time_sec = None
            try:
                # Convert rt["entry_time"] to seconds for comparison if possible
                h, m, s = map(int, rt["entry_time"].split(":"))
                rt_time_sec = h * 3600 + m * 60 + s
            except:
                pass
                
            for sj in strategy_journal:
                # Check instrument match
                sj_inst = (sj.get("instrument") or "").upper()
                rt_sym = (rt.get("symbol") or "").upper()
                if sj_inst not in rt_sym and rt_sym not in sj_inst:
                    continue
                    
                # Check direction match
                if sj.get("direction") != rt.get("direction"):
                    continue
                    
                # Check date match
                if sj.get("entry_date") != rt.get("entry_date"):
                    continue
                    
                # Check time proximity (within 10 minutes)
                if rt_time_sec is not None:
                    try:
                        sj_time = sj.get("entry_time", "")
                        sh, sm, ss = map(int, sj_time.split(":"))
                        sj_time_sec = sh * 3600 + sm * 60 + ss
                        if abs(rt_time_sec - sj_time_sec) <= 600: # 10 mins
                            match = sj
                            break
                    except:
                        pass
                        
            if match:
                # Enrich with strategy details
                rt["ml_prob"] = match.get("ml_prob")
                rt["weighted_score"] = match.get("weighted_score")
                rt["macro_bias"] = match.get("macro_bias")
                rt["h1_trend"] = match.get("h1_trend")
                rt["reasons"] = match.get("reasons", [])
                
    except Exception as e:
        logger.warning(f"Error aligning reconstructed trades with strategy journal: {e}")
        
    return reconstructed_trades


@app.get("/api/journal")
async def get_journal(from_date: Optional[str] = None, to_date: Optional[str] = None):
    """Fetch actual broker-executed trades from Dhan API. Fallbacks to empty if disconnected."""
    try:
        if not broker.is_connected():
            return {
                "journal": [],
                "status": "disconnected",
                "error": "Connect to Dhan broker to view actual broker execution logs."
            }
            
        from datetime import timedelta
        
        # Calculate defaults: today + last 7 days
        now_ist = datetime.now(_IST)
        if not from_date:
            from_date = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")
        if not to_date:
            to_date = now_ist.strftime("%Y-%m-%d")
            
        # Fetch trades from history
        logger.info(f"get_journal: from_date={from_date}, to_date={to_date}")
        hist_trades = broker.get_trade_history(from_date, to_date) if hasattr(broker, "get_trade_history") else []
        logger.info(f"get_journal: hist_trades count = {len(hist_trades)}")
        
        # Fetch trades from today's book
        today_trades = broker.get_trade_book() if hasattr(broker, "get_trade_book") else []
        logger.info(f"get_journal: today_trades count = {len(today_trades)}")
        
        # Combine and deduplicate
        all_execs = {}
        for idx, t in enumerate(hist_trades + today_trades):
            etid = t.get("exchangeTradeId") or t.get("tradeId")
            tx_type = t.get("transactionType") or t.get("type") or ""
            px = t.get("tradedPrice") or t.get("price") or 0.0
            order_id = t.get("orderId") or ""
            if etid and etid != "0":
                trade_key = etid
            else:
                trade_key = f"{order_id}_{tx_type}_{px}_{idx}"
            all_execs[trade_key] = t
                
        # Reconstruct closed/open trades
        reconstructed = reconstruct_dhan_trades(list(all_execs.values()))
        logger.info(f"get_journal: reconstructed count = {len(reconstructed)}")
        
        # Enrich with strategy journal reasons
        enriched = align_with_strategy_journal(reconstructed)
        

        # Sort oldest first (chronological)
        def sort_key(t):
            d = t.get("entry_date", "")
            tm = t.get("entry_time", "")
            return f"{d} {tm}"

        enriched_sorted = sorted(enriched, key=sort_key)

        # Filter by requested date range
        filtered_journal = []
        for t in enriched_sorted:
            ed = t.get("entry_date") or ""
            if not ed and t.get("entry_time"):
                ed = str(t.get("entry_time")).split("T")[0].split(" ")[0]
            if from_date and ed and ed.strip() < from_date.strip():
                continue
            if to_date and ed and ed.strip() > to_date.strip():
                continue
            filtered_journal.append(t)

        return {
            "journal": filtered_journal,
            "status": "connected",
            "from_date": from_date,
            "to_date": to_date
        }
        
    except Exception as e:
        logger.error(f"Error loading broker journal: {e}")
        return {"journal": [], "error": str(e)}


@app.post("/api/telegram/test")
async def test_telegram_alert():
    """Trigger a test Telegram alert using 100% LIVE data — real strategy engine + real Tradehull option data."""
    try:
        cfg = get_settings()
        logger.info(f"Telegram test requested. Bot token len: {len(cfg.telegram_bot_token)}, Chat ID: {cfg.telegram_chat_id}")
        if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
            raise HTTPException(status_code=400, detail="Telegram bot credentials are not configured in settings/.env")

        if not broker.is_connected():
            raise HTTPException(status_code=400, detail="Connect to Dhan first to fetch live data")

        # ── 1. Run the REAL strategy engine to get live scores/reasons ────
        frames = await asyncio.to_thread(_fetch_all_frames, cfg.instrument)
        if not frames:
            raise HTTPException(status_code=500, detail="Failed to fetch market data for strategy engine")

        live_sig = await asyncio.to_thread(strategy_router.get_current_signal, frames, position="NONE")

        # Use whatever the strategy says (LONG, SHORT, or HOLD)
        signal_direction = live_sig.get("signal", "HOLD")
        # For the test alert, if signal is HOLD we still send a demo using LONG direction
        direction = signal_direction if signal_direction in ("LONG", "SHORT") else "LONG"

        # ── 2. Resolve real prices ────────────────────────────────────────
        meta = INSTRUMENT_META[cfg.instrument]
        lot_size = broker.get_lot_size(cfg.instrument)
        qty = lot_size * cfg.lot_multiplier

        # Fetch live index LTP
        index_ltp = await asyncio.to_thread(broker.get_ltp, cfg.instrument)
        if not index_ltp:
            fallback = {"BANKNIFTY": 57150.0, "NIFTY": 24500.0, "SENSEX": 80000.0}
            index_ltp = fallback.get(cfg.instrument, 57150.0)

        if cfg.trade_mode == "OPTIONS":
            # Resolve real option symbol via Tradehull API
            expiry = cfg.options_expiry
            opt_symbol, opt_strike = broker.get_option_symbol(
                cfg.instrument, direction, expiry,
                cfg.strike_type, cfg.strike_offset
            )
            if opt_symbol is None:
                raise HTTPException(status_code=500, detail="Could not resolve option symbol from Dhan")

            # Fetch live option premium (supplementary only — index prices remain the main prices)
            try:
                opt_premium = broker.get_option_ltp(opt_symbol)
            except Exception as e:
                logger.warning(f"Could not fetch option LTP for {opt_symbol}: {e}")
                opt_premium = 0.0

            # Always use index-level prices as the main prices
            entry_price = live_sig.get("entry", index_ltp)
            sl_price    = live_sig.get("sl", index_ltp * 0.997)
            t1_price    = live_sig.get("target1", index_ltp * 1.004)
            t2_price    = live_sig.get("target2", index_ltp * 1.007)

            symbol = opt_symbol
            opt_type_label = "CE" if direction == "LONG" else "PE"
        else:
            # INDEX mode — use strategy's real SL/target levels (already index-level)
            entry_price = live_sig.get("entry", index_ltp)
            sl_price    = live_sig.get("sl", index_ltp * 0.997)
            t1_price    = live_sig.get("target1", index_ltp * 1.004)
            t2_price    = live_sig.get("target2", index_ltp * 1.007)
            symbol = f"{cfg.instrument} INDEX"
            opt_strike = None
            opt_type_label = None
            opt_premium = 0.0

        # ── 3. Build signal dict from REAL strategy output ────────────────
        real_sig = {
            "entry": entry_price,
            "sl": sl_price,
            "target1": t1_price,
            "target2": t2_price,
            # Real scores from the strategy engine
            "weighted_score": live_sig.get("weighted_score", 0.0),
            "ml_prob": live_sig.get("ml_prob", 0.0),
            "macro_bias": live_sig.get("macro_bias", "NEUTRAL"),
            "h1_trend": live_sig.get("h1_trend", "NEUTRAL"),
            "macro_confidence": live_sig.get("macro_confidence"),
            "structure_confidence": live_sig.get("structure_confidence"),
            "momentum_confidence": live_sig.get("momentum_confidence"),
            "trigger_quality": live_sig.get("trigger_quality"),
            "volume_confirms": live_sig.get("volume_confirms"),
            "memory_win_rate": live_sig.get("memory_win_rate"),
            # Options supplementary info
            "opt_strike": opt_strike if cfg.trade_mode == "OPTIONS" else None,
            "opt_type": opt_type_label if cfg.trade_mode == "OPTIONS" else None,
            "opt_premium": opt_premium if cfg.trade_mode == "OPTIONS" and opt_premium > 0 else None,
            "opt_symbol": symbol if cfg.trade_mode == "OPTIONS" else None,
            # Real reasons from the agents
            "reasons": live_sig.get("reasons", []),
        }

        result = {
            "success": True,
            "direction": direction,
            "symbol": symbol,
            "qty": qty,
            "order_id": "TEST_LIVE_" + datetime.now(_IST).strftime("%H%M%S")
        }

        send_telegram_entry_alert(real_sig, result)

        signal_label = signal_direction if signal_direction != "HOLD" else "HOLD (sent as LONG demo)"
        return {
            "success": True,
            "message": f"Test alert sent -- {symbol} @ Rs.{entry_price:.2f} | Signal: {signal_label}",
            "details": {
                "symbol": symbol,
                "signal": signal_direction,
                "direction_used": direction,
                "entry": entry_price,
                "sl": sl_price,
                "t1": t1_price,
                "t2": t2_price,
                "index_ltp": index_ltp,
                "qty": qty,
                "weighted_score": live_sig.get("weighted_score"),
                "ml_prob": live_sig.get("ml_prob"),
                "macro_bias": live_sig.get("macro_bias"),
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send test Telegram alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cancel_all")
async def cancel_all():
    if not broker.is_connected():
        raise HTTPException(status_code=400, detail="Not connected")
    try:
        broker.get_tsl().cancel_all_orders()
        get_trade_manager().position = None
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}



# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    logger.info("WebSocket client connected")
    try:
        # Send current state immediately on connect
        await ws.send_json({"type": "init", "data": {
            "connected": broker.is_connected(),
            "signal": _last_signal,
            "state": get_trade_manager().get_state(),
        }})
        while True:
            # Keep alive — client can send pings
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=30)
                if data == "ping":
                    await ws.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                await ws.send_json({"type": "heartbeat", "ts": datetime.now(_IST).isoformat()})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
        logger.info("WebSocket client disconnected")


async def _heartbeat_loop():
    """Periodically update data/heartbeat.json so watchdog knows main.py is alive."""
    from watchdog import write_heartbeat
    while True:
        try:
            write_heartbeat()
        except Exception as e:
            logger.debug(f"Heartbeat write error: {e}")
        await asyncio.sleep(10)


async def _feed_reconcile_loop():
    """Periodically patches the live feed's candles against fresh REST data.

    seed_candles() only runs once, at startup/reconnect; every candle after
    that is built purely from local WebSocket ticks with no cross-check
    against Dhan's own settled OHLC (see live_feed.py's reconcile_recent
    docstring — confirmed live on 2026-09-29 to drift 100+ points on a sharp
    opening move, since it depends on exactly which ticks got captured
    first). This closes that gap for candles old enough to have settled;
    the in-progress candle is never touched here."""
    await asyncio.sleep(90)  # let the feed connect and take a few real ticks first
    while True:
        try:
            _feed = get_live_feed()
            if _feed.is_running and broker.is_connected():
                cfg = get_settings()
                _hist_5m = await asyncio.to_thread(
                    broker.get_historical_data, cfg.instrument, "5",
                    (datetime.now(_IST) - timedelta(days=1)).strftime("%Y-%m-%d"),
                    (datetime.now(_IST) + timedelta(days=1)).strftime("%Y-%m-%d"))
                _hist_1m = await asyncio.to_thread(
                    broker.get_historical_data, cfg.instrument, "1",
                    (datetime.now(_IST) - timedelta(days=1)).strftime("%Y-%m-%d"),
                    (datetime.now(_IST) + timedelta(days=1)).strftime("%Y-%m-%d"))
                n5, n1 = _feed.reconcile_recent(_hist_5m, _hist_1m)
                if n5 or n1:
                    add_activity_log(f"Live feed reconciled against REST: {n5}x5m, {n1}x1m candle(s) corrected.")
        except Exception as e:
            logger.debug(f"Feed reconcile loop error: {e}")
        await asyncio.sleep(300)  # every 5 minutes — matches the 5m bar cadence


# ── Background polling (auto-signal every 5 min bar) ─────────────────────────

@app.on_event("startup")
async def startup_event():
    Path("logs").mkdir(exist_ok=True)
    _load_signal_history()
    add_activity_log("Trading Engine Booted successfully.")
    
    # Auto-connect if credentials are provided in config/env
    cfg = get_settings()
    if cfg.dhan_client_code and cfg.dhan_access_token:
        logger.info("Auto-connecting to Dhan on startup...")
        success, msg = broker.connect(cfg.dhan_client_code, cfg.dhan_access_token)
        if success:
            logger.info("Dhan auto-connection successful")
            add_activity_log("Dhan auto-connection successful.")
        else:
            logger.warning(f"Dhan auto-connection failed: {msg}")
            add_activity_log(f"Dhan auto-connection failed: {msg}")

    # Start the Order Update WebSocket -- a fast-path wake-up for
    # verify_order_fill() (see order_update_feed.py). Purely a latency
    # optimization; fill confirmation still falls back to its normal
    # REST poll cadence if this feed isn't connected.
    if broker.is_connected():
        try:
            import order_update_feed
            order_update_feed.start(cfg.dhan_client_code, cfg.dhan_access_token)
        except Exception as _ouf_err:
            logger.warning(f"Could not start Order Update feed: {_ouf_err}")

    # Start the CAS spike scanner -- fully independent of broker.py's
    # connection/rate-limiter, uses its own dedicated credentials
    # (cfg.dhan_cas_*). No-ops on its own if those aren't configured, so
    # this is always safe to call regardless of main broker connection state.
    try:
        import cas_scanner
        _main_loop = asyncio.get_event_loop()

        def _cas_ws_broadcast(msg: dict):
            asyncio.run_coroutine_threadsafe(ws_manager.broadcast(msg), _main_loop)

        def _cas_telegram(message: str):
            _send_telegram_alert_wrapper(message, cfg.telegram_bot_token, cfg.telegram_chat_id)

        cas_scanner.set_broadcast_hooks(_cas_ws_broadcast, _cas_telegram)
        cas_scanner.start()
    except Exception as _cas_err:
        logger.warning(f"Could not start CAS scanner: {_cas_err}")

    # Start live market feed for near-instant signal processing
    if broker.is_connected():
        try:
            _feed = get_live_feed()
            _sub = broker.get_feed_subscription(cfg.instrument)
            if _sub is None:
                raise RuntimeError(f"cannot resolve feed subscription for {cfg.instrument}")
            _sec_id, _exch_seg = _sub
            _feed.configure(
                instrument=cfg.instrument,
                security_id=_sec_id,
                exchange_segment=_exch_seg,
                loop=asyncio.get_event_loop(),
            )
            # Seed with historical candles so indicators work from first tick
            try:
                import time as _seed_time
                _hist_5m = broker.get_historical_data(cfg.instrument, "5",
                    (datetime.now(_IST) - timedelta(days=30)).strftime("%Y-%m-%d"),
                    (datetime.now(_IST) + timedelta(days=1)).strftime("%Y-%m-%d"))
                _hist_1m = broker.get_historical_data(cfg.instrument, "1",
                    (datetime.now(_IST) - timedelta(days=5)).strftime("%Y-%m-%d"),
                    (datetime.now(_IST) + timedelta(days=1)).strftime("%Y-%m-%d"))
                _seed_time.sleep(1)
                _feed.seed_candles(_hist_5m, _hist_1m)
            except Exception as _seed_err:
                logger.warning(f"Could not seed live feed candles: {_seed_err}")
            _feed.start(cfg.dhan_client_code, cfg.dhan_access_token)
            add_activity_log(f"Live market feed started for {cfg.instrument}")
        except Exception as _feed_err:
            logger.warning(f"Could not start live feed: {_feed_err}")
            add_activity_log(f"Live feed start failed: {_feed_err}")

    # Sync position from broker on startup if auto_trade is enabled
    if cfg.auto_trade and broker.is_connected():
        logger.info("Auto-trade ON: syncing position from broker...")
        try:
            broker_pos = broker.sync_position_from_broker(cfg.instrument)
            if broker_pos and broker_pos.get("has_position"):
                tm = get_trade_manager()
                tm.sync_from_broker(broker_pos, cfg.instrument)
                logger.info(f"Restored position from broker: {broker_pos['direction']} {broker_pos['symbol']}")
                add_activity_log(f"Restored position from broker: {broker_pos['direction']} {broker_pos['symbol']}")
            else:
                logger.info("No open position found at broker on startup")
                add_activity_log("No open position found at broker on startup.")
        except Exception as e:
            logger.error(f"Broker position sync on startup failed: {e}")
            add_activity_log(f"Broker position sync on startup failed: {e}")

    # Sync capital tracker with settings on startup
    ct = get_capital_tracker()
    ct.update_config(cfg.starting_capital)

    asyncio.create_task(_signal_polling_loop())
    asyncio.create_task(_heartbeat_loop())
    asyncio.create_task(_feed_reconcile_loop())

    # Mark app as running for watchdog crash detection
    try:
        save_app_state("RUNNING", "Started normally")
        write_heartbeat()
    except Exception:
        pass
    add_activity_log("Engine Polling Loop active.")

    logger.info("Dhan ML Trading Engine started")


@app.on_event("shutdown")
async def shutdown_event():
    """Graceful shutdown: save state, log daily performance snapshot."""
    logger.info("Shutting down gracefully...")
    # Stop live feed
    try:
        get_live_feed().stop()
    except Exception:
        pass
    # Stop order update feed
    try:
        import order_update_feed
        order_update_feed.stop()
    except Exception:
        pass
    # Stop CAS scanner
    try:
        import cas_scanner
        cas_scanner.stop()
    except Exception:
        pass
    try:
        save_app_state("STOPPED", "Graceful shutdown")
    except Exception:
        pass
    try:
        cfg = get_settings()
        performance_tracker.log_daily_snapshot(cfg.starting_capital)
    except Exception as e:
        logger.warning(f"Failed to log daily performance snapshot: {e}")
    logger.info("Shutdown complete")


def _get_strategy_position(strat_id: str, instrument: str) -> str:
    """Check the signal journal for any active OPEN entries to find the strategy's virtual position."""
    try:
        from signal_journal_manager import _load
        entries = _load()
        for e in reversed(entries):
            if (e.get("strategy") == strat_id and 
                e.get("instrument") == instrument and 
                e.get("status") == "OPEN"):
                return e.get("direction", "NONE")
    except Exception as e:
        logger.warning(f"Error checking virtual position for {strat_id}: {e}")
    return "NONE"


def _check_virtual_exits(ltp: float, cfg, frames=None, latest_candle_ts: str = None):
    """
    Check open signal journal entries for all strategies.
    If the current index LTP hits the entry's SL or Target 2, close the entry.
    Also log the exit signal to history so the UI clears.
    Also trails the stop loss dynamically in real-time matching the strategy settings.
    """
    if frames is not None and not isinstance(frames, dict):
        latest_candle_ts = frames
        frames = None
        
    if not ltp or ltp <= 0:
        return
    try:
        from signal_journal_manager import _load, close_entry, _save
        import pandas as pd
        tm = get_trade_manager()
        entries = _load()
        journal_modified = False

        # Get ATR for trailing SL calculations
        atr_v = ltp * 0.002
        if frames and "5" in frames and not frames["5"].empty:
            try:
                last_row = frames["5"].iloc[-1]
                atr_v = float(last_row.get("atr", ltp * 0.002))
                if pd.isna(atr_v) or atr_v < 5:
                    atr_v = ltp * 0.002
            except Exception:
                pass

        for e in entries:
            if e.get("status") != "OPEN" or e.get("instrument") != cfg.instrument:
                continue
            
            strat_id = e.get("strategy")
            
            # If the active strategy is currently in a live/paper trade, let position monitor handle it
            if tm.position and tm.position.instrument == cfg.instrument and strat_id == cfg.strategy:
                continue

            # Processor-managed strategies: exits are handled by LiveBarProcessor
            # on bar boundaries (matching backtest exactly). Skip real-time exit checks.
            if strat_id in ("regime_trend_range", "multi_agent"):
                continue

                
            direction = e.get("direction")
            sl = float(e.get("sl", 0) or 0)
            t2 = float(e.get("target2", 0) or 0)
            entry_price = float(e.get("entry_price", 0) or 0)

            # ── Dynamic Trailing SL updates for virtual entries ─────────────
            if entry_price > 0:
                # Initialize tracking variables if they don't exist
                if "highest_since_entry" not in e:
                    e["highest_since_entry"] = entry_price
                if "lowest_since_entry" not in e:
                    e["lowest_since_entry"] = entry_price
                if "trail_step" not in e:
                    e["trail_step"] = 0

                if strat_id == "regime_trend_range":
                    # Regime strategy trailing logic
                    trail_mult = getattr(cfg, "regime_trail_mult", 1.5)
                    trail_activation = getattr(cfg, "regime_trail_activation", 0.3)
                    be_trigger = getattr(cfg, "regime_be_trigger", 0.4)
                    be_buffer = getattr(cfg, "regime_be_buffer", 0.3)
                    
                    if direction == "LONG":
                        if ltp > e["highest_since_entry"]:
                            e["highest_since_entry"] = ltp
                            journal_modified = True
                        profit = e["highest_since_entry"] - entry_price
                        if profit >= atr_v * trail_activation:
                            trail_sl = e["highest_since_entry"] - atr_v * trail_mult
                            if be_trigger > 0 and profit > atr_v * be_trigger:
                                trail_sl = max(trail_sl, entry_price + atr_v * be_buffer)
                            if trail_sl > sl:
                                sl = trail_sl
                                e["sl"] = round(sl, 2)
                                journal_modified = True
                    elif direction == "SHORT":
                        if ltp < e["lowest_since_entry"]:
                            e["lowest_since_entry"] = ltp
                            journal_modified = True
                        profit = entry_price - e["lowest_since_entry"]
                        if profit >= atr_v * trail_activation:
                            trail_sl = e["lowest_since_entry"] + atr_v * trail_mult
                            if be_trigger > 0 and profit > atr_v * be_trigger:
                                trail_sl = min(trail_sl, entry_price - atr_v * be_buffer)
                            if sl == 0 or trail_sl < sl:
                                sl = trail_sl
                                e["sl"] = round(sl, 2)
                                journal_modified = True
                                
                elif strat_id == "multi_agent":
                    # Multi-agent strategy trailing logic
                    be_trigger = getattr(cfg, 'trailing_be_trigger_atr', 2.0)
                    trail_start = getattr(cfg, 'trailing_start_atr', 2.5)
                    trail_offset = getattr(cfg, 'trailing_offset_atr', 0.5)
                    
                    if atr_v > 0 and be_trigger > 0:
                        if direction == "LONG":
                            if ltp > e["highest_since_entry"]:
                                e["highest_since_entry"] = ltp
                                journal_modified = True
                            profit = e["highest_since_entry"] - entry_price
                            profit_atr = profit / atr_v
                            
                            # BE step
                            if e["trail_step"] == 0 and profit_atr >= be_trigger:
                                sl = entry_price
                                e["sl"] = round(sl, 2)
                                e["trail_step"] = 1
                                journal_modified = True
                            # Trail activation step
                            if trail_start > 0 and e["trail_step"] >= 1 and profit_atr >= trail_start:
                                e["trail_step"] = 2
                                journal_modified = True
                            # Trailing continuous
                            if e["trail_step"] >= 2 and trail_offset > 0:
                                trail_sl = entry_price + profit - trail_offset * atr_v
                                if trail_sl > sl:
                                    sl = trail_sl
                                    e["sl"] = round(sl, 2)
                                    journal_modified = True
                                    
                        elif direction == "SHORT":
                            if ltp < e["lowest_since_entry"]:
                                e["lowest_since_entry"] = ltp
                                journal_modified = True
                            profit = entry_price - e["lowest_since_entry"]
                            profit_atr = profit / atr_v
                            
                            # BE step
                            if e["trail_step"] == 0 and profit_atr >= be_trigger:
                                sl = entry_price
                                e["sl"] = round(sl, 2)
                                e["trail_step"] = 1
                                journal_modified = True
                            # Trail activation step
                            if trail_start > 0 and e["trail_step"] >= 1 and profit_atr >= trail_start:
                                e["trail_step"] = 2
                                journal_modified = True
                            # Trailing continuous
                            if e["trail_step"] >= 2 and trail_offset > 0:
                                trail_sl = entry_price - profit + trail_offset * atr_v
                                if sl == 0 or trail_sl < sl:
                                    sl = trail_sl
                                    e["sl"] = round(sl, 2)
                                    journal_modified = True

            # ── Stop Loss hit detection ──
            # Use trail_step to distinguish trailing SL from initial SL
            # (matches backtest: SL_HIT, TRAIL_S1, TRAIL_S2)
            exit_triggered = False
            exit_reason = ""
            if sl > 0:
                if direction == "LONG" and ltp <= sl:
                    exit_triggered = True
                    _ts = e.get("trail_step", 0)
                    exit_reason = f"TRAIL_S{_ts}" if _ts > 0 else "SL_HIT"
                elif direction == "SHORT" and ltp >= sl:
                    exit_triggered = True
                    _ts = e.get("trail_step", 0)
                    exit_reason = f"TRAIL_S{_ts}" if _ts > 0 else "SL_HIT"
            
            # Target 2 hit detection
            if t2 > 0:
                if direction == "LONG" and ltp >= t2:
                    exit_triggered = True
                    exit_reason = "T2_HIT"
                elif direction == "SHORT" and ltp <= t2:
                    exit_triggered = True
                    exit_reason = "T2_HIT"
            
            if exit_triggered:
                # Construct exit signal to close entry and add to history
                exit_sig = {
                    "signal": "LONG_EXIT" if direction == "LONG" else "SHORT_EXIT",
                    "time": _format_ist_timestamp(latest_candle_ts),
                    "entry": ltp,
                    "close": ltp,
                    "strategy": strat_id,
                    "instrument": cfg.instrument,
                    "reason": exit_reason,
                    "reasons": [f"Virtual exit triggered: {exit_reason} (LTP: {ltp})"]
                }
                
                # Log to history -- unconditional, all strategies: the dashboard's
                # multi-strategy panel and journal/performance pages need every
                # strategy's virtual history regardless of which one is active.
                _add_signal_to_history(exit_sig)
                logger.info(f"[{strat_id}] Virtual Exit {exit_reason} logged to history")

                # Send Telegram alert for virtual exit -- ACTIVE STRATEGY ONLY
                # (2026-08-28, explicit user request: this loop runs for every
                # strategy's virtual journal entry, but was sending Telegram for
                # ALL of them regardless of which one is actually selected on the
                # Live Trading page -- inconsistent with the processor entry/exit
                # path (main.py ~line 2828/2941), which already gates Telegram on
                # `_strat_id == cfg.strategy`. History/journal logging above stays
                # unconditional; only the alert itself is restricted.
                if strat_id == cfg.strategy and cfg.telegram_bot_token and cfg.telegram_chat_id:
                    try:
                        _sig_emoji = "🚪"
                        _sig_msg = (
                            f"{_sig_emoji} {strat_id.upper()} — {exit_sig['signal']}\n"
                            f"{cfg.instrument} @ {ltp}\n"
                            f"Reason: {exit_reason}\n"
                        )
                        _send_telegram_alert_wrapper(_sig_msg, cfg.telegram_bot_token, cfg.telegram_chat_id)
                    except Exception as _tg_err:
                        logger.warning(f"Failed to send telegram alert for virtual exit: {_tg_err}")

                # Close the journal entry in the local list so the state update is written to disk correctly
                exit_price = ltp
                if direction == "LONG":
                    pnl_pts = round(exit_price - entry_price, 2)
                else:
                    pnl_pts = round(entry_price - exit_price, 2)

                lot_size = int(e.get("lot_size", 1))
                pnl_inr = round(pnl_pts * lot_size, 2)

                e.update({
                    "exit_time":   exit_sig.get("time", datetime.now(_IST).isoformat()),
                    "exit_price":  exit_price,
                    "exit_reason": exit_reason,
                    "pnl_pts":     pnl_pts,
                    "pnl_inr":     pnl_inr,
                    "status":      "WIN" if pnl_pts > 0 else "LOSS",
                })
                journal_modified = True

                # Fix B: Record cooldown for this strategy after a losing exit
                # Mirrors backtest's block_long_until / block_short_until logic
                global _virtual_cooldown
                cooldown_bars = getattr(cfg, 'cooldown_bars', 0)
                if pnl_pts <= 0 and cooldown_bars > 0:
                    import pytz
                    _cooldown_mins = cooldown_bars * 5  # each bar = 5 minutes
                    _virtual_cooldown[strat_id] = {
                        "direction": direction,
                        "until": datetime.now(pytz.timezone("Asia/Kolkata")) + timedelta(minutes=_cooldown_mins),
                    }
                    logger.info(f"[{strat_id}] Cooldown set: {direction} blocked for {_cooldown_mins} mins after loss")

                logger.info(
                    f"Signal journal (virtual): CLOSE {direction} @ {exit_price} -> "
                    f"{pnl_pts:+.0f} pts / Rs.{pnl_inr:+,.0f} [{cfg.instrument}]"
                )

        if journal_modified:
            _save(entries)

    except Exception as e:
        logger.error(f"Error checking virtual exits: {e}")


_feed_restart_last_attempt = 0.0

async def _ensure_live_feed(cfg, now_ist):
    """Live-feed watchdog: (re)start the tick feed if it is down, or rebind it
    when the configured instrument changed. Without this, a dead feed silently
    degrades the app to 15s polling + stale REST data (1-2 candle signal lag).
    Throttled to one attempt per 90s."""
    global _feed_restart_last_attempt
    import time as _t
    _feed = get_live_feed()
    wrong_inst = getattr(_feed, "_instrument", None) != cfg.instrument
    if _feed.is_running and not wrong_inst:
        return
    if _t.time() - _feed_restart_last_attempt < 90:
        return
    _feed_restart_last_attempt = _t.time()
    reason = "wrong instrument" if (_feed.is_running and wrong_inst) else "not running"
    logger.warning(f"Live feed {reason} — (re)starting for {cfg.instrument}")
    add_activity_log(f"Live feed restart ({reason}): {cfg.instrument}")
    _old_thread = getattr(_feed, "_thread", None)
    try:
        _feed.stop()
    except Exception:
        pass
    if _old_thread is not None and _old_thread.is_alive():
        await asyncio.to_thread(_old_thread.join, 6.0)
    try:
        _sub = await asyncio.to_thread(broker.get_feed_subscription, cfg.instrument)
        if _sub is None:
            logger.error(f"Feed watchdog: cannot resolve subscription for {cfg.instrument} — "
                         "staying on REST polling")
            return
        _sec_id, _exch_seg = _sub
        _feed.configure(instrument=cfg.instrument, security_id=_sec_id,
                        exchange_segment=_exch_seg, loop=asyncio.get_event_loop())
        _feed.last_price = 0.0
        _feed._rejected_ticks = 0
        # fresh builders so candles from the old instrument never mix in
        from live_feed import CandleBuilder
        _feed.candle_1m = CandleBuilder(1, max_candles=500)
        _feed.candle_5m = CandleBuilder(5, max_candles=2000)
        _hist_5m = await asyncio.to_thread(
            broker.get_historical_data, cfg.instrument, "5",
            (now_ist - timedelta(days=30)).strftime("%Y-%m-%d"),
            (now_ist + timedelta(days=1)).strftime("%Y-%m-%d"))
        _hist_1m = await asyncio.to_thread(
            broker.get_historical_data, cfg.instrument, "1",
            (now_ist - timedelta(days=5)).strftime("%Y-%m-%d"),
            (now_ist + timedelta(days=1)).strftime("%Y-%m-%d"))
        _feed.seed_candles(_hist_5m, _hist_1m)
        _feed.start(cfg.dhan_client_code, cfg.dhan_access_token)
        add_activity_log(f"Live market feed restarted for {cfg.instrument}")
    except Exception as _fs_err:
        logger.error(f"Live feed restart failed: {_fs_err}")


# ── Live-strategy exit-management config, keyed by strategy_id ────────────
# Generalizes what used to be hardcoded `if cfg.strategy == "regime_trend_range"`
# / `elif cfg.strategy == "multi_agent"` branches so any live strategy using
# the same "continuous ATR trail + breakeven lock" exit shape (which is what
# each of these 3 kernels' own already-validated internal exit logic uses)
# gets the broker-side trailing-SL safety net without a new copy-pasted
# elif block per strategy. Values match each kernel's own validated
# TRAIL_MULT / TRAIL_ACTIVATION / BE_TRIGGER / BE_BUFFER class attributes
# (backend/strategies/regime_trend_range_v1_research.py).
_TRAIL_PARAMS = {
    "custom_regime_v1_trend_range_final": {"trail_mult": 3.5, "trail_activation": 0.3, "be_trigger": 2.0, "be_buffer": 0.4},
    "custom_halftrend_hull_standalone":   {"trail_mult": 3.5, "trail_activation": 0.3, "be_trigger": 2.0, "be_buffer": 0.4},
    "custom_cusum15_nodonchian_cd8":      {"trail_mult": 3.5, "trail_activation": 0.3, "be_trigger": 2.0, "be_buffer": 0.4},
    # Alpha Combo inherits TRAIL_MULT=3.5/BE_TRIGGER=2.0 unchanged from the same
    # CUSUM15PlusRawHalfTrendKernel lineage as the CD8 row above (only
    # CUSUM_THRESHOLD_ATR differs, 1.25 vs 1.5) -- same trail/BE shape applies.
    "custom_alpha_combo_cusum125":        {"trail_mult": 3.5, "trail_activation": 0.3, "be_trigger": 2.0, "be_buffer": 0.4},
    # Time-Gated Alpha Combo is Alpha Combo plus an entry-time filter only --
    # trail/BE params are untouched, same values apply.
    "custom_time_gated_alpha_combo":      {"trail_mult": 3.5, "trail_activation": 0.3, "be_trigger": 2.0, "be_buffer": 0.4},
}

# Cooldown-after-loss bar counts, keyed by strategy_id. CUSUM's 8-bar
# cooldown is load-bearing for its validated drawdown control (it's what
# brought maxDD down from -11.44% to -7.92% in backtest) and must be
# respected here exactly, not silently default to 0 like an unrecognized
# strategy would.
_COOLDOWN_BARS = {
    "custom_regime_v1_trend_range_final": 4,
    "custom_halftrend_hull_standalone": 4,
    "custom_cusum15_nodonchian_cd8": 8,
    # Alpha Combo inherits COOLDOWN_BARS=8 unchanged from CUSUM15PlusRawHalfTrendKernel,
    # same as the CD8 row above.
    "custom_alpha_combo_cusum125": 8,
    # Time-Gated Alpha Combo inherits the same COOLDOWN_BARS=8 (only adds an
    # entry-time filter, doesn't touch cooldown).
    "custom_time_gated_alpha_combo": 8,
}


async def _apply_generic_trailing_sl(pos, cfg, ltp, sig):
    """Continuous ATR trail + breakeven lock, parameterized by
    _TRAIL_PARAMS[cfg.strategy]. Mirrors the shape of the (still-present,
    now-dormant) regime_trend_range branch below, generalized so any live
    strategy in _TRAIL_PARAMS gets the same broker-side trailing-SL safety
    net without its own hardcoded elif block. Returns True if it updated
    pos.sl (caller does nothing further; _sync_broker_sl is called here)."""
    params = _TRAIL_PARAMS.get(cfg.strategy)
    if not params:
        return False
    trail_mult = params["trail_mult"]
    trail_activation = params["trail_activation"]
    be_trigger = params["be_trigger"]
    be_buffer = params["be_buffer"]
    atr_v = pos.entry_atr or sig.get("atr_5m", 0) or (ltp * 0.002)
    updated = False
    if pos.direction == "LONG":
        if ltp > pos.highest_since_entry:
            pos.highest_since_entry = ltp
        profit = pos.highest_since_entry - pos.index_entry_price
        if profit >= atr_v * trail_activation:
            trail_sl = pos.highest_since_entry - atr_v * trail_mult
            if be_trigger > 0 and profit > atr_v * be_trigger:
                trail_sl = max(trail_sl, pos.index_entry_price + atr_v * be_buffer)
            # Clamp: the ATR-derived breakeven level can otherwise be pushed
            # above the highest LTP this LONG has actually traded at,
            # producing an "SL hit" price the market never reached.
            trail_sl = min(trail_sl, pos.highest_since_entry)
            if trail_sl > pos.sl:
                logger.info(f"Trailing SL updated: {pos.sl:.2f} -> {trail_sl:.2f} (highest={pos.highest_since_entry:.2f}, ATR={atr_v:.2f})")
                pos.sl = trail_sl
                updated = True
    elif pos.direction == "SHORT":
        if ltp < pos.lowest_since_entry:
            pos.lowest_since_entry = ltp
        profit = pos.index_entry_price - pos.lowest_since_entry
        if profit >= atr_v * trail_activation:
            trail_sl = pos.lowest_since_entry + atr_v * trail_mult
            if be_trigger > 0 and profit > atr_v * be_trigger:
                trail_sl = min(trail_sl, pos.index_entry_price - atr_v * be_buffer)
            # Clamp: mirrored fix — must never fall below the lowest LTP
            # this SHORT has actually traded at.
            trail_sl = max(trail_sl, pos.lowest_since_entry)
            if trail_sl < pos.sl:
                logger.info(f"Trailing SL updated: {pos.sl:.2f} -> {trail_sl:.2f} (lowest={pos.lowest_since_entry:.2f}, ATR={atr_v:.2f})")
                pos.sl = trail_sl
                updated = True
    if updated:
        await asyncio.to_thread(_sync_broker_sl, pos, cfg)
    return updated


async def _signal_polling_loop():
    """Poll for new signals — driven by live feed candle events (1-2s latency)
    with 15s fallback if live feed is not connected."""
    while True:
        # Wait for candle close event from live feed, or fall back to 15s polling
        _feed = get_live_feed()
        if _feed.is_running and _feed.candle_event:
            try:
                await asyncio.wait_for(_feed.candle_event.wait(), timeout=15)
                _feed.candle_event.clear()
                logger.debug("Signal loop triggered by live feed candle close")
            except asyncio.TimeoutError:
                pass  # No candle event — proceed with normal poll
        else:
            await asyncio.sleep(15)
        try:
            # Application-level pause: skip all work when stopped
            if not _app_running:
                continue
            # Write heartbeat for watchdog liveness monitoring
            try:
                write_heartbeat()
            except Exception:
                pass

            if not broker.is_connected():
                continue

            add_activity_log("Engine Heartbeat: Active & monitoring status.")
            import pytz
            now = datetime.now(pytz.timezone("Asia/Kolkata"))
            cfg = get_settings()
            tm  = get_trade_manager()

            # Skip weekends — both NSE and MCX are closed Sat/Sun
            if now.weekday() >= 5:
                continue

            # Instrument-aware market hours
            _inst_exchange = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
            if _inst_exchange == "MCX":
                # MCX CrudeOil: 9:00 AM – 11:30 PM IST (Mon–Fri)
                _is_open = (now.hour >= 9) and (now.hour < 23 or (now.hour == 23 and now.minute <= 30))
            else:
                # NSE / BSE: 9:15 AM – 3:30 PM IST (Mon–Fri)
                _is_open = (now.hour > 9 or (now.hour == 9 and now.minute >= 15)) and \
                           (now.hour < 15 or (now.hour == 15 and now.minute <= 30))
            if not _is_open:
                continue

            # Keep the tick feed alive and bound to the right instrument
            try:
                await _ensure_live_feed(cfg, now)
            except Exception as _fw_err:
                logger.debug(f"Live feed watchdog error: {_fw_err}")

            frames = await asyncio.to_thread(_fetch_all_frames, cfg.instrument)

            # Cache frames for chart_signals endpoint (avoids 12.5s re-fetch)
            global _cached_frames, _cached_frames_ts, _cached_frames_instrument
            if frames:
                import time as _time_mod
                _cached_frames = frames
                _cached_frames_ts = _time_mod.time()
                _cached_frames_instrument = cfg.instrument

            latest_ts = None
            if frames and "5" in frames and not frames["5"].empty:
                latest_bar = frames["5"].iloc[-1]
                latest_ts = latest_bar.get("timestamp")
                try:
                    _c_ts = pd.to_datetime(latest_bar["timestamp"])
                    if getattr(_c_ts, "tzinfo", None) is not None:
                        _c_ts = _c_ts.tz_convert("Asia/Kolkata").tz_localize(None)
                    _c_time = int(_c_ts.timestamp()) + 19800
                    await ws_manager.broadcast({
                        "type": "candle_update",
                        "instrument": cfg.instrument,
                        "data": {
                            "time": _c_time,
                            "open": float(latest_bar["open"]),
                            "high": float(latest_bar["high"]),
                            "low": float(latest_bar["low"]),
                            "close": float(latest_bar["close"]),
                            "volume": float(latest_bar.get("volume", 0))
                        }
                    })
                except Exception as _c_err:
                    logger.debug(f"Candle WS broadcast error: {_c_err}")

            # Synchronize positions from Dhan!
            await asyncio.to_thread(_sync_dhan_positions, cfg, tm, latest_ts)

            # ── Data staleness check ─────────────────────────────────────
            _check_data_staleness(frames, cfg)

            if frames:
                global _last_signal, _last_telegram_signal_key, _pending_telegram_signal
                global _active_trade_signal, _recently_closed_symbols, _exit_signal_logged_for_position, _exit_telegram_sent_for_position
                global _last_loss_direction, _last_loss_time
                global _manual_exit_alert_last_sent

                # Fetch current index LTP early for logging, consensus check and alerts
                ltp = (await asyncio.to_thread(broker.get_ltp, cfg.instrument)) or 0
                add_activity_log(f"Polling loop check: {cfg.instrument} LTP = {ltp:,.2f}")

                # Check for SL/Target hits on virtual journal entries
                _check_virtual_exits(ltp, cfg, frames, latest_ts)

                # Skip new signal generation if data is stale (but still monitor open positions)
                if _data_health_state["is_stale"] and not (tm.position and tm.position.instrument == cfg.instrument):
                    logger.warning("Data stale — skipping signal generation (no open position)")
                    continue

                pos_str = tm.position.direction if (tm.position and tm.position.instrument == cfg.instrument) else "NONE"

                def send_strat_telegram_alert(strat_id, sig_dict):
                    try:
                        _sig_dir = sig_dict.get("signal", "")
                        _sig_entry = sig_dict.get("entry", 0)
                        _sig_sl = sig_dict.get("sl", 0)
                        _sig_t1 = sig_dict.get("target1", 0)
                        _sig_reasons = sig_dict.get("reasons", [])[:3]
                        _sig_emoji = "\U0001f7e2" if "LONG" in _sig_dir else "\U0001f534" if "SHORT" in _sig_dir else "\u26aa"
                        _sig_msg = (
                            f"{_sig_emoji} {strat_id.upper()} \u2014 {_sig_dir}\n"
                            f"{cfg.instrument} @ {ltp}\n"
                        )
                        if _sig_entry and "EXIT" not in _sig_dir:
                            _sig_msg += f"Entry: {_sig_entry}  SL: {_sig_sl}  T1: {_sig_t1}\n"
                        if _sig_reasons:
                            _sig_msg += "\n".join(_sig_reasons[:3])
                        _send_telegram_alert_wrapper(_sig_msg, cfg.telegram_bot_token, cfg.telegram_chat_id)
                    except Exception as _tg_err:
                        logger.debug(f"Signal telegram alert failed for {strat_id}: {_tg_err}")

                # ── Fix D: Daily regime state reset ──────────────────────────
                # Reset regime strategy singletons AND live bar processors at
                # IST day boundary so stale state doesn't bleed into today.
                global _last_regime_reset_date
                _today_date = now.date()
                if _last_regime_reset_date != _today_date:
                    _last_regime_reset_date = _today_date
                    try:
                        from live_bar_processor import reset_all_processors
                        reset_all_processors()  # resets regime globals + processor state
                        logger.info("Daily processor + regime state reset (IST day boundary)")
                    except Exception as _reset_err:
                        logger.debug(f"Processor reset failed: {_reset_err}")
                    # Auto-close stale OPEN journal entries from previous days
                    try:
                        signal_journal_manager.auto_close_stale_entries()
                    except Exception as _stale_err:
                        logger.debug(f"Stale journal cleanup failed: {_stale_err}")
                    # Clear today's signal history — processors will regenerate
                    # from scratch during catch-up replay.  Keeps yesterday's
                    # signals intact for continuity.
                    try:
                        _today_str = _today_date.isoformat()
                        _before = len(_signal_history)
                        _signal_history[:] = [
                            s for s in _signal_history
                            if not s.get("time", "").startswith(_today_str)
                        ]
                        if len(_signal_history) != _before:
                            _save_signal_history()
                            logger.info(f"Cleared {_before - len(_signal_history)} stale today-signals from history")
                    except Exception as _clr_err:
                        logger.debug(f"Signal history cleanup failed: {_clr_err}")

                # ── Bar-by-bar processor: mirrors backtest exactly ─────────
                # Instead of evaluating only the latest bar (which misses brief
                # regime transitions), processors evaluate ALL new completed
                # bars since the last poll, using the EXACT same inner loop
                # as each strategy's run_backtest.
                global _last_processed_candle_ts, _virtual_cooldown
                global _all_strat_sigs_cache

                from live_bar_processor import (
                    get_regime_v1_final_processor, get_halftrend_hull_processor, get_cusum15_processor,
                    get_alpha_combo_processor, get_time_gated_alpha_combo_processor,
                )

                _lot_size = broker.get_lot_size(cfg.instrument)
                _qty = int(_lot_size * cfg.lot_multiplier)

                _all_strat_sigs = {}

                # Five PRODUCTION strategies run live: the original 3 research
                # strategies validated in scratch/research_v1/ (Regime T/R
                # V1 Final, HalfTrend+Hull Standalone, CUSUM 1.5/No-Donchian/
                # CD8), Alpha Combo (CUSUM 1.25 Tuned) -- the strategy that
                # beat CUSUM 1.5 and every dual-engine pyramid variant under
                # train/validate/full discipline, promoted 2026-08-22 and the
                # default (see settings.json / config.py Settings.strategy)
                # -- and Time-Gated Alpha Combo, promoted 2026-08-23 after the
                # same discipline plus a deep trade-level audit (BankNifty-only
                # validation, see STRATEGY_REGISTRY.md). regime_trend_range/
                # multi_agent remain registered for the Backtest page but are
                # no longer evaluated in the live loop; regime_reversal was
                # removed from the registry entirely on 2026-08-23 (worst
                # drawdown of any tested strategy, see STRATEGY_REGISTRY.md).
                # V2 / V2-B / Donchian also stay backtest-only, as before.
                _proc_list = [
                    ("custom_alpha_combo_cusum125", get_alpha_combo_processor()),
                    ("custom_time_gated_alpha_combo", get_time_gated_alpha_combo_processor()),
                    ("custom_regime_v1_trend_range_final", get_regime_v1_final_processor()),
                    ("custom_halftrend_hull_standalone", get_halftrend_hull_processor()),
                    ("custom_cusum15_nodonchian_cd8", get_cusum15_processor()),
                ]
                # Active strategy FIRST — its chart marker, telegram alert and
                # auto-trade execution must fire with minimum latency; the
                # other strategies' virtual tracking follows right after.
                _proc_list.sort(key=lambda _x: 0 if _x[0] == cfg.strategy else 1)

                # Evaluate all 3 strategies CONCURRENTLY in background threads
                # instead of sequentially blocking the event loop — each
                # process_frames() call re-runs a strategy's full run_backtest()
                # (~850-1040ms per strategy), so evaluating them one after
                # another cost ~2.8s per candle close and froze WebSocket
                # delivery/other requests for that whole window. Each processor
                # is an independent singleton with its own state and its own
                # kernel's own lock (safe_run_backtest), so concurrent
                # evaluation across strategies is safe. Side-effect processing
                # below (broadcast/telegram/order execution) stays sequential,
                # in the same active-strategy-first order as before — only the
                # heavy evaluation itself is parallelized.
                _eval_results = await asyncio.gather(
                    *[asyncio.to_thread(_processor.process_frames, frames, cfg, _qty)
                      for _strat_id, _processor in _proc_list],
                    return_exceptions=True,
                )

                for (_strat_id, _processor), new_signals in zip(_proc_list, _eval_results):
                    try:
                        if isinstance(new_signals, BaseException):
                            raise new_signals

                        for _live_sig in new_signals:
                            sig_dict = _live_sig.to_signal_dict()
                            sig_dict["instrument"] = cfg.instrument

                            if _live_sig.signal_type == "ENTRY":
                                _tid = _trace_id(_strat_id, _live_sig.signal, _live_sig.time)
                                _trace(_tid, "DETECTED", f"path=processor strategy={_strat_id} dir={_live_sig.signal} "
                                                          f"entry={_live_sig.entry_price} sl={_live_sig.sl} sig_time={_live_sig.time}")
                                _add_signal_to_history(sig_dict)
                                add_activity_log(f"[{_strat_id.upper()}] Processor Entry: {_live_sig.signal} @ {_live_sig.entry_price}")
                                logger.info(f"[{_strat_id}] Processor Entry: {_live_sig.signal} @ {_live_sig.entry_price} SL={_live_sig.sl}")
                                # Diagnostic (2026-08-22 audit): a phantom entry was found in Telegram
                                # history whose "Entry:" price never matched any real market data,
                                # while its exit's price exactly matched its OWN (already-wrong) SL --
                                # pointing at stale in-memory state rather than a data/timing issue.
                                # Root cause couldn't be pinned down after the fact with no logs from
                                # that moment. Log raw state on every entry so a recurrence is
                                # catchable with real evidence instead of reconstructed days later.
                                try:
                                    _last5 = frames.get("5")
                                    _last5_bars = (
                                        _last5.tail(3)[["timestamp", "open", "high", "low", "close"]].to_dict("records")
                                        if _last5 is not None and not _last5.empty else []
                                    )
                                    logger.info(
                                        f"[{_strat_id}] ENTRY diagnostic: sig_dict={sig_dict} "
                                        f"proc_state=(position={_processor.position}, entry_price={_processor.entry_price}, "
                                        f"sl={_processor.sl}, target1={_processor.target1}, target2={_processor.target2}, "
                                        f"prev_trade_keys_n={len(_processor._prev_trade_keys)}, prev_open_key={_processor._prev_open_key}) "
                                        f"last5_bars={_last5_bars}"
                                    )
                                except Exception as _diag_err:
                                    logger.debug(f"Entry diagnostic logging failed: {_diag_err}")
                                # Freshness check (2026-08-24 audit): a rolling-window backtest re-diff
                                # can "rediscover" a days-old trade as if it were new (data-gap catch-up
                                # burst). Telegram was already gated on this; the chart broadcast/cache
                                # invalidation was not, so stale replays were showing up as live markers
                                # on the chart even though nothing new had actually happened. Gate both
                                # on the same freshness check.
                                _entry_sig_age = _signal_age_minutes(_live_sig.time)
                                _entry_is_fresh = _entry_sig_age is None or _entry_sig_age <= 10.0
                                if _entry_is_fresh:
                                    # Invalidate chart cache so next fetch gets fresh backtest with this signal
                                    _chart_signals_cache.clear()
                                    # Notify frontend instantly with complete signal marker data via WebSocket
                                    await ws_manager.broadcast({"type": "signal_event", "data": sig_dict})
                                    await ws_manager.broadcast({"type": "chart_signals_updated", "data": {"strategy": _strat_id, "signal": _live_sig.signal}})
                                else:
                                    _trace(_tid, "CHART", f"skipped, age={_entry_sig_age:.1f}m (historical replay, not pushed to chart)")
                                # Send Telegram in background (non-blocking) for active strategy --
                                # skip stale/historical-replay signals so a data-gap catch-up burst
                                # doesn't alert as if each old signal just happened live. Also skip
                                # when the single position-tracking slot is already occupied (e.g. a
                                # manual/DHAN_SYNC position) -- this entry will never be attempted (see
                                # gate below), so alerting "Entry: LONG" here would announce a trade
                                # that never actually happened, not even a paper one (found live,
                                # 2026-09-30: slot occupied by a manual position all morning, Telegram
                                # kept firing anyway since it had no ownership/slot check, unlike the
                                # matching EXIT alert path which already suppresses on ownership).
                                _entry_slot_free = not tm.position or tm.position.instrument != cfg.instrument
                                if _strat_id == cfg.strategy:
                                    if _entry_is_fresh and _entry_slot_free:
                                        _trace(_tid, "TELEGRAM", "sending")
                                        asyncio.get_event_loop().run_in_executor(None, send_strat_telegram_alert, _strat_id, sig_dict)
                                    elif not _entry_is_fresh:
                                        _trace(_tid, "TELEGRAM", f"skipped, age={_entry_sig_age:.1f}m")
                                        logger.info(f"[{_strat_id}] Telegram entry alert skipped — signal {_entry_sig_age:.1f} mins old")
                                    else:
                                        _trace(_tid, "TELEGRAM", f"skipped, position slot occupied by {tm.position.symbol}")
                                        logger.info(f"[{_strat_id}] Telegram entry alert skipped — position slot occupied by {tm.position.symbol}, this entry will not be attempted")

                                # Instant order execution for active strategy (1-2s latency matching backtest)
                                if _strat_id == cfg.strategy and _entry_slot_free:
                                    ok, reason = True, "OK"
                                    if cfg.auto_trade:
                                        # Signal freshness check: skip historical replay signals (>10 mins old)
                                        _sig_age = _signal_age_minutes(_live_sig.time)
                                        if _sig_age is not None and _sig_age > 10.0:
                                            ok, reason = False, f"Historical replay signal skipped ({_sig_age:.1f} mins old)"

                                        if ok:
                                            ok, reason = tm.can_trade
                                        if ok:
                                            ct_ok, ct_reason = get_capital_tracker().can_trade()
                                            if not ct_ok:
                                                ok, reason = False, ct_reason
                                        if ok and broker.is_connected():
                                            try:
                                                bp = await asyncio.to_thread(broker.sync_position_from_broker, cfg.instrument)
                                                if bp and bp.get("has_position"):
                                                    ok, reason = False, f"Broker already has open position: {bp.get('symbol')}"
                                            except Exception:
                                                pass
                                    if ok:
                                        cooldown_bars = _COOLDOWN_BARS.get(cfg.strategy, 0)
                                        if cooldown_bars > 0 and _last_loss_direction == _live_sig.signal and _last_loss_time:
                                            elapsed_mins = (datetime.now(_IST) - _last_loss_time).total_seconds() / 60.0
                                            cooldown_mins = cooldown_bars * 5
                                            if elapsed_mins < cooldown_mins:
                                                ok, reason = False, f"Cooldown active for {cooldown_mins - elapsed_mins:.1f} mins"
                                    _trace(_tid, "ORDER_ATTEMPT", f"path=processor auto_trade={cfg.auto_trade} ok={ok} reason={reason}")
                                    if ok:
                                        result = await asyncio.to_thread(_execute_order, sig_dict, cfg, _live_sig.signal)
                                        _trace(_tid, "BROKER_RESULT", f"success={result.get('success')} "
                                                                       f"order_id={result.get('order_id','')} error={result.get('error','')}")
                                        if result.get("success"):
                                            tm.reset_order_failures()
                                            await ws_manager.broadcast({"type": "trade_opened", "data": result})
                                            logger.info(f"Instant trade executed: {_live_sig.signal} {cfg.instrument}")
                                        else:
                                            tm.record_order_failure()
                                            logger.error(f"Trade execution failed: {result.get('error')}")
                                    else:
                                        logger.info(f"Instant trade blocked: {reason}")

                                # Always open journal entry for virtual tracking
                                try:
                                    signal_journal_manager.open_entry(sig_dict, cfg.instrument, _qty)
                                except Exception as _sj_err:
                                    logger.error(f"Failed to open journal for {_strat_id}: {_sj_err}")

                            elif _live_sig.signal_type == "EXIT":
                                _tid = _trace_id(_strat_id, _live_sig.signal, _live_sig.time)
                                _trace(_tid, "DETECTED", f"path=processor strategy={_strat_id} dir={_live_sig.signal} "
                                                          f"exit={_live_sig.exit_price} reason={_live_sig.exit_reason} sig_time={_live_sig.time}")
                                _add_signal_to_history(sig_dict)
                                logger.info(f"[{_strat_id}] Processor Exit: {_live_sig.signal} ({_live_sig.exit_reason}) @ {_live_sig.exit_price}")
                                # Diagnostic (2026-08-22 audit) -- same reasoning as the entry diagnostic above.
                                try:
                                    logger.info(
                                        f"[{_strat_id}] EXIT diagnostic: sig_dict={sig_dict} "
                                        f"tm_position=(exists={tm.position is not None}, "
                                        f"strategy={getattr(tm.position, 'strategy', None)}, "
                                        f"order_id={getattr(tm.position, 'order_id', None)}, "
                                        f"sl={getattr(tm.position, 'sl', None)}, entry_price={getattr(tm.position, 'entry_price', None)})"
                                    )
                                except Exception as _diag_err:
                                    logger.debug(f"Exit diagnostic logging failed: {_diag_err}")
                                # Ownership check moved up here, BEFORE the chart broadcast/Telegram send (previously
                                # it only ran further below, right before the broker-close attempt).
                                # A strategy's own kernel detects exits purely from its own internal
                                # diff state -- completely decoupled from tm.position -- so if the
                                # currently-tracked real position belongs to a different strategy (or
                                # is a DHAN_SYNC broker-carried position), this strategy's "exit"
                                # doesn't correspond to anything real happening to that position. Left
                                # unchecked, that produced contradictory duplicate alerts: e.g. one
                                # exit reason from this processor's own logic, a different reason/price
                                # moments later from the position's rightful owner (or the generic
                                # position monitor) actually closing it.
                                _pos = tm.position if (tm.position and tm.position.instrument == cfg.instrument) else None
                                _owns_position = bool(
                                    _pos and not _pos.order_id.startswith("DHAN_SYNC_")
                                    and (not _pos.strategy or _pos.strategy == _strat_id)
                                )
                                _conflicting_real_position = bool(_pos and not _owns_position)
                                _exit_sig_age = _signal_age_minutes(_live_sig.time)
                                _exit_is_fresh = _exit_sig_age is None or _exit_sig_age <= 10.0

                                # Chart broadcast/cache: same rule as Telegram below (owns it -> always
                                # show; conflicts with a real position -> never show, it doesn't
                                # describe anything real; otherwise gated on freshness so a rolling-
                                # window catch-up burst doesn't paint a days-old exit as live).
                                if _owns_position or (not _conflicting_real_position and _exit_is_fresh):
                                    _chart_signals_cache.clear()
                                    await ws_manager.broadcast({"type": "signal_event", "data": sig_dict})
                                    await ws_manager.broadcast({"type": "chart_signals_updated", "data": {"strategy": _strat_id, "signal": _live_sig.signal}})
                                else:
                                    _trace(_tid, "CHART", f"skipped, age={_exit_sig_age}m conflicting={_conflicting_real_position} (not pushed to chart)")

                                # Send Telegram in background (non-blocking) for active strategy.
                                # - Owns the position: always alert, regardless of detection delay --
                                #   never suppress information about capital that's actually moving.
                                # - No position at all: virtual/journal-only signal, freshness-gated
                                #   (a stale catch-up burst shouldn't alert as if live).
                                # - A REAL position exists but belongs to someone else: suppress
                                #   entirely -- this signal doesn't describe anything real, at any age.
                                if _strat_id == cfg.strategy and _conflicting_real_position:
                                    _trace(_tid, "TELEGRAM", f"suppressed, position belongs to '{_pos.strategy or 'broker_sync'}'")
                                    logger.info(
                                        f"[{_strat_id}] Telegram exit alert suppressed — current position "
                                        f"{_pos.symbol} belongs to '{_pos.strategy or 'broker_sync'}', not this strategy"
                                    )
                                elif _strat_id == cfg.strategy:
                                    if _owns_position or _exit_is_fresh:
                                        _trace(_tid, "TELEGRAM", "sending")
                                        asyncio.get_event_loop().run_in_executor(None, send_strat_telegram_alert, _strat_id, sig_dict)
                                    else:
                                        _trace(_tid, "TELEGRAM", f"skipped, age={_exit_sig_age:.1f}m")
                                        logger.info(f"[{_strat_id}] Telegram exit alert skipped (no real position) — signal {_exit_sig_age:.1f} mins old")

                                # Execute broker exit for active strategy trade if open
                                if _strat_id == cfg.strategy and _pos is not None:
                                    pos = _pos
                                    # Ownership guard: only close a position this strategy actually
                                    # opened. Without this, a broker-synced carry position (or a
                                    # position opened by a different strategy) sitting in the same
                                    # single tracking slot could get closed by a signal that was never
                                    # meant for it — this happened for real on 2026-07-27, where a
                                    # regime_trend_range exit closed an unrelated Dhan carry position.
                                    if not _owns_position:
                                        _trace(_tid, "ORDER_ATTEMPT", "skipped, position not owned by this strategy")
                                        continue  # already logged above
                                    _trace(_tid, "ORDER_ATTEMPT", f"path=processor auto_trade={cfg.auto_trade} closing {pos.symbol}")
                                    logger.info(f"Processor EXIT ({_live_sig.signal}) for active strategy — closing position {pos.symbol}")
                                    if cfg.auto_trade and not pos.order_id.startswith("PAPER_"):
                                        if getattr(pos, "sl_order_id", None):
                                            try:
                                                await asyncio.to_thread(broker.cancel_broker_sl, pos.sl_order_id)
                                            except Exception as _sl_c_err:
                                                logger.warning(f"Could not cancel broker SL on processor exit: {_sl_c_err}")
                                            pos.sl_order_id = None
                                        _proc_exit = await asyncio.to_thread(broker.place_exit_order, pos.symbol, pos.exchange, pos.direction, pos.qty)
                                        _trace(_tid, "BROKER_RESULT", f"success={_proc_exit.get('success')} error={_proc_exit.get('error','')}")
                                        if not _proc_exit.get("success"):
                                            logger.error(f"Processor EXIT FAILED for {pos.symbol}: {_proc_exit.get('error')}")
                                            try:
                                                _send_telegram_alert_wrapper(
                                                    f"EXIT ORDER FAILED (processor)\n"
                                                    f"{pos.direction} {pos.symbol}\n"
                                                    f"Reason: {_proc_exit.get('error')}\n"
                                                    f"Position may still be open — check Dhan!",
                                                    cfg.telegram_bot_token, cfg.telegram_chat_id
                                                )
                                            except Exception:
                                                pass
                                            continue  # skip local position close — broker exit failed
                                        # Exit order succeeded — close position locally
                                        logger.info(f"Processor EXIT order placed successfully for {pos.symbol}")
                                        ltp_idx = (await asyncio.to_thread(broker.get_ltp, pos.instrument)) or _live_sig.exit_price
                                        pnl_exit_price = _safe_pnl_exit_price(pos, ltp_idx)
                                        _recently_closed_symbols[pos.symbol] = datetime.now(_IST)
                                        rec = tm.close_position(pnl_exit_price, _live_sig.exit_reason)
                                        if cfg.auto_trade:
                                            get_capital_tracker().record_trade_pnl(rec.get("pnl", 0.0))
                                        await ws_manager.broadcast({"type": "trade_closed", "data": rec})
                                    else:
                                        _trace(_tid, "BROKER_RESULT", "paper close, no broker call")
                                        ltp_idx = (await asyncio.to_thread(broker.get_ltp, pos.instrument)) or _live_sig.exit_price
                                        pnl_exit_price = _safe_pnl_exit_price(pos, ltp_idx)
                                        _recently_closed_symbols[pos.symbol] = datetime.now(_IST)
                                        rec = tm.close_position(pnl_exit_price, _live_sig.exit_reason)
                                        await ws_manager.broadcast({"type": "trade_closed", "data": rec})

                                # Always close journal entry for virtual tracking
                                try:
                                    signal_journal_manager.close_entry(
                                        sig_dict, cfg.instrument,
                                        _live_sig.exit_reason,
                                        index_price=_live_sig.exit_price
                                    )
                                except Exception as _sj_err:
                                    logger.error(f"Failed to close journal for {_strat_id}: {_sj_err}")

                        # Cache UI signal from processor state
                        _ui_sig = _processor.get_ui_signal(cfg)
                        _ui_sig["instrument"] = cfg.instrument
                        _all_strat_sigs[_strat_id] = _ui_sig

                    except Exception as _s_err:
                        logger.error(f"Processor {_strat_id} error: {_s_err}", exc_info=True)
                        _all_strat_sigs[_strat_id] = {"signal": "ERROR", "strategy": _strat_id, "instrument": cfg.instrument, "reason": str(_s_err)}

                # Publish all-strategy signals to global cache for /api/signals_all
                # MERGE into cache — don't replace — so dedup-skipped strategies
                # keep their last known signal instead of disappearing.
                if _all_strat_sigs:
                    _all_strat_sigs_cache.update(_all_strat_sigs)

                # Fix C: Active strategy signal for auto-trade and UI panel
                # Use cached signal from loop above. NEVER re-call the strategy —
                # that was causing a 3rd evaluation and duplicate signals.
                sig = _all_strat_sigs.get(cfg.strategy)
                if sig is None:
                    # Strategy was skipped (same candle dedup) — use last known signal
                    sig = _last_signal if _last_signal else {"signal": "HOLD", "strategy": cfg.strategy}
                sig["instrument"] = cfg.instrument
                _last_signal = sig
                await ws_manager.broadcast({"type": "signal", "data": sig})

                # ── Auto square-off check near market close ──
                if cfg.auto_trade and tm.position and tm.position.instrument == cfg.instrument:
                    _meta_ex = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_fut", "NFO")
                    _mh = MARKET_HOURS.get(_meta_ex, MARKET_HOURS.get("NFO", (9,15,15,30)))
                    _close_h, _close_m = _mh[2], _mh[3]
                    _squareoff_mins = cfg.auto_square_off_minutes
                    # Calculate minutes until market close
                    _close_dt = now.replace(hour=_close_h, minute=_close_m, second=0, microsecond=0)
                    _mins_to_close = (_close_dt - now).total_seconds() / 60
                    if 0 < _mins_to_close <= _squareoff_mins:
                        pos = tm.position
                        _tid = _trace_id(cfg.strategy, f"{pos.direction}_EXIT", pos.entry_time)
                        _trace(_tid, "DETECTED", f"path=auto_squareoff strategy={cfg.strategy} "
                                                  f"mins_to_close={_mins_to_close:.0f} pos={pos.symbol}")
                        logger.info(f"Auto square-off: {_mins_to_close:.0f} mins to close, squaring off position")
                        if not pos.order_id.startswith("PAPER_"):
                            _trace(_tid, "ORDER_ATTEMPT", f"path=auto_squareoff closing {pos.symbol}")
                            _sq_exit = await asyncio.to_thread(broker.place_exit_order, pos.symbol, pos.exchange, pos.direction, pos.qty)
                            _trace(_tid, "BROKER_RESULT", f"success={_sq_exit.get('success')} error={_sq_exit.get('error','')}")
                            if not _sq_exit.get("success"):
                                logger.error(f"Auto-squareoff exit FAILED: {_sq_exit.get('error')}. Position kept open.")
                                try:
                                    _send_telegram_alert_wrapper(
                                        f"AUTO-SQUAREOFF EXIT FAILED\n"
                                        f"{pos.direction} {pos.symbol}\n"
                                        f"Reason: {_sq_exit.get('error')}\n"
                                        f"URGENT: Market closing — manual exit needed!",
                                        cfg.telegram_bot_token, cfg.telegram_chat_id
                                    )
                                except Exception:
                                    pass
                                continue  # skip local close — position still at broker
                        ltp = (await asyncio.to_thread(broker.get_ltp, pos.instrument)) or 0
                        _pnl_exit = _safe_pnl_exit_price(pos, ltp)
                        rec = tm.close_position(_pnl_exit, "AUTO_SQUAREOFF")
                        if cfg.auto_trade and not pos.order_id.startswith("PAPER_"):
                            get_capital_tracker().record_trade_pnl(rec.get("pnl", 0.0))
                        slippage_tracker.log_exit_fill(
                            symbol=pos.symbol,
                            direction=pos.direction,
                            expected_price=ltp,
                            actual_price=ltp,
                            qty=pos.qty,
                            order_id=pos.order_id,
                            exit_reason="AUTO_SQUAREOFF",
                            trade_mode=pos.trade_mode,
                            instrument=pos.instrument,
                        )
                        _active_trade_signal = {}
                        try:
                            exit_sig = {
                                "signal": "LONG_EXIT" if pos.direction == "LONG" else "SHORT_EXIT",
                                "time": datetime.now(_IST).isoformat(),
                                "entry": ltp, "close": ltp,
                                "strategy": getattr(pos, "strategy", cfg.strategy),
                                "reason": "AUTO_SQUAREOFF",
                                "reasons": [f"Auto square-off {_squareoff_mins}min before close"]
                            }
                            _add_signal_to_history(exit_sig)
                            signal_journal_manager.close_entry(exit_sig, cfg.instrument, "AUTO_SQUAREOFF", index_price=ltp)
                        except Exception as _sq_err:
                            logger.error(f"Square-off logging error: {_sq_err}")
                        # Telegram exit alert for auto square-off
                        try:
                            _trace(_tid, "TELEGRAM", "sending")
                            send_telegram_exit_alert(pos, ltp, "AUTO_SQUAREOFF", rec.get("pnl", 0.0), index_exit_price=ltp)
                        except Exception as _tg_err:
                            logger.warning(f"Telegram square-off alert failed: {_tg_err}")
                        await ws_manager.broadcast({"type": "trade_closed", "data": rec})

                # ── Entry / flip / hold logic ──
                sig_direction = sig.get("signal", "")

                if sig_direction in ("LONG", "SHORT"):
                    sig_ts = sig.get("time", "")
                    current_key = (sig_direction, sig_ts)

                    # If we already have a position in the SAME direction for this instrument — no action
                    if tm.position and tm.position.instrument == sig.get("instrument") and tm.position.direction == sig_direction:
                        _pending_telegram_signal = {}
                        pass  # already in correct position

                    # If we have a position in OPPOSITE direction for this instrument — EXIT ONLY (no auto-flip)
                    # Avoids double-whipsaw trap on 5-min fake moves.
                    # Re-entry in the new direction requires fresh 2-poll confirmation.
                    elif tm.position and tm.position.instrument == sig.get("instrument") and tm.position.direction != sig_direction:
                        pos = tm.position

                        # --- Ownership guard ---
                        # Same check as the processor path (added after the 2026-07-27 incident
                        # where a strategy's exit closed an unrelated Dhan carry position — see
                        # _owns_position above). A DHAN_SYNC position, or one belonging to a
                        # different strategy, must never be closed by this signal — live or
                        # local — regardless of auto_trade. Previously this only blocked the
                        # broker call when auto_trade was OFF (to stop a re-import log-spam
                        # loop); that left a real gap once auto_trade was turned ON, where this
                        # path would place a REAL exit order against a position it doesn't own.
                        _is_dhan_sync = getattr(pos, "order_id", "").startswith("DHAN_SYNC_")
                        _owns_position = not _is_dhan_sync and (not getattr(pos, "strategy", None) or pos.strategy == cfg.strategy)
                        if not _owns_position:
                            # Throttle: this condition stays true every poll for as long as
                            # the opposite signal persists and the position stays open --
                            # without a throttle this re-logs (and re-traces) every ~15s
                            # indefinitely. Confirmed in production (2026-08-24): the same
                            # event logged continuously for 4+ minutes straight. Reuses the
                            # same once-per-15-min throttle as the MANUAL_EXIT_NEEDED
                            # reminder -- identical underlying situation (a position needing
                            # manual intervention or belonging to someone else), just detected
                            # via a different code path.
                            _now_alert = datetime.now(_IST)
                            _last_alert = _manual_exit_alert_last_sent.get(pos.order_id)
                            if _last_alert is None or (_now_alert - _last_alert).total_seconds() >= 900:
                                _manual_exit_alert_last_sent[pos.order_id] = _now_alert
                                _tid = _trace_id(cfg.strategy, f"{pos.direction}_EXIT", sig_ts)
                                _trace(_tid, "DETECTED", f"path=legacy_opposite_signal strategy={cfg.strategy} "
                                                          f"closing={pos.direction} new_signal={sig_direction} sig_time={sig_ts}")
                                _owner_desc = "DHAN_SYNC" if _is_dhan_sync else f"strategy '{pos.strategy}'"
                                logger.warning(
                                    f"Opposite signal ({sig_direction}) vs {_owner_desc} {pos.direction} {pos.symbol} "
                                    f"— not owned by active strategy '{cfg.strategy}' (auto_trade={cfg.auto_trade}), "
                                    f"skipping internal close. Manage this position manually on Dhan."
                                )
                            _pending_telegram_signal = sig
                            _last_telegram_signal_key = ("", "")
                            continue  # skip — not this strategy's position to act on, broker or local

                        _tid = _trace_id(cfg.strategy, f"{pos.direction}_EXIT", sig_ts)
                        _trace(_tid, "DETECTED", f"path=legacy_opposite_signal strategy={cfg.strategy} "
                                                  f"closing={pos.direction} new_signal={sig_direction} sig_time={sig_ts}")
                        logger.info(f"Opposite signal ({sig_direction}) while in {pos.direction} — exiting only (no auto-flip)")

                        _trace(_tid, "ORDER_ATTEMPT", f"path=legacy_opposite_signal auto_trade={cfg.auto_trade} closing {pos.symbol}")
                        if cfg.auto_trade and not pos.order_id.startswith("PAPER_"):
                            _opp_exit = await asyncio.to_thread(broker.place_exit_order, pos.symbol, pos.exchange, pos.direction, pos.qty)
                            _trace(_tid, "BROKER_RESULT", f"success={_opp_exit.get('success')} error={_opp_exit.get('error','')}")
                            if not _opp_exit.get("success"):
                                logger.error(f"Opposite-signal exit FAILED: {_opp_exit.get('error')}. Position kept open.")
                                try:
                                    _send_telegram_alert_wrapper(
                                        f"EXIT ORDER FAILED (opposite signal)\n"
                                        f"{pos.direction} {pos.symbol}\n"
                                        f"Reason: {_opp_exit.get('error')}\n"
                                        f"Position still open — manual exit needed!",
                                        cfg.telegram_bot_token, cfg.telegram_chat_id
                                    )
                                except Exception:
                                    pass
                                _pending_telegram_signal = sig
                                _last_telegram_signal_key = ("", "")
                                continue  # skip local close
                        ltp = (await asyncio.to_thread(broker.get_ltp, pos.instrument)) or 0
                        exit_reason = f"OPPOSITE_SIGNAL_{sig_direction}"
                        _pnl_exit = _safe_pnl_exit_price(pos, ltp)
                        rec = tm.close_position(_pnl_exit, exit_reason)
                        if cfg.strategy in _COOLDOWN_BARS and rec.get("pnl", 0.0) <= 0.0:
                            record_cooldown_loss(pos.direction)
                        _active_trade_signal = {}
                        try:
                            exit_sig = {
                                "signal": "LONG_EXIT" if pos.direction == "LONG" else "SHORT_EXIT",
                                "time": datetime.now(_IST).isoformat(),
                                "entry": ltp, "close": ltp,
                                "strategy": getattr(pos, "strategy", cfg.strategy),
                                "reason": exit_reason,
                                "reasons": [f"Exited {pos.direction} on opposite {sig_direction} signal"]
                            }
                            _add_signal_to_history(exit_sig)
                            signal_journal_manager.close_entry(exit_sig, cfg.instrument, exit_reason, index_price=ltp)
                        except Exception as _fl_err:
                            logger.error(f"Opposite-signal exit logging error: {_fl_err}")
                        try:
                            _trace(_tid, "TELEGRAM", "sending")
                            send_telegram_exit_alert(pos, ltp, exit_reason, rec.get("pnl", 0.0), index_exit_price=ltp)
                        except Exception as _tg_err:
                            logger.warning(f"Telegram opposite-exit alert failed: {_tg_err}")
                        await ws_manager.broadcast({"type": "trade_closed", "data": rec})

                        # Seed the pending buffer so 2-poll confirmation starts NOW.
                        # If the same signal fires again next poll, it enters as a fresh trade.
                        _pending_telegram_signal = sig
                        _last_telegram_signal_key = ("", "")

                    # No position — use 2-poll confirmation then enter (or bypass if confirm_signals is False)
                    else:
                        confirm_signals = getattr(cfg, "confirm_signals", False)
                        should_enter = False
                        if not confirm_signals:
                            if current_key != _last_telegram_signal_key:
                                should_enter = True
                                _last_telegram_signal_key = current_key
                        else:
                            pending_key = (_pending_telegram_signal.get("signal", ""),
                                           _pending_telegram_signal.get("time", ""))
                            if current_key == pending_key and current_key != _last_telegram_signal_key:
                                should_enter = True
                                _last_telegram_signal_key = current_key
                                _pending_telegram_signal = {}
                            else:
                                _pending_telegram_signal = sig
                                logger.info(f"Signal buffered for confirmation: {sig_direction} at {sig_ts}")

                        if should_enter:
                            _tid = _trace_id(cfg.strategy, sig_direction, sig_ts)
                            _trace(_tid, "DETECTED", f"path=legacy strategy={cfg.strategy} dir={sig_direction} sig_time={sig_ts}")
                            ok = True
                            reason = "OK"
                            if cfg.auto_trade:
                                # Signal freshness check: `sig` reflects the processor's CURRENT
                                # state (self.position), not a fresh event -- if it's been showing
                                # LONG/SHORT for a while (e.g. accumulated while auto_trade was OFF)
                                # and _last_telegram_signal_key gets reset for any reason (backend
                                # restart, an opposite-signal exit, first poll after auto_trade is
                                # turned on), the very next poll would otherwise treat that stale
                                # signal as brand new and place a REAL order for an entry price that
                                # may be many minutes old -- a real incident, not hypothetical. Same
                                # 10-minute cutoff as the newer bar-by-bar processor path uses.
                                _sig_age = _signal_age_minutes(sig_ts)
                                if _sig_age is not None and _sig_age > 10.0:
                                    ok, reason = False, f"Historical replay signal skipped ({_sig_age:.1f} mins old)"

                            if ok and cfg.auto_trade:
                                ok, reason = tm.can_trade
                                if ok:
                                    ct_ok, ct_reason = get_capital_tracker().can_trade()
                                    if not ct_ok:
                                        ok, reason = False, ct_reason
                                # Safety: verify no same-instrument position already open at broker
                                # Prevents duplicate entries if local state got out of sync
                                if ok and broker.is_connected():
                                    try:
                                        bp = await asyncio.to_thread(broker.sync_position_from_broker, cfg.instrument)
                                        if bp and bp.get("has_position"):
                                            ok = False
                                            reason = f"Broker already has open position: {bp.get('symbol')} qty={bp.get('qty')}"
                                            logger.warning(f"Entry blocked — {reason}")
                                    except Exception:
                                        pass  # don't block entry if check fails
                            if ok:
                                cooldown_bars = _COOLDOWN_BARS.get(cfg.strategy, 0)
                                if cooldown_bars > 0 and _last_loss_direction == sig_direction and _last_loss_time:
                                    elapsed_mins = (datetime.now(_IST) - _last_loss_time).total_seconds() / 60.0
                                    cooldown_mins = cooldown_bars * 5
                                    if elapsed_mins < cooldown_mins:
                                        ok = False
                                        reason = f"Cooldown: same direction ({sig_direction}) blocked for {cooldown_mins - elapsed_mins:.1f} more mins"
                            _trace(_tid, "ORDER_ATTEMPT", f"path=legacy auto_trade={cfg.auto_trade} ok={ok} reason={reason}")
                            if ok:
                                result = await asyncio.to_thread(_execute_order, sig, cfg, sig_direction)
                                _trace(_tid, "BROKER_RESULT", f"success={result.get('success')} "
                                                               f"order_id={result.get('order_id','')} error={result.get('error','')}")
                                if result.get("success"):
                                    tm.reset_order_failures()
                                    await ws_manager.broadcast({"type": "trade_opened", "data": result})
                                    trade_type_label = "Live" if cfg.auto_trade else "Paper"
                                    logger.info(f"{trade_type_label} trade executed: {sig_direction} {cfg.instrument}")
                                else:
                                    fail_count = tm.record_order_failure()
                                    logger.error(f"Trade execution failed: {result.get('error')}")
                                    # Kill switch check
                                    if cfg.auto_kill_switch and fail_count >= cfg.auto_kill_switch_max_failures:
                                        logger.error(f"Kill switch triggered after {fail_count} consecutive failures — disabling auto_trade")
                                        save_settings({"auto_trade": False})
                            else:
                                logger.info(f"Trade blocked: {reason}")
                        else:
                            # First appearance — buffer it, wait for confirmation
                            _pending_telegram_signal = sig
                            logger.info(f"Signal buffered for confirmation: {sig_direction} at {sig_ts}")

                else:
                    # No entry signal (HOLD / EXIT) — clear the pending buffer
                    _pending_telegram_signal = {}

                # Monitor open position
                if tm.position:
                    pos = tm.position
                    ltp = await asyncio.to_thread(broker.get_ltp, pos.instrument)  # INDEX LTP — used for SL/T1/T2 hit detection

                    # For OPTIONS: also fetch the option premium for correct P&L tracking
                    opt_ltp = _safe_pnl_exit_price(pos, ltp)

                    if ltp:
                        # update_pnl uses option premium so UI shows correct unrealized P&L
                        tm.update_pnl(opt_ltp)

                        exit_triggered = False
                        exit_reason = ""
                        exit_price = ltp
                        pnl_price = opt_ltp

                        # Verify if position was closed at broker (e.g. SL trigger)
                        if cfg.auto_trade and not pos.order_id.startswith("PAPER_"):
                            try:
                                bp = await asyncio.to_thread(
                                    broker.sync_position_from_broker,
                                    pos.instrument,
                                    tracked_symbol=pos.symbol  # exact match — avoids false close from user's other positions
                                )
                                if not bp or not bp.get("has_position"):
                                    logger.warning(f"Active position {pos.symbol} was closed at broker. Closing locally.")
                                    exit_triggered = True
                                    exit_reason = "BROKER_SL_HIT"
                                    exit_price = ltp
                                    pnl_price = opt_ltp
                            except Exception as bp_err:
                                logger.error(f"Error checking position sync from broker: {bp_err}")

                        # ── Trailing SL for the 3 live research strategies ────
                        # Generic version — see _apply_generic_trailing_sl and
                        # _TRAIL_PARAMS above _signal_polling_loop's definition.
                        if not exit_triggered and cfg.strategy in _TRAIL_PARAMS:
                            await _apply_generic_trailing_sl(pos, cfg, ltp, sig)

                        # ── Trailing SL for regime_trend_range (dormant —
                        # regime_trend_range is no longer live-selectable, but
                        # this stays intact since the strategy is still valid
                        # for backtesting) ────────────────────────────────────
                        elif not exit_triggered and cfg.strategy == "regime_trend_range":
                            trail_mult = getattr(cfg, "regime_trail_mult", 1.5)
                            trail_activation = getattr(cfg, "regime_trail_activation", 0.3)
                            be_trigger = getattr(cfg, "regime_be_trigger", 0.4)
                            be_buffer = getattr(cfg, "regime_be_buffer", 0.3)
                            atr_v = pos.entry_atr or sig.get("atr_5m", 0) or (ltp * 0.002)
                            if pos.direction == "LONG":
                                if ltp > pos.highest_since_entry:
                                    pos.highest_since_entry = ltp
                                profit = pos.highest_since_entry - pos.index_entry_price
                                if profit >= atr_v * trail_activation:
                                    trail_sl = pos.highest_since_entry - atr_v * trail_mult
                                    if be_trigger > 0 and profit > atr_v * be_trigger:
                                        trail_sl = max(trail_sl, pos.index_entry_price + atr_v * be_buffer)
                                    # Clamp: same fix as regime_trend_kernel.py's backtest —
                                    # the ATR-derived breakeven level can otherwise be pushed
                                    # above the highest LTP this LONG has actually traded at,
                                    # producing an "SL hit" price the market never reached.
                                    trail_sl = min(trail_sl, pos.highest_since_entry)
                                    if trail_sl > pos.sl:
                                        logger.info(f"Trailing SL updated: {pos.sl:.2f} -> {trail_sl:.2f} (highest={pos.highest_since_entry:.2f}, ATR={atr_v:.2f})")
                                        pos.sl = trail_sl
                                        await asyncio.to_thread(_sync_broker_sl, pos, cfg)
                            elif pos.direction == "SHORT":
                                if ltp < pos.lowest_since_entry:
                                    pos.lowest_since_entry = ltp
                                profit = pos.index_entry_price - pos.lowest_since_entry
                                if profit >= atr_v * trail_activation:
                                    trail_sl = pos.lowest_since_entry + atr_v * trail_mult
                                    if be_trigger > 0 and profit > atr_v * be_trigger:
                                        trail_sl = min(trail_sl, pos.index_entry_price - atr_v * be_buffer)
                                    # Clamp: mirrored fix — must never fall below the lowest
                                    # LTP this SHORT has actually traded at.
                                    trail_sl = max(trail_sl, pos.lowest_since_entry)
                                    if trail_sl < pos.sl:
                                        logger.info(f"Trailing SL updated: {pos.sl:.2f} -> {trail_sl:.2f} (lowest={pos.lowest_since_entry:.2f}, ATR={atr_v:.2f})")
                                        pos.sl = trail_sl
                                        await asyncio.to_thread(_sync_broker_sl, pos, cfg)

                        # ── Trailing SL for multi_agent strategy ──────────────
                        # 3-step: initial → breakeven at +2.0 ATR → trail at +2.5 ATR, offset 0.5 ATR
                        elif not exit_triggered and cfg.strategy == "multi_agent":
                            be_trigger = getattr(cfg, 'trailing_be_trigger_atr', 0)
                            trail_start = getattr(cfg, 'trailing_start_atr', 0)
                            trail_offset = getattr(cfg, 'trailing_offset_atr', 0.5)
                            atr_v = pos.entry_atr or sig.get("atr_5m", 0) or (ltp * 0.002)

                            if atr_v > 0 and be_trigger > 0:
                                if pos.direction == "LONG":
                                    if ltp > pos.highest_since_entry:
                                        pos.highest_since_entry = ltp
                                    profit = pos.highest_since_entry - pos.index_entry_price
                                    profit_atr = profit / atr_v
                                    peak_profit_atr = profit_atr  # peak = highest since entry

                                    # Step 1: Move to breakeven
                                    if pos.trail_step == 0 and profit_atr >= be_trigger:
                                        pos.sl = pos.index_entry_price
                                        pos.trail_step = 1
                                        logger.info(f"V3 Trail: BE triggered (profit={profit_atr:.1f} ATR), SL -> {pos.sl:.2f}")
                                        await asyncio.to_thread(_sync_broker_sl, pos, cfg)
                                    # Step 2: Start trailing
                                    if trail_start > 0 and pos.trail_step >= 1 and peak_profit_atr >= trail_start:
                                        pos.trail_step = 2
                                    # Continuous trail
                                    if pos.trail_step >= 2 and trail_offset > 0:
                                        trail_sl = pos.index_entry_price + profit - trail_offset * atr_v
                                        if trail_sl > pos.sl:
                                            logger.info(f"V3 Trail: SL {pos.sl:.2f} -> {trail_sl:.2f} (peak={profit:.0f}pts, ATR={atr_v:.0f})")
                                            pos.sl = trail_sl
                                            await asyncio.to_thread(_sync_broker_sl, pos, cfg)

                                elif pos.direction == "SHORT":
                                    if ltp < pos.lowest_since_entry:
                                        pos.lowest_since_entry = ltp
                                    profit = pos.index_entry_price - pos.lowest_since_entry
                                    profit_atr = profit / atr_v
                                    peak_profit_atr = profit_atr

                                    if pos.trail_step == 0 and profit_atr >= be_trigger:
                                        pos.sl = pos.index_entry_price
                                        pos.trail_step = 1
                                        logger.info(f"V3 Trail: BE triggered (profit={profit_atr:.1f} ATR), SL -> {pos.sl:.2f}")
                                        await asyncio.to_thread(_sync_broker_sl, pos, cfg)
                                    if trail_start > 0 and pos.trail_step >= 1 and peak_profit_atr >= trail_start:
                                        pos.trail_step = 2
                                    if pos.trail_step >= 2 and trail_offset > 0:
                                        trail_sl = pos.index_entry_price - profit + trail_offset * atr_v
                                        if trail_sl < pos.sl:
                                            logger.info(f"V3 Trail: SL {pos.sl:.2f} -> {trail_sl:.2f} (peak={profit:.0f}pts, ATR={atr_v:.0f})")
                                            pos.sl = trail_sl
                                            await asyncio.to_thread(_sync_broker_sl, pos, cfg)



                        # SL/target/exit checks run for ALL positions (PAPER_, DHAN_SYNC_, SIG_).
                        # The broker exit order is conditional on auto_trade (checked below at line that calls place_exit_order).
                        # For DHAN_SYNC_ with auto_trade=OFF: we detect SL hit, close locally, and alert user.

                        if not exit_triggered:
                            # 0. Intraday Daily Loss Limit — DISABLED
                            # Was producing false exits. Re-enable when PnL tracking is reliable.
                            # daily_pnl = tm.day_stats.gross_pnl
                            # current_unpnl = getattr(pos, "current_pnl", 0.0)
                            # ct = get_capital_tracker()
                            # breach_triggered, breach_msg = ct.should_force_close(
                            #     daily_realised_pnl=daily_pnl,
                            #     unrealised_pnl=current_unpnl,
                            #     max_daily_loss=tm._max_daily_loss
                            # )
                            # if breach_triggered:
                            #     exit_triggered = True
                            #     exit_reason = "DAILY_LOSS_LIMIT_BREACH"
                            #     exit_price = ltp
                            #     logger.warning(f"Intra-trade guard: {breach_msg}. Forcing exit.")
                            # 1. Target 2 hit (index level)
                            if pos.target2 > 0 and (
                               (pos.direction == "LONG" and ltp >= pos.target2) or
                               (pos.direction == "SHORT" and ltp <= pos.target2)
                            ):
                                exit_triggered = True
                                exit_reason = "T2_HIT"
                                # Real-fill: book at the actual observed LTP, not the
                                # preset target level — ltp is always a real, traded
                                # price; target2 is just the trigger threshold.
                                exit_price = ltp
                            # 2. Stop Loss hit (index level)
                            # Distinguish trailing SL from initial SL (matches backtest)
                            elif pos.sl > 0 and (
                                 (pos.direction == "LONG" and ltp <= pos.sl) or
                                 (pos.direction == "SHORT" and ltp >= pos.sl)
                            ):
                                exit_triggered = True
                                _ts = getattr(pos, 'trail_step', 0)
                                exit_reason = f"TRAIL_S{_ts}" if _ts > 0 else "SL_HIT"
                                # Real-fill fix (live version of the same bug fixed in
                                # regime_trend_kernel.py's backtest): pos.sl can be a
                                # stale trailing/breakeven level that the market had
                                # already moved past before this tick — booking at
                                # pos.sl in that case would log a price that was never
                                # actually traded. ltp is always the real, current,
                                # actually-observed price, so book the exit there.
                                exit_price = ltp
                            # 3. Model exit signal (SIG_EXIT — same name as backtest)
                            elif sig.get("instrument") == pos.instrument and (
                                 (pos.direction == "LONG" and sig.get("signal") == "LONG_EXIT") or
                                 (pos.direction == "SHORT" and sig.get("signal") == "SHORT_EXIT")
                            ):
                                exit_triggered = True
                                exit_reason = "SIG_EXIT"
                            # 4. Time limit exit
                            else:
                                try:
                                    entry_dt = datetime.fromisoformat(pos.entry_time)
                                    if entry_dt.tzinfo is None:
                                        entry_dt = pytz.timezone("Asia/Kolkata").localize(entry_dt)
                                    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
                                    duration_mins = (now_ist - entry_dt).total_seconds() / 60
                                    if duration_mins >= (cfg.max_hold_bars * 5):
                                        exit_triggered = True
                                        exit_reason = "TIME_EXIT"
                                except Exception as e:
                                    logger.error(f"Error checking time exit: {e}")

                        # Log LONG_EXIT / SHORT_EXIT model signal to history exactly ONCE per position.
                        # _exit_signal_logged_for_position tracks the order_id so even if the bar
                        # timestamp changes across polls we never write a second entry.
                        if sig.get("signal") in ("LONG_EXIT", "SHORT_EXIT"):
                            if _exit_signal_logged_for_position != pos.order_id:
                                try:
                                    model_exit_sig = {
                                        "signal": sig.get("signal"),
                                        "time": sig.get("time", datetime.now(_IST).isoformat()),
                                        "entry": opt_ltp,
                                        "close": opt_ltp,
                                        "reason": "SIG_EXIT",
                                        "reasons": sig.get("reasons", ["Model exit signal"])
                                    }
                                    _add_signal_to_history(model_exit_sig)
                                    signal_journal_manager.close_entry(
                                        model_exit_sig, cfg.instrument, "SIG_EXIT", index_price=ltp
                                    )
                                    _exit_signal_logged_for_position = pos.order_id
                                except Exception as ex_err:
                                    logger.error(f"Failed to log model exit signal: {ex_err}")

                        if exit_triggered:
                            _tid = _trace_id(cfg.strategy, f"{pos.direction}_EXIT", pos.entry_time)
                            _trace(_tid, "DETECTED", f"path=position_monitor strategy={cfg.strategy} "
                                                      f"reason={exit_reason} pos={pos.symbol} entry_time={pos.entry_time}")
                            # Ownership guard — same check as the processor and legacy_opposite_signal
                            # paths (2026-07-27 incident). Local SL/target/time detection above
                            # intentionally runs for every position, DHAN_SYNC included, so the app's
                            # own bookkeeping and the user alert stay accurate — but a real broker
                            # order must only ever be placed for a position this strategy actually
                            # opened. Previously this only checked auto_trade + not-PAPER_, so a
                            # DHAN_SYNC (or another strategy's) position would get a real exit order
                            # placed against it once auto_trade was ON, based on a stop/target level
                            # this strategy computed for its own trade, not the user's.
                            _pm_is_dhan_sync = getattr(pos, "order_id", "").startswith("DHAN_SYNC_")
                            _pm_owns_position = not _pm_is_dhan_sync and (not getattr(pos, "strategy", None) or pos.strategy == cfg.strategy)
                            if cfg.auto_trade and not _pm_owns_position and not pos.order_id.startswith("PAPER_"):
                                logger.warning(
                                    f"{exit_reason} on {pos.symbol} ({'DHAN_SYNC' if _pm_is_dhan_sync else f'strategy {pos.strategy!r}'}) "
                                    f"— not owned by active strategy '{cfg.strategy}', auto_trade is ON but skipping "
                                    f"broker close. Manage this position manually on Dhan."
                                )
                            # Only place real broker exit order in live auto-trade mode, for a position we own
                            if cfg.auto_trade and _pm_owns_position and not pos.order_id.startswith("PAPER_") and exit_reason != "BROKER_SL_HIT":
                                _trace(_tid, "ORDER_ATTEMPT", f"path=position_monitor auto_trade={cfg.auto_trade} closing {pos.symbol}")
                                if getattr(pos, "sl_order_id", None):
                                    try:
                                        await asyncio.to_thread(broker.cancel_broker_sl, pos.sl_order_id)
                                    except Exception as sl_cancel_err:
                                        logger.warning(f"Could not cancel broker SL: {sl_cancel_err}")
                                    pos.sl_order_id = None

                                exit_result = await asyncio.to_thread(broker.place_exit_order, pos.symbol, pos.exchange, pos.direction, pos.qty)
                                _trace(_tid, "BROKER_RESULT", f"success={exit_result.get('success')} error={exit_result.get('error','')}")
                                if exit_result.get("success"):
                                    logger.info(f"SL/target EXIT order placed successfully for {pos.symbol} ({exit_reason})")
                                else:
                                    logger.error(f"SL/target EXIT FAILED for {pos.symbol}: {exit_result.get('error')}")
                                    try:
                                        _send_telegram_alert_wrapper(
                                            f"EXIT ORDER FAILED ({exit_reason})\n"
                                            f"{pos.direction} {pos.symbol}\n"
                                            f"Reason: {exit_result.get('error')}\n"
                                            f"Position may still be open — check Dhan!",
                                            cfg.telegram_bot_token, cfg.telegram_chat_id
                                        )
                                    except Exception:
                                        pass
                                    continue  # skip local position close — broker exit failed
                            elif getattr(pos, "sl_order_id", None):
                                pos.sl_order_id = None
                            elif pos.order_id.startswith("DHAN_SYNC_") and not cfg.auto_trade:
                                # DHAN_SYNC position (broker-synced, e.g. a carry trade the user is
                                # deliberately holding) hit SL/target but auto_trade is OFF — alert the
                                # user and leave it alone entirely. Do NOT close it locally: no real
                                # broker order fires, so an internal close here would just be a phantom
                                # trade (it doesn't reflect anything that actually happened to the real
                                # position) that then gets re-imported and re-flagged again next cycle.
                                logger.warning(f"DHAN_SYNC position {exit_reason} but auto_trade=OFF — user must exit manually on Dhan! Not closing internally.")
                                # Reminder cooldown: this condition stays true every single poll
                                # cycle until the user actually closes the position on Dhan, so
                                # without a cooldown this alert fires every ~15s and floods
                                # Telegram. Send it once, then only again every 1 hour as a
                                # reminder — not on every cycle. Widened from 15 min to 1 hour
                                # (2026-08-28, explicit user request).
                                _now_alert = datetime.now(_IST)
                                _last_alert = _manual_exit_alert_last_sent.get(pos.order_id)
                                if _last_alert is None or (_now_alert - _last_alert).total_seconds() >= 3600:
                                    _manual_exit_alert_last_sent[pos.order_id] = _now_alert
                                    _trace(_tid, "TELEGRAM", f"MANUAL_EXIT_NEEDED reminder sent, reason={exit_reason}")
                                    try:
                                        _send_telegram_alert_wrapper(
                                            f"\u26a0\ufe0f MANUAL EXIT NEEDED\n"
                                            f"{pos.direction} {pos.symbol}\n"
                                            f"Reason: {exit_reason} (LTP: {ltp})\n"
                                            f"Auto-trade is OFF — please exit on Dhan manually!\n"
                                            f"(Reminder every 1 hour until closed)",
                                            cfg.telegram_bot_token, cfg.telegram_chat_id
                                        )
                                    except Exception:
                                        pass
                                    # Prune stale entries so this dict can't grow unbounded over
                                    # many days of operation (positions never repeat order_ids).
                                    if len(_manual_exit_alert_last_sent) > 200:
                                        _cutoff = _now_alert - timedelta(hours=6)
                                        for _oid in [k for k, v in _manual_exit_alert_last_sent.items() if v < _cutoff]:
                                            del _manual_exit_alert_last_sent[_oid]
                                continue  # skip local position close — this position is not ours to close

                            # Prevent immediate Dhan re-import for 5 minutes
                            _recently_closed_symbols[pos.symbol] = datetime.now(_IST)
                            _exit_signal_logged_for_position = ""
                            _exit_telegram_sent_for_position = ""

                            # PnL calculated on option premium (not index price)
                            rec = tm.close_position(pnl_price, exit_reason)
                            if cfg.auto_trade and not pos.order_id.startswith("PAPER_"):
                                get_capital_tracker().record_trade_pnl(rec.get("pnl", 0.0))
                            if cfg.strategy in _COOLDOWN_BARS and rec.get("pnl", 0.0) <= 0.0:
                                record_cooldown_loss(pos.direction)

                            # Log exit fill slippage.
                            # For SL/T2 hits: expected = the SL/T2 level, actual = ltp at exit.
                            # exit_price/pos.sl/pos.target2 are always INDEX-level (the hit
                            # detection compares index LTP against them), but pnl_price is the
                            # PREMIUM for OPTIONS mode -- comparing them produced a nonsensical
                            # logged "slippage" (confirmed live, 2026-08-26, e.g.
                            # expected=57930.35 actual=1326.60 => -97.71%). No premium-level
                            # "expected at exit" is tracked anywhere in this path, so for
                            # OPTIONS mode there's nothing valid to compare pnl_price against --
                            # fall back to 0 logged slippage rather than mixing units, same
                            # reasoning as the entry-side fix just above.
                            if pos.trade_mode == "OPTIONS":
                                _expected_exit = pnl_price
                            else:
                                _expected_exit = exit_price  # index-level SL/T2 hit price
                                if exit_reason == "SL_HIT" and pos.sl > 0:
                                    _expected_exit = pos.sl
                                elif exit_reason == "T2_HIT" and pos.target2 > 0:
                                    _expected_exit = pos.target2
                            slippage_tracker.log_exit_fill(
                                symbol=pos.symbol,
                                direction=pos.direction,
                                expected_price=_expected_exit,
                                actual_price=pnl_price,
                                qty=pos.qty,
                                order_id=pos.order_id,
                                exit_reason=exit_reason,
                                trade_mode=pos.trade_mode,
                                instrument=pos.instrument,
                            )

                            _active_trade_signal = {}

                            # Log to history only for non-SIG_EXIT (SIG_EXIT already logged above)
                            if exit_reason != "SIG_EXIT":
                                try:
                                    exit_sig = {
                                        "signal": "LONG_EXIT" if pos.direction == "LONG" else "SHORT_EXIT",
                                        "time": sig.get("time", datetime.now(_IST).isoformat()),
                                        "entry": pnl_price,
                                        "close": pnl_price,
                                        "strategy": getattr(pos, "strategy", cfg.strategy),
                                        "reason": exit_reason,
                                        "reasons": [f"Exit triggered: {exit_reason}"]
                                    }
                                    _add_signal_to_history(exit_sig)
                                    signal_journal_manager.close_entry(
                                        exit_sig, cfg.instrument, exit_reason, index_price=exit_price
                                    )
                                except Exception as ex_err:
                                    logger.error(f"Failed to log exit signal: {ex_err}")

                            # Update Trading Journal (only for real broker trades, not paper)
                            if not pos.order_id.startswith("PAPER_"):
                                try:
                                    from journal_manager import close_journal_entry
                                    close_journal_entry(
                                        symbol=pos.symbol,
                                        exit_price=exit_price,   # INDEX level (ltp or SL/T2 hit level)
                                        exit_time=datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S"),
                                        exit_reason=exit_reason,
                                        pnl=rec.get("pnl", 0.0),
                                        order_id=pos.order_id
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to close journal entry: {e}")

                            # Send Telegram exit alert (index exit price shown, option premium for PnL context)
                            try:
                                send_telegram_exit_alert(pos, pnl_price, exit_reason, rec.get("pnl", 0.0), index_exit_price=exit_price)
                            except Exception as e:
                                logger.warning(f"Telegram exit alert failed: {e}")

                            await ws_manager.broadcast({"type": "trade_closed", "data": rec})
                            # Invalidate chart cache so exit marker appears on next chart fetch
                            _chart_signals_cache.clear()
                            await ws_manager.broadcast({"type": "chart_signals_updated", "data": {"strategy": cfg.strategy, "signal": "EXIT"}})
                        else:
                            # T1 hit check (only if not exited)
                            if not pos.t1_hit and pos.target1 > 0:
                                if (pos.direction == "LONG" and ltp >= pos.target1) or \
                                   (pos.direction == "SHORT" and ltp <= pos.target1):
                                    tm.mark_t1_hit()

                        # Persist the position after any trailing-SL/highest-since-entry mutations
                        # made directly on the dataclass above (open_position/close_position already
                        # save on their own) — keeps the on-disk copy current so a restart can
                        # restore exactly what was open instead of losing track of it silently.
                        if tm.position:
                            tm.save_position_state()

                await ws_manager.broadcast({"type": "state", "data": tm.get_state()})
        except Exception as e:
            logger.error(f"Polling loop error: {e}")


# ── Capital Tracker API ───────────────────────────────────────────────────────

@app.get("/api/capital")
async def get_capital_state():
    cfg = get_settings()
    ct = get_capital_tracker()
    state = ct.get_state()
    
    # 1. Fetch real-time equity (current capital) from Dhan if connected
    equity = state["current_equity"]
    if broker.is_connected():
        try:
            dhan_bal = await asyncio.to_thread(broker.get_balance)
            if dhan_bal > 0 and cfg.auto_trade:
                equity = dhan_bal
                # Update capital tracker in-memory and on-disk
                ct.current_equity = equity
                if ct.current_equity > ct.peak_equity:
                    ct.peak_equity = ct.current_equity
                ct._check_breach()
                ct._save()
        except Exception as _bal_err:
            logger.warning(f"Failed to fetch real-time balance for capital protection: {_bal_err}")

    # 2. Get today's P&L (realized + unrealized) from Dhan if connected
    today_pnl = 0.0
    if broker.is_connected():
        try:
            today_pnl = await asyncio.to_thread(broker.get_today_pnl)
        except Exception as _pnl_err:
            logger.warning(f"Failed to fetch real-time today's pnl from Dhan: {_pnl_err}")
            # Fallback to local calculation if Dhan call fails
            realized_pnl = 0.0
            try:
                from journal_manager import get_today_journal_stats
                journal_stats = get_today_journal_stats(cfg.instrument)
                realized_pnl = journal_stats.get("gross_pnl", 0.0)
            except Exception:
                pass
            tm = get_trade_manager()
            unrealized_pnl = 0.0
            if tm.position and tm.position.instrument == cfg.instrument and not tm.position.order_id.startswith("PAPER_"):
                unrealized_pnl = tm.position.current_pnl
            today_pnl = realized_pnl + unrealized_pnl
    else:
        # Paper/offline mode fallback
        realized_pnl = 0.0
        try:
            from journal_manager import get_today_journal_stats
            journal_stats = get_today_journal_stats(cfg.instrument)
            realized_pnl = journal_stats.get("gross_pnl", 0.0)
        except Exception:
            pass
        tm = get_trade_manager()
        unrealized_pnl = 0.0
        if tm.position and tm.position.instrument == cfg.instrument:
            unrealized_pnl = tm.position.current_pnl
        today_pnl = realized_pnl + unrealized_pnl

    # 3. Calculate peak, drawdown, and limit
    if today_pnl < 0:
        # Loss scenario: Peak = Equity + Loss
        loss = abs(today_pnl)
        peak = equity + loss
        drawdown = loss
        drawdown_pct = (drawdown / peak) * 100 if peak > 0 else 0.0
    else:
        # Profit scenario: Peak = Equity
        peak = equity
        drawdown = 0.0
        drawdown_pct = 0.0

    limit = peak * 0.10

    return {
        "starting_capital": float(state["starting_capital"]),
        "peak_equity": round(float(peak), 2),
        "current_equity": round(float(equity), 2),
        "max_drawdown_pct": 10.0,
        "drawdown_limit": round(float(limit), 2),
        "current_drawdown": round(float(drawdown), 2),
        "current_drawdown_pct": round(float(drawdown_pct), 2),
        "drawdown_breached": bool(state["drawdown_breached"]),
        "last_updated": state["last_updated"],
        "today_pnl": round(float(today_pnl), 2),
        "is_profit": bool(today_pnl >= 0)
    }


@app.post("/api/capital/reset-breach")
async def reset_capital_breach():
    ct = get_capital_tracker()
    ct.reset_breach()
    return {"success": True, "state": ct.get_state()}


@app.post("/api/capital/reset-all")
async def reset_capital_all():
    cfg = get_settings()
    ct = get_capital_tracker()
    ct.reset_all(cfg.starting_capital)
    return {"success": True, "state": ct.get_state()}


# ── Data Health API ───────────────────────────────────────────────────────────

@app.get("/api/data-health")
async def get_data_health():
    return _data_health_state


@app.get("/api/slippage")
async def get_slippage():
    return slippage_tracker.get_slippage_stats()


@app.get("/api/performance")
async def get_performance():
    return await asyncio.to_thread(performance_tracker.compute_metrics)


@app.post("/api/performance/snapshot")
async def log_performance_snapshot():
    cfg = get_settings()
    await asyncio.to_thread(performance_tracker.log_daily_snapshot, cfg.starting_capital)
    return {"success": True}


@app.get("/api/system-health")
async def get_system_health():
    """Return heartbeat, app state, and uptime info for the system health UI card."""
    from watchdog import HEARTBEAT_FILE, STATE_FILE

    result = {
        "heartbeat_ok": False,
        "heartbeat_age_seconds": None,
        "app_state": "UNKNOWN",
        "app_state_time": None,
        "watchdog_active": False,
        "uptime_seconds": None,
        "pid": os.getpid(),
    }

    # Read heartbeat
    if HEARTBEAT_FILE.exists():
        try:
            hb = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
            last_beat = datetime.fromisoformat(hb["time"])
            if last_beat.tzinfo is None:
                last_beat = _IST.localize(last_beat)
            age = (datetime.now(_IST) - last_beat).total_seconds()
            result["heartbeat_ok"] = age < 120
            result["heartbeat_age_seconds"] = round(age, 1)
            if "uptime_seconds" in hb:
                result["watchdog_active"] = True
        except Exception as e:
            logger.warning(f"Error checking heartbeat in status API: {e}")

    # Read app state
    if STATE_FILE.exists():
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            result["app_state"] = st.get("state", "UNKNOWN")
            result["app_state_time"] = st.get("time")
            if "watchdog" in st.get("details", "").lower():
                result["watchdog_active"] = True
        except Exception:
            pass

    # Compute uptime from app state time
    if result["app_state"] == "RUNNING" and result["app_state_time"]:
        try:
            start = datetime.fromisoformat(result["app_state_time"])
            if start.tzinfo is None:
                start = _IST.localize(start)
            result["uptime_seconds"] = round((datetime.now(_IST) - start).total_seconds(), 0)
        except Exception:
            pass

    return result


@app.get("/api/download/performance-log")
async def download_performance_log():
    """Download the performance_log.csv file."""
    from starlette.responses import FileResponse
    csv_path = Path(get_settings().data_dir) / "performance_log.csv"
    if not csv_path.exists():
        return {"error": "No performance log yet"}
    return FileResponse(str(csv_path), media_type="text/csv", filename="performance_log.csv")


@app.get("/api/download/slippage-log")
async def download_slippage_log():
    """Download the slippage_log.csv file."""
    from starlette.responses import FileResponse
    csv_path = Path(get_settings().data_dir) / "slippage_log.csv"
    if not csv_path.exists():
        return {"error": "No slippage log yet"}
    return FileResponse(str(csv_path), media_type="text/csv", filename="slippage_log.csv")


# ── Global Market Context ────────────────────────────────────────────────────

@app.get("/api/global-markets")
async def get_global_markets():
    """Global indices and futures data (cached 5 min)."""
    from global_markets import fetch_global_markets
    return await asyncio.to_thread(fetch_global_markets)

@app.get("/api/market-context")
async def get_market_context():
    """Combined: global markets + bias + sentiment + fear & greed + VIX + expiry + composite."""
    from global_markets import get_market_context
    return await asyncio.to_thread(get_market_context)

@app.post("/api/market-context/refresh")
async def refresh_market_context():
    """Clear all market context caches and re-fetch fresh data."""
    from global_markets import clear_all_cache, get_market_context
    clear_all_cache()
    return await asyncio.to_thread(get_market_context)

@app.get("/api/india-vix")
async def get_india_vix():
    """India VIX from Dhan API."""
    from global_markets import fetch_india_vix
    return await asyncio.to_thread(fetch_india_vix)

@app.get("/api/expiry-today")
async def get_expiry_today():
    """Check which instruments have expiry today."""
    from global_markets import fetch_expiry_today
    return await asyncio.to_thread(fetch_expiry_today)

@app.get("/api/oi-analysis")
async def get_oi_analysis(instrument: str = "BANKNIFTY", expiry: str = None):
    """Open Interest analysis from Dhan option chain."""
    from global_markets import fetch_oi_analysis
    return await asyncio.to_thread(fetch_oi_analysis, instrument, expiry)

@app.get("/api/oi-expiry-list")
async def get_oi_expiry_list(instrument: str = "BANKNIFTY"):
    """Get available option expiry dates for an instrument."""
    from global_markets import fetch_oi_expiry_list
    return await asyncio.to_thread(fetch_oi_expiry_list, instrument)

@app.get("/api/options-context")
async def get_options_context(instrument: str = "BANKNIFTY", expiry: str = None, direction: str = None):
    """Options awareness context for Live Trading — strike recommendations, Greeks, IV, theta decay."""
    from global_markets import fetch_options_context
    return await asyncio.to_thread(fetch_options_context, instrument, expiry, direction)


# ── Research Pipeline API ────────────────────────────────────────────────────

_research_pull_task = None  # Background task reference for Phase 1 data pull
_research_pull_progress = {}  # Shared progress dict for Phase 1

@app.get("/api/research/status")
async def get_research_status():
    """Return pipeline status (which phases are complete)."""
    status_path = Path(__file__).parent.parent / "Research" / "pipeline_status.json"
    if not status_path.exists():
        return {"error": "Pipeline not initialized. Run scaffold first."}
    with open(status_path, "r") as f:
        return json.load(f)

@app.post("/api/research/scaffold")
async def scaffold_research():
    """Create Phase 0 folder structure + config files if not already present."""
    research_root = Path(__file__).parent.parent / "Research"
    dirs = [
        "config", "src/data", "src/analysis", "src/features",
        "data/raw", "data/clean", "data/meta", "reports"
    ]
    created = []
    for d in dirs:
        p = research_root / d
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(str(d))
    
    # Update pipeline status
    status_path = research_root / "pipeline_status.json"
    if status_path.exists():
        with open(status_path, "r") as f:
            status = json.load(f)
        phase0 = status.get("phases", {}).get("phase_0", {})
        phase0["status"] = "complete"
        steps = phase0.get("steps", {})
        steps["folder_structure"] = True
        if (research_root / "config" / "universe.yaml").exists():
            steps["universe_yaml"] = True
        if (research_root / "config" / "splits.yaml").exists():
            steps["splits_yaml"] = True
        steps["python_env"] = True  # Already set up
        steps["dhan_client_wrapper"] = (research_root / "src" / "data" / "dhan_research_client.py").exists()
        phase0["steps"] = steps
        status["phases"]["phase_0"] = phase0
        status["last_updated"] = datetime.now(_IST).isoformat()
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)
    
    return {"success": True, "dirs_created": created, "message": "Phase 0 scaffolding complete"}

@app.post("/api/research/run/phase1")
async def run_research_phase1(background_tasks=None):
    """
    Trigger Phase 1 data acquisition for all constituents.
    Runs as a background task because it takes minutes (Dhan rate limits).
    """
    global _research_pull_task, _research_pull_progress
    
    if not broker.is_connected():
        return {"error": "Broker not connected. Connect to Dhan first."}
    
    if _research_pull_task and not _research_pull_task.done():
        return {"error": "Phase 1 pull already in progress.", "progress": _research_pull_progress}
    
    import asyncio
    
    _research_pull_progress = {"status": "starting", "symbols_done": 0, "total": 0, "current": "", "errors": []}
    
    async def _run_pull():
        global _research_pull_progress
        import sys
        research_root = Path(__file__).parent.parent / "Research"
        sys.path.insert(0, str(research_root / "src"))
        
        try:
            from data.dhan_research_client import ResearchDataClient
            client = ResearchDataClient()
            _research_pull_progress["total"] = client._count_total_symbols()
            _research_pull_progress["status"] = "pulling"
            
            def on_symbol_done(symbol, result):
                _research_pull_progress["symbols_done"] += 1
                _research_pull_progress["current"] = symbol
                success = any(result.values()) if isinstance(result, dict) else False
                if not success:
                    _research_pull_progress["errors"].append(symbol)
                logger.info(f"Research pull: {symbol} done ({_research_pull_progress['symbols_done']}/{_research_pull_progress['total']})")
            
            # Run synchronously in thread to avoid blocking event loop
            import concurrent.futures
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                results = await loop.run_in_executor(pool, lambda: client.pull_all(callback=on_symbol_done))
            
            _research_pull_progress["status"] = "complete"
            _research_pull_progress["results"] = {
                k: {tf: ok for tf, ok in v.items()} 
                for k, v in results.items() 
                if isinstance(v, dict)
            }
            
            # Run corporate action check
            _research_pull_progress["status"] = "checking_corporate_actions"
            from data.corporate_actions import run_corporate_action_check
            with concurrent.futures.ThreadPoolExecutor() as pool:
                ca_results = await loop.run_in_executor(pool, run_corporate_action_check)
            _research_pull_progress["corporate_actions"] = ca_results
            
            # Run data quality report
            _research_pull_progress["status"] = "generating_quality_report"
            from data.data_quality import generate_data_quality_report
            with concurrent.futures.ThreadPoolExecutor() as pool:
                dq_results = await loop.run_in_executor(pool, generate_data_quality_report)
            _research_pull_progress["data_quality"] = dq_results.get("summary", {})
            
            # Clean and align data (Phase 1 Steps 4-7)
            _research_pull_progress["status"] = "cleaning_and_aligning"
            from data.clean_data import run_cleaning_pipeline
            with concurrent.futures.ThreadPoolExecutor() as pool:
                clean_results = await loop.run_in_executor(pool, run_cleaning_pipeline)
            _research_pull_progress["clean_data"] = clean_results
            
            _research_pull_progress["status"] = "done"
            logger.info("Research Phase 1 complete!")
            
        except Exception as e:
            logger.error(f"Research Phase 1 error: {e}", exc_info=True)
            _research_pull_progress["status"] = "error"
            _research_pull_progress["error_message"] = str(e)
    
    _research_pull_task = asyncio.create_task(_run_pull())
    
    return {"success": True, "message": "Phase 1 data pull started in background."}

@app.get("/api/research/phase1/status")
async def get_research_phase1_status():
    """Get current progress of Phase 1 data pull."""
    global _research_pull_progress
    return _research_pull_progress or {"status": "not_started"}

@app.get("/api/research/phase1/quality")
async def get_research_data_quality():
    """Get data quality report."""
    report_path = Path(__file__).parent.parent / "Research" / "reports" / "data_quality_report.json"
    if not report_path.exists():
        return {"error": "No data quality report yet. Run Phase 1 first."}
    with open(report_path, "r") as f:
        return json.load(f)

@app.get("/api/research/inventory")
async def get_research_data_inventory():
    """Get inventory of all downloaded research data files."""
    raw_dir = Path(__file__).parent.parent / "Research" / "data" / "raw"
    if not raw_dir.exists():
        return {"files": [], "total_size_mb": 0}
    
    files = []
    total_size = 0
    for f in sorted(raw_dir.glob("*.parquet")):
        size = f.stat().st_size
        total_size += size
        parts = f.stem.split("_")
        files.append({
            "name": f.name,
            "symbol": parts[0].upper() if parts else f.stem,
            "timeframe": parts[1] if len(parts) > 1 else "unknown",
            "size_kb": round(size / 1024, 1),
        })
    
    return {"files": files, "total_size_mb": round(total_size / (1024*1024), 2)}

@app.get("/api/research/notes")
async def get_research_notes():
    """Get research notes."""
    notes_path = Path(__file__).parent.parent / "Research" / "NOTES.md"
    if not notes_path.exists():
        return {"content": ""}
    return {"content": notes_path.read_text(encoding="utf-8")}

@app.post("/api/research/notes")
async def save_research_notes(req: dict):
    """Save research notes."""
    notes_path = Path(__file__).parent.parent / "Research" / "NOTES.md"
    content = req.get("content", "")
    notes_path.write_text(content, encoding="utf-8")
    return {"success": True}

@app.post("/api/research/pipeline/update")
async def update_pipeline_step(req: dict):
    """Update a specific pipeline phase/step status."""
    status_path = Path(__file__).parent.parent / "Research" / "pipeline_status.json"
    if not status_path.exists():
        return {"error": "Pipeline not initialized"}
    
    with open(status_path, "r") as f:
        status = json.load(f)
    
    phase_id = req.get("phase")  # e.g. "phase_1"
    step_id = req.get("step")    # e.g. "pull_banknifty_index"
    value = req.get("value", True)
    
    if phase_id and phase_id in status.get("phases", {}):
        phase = status["phases"][phase_id]
        if step_id and step_id in phase.get("steps", {}):
            phase["steps"][step_id] = value
        
        # Auto-update phase status based on steps
        steps = phase.get("steps", {})
        if all(steps.values()):
            phase["status"] = "complete"
        elif any(steps.values()):
            phase["status"] = "in_progress"
        else:
            phase["status"] = "not_started"
        
        status["phases"][phase_id] = phase
        status["last_updated"] = datetime.now(_IST).isoformat()
        
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)
        
        return {"success": True, "phase": phase_id, "status": phase["status"]}
    
    return {"error": f"Phase '{phase_id}' not found"}


@app.post("/api/research/run/phase2")
async def run_research_phase2():
    """Trigger Phase 2 exploratory analysis."""
    import sys
    import concurrent.futures
    research_root = Path(__file__).parent.parent / "Research"
    sys.path.insert(0, str(research_root / "src"))
    try:
        from analysis.phase2_exploration import run_exploratory_analysis
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(pool, run_exploratory_analysis)
        if "error" in results:
            return {"success": False, "error": results["error"]}
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"Research Phase 2 error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/api/research/phase2/data")
async def get_research_phase2_data():
    """Get Phase 2 exploration data for visualization."""
    report_path = Path(__file__).parent.parent / "Research" / "reports" / "phase2_exploration.json"
    if not report_path.exists():
        return {"error": "No Phase 2 data yet. Run Phase 2 first."}
    try:
        with open(report_path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"Failed to load Phase 2 data: {e}"}


@app.post("/api/research/run/phase3")
async def run_research_phase3():
    """Trigger Phase 3 PCA factor analysis."""
    import sys
    import concurrent.futures
    research_root = Path(__file__).parent.parent / "Research"
    sys.path.insert(0, str(research_root / "src"))
    try:
        from analysis.phase3_pca import run_pca_pipeline
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(pool, run_pca_pipeline)
        if "error" in results:
            return {"success": False, "error": results["error"]}
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"Research Phase 3 error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/api/research/phase3/data")
async def get_research_phase3_data():
    """Get Phase 3 PCA data for visualization."""
    report_path = Path(__file__).parent.parent / "Research" / "reports" / "phase3_pca.json"
    if not report_path.exists():
        return {"error": "No Phase 3 data yet. Run Phase 3 first."}
    try:
        with open(report_path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"Failed to load Phase 3 data: {e}"}


@app.post("/api/research/run/phase4")
async def run_research_phase4():
    """Trigger Phase 4 RMT analysis."""
    import sys
    import concurrent.futures
    research_root = Path(__file__).parent.parent / "Research"
    sys.path.insert(0, str(research_root / "src"))
    try:
        from analysis.phase4_rmt import run_rmt_analysis
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(pool, run_rmt_analysis)
        if "error" in results:
            return {"success": False, "error": results["error"]}
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"Research Phase 4 error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/api/research/phase4/data")
async def get_research_phase4_data():
    """Get Phase 4 RMT data for visualization."""
    report_path = Path(__file__).parent.parent / "Research" / "reports" / "phase4_rmt.json"
    if not report_path.exists():
        return {"error": "No Phase 4 data yet. Run Phase 4 first."}
    try:
        with open(report_path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"Failed to load Phase 4 data: {e}"}


@app.post("/api/research/run/phase5")
async def run_research_phase5():
    """Trigger Phase 5 residual analysis."""
    import sys
    import concurrent.futures
    research_root = Path(__file__).parent.parent / "Research"
    sys.path.insert(0, str(research_root / "src"))
    try:
        from analysis.phase5_residuals import run_residuals_analysis
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(pool, run_residuals_analysis)
        if "error" in results:
            return {"success": False, "error": results["error"]}
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"Research Phase 5 error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/api/research/phase5/data")
async def get_research_phase5_data():
    """Get Phase 5 residual data for visualization."""
    report_path = Path(__file__).parent.parent / "Research" / "reports" / "phase5_residuals.json"
    if not report_path.exists():
        return {"error": "No Phase 5 data yet. Run Phase 5 first."}
    try:
        with open(report_path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"Failed to load Phase 5 data: {e}"}


@app.post("/api/research/run/phase6")
async def run_research_phase6():
    """Trigger Phase 6 baseline analysis."""
    import sys
    import concurrent.futures
    research_root = Path(__file__).parent.parent / "Research"
    sys.path.insert(0, str(research_root / "src"))
    try:
        from analysis.phase6_baseline import run_baseline_pipeline
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(pool, run_baseline_pipeline)
        if "error" in results:
            return {"success": False, "error": results["error"]}
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"Research Phase 6 error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/api/research/phase6/data")
async def get_research_phase6_data():
    """Get Phase 6 baseline data for visualization."""
    report_path = Path(__file__).parent.parent / "Research" / "reports" / "phase6_baseline.json"
    if not report_path.exists():
        return {"error": "No Phase 6 data yet. Run Phase 6 first."}
    try:
        with open(report_path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"Failed to load Phase 6 data: {e}"}


@app.post("/api/research/run/phase7")
async def run_research_phase7():
    """Trigger Phase 7 feature engineering."""
    import sys
    import concurrent.futures
    research_root = Path(__file__).parent.parent / "Research"
    sys.path.insert(0, str(research_root / "src"))
    try:
        from features.build_features import build_feature_matrix
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(pool, build_feature_matrix)
        if "error" in results:
            return {"success": False, "error": results["error"]}
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"Research Phase 7 error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/api/research/phase7/data")
async def get_research_phase7_data():
    """Get Phase 7 features status/preview."""
    train_path = Path(__file__).parent.parent / "Research" / "data" / "clean" / "features_train.parquet"
    val_path = Path(__file__).parent.parent / "Research" / "data" / "clean" / "features_val.parquet"
    test_path = Path(__file__).parent.parent / "Research" / "data" / "clean" / "features_test.parquet"
    
    if not train_path.exists():
        return {"error": "No Phase 7 feature data yet. Run Phase 7 first."}
    try:
        df_preview = pd.read_parquet(train_path).head(5)
        columns = list(df_preview.columns)
        
        # Load only 'symbol' column for counting rows to optimize memory and speed
        df_train = pd.read_parquet(train_path, columns=["symbol"])
        df_val = pd.read_parquet(val_path, columns=["symbol"])
        df_test = pd.read_parquet(test_path, columns=["symbol"])
        
        return {
            "train_rows": len(df_train),
            "val_rows": len(df_val),
            "test_rows": len(df_test),
            "columns": columns,
            "preview": df_preview.fillna("").to_dict("records")
        }
    except Exception as e:
        return {"error": f"Failed to load Phase 7 data: {e}"}


@app.post("/api/research/run/phase8")
async def run_research_phase8():
    """Trigger Phase 8 ML training and evaluation."""
    import sys
    import concurrent.futures
    research_root = Path(__file__).parent.parent / "Research"
    sys.path.insert(0, str(research_root / "src"))
    try:
        from analysis.phase8_ml import run_ml_comparison
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(pool, run_ml_comparison)
        if "error" in results:
            return {"success": False, "error": results["error"]}
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"Research Phase 8 error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/api/research/phase8/data")
async def get_research_phase8_data():
    """Get Phase 8 ML comparison results for visualization."""
    report_path = Path(__file__).parent.parent / "Research" / "reports" / "phase8_ml_comparison.json"
    if not report_path.exists():
        return {"error": "No Phase 8 data yet. Run Phase 8 first."}
    try:
        with open(report_path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"Failed to load Phase 8 data: {e}"}


@app.post("/api/research/run/phase9")
async def run_research_phase9():
    """Trigger Phase 9 Walk-Forward Backtesting."""
    import sys
    import concurrent.futures
    research_root = Path(__file__).parent.parent / "Research"
    sys.path.insert(0, str(research_root / "src"))
    try:
        from analysis.phase9_walkforward import run_walkforward_backtest
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = await loop.run_in_executor(pool, run_walkforward_backtest)
        if "error" in results:
            return {"success": False, "error": results["error"]}
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"Research Phase 9 error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@app.get("/api/research/phase9/data")
async def get_research_phase9_data():
    """Get Phase 9 Walk-Forward Backtesting results for visualization."""
    report_path = Path(__file__).parent.parent / "Research" / "reports" / "phase9_walkforward.json"
    if not report_path.exists():
        return {"error": "No Phase 9 data yet. Run Phase 9 first."}
    try:
        with open(report_path, "r") as f:
            return json.load(f)
    except Exception as e:
        return {"error": f"Failed to load Phase 9 data: {e}"}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _send_telegram_direct(message: str, bot_token: str, chat_id: str):
    """Send a Telegram message directly using HTTP POST, allowing custom API URL override."""
    import os
    import requests
    
    api_url = os.environ.get("TELEGRAM_API_URL", "https://api.telegram.org").strip().rstrip("/")
    url = f"{api_url}/bot{bot_token}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": message,
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()


def _send_telegram_alert_wrapper(message: str, bot_token: str, chat_id: str):
    """Sends a Telegram alert. Uses proxy direct path if TELEGRAM_API_URL is configured,
    otherwise falls back to Tradehull's send_telegram_alert, with a final fallback
    to direct HTTP POST.
    """
    import os
    # 1. Check if a proxy is configured (e.g. on Hugging Face Spaces)
    if os.environ.get("TELEGRAM_API_URL"):
        try:
            _send_telegram_direct(message, bot_token, chat_id)
            return
        except Exception as e:
            logger.warning(f"Telegram direct send failed: {e}")
        
    # 2. Try the normal Tradehull connection (standard local desktop behavior)
    try:
        if broker.is_connected():
            broker.get_tsl().send_telegram_alert(
                message=message,
                receiver_chat_id=chat_id,
                bot_token=bot_token,
            )
            return
    except Exception as e:
        logger.warning(f"Tradehull telegram alert failed, trying direct HTTP fallback: {e}")
        
    # 3. Fallback to direct HTTP POST to api.telegram.org
    _send_telegram_direct(message, bot_token, chat_id)


def send_telegram_entry_alert(sig: dict, result: dict):
    """Send a detailed styled Telegram message for entry orders matching the screenshot design."""
    cfg = get_settings()
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        return
        
    direction = result["direction"]
    symbol = result["symbol"]
    qty = result["qty"]
    trade_mode = cfg.trade_mode
    instrument = cfg.instrument
    
    # Parse options strike from symbol, with fallback to sig-level override
    from journal_manager import parse_option_symbol
    strike, opt_type = parse_option_symbol(symbol)
    
    # Fallback: use explicit strike info from signal dict (e.g. from test alert or live order)
    if not strike and sig.get("opt_strike"):
        strike = sig["opt_strike"]
        opt_type = sig.get("opt_type", "CE" if direction == "LONG" else "PE")
    # Last fallback: infer option type from direction if symbol clearly is an option
    if not opt_type and trade_mode == "OPTIONS":
        opt_type = "CE" if direction == "LONG" else "PE"
    
    # Header tag matching screenshot format
    scalp_type = "OPTIONS" if trade_mode == "OPTIONS" else "INDEX"
    msg_type = "PAPER" if not cfg.auto_trade else "LIVE"
    
    # Option supplementary info
    opt_premium = sig.get("opt_premium", 0.0)
    opt_symbol  = sig.get("opt_symbol", symbol)
    option_line = ""
    if trade_mode == "OPTIONS":
        prem_str = f" | Premium: Rs.{opt_premium:,.2f}" if opt_premium > 0 else ""
        sym_display = opt_symbol or symbol
        option_line = f"Option: {sym_display}{prem_str}\n"
    strike_str = f"Strike: {strike} {opt_type}\n" if strike else ""

    # Confidence score -- only meaningful for multi-agent-scored strategies;
    # plain strategy-based kernels always report weighted_score=0.0, so
    # "Score: 0/15" is boilerplate, not information. Omit when not scored.
    score_val = int(sig.get("weighted_score", 0.0) * 15)
    score_str = f" | Score: {score_val}/15" if sig.get("weighted_score", 0.0) > 0 else ""

    # Score breakdown details
    breakdown_parts = []
    if sig.get("macro_bias"):
        breakdown_parts.append(f"Macro Bias ({int(cfg.agent_weight_macro*100)}%): {sig['macro_bias']}")
    if sig.get("structure_confidence") is not None:
        breakdown_parts.append(f"Structure ({int(cfg.agent_weight_structure*100)}%): {int(sig['structure_confidence']*100)}%")
    if sig.get("momentum_confidence") is not None:
        breakdown_parts.append(f"Momentum ({int(cfg.agent_weight_momentum*100)}%): {int(sig['momentum_confidence']*100)}%")
    if sig.get("trigger_quality") is not None:
        breakdown_parts.append(f"Trigger Timing ({int(cfg.agent_weight_trigger*100)}%): {int(sig['trigger_quality']*100)}%")
    if sig.get("volume_confirms") is not None:
        breakdown_parts.append(f"Volume Confirm ({int(cfg.agent_weight_volume*100)}%): {'YES' if sig['volume_confirms'] else 'NO'}")
    if sig.get("memory_win_rate") is not None:
        breakdown_parts.append(f"Memory Win Rate ({int(cfg.agent_weight_memory*100)}%): {int(sig['memory_win_rate']*100)}%")

    breakdown_str = "\n".join(f"• {p}" for p in breakdown_parts)

    # Detailed reasons from agents. Simple strategy-based kernels (Time-Gated,
    # Regime V1, etc.) don't populate this beyond a generic "Source: STRATEGY"
    # placeholder -- that's not an actual reason, so treat it the same as "no
    # reasons" rather than printing it as if it were meaningful content.
    real_reasons = [
        r for r in (sig.get("reasons") or [])
        if str(r).strip().lower() != "source: strategy"
    ]
    reasons_str = "\n".join(f"• {r}" for r in real_reasons[:5])

    # Build Markdown message — index prices as main, option info as supplementary.
    # Consensus Breakdown / Agent Reasons only apply to the multi-agent-scored
    # strategies; for plain strategy-based kernels both are always empty, so
    # skip those sections entirely instead of showing an empty header (2026-08-25).
    msg_parts = [
        f"⚡📝 {msg_type} — {instrument} {direction} {scalp_type}",
        f"Time: {datetime.now(_IST).strftime('%H:%M:%S')} IST{score_str}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"{strike_str}{option_line}"
        f"Index Entry: Rs.{sig.get('entry', 0.0):,.2f} | Qty: {qty}\n"
        f"SL: Rs.{sig.get('sl', 0.0):,.2f}\n"
        f"Target 1: Rs.{sig.get('target1', 0.0):,.2f} (Breakeven Trail)\n"
        f"Target 2: Rs.{sig.get('target2', 0.0):,.2f}",
    ]
    if breakdown_str:
        msg_parts.append(f"📈 *Consensus Breakdown:*\n{breakdown_str}")
    if reasons_str:
        msg_parts.append(f"🤖 *Agent Reasons:*\n{reasons_str}")
    footer_lines = []
    if sig.get("ml_prob", 0.0) > 0:
        footer_lines.append(f"• ML Prob: {int(sig['ml_prob']*100)}% (Threshold: {cfg.ml_threshold:.2f})")
    footer_lines.append(f"• Entry Slippage: Rs.{result.get('entry_slippage', 0.0):,.2f}")
    msg_parts.append("\n".join(footer_lines))
    msg = "\n\n".join(msg_parts)
    
    try:
        _send_telegram_alert_wrapper(
            message=msg,
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
        )
    except Exception as e:
        logger.error(f"Telegram entry alert failed: {e}", exc_info=True)


def send_telegram_exit_alert(pos, exit_price: float, reason: str, pnl: float, index_exit_price: float = 0.0):
    """Send Telegram exit alert. index_exit_price is the INDEX level; exit_price may be option premium for PnL context."""
    cfg = get_settings()
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        return

    # Use index prices for display; option premium only for PnL% calculation
    idx_entry = getattr(pos, 'index_entry_price', 0.0) or pos.entry_price
    idx_exit  = index_exit_price if index_exit_price > 0 else exit_price

    pnl_percent = (pnl / (pos.entry_price * pos.qty)) * 100 if pos.entry_price > 0 else 0.0
    pnl_sign  = "+" if pnl >= 0 else ""
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    msg_type  = "PAPER" if not cfg.auto_trade else "LIVE"

    # Option premium line (supplementary)
    opt_prem_line = ""
    if pos.trade_mode == "OPTIONS" and exit_price != idx_exit:
        opt_prem_line = f"Option Premium: Entry Rs.{pos.entry_price:,.2f} -> Exit Rs.{exit_price:,.2f}\n"

    tm = get_trade_manager()
    today_gross = tm.day_stats.gross_pnl

    msg = (
        f"🚪 {pnl_emoji} {msg_type} — EXIT {pos.instrument} {pos.direction}\n"
        f"Time: {datetime.now(_IST).strftime('%H:%M:%S')} IST\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Contract: `{pos.symbol}`\n"
        f"Index: Entry Rs.{idx_entry:,.2f} -> Exit Rs.{idx_exit:,.2f}\n"
        f"{opt_prem_line}"
        f"Quantity: {pos.qty} | Reason: `{reason}`\n\n"
        f"💰 *PnL Realized:*\n"
        f"• Net Trade PnL: *{pnl_sign}Rs.{pnl:,.2f}* ({pnl_sign}{pnl_percent:.2f}%)\n"
        f"• Today's Gross PnL: *Rs.{today_gross:,.2f}*\n"
        f"• Entry Slippage: Rs.{getattr(pos, 'entry_slippage', 0.0):,.2f}\n"
    )
    
    try:
        _send_telegram_alert_wrapper(
            message=msg,
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
        )
    except Exception as e:
        logger.error(f"Telegram exit alert failed: {e}", exc_info=True)


def _sync_dhan_positions(cfg, tm, latest_candle_ts: str = None):
    """
    Synchronize the TradeManager's active position with the broker's actual positions.
    - If TradeManager has no position, but Dhan has an open position, we import it.
    - If TradeManager has a live position (starts with DHAN_SYNC_, SIG_, or TEST_), but Dhan has no matching open position, we close it.
    """
    if not broker.is_connected():
        return

    try:
        df = broker.get_positions()
        
        # Find open positions in Dhan (netQty != 0)
        open_rows = []
        if df is not None and not df.empty:
            net_qty_col = None
            for col in ['netQty', 'net_qty', 'netQtyValue']:
                if col in df.columns:
                    net_qty_col = col
                    break
            if net_qty_col:
                df[net_qty_col] = pd.to_numeric(df[net_qty_col], errors='coerce').fillna(0)
                open_rows = df[df[net_qty_col] != 0].to_dict(orient="records")

        # 1. No position tracked locally, but Dhan has an open position -> Import it
        if tm.position is None:
            # Filter open rows to only those matching cfg.instrument
            matching_rows = []
            for row in open_rows:
                sym = row.get('tradingSymbol', '')
                inst = cfg.instrument
                for possible_inst in ("BANKNIFTY", "NIFTY", "SENSEX", "CRUDEOIL"):
                    if possible_inst in sym.upper():
                        inst = possible_inst
                        break
                if inst == cfg.instrument:
                    matching_rows.append(row)

            if matching_rows:
                row = matching_rows[0]
                symbol = row['tradingSymbol']

                # Skip re-import if this symbol was closed locally within the last 5 minutes
                # (prevents re-import loop while Dhan still shows the position as open)
                global _recently_closed_symbols
                if symbol in _recently_closed_symbols:
                    age_secs = (datetime.now(_IST) - _recently_closed_symbols[symbol]).total_seconds()
                    if age_secs < 30:
                        return
                    else:
                        del _recently_closed_symbols[symbol]

                net_qty = int(row[net_qty_col])
                qty = abs(net_qty)
                entry_price = float(row.get('buyAvg', 0.0) or row.get('costPrice', 0.0) or 0.0)
                if entry_price == 0.0:
                    entry_price = float(row.get('sellAvg', 0.0) or 0.0)

                is_option = any(term in symbol.upper() for term in ('-CE', '-PE', ' CALL', ' PUT'))
                trade_mode = "OPTIONS" if is_option else "INDEX"

                # For options: determine market direction from CE/PE, not raw net_qty sign
                # (buying PE = bearish SHORT on underlying, even though net_qty is positive)
                if is_option:
                    direction = "LONG" if ('-CE' in symbol.upper() or 'CALL' in symbol.upper()) else "SHORT"
                else:
                    direction = "LONG" if net_qty > 0 else "SHORT"

                # Resolve instrument
                instrument = cfg.instrument
                for inst in ("BANKNIFTY", "NIFTY", "SENSEX", "CRUDEOIL"):
                    if inst in symbol.upper():
                        instrument = inst
                        break

                # Compute SL/T1/T2 from index LTP + ATR at import time
                index_ltp = broker.get_ltp(instrument) or entry_price
                atr = _last_signal.get("atr_5m", index_ltp * 0.003) if _last_signal else index_ltp * 0.003
                sl_mult  = cfg.atr_sl_mult
                t1_mult  = cfg.atr_t1_mult
                t2_mult  = cfg.atr_t2_mult
                if direction == "LONG":
                    sl_price  = round(index_ltp - atr * sl_mult, 2)
                    t1_price  = round(index_ltp + atr * t1_mult, 2)
                    t2_price  = round(index_ltp + atr * t2_mult, 2)
                else:
                    sl_price  = round(index_ltp + atr * sl_mult, 2)
                    t1_price  = round(index_ltp - atr * t1_mult, 2)
                    t2_price  = round(index_ltp - atr * t2_mult, 2)

                pos = ActivePosition(
                    instrument        = instrument,
                    symbol            = symbol,
                    exchange          = row.get('exchangeSegment', 'NSE_FNO'),
                    direction         = direction,
                    entry_price       = entry_price,
                    sl                = sl_price,
                    target1           = t1_price,
                    target2           = t2_price,
                    qty               = qty,
                    order_id          = "DHAN_SYNC_" + symbol,
                    entry_time        = datetime.now(_IST).isoformat(),
                    trade_mode        = trade_mode,
                    index_entry_price = index_ltp,
                    highest_since_entry = index_ltp,
                    lowest_since_entry = index_ltp,
                    entry_atr         = atr,
                    strategy          = "broker_sync",
                )
                tm.open_position(pos)
                logger.info(f"Imported open position from Dhan: {direction} {symbol} qty={qty} SL={sl_price} T1={t1_price} T2={t2_price}")
                
        # 2. Position is tracked locally, but it's a live position and Dhan has no open position -> Close it
        elif tm.position:
            pos = tm.position
            # Only sync live/Dhan positions (exclude paper trades)
            if not pos.order_id.startswith("PAPER_"):
                dhan_open = False
                dhan_row = None
                for row in open_rows:
                    if row['tradingSymbol'] == pos.symbol:
                        dhan_open = True
                        dhan_row = row
                        break
                        
                if not dhan_open:
                    # Sync close — Dhan confirms position is gone, close locally
                    realized_pnl = 0.0
                    if df is not None and not df.empty:
                        matched_rows = df[df['tradingSymbol'] == pos.symbol].to_dict(orient="records")
                        if matched_rows:
                            matched_row = matched_rows[0]
                            realized_pnl = float(matched_row.get('realizedProfit', 0.0) or matched_row.get('realisedProfit', 0.0) or 0.0)

                    # Save index LTP first (for journal/display), then override with option premium for PnL
                    index_exit_ltp = broker.get_ltp(pos.instrument) or pos.index_entry_price or pos.entry_price
                    exit_ltp = index_exit_ltp  # start with index; override for OPTIONS PnL
                    if pos.trade_mode == "OPTIONS" and pos.symbol and not pos.symbol.endswith("INDEX"):
                        try:
                            fetched = broker.get_option_ltp(pos.symbol)
                            if fetched > 0:
                                exit_ltp = fetched  # option premium used for PnL calc
                        except Exception:
                            pass

                    global _active_trade_signal, _exit_signal_logged_for_position, _exit_telegram_sent_for_position
                    _recently_closed_symbols[pos.symbol] = datetime.now(_IST)
                    _exit_signal_logged_for_position = ""
                    _exit_telegram_sent_for_position = ""
                    rec = tm.close_position(exit_ltp, "DHAN_SYNC_EXIT", pnl_override=realized_pnl)
                    if cfg.strategy in _COOLDOWN_BARS and rec.get("pnl", 0.0) <= 0.0:
                        record_cooldown_loss(pos.direction)
                    _active_trade_signal = {}

                    # Log exit signal to history log (use current IST time as the dedup key here
                    # since this is a broker-confirmed event, not a model signal)
                    try:
                        exit_sig = {
                            "signal": "LONG_EXIT" if pos.direction == "LONG" else "SHORT_EXIT",
                            "time": _format_ist_timestamp(latest_candle_ts),
                            "entry": exit_ltp,
                            "close": exit_ltp,
                            "strategy": getattr(pos, "strategy", cfg.strategy),
                            "reason": "DHAN_SYNC_EXIT",
                            "reasons": ["Exit synchronized from Dhan broker portal"]
                        }
                        _add_signal_to_history(exit_sig, source="broker_sync")
                        signal_journal_manager.close_entry(
                            exit_sig, pos.instrument or cfg.instrument,
                            "DHAN_SYNC_EXIT", index_price=index_exit_ltp
                        )
                    except Exception as ex_err:
                        logger.error(f"Failed to log sync exit signal: {ex_err}")
                    
                    # Update Trading Journal (index_exit_ltp for display consistency)
                    try:
                        import pytz
                        from journal_manager import close_journal_entry
                        
                        exit_time_dt = datetime.now(_IST)
                        if latest_candle_ts:
                            try:
                                parsed = pd.to_datetime(latest_candle_ts)
                                exit_time_dt = parsed.to_pydatetime()
                                if exit_time_dt.tzinfo is None:
                                    exit_time_dt = _IST.localize(exit_time_dt)
                                else:
                                    exit_time_dt = exit_time_dt.astimezone(_IST)
                            except Exception:
                                pass

                        close_journal_entry(
                            symbol=pos.symbol,
                            exit_price=index_exit_ltp if index_exit_ltp > 0 else exit_ltp,
                            exit_time=exit_time_dt.strftime("%Y-%m-%d %H:%M:%S"),
                            exit_reason="DHAN_SYNC_EXIT",
                            pnl=realized_pnl,
                            order_id=pos.order_id
                        )
                    except Exception as e:
                        logger.error(f"Failed to close journal entry on sync exit: {e}")

                    logger.info(f"Closed local position {pos.symbol} because it was exited on Dhan.")
                    _chart_signals_cache.clear()
                else:
                    # Update current P&L directly from Dhan's actual unrealized profit (very accurate!)
                    unrealized_profit = float(dhan_row.get('unrealizedProfit', 0.0) or dhan_row.get('unrealisedProfit', 0.0) or 0.0)
                    pos.current_pnl = unrealized_profit

    except Exception as e:
        logger.error(f"Error synchronizing Dhan positions: {e}", exc_info=True)


def _build_alert_sig(sig: dict, cfg, symbol: str, direction: str) -> dict:
    """Build alert signal dict. Index prices are kept as-is; option premium added as supplementary field."""
    alert_sig = sig.copy()

    if cfg.trade_mode == "OPTIONS":
        # Fetch live option premium — stored as supplementary info, NOT replacing index prices
        opt_premium = 0.0
        if broker.is_connected() and symbol:
            try:
                opt_premium = broker.get_option_ltp(symbol)
            except Exception as e:
                logger.warning(f"Could not fetch option LTP for alert: {e}")

        if opt_premium > 0:
            alert_sig["opt_premium"] = opt_premium

        alert_sig["opt_symbol"] = symbol

        from journal_manager import parse_option_symbol
        strike, opt_type = parse_option_symbol(symbol)
        alert_sig["opt_strike"] = strike
        alert_sig["opt_type"] = opt_type
        # Index prices (entry/sl/target1/target2) remain unchanged — they are the index levels

    return alert_sig

def _trace_id(strategy_id: str, direction: str, sig_time) -> str:
    """Short, stable id for one signal's end-to-end lifecycle. Deterministic
    from (strategy, direction, time) -- the same natural key already used for
    signal dedup elsewhere -- so independent code paths that touch the "same"
    signal (detection, Telegram, order execution, broker result) all produce
    the identical id without needing to pass a generated value between them.
    `grep 'TRACE\\[<id>\\]' backend/logs/app.log` reconstructs the full
    lifecycle of any one trade signal in order. Built 2026-08-23 after a real
    incident (a stale signal fired a live order the moment auto_trade was
    turned on) took far longer to diagnose than it should have because no
    single log line tied detection -> Telegram -> order -> broker result
    together for the same signal."""
    import hashlib
    key = f"{strategy_id}|{direction}|{sig_time}"
    return hashlib.md5(key.encode()).hexdigest()[:8]


def _trace(trace_id: str, stage: str, detail: str = "") -> None:
    """One standardized, greppable log line per pipeline stage."""
    logger.info(f"TRACE[{trace_id}] {stage}: {detail}" if detail else f"TRACE[{trace_id}] {stage}")


def _signal_age_minutes(sig_time) -> Optional[float]:
    """Age of a signal's timestamp in minutes, or None if unparseable.
    Shared by both the auto-trade order-execution freshness gate and the
    Telegram alert freshness gate below -- previously only order execution
    had this check, so a catch-up burst of historical/replay signals (e.g.
    after a data gap) correctly skipped placing orders but still fired a
    Telegram alert for every stale signal as if it were live. Both paths
    now use the same >10-minute cutoff."""
    try:
        parsed = pd.to_datetime(sig_time)
        if hasattr(parsed, "tzinfo") and parsed.tzinfo is not None:
            parsed = parsed.tz_convert("Asia/Kolkata").tz_localize(None)
        now_naive = datetime.now(_IST).replace(tzinfo=None)
        return (now_naive - parsed).total_seconds() / 60.0
    except Exception:
        return None


def _check_data_staleness(frames: dict, cfg) -> None:
    """Check if candle data is stale and update health state."""
    global _data_health_state
    import pytz
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    _data_health_state["last_check_time"] = now_ist.isoformat()

    # Bypassed if threshold is <= 0 (e.g. for testing outside of market hours)
    if cfg.data_stale_threshold_min <= 0:
        _data_health_state["is_stale"] = False
        _data_health_state["broker_failures"] = 0
        return

    if not frames:
        _data_health_state["broker_failures"] += 1
        if _data_health_state["broker_failures"] >= 3:
            if not _data_health_state["is_stale"]:
                logger.error("DATA STALE: No frames returned for 3+ consecutive polls")
                _data_health_state["is_stale"] = True
                try:
                    _send_telegram_alert_wrapper(
                        "\u26a0\ufe0f DATA STALE: No market data received for 3+ polls. Trading paused.",
                        cfg.telegram_bot_token, cfg.telegram_chat_id
                    )
                except Exception:
                    pass
        return

    # Reset broker failure count on successful fetch
    _data_health_state["broker_failures"] = 0

    # Check the 5-min candle (primary trading timeframe)
    df_5 = frames.get("5")
    if df_5 is not None and len(df_5) > 0:
        last_ts = df_5["timestamp"].iloc[-1]
        if hasattr(last_ts, "tzinfo") and last_ts.tzinfo is not None:
            last_candle_ist = last_ts.astimezone(pytz.timezone("Asia/Kolkata"))
        else:
            # The last candle timestamp is already naive IST
            last_candle_ist = pytz.timezone("Asia/Kolkata").localize(
                pd.Timestamp(last_ts).to_pydatetime()
            )
        staleness_sec = (now_ist - last_candle_ist).total_seconds()
        threshold_sec = cfg.data_stale_threshold_min * 60

        _data_health_state["last_candle_time"] = last_candle_ist.isoformat()
        _data_health_state["staleness_seconds"] = int(staleness_sec)

        was_stale = _data_health_state["is_stale"]
        if staleness_sec > threshold_sec:
            # Hysteresis: require _STALE_CONFIRM_POLLS consecutive stale readings
            # before flagging/alerting. A single borderline poll (e.g. right at a
            # candle boundary, or mid-HTF-cache-cycle) is normal, not a real
            # outage -- alerting on every such blip is what caused this check to
            # get disabled entirely in the past. Only sustained staleness counts.
            _data_health_state["stale_poll_count"] += 1
            if _data_health_state["stale_poll_count"] >= _STALE_CONFIRM_POLLS:
                _data_health_state["is_stale"] = True
                if not was_stale:
                    logger.warning(
                        f"DATA STALE: Last 5min candle is {staleness_sec/60:.1f} min old "
                        f"(threshold: {cfg.data_stale_threshold_min} min, confirmed over "
                        f"{_data_health_state['stale_poll_count']} consecutive polls). "
                        "To disable this check for off-hours testing, set data_stale_threshold_min to 0 in settings."
                    )
                    try:
                        _send_telegram_alert_wrapper(
                            f"\u26a0\ufe0f DATA STALE: Last candle {staleness_sec/60:.1f} min old. Check broker connection.",
                            cfg.telegram_bot_token, cfg.telegram_chat_id
                        )
                    except Exception:
                        pass
        else:
            _data_health_state["stale_poll_count"] = 0
            _data_health_state["is_stale"] = False
            if was_stale:
                logger.info("Data freshness restored")


_htf_cache = {}
_rest_5m_cache: dict = {}  # instrument -> REST 5m base (volume-bearing)       # cached higher-TF frames: {instrument: {"1D": df, "60": df, "15": df}}
_htf_cache_ts = 0.0   # last time HTF were fetched
_htf_poll_count = 0   # cycle counter for periodic HTF refresh

def _fetch_5m_rest_with_freshness_retry(instrument: str, from_d: str, today: str, now_ist) -> Optional[pd.DataFrame]:
    """Fetch 5m candles via the historical REST API and, if the result is
    already >7 minutes stale (matching the live-feed path's freshness guard
    at _fetch_all_frames' "Freshness guard" block above), retry once after a
    short pause -- covers transient lag right at a candle-close boundary.
    Always logs if the result is still stale after the retry, so staleness is
    visible in logs/health-state regardless of what data_stale_threshold_min
    is set to. This closes the gap where only the live-feed-merge path had
    any freshness handling; the pure-REST fallback (live feed down, or not
    enough live candles yet) previously had none at all."""
    import time as _time_mod

    def _age_min(df):
        if df is None or len(df) == 0:
            return None
        last_ts = pd.to_datetime(df["timestamp"].max())
        now_naive = datetime.now(_IST).replace(tzinfo=None)
        return (now_naive - last_ts).total_seconds() / 60.0

    df = broker.get_historical_data(instrument, "5", from_d, today)
    age = _age_min(df)
    if age is not None and age > 7:
        logger.debug(f"5m REST fetch stale ({age:.1f} min old) — retrying once")
        _time_mod.sleep(1.0)
        retry_df = broker.get_historical_data(instrument, "5", from_d, today)
        retry_age = _age_min(retry_df)
        if retry_age is not None and (age is None or retry_age < age):
            df, age = retry_df, retry_age
        if age is not None and age > 7:
            logger.warning(
                f"5m REST data still {age:.1f} min old after retry — "
                f"broker's historical API itself appears delayed, not an app-side gap."
            )
    return df


def _fetch_all_frames(instrument: str) -> dict:
    """Fetch all timeframes needed for the strategy.

    Optimization: Daily/60m/15m data changes slowly, so we cache them and
    only re-fetch every 5th cycle (~75s). The fast-moving 5m and 1m frames
    are always fetched fresh.
    """
    import time as _time_mod
    import pytz
    global _htf_cache, _htf_cache_ts, _htf_poll_count, _rest_5m_cache

    kolkata_tz = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(kolkata_tz)
    today = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")

    frames = {}
    _htf_poll_count += 1

    # Higher timeframes: refresh every 5th cycle (~75s) or on first run
    htf_stale = (_htf_poll_count % 5 == 1) or not _htf_cache.get(instrument)

    if htf_stale:
        # Refresh the REST 5m base too — it carries exchange volume (live tick
        # candles for indices don't), keeping live evaluation identical to
        # what the backtest engine sees.
        try:
            _from5 = (now_ist - timedelta(days=30)).strftime("%Y-%m-%d")
            _df5 = broker.get_historical_data(instrument, "5", _from5, today)
            if _df5 is not None and len(_df5) >= 30:
                _rest_5m_cache[instrument] = _df5
            _time_mod.sleep(0.3)
        except Exception as _r5_err:
            logger.debug(f"REST 5m base refresh failed: {_r5_err}")
        for tf_key, days in [("1D", 1000), ("60", 120), ("15", 60)]:
            tf_dhan = "DAY" if tf_key == "1D" else tf_key
            from_d = (now_ist - timedelta(days=days)).strftime("%Y-%m-%d")
            df = broker.get_historical_data(instrument, tf_dhan, from_d, today)
            if df is not None and len(df) >= 30:
                frames[tf_key] = df
            _time_mod.sleep(0.3)  # 0.3s throttle (Dhan allows ~10 req/s, retry handles DH-904)
        _htf_cache[instrument] = {k: v for k, v in frames.items() if k in ("1D", "60", "15")}
        _htf_cache_ts = _time_mod.time()
    else:
        # Reuse cached HTF data
        frames.update(_htf_cache.get(instrument, {}))

    # Fast timeframes: prefer live feed candles (near-instant) with historical API fallback
    _feed = get_live_feed()
    _feed_ok = (_feed.is_running
                and getattr(_feed, "_instrument", None) == instrument
                and len(_feed.candle_5m.candles) >= 50)
    if _feed_ok:
        # Use live feed candles — already up-to-date from WebSocket ticks
        live_5m = _feed.get_live_candles("5")
        if live_5m is not None and len(live_5m) >= 50:
            # Merge: REST base (has exchange volume, identical to backtest
            # data) + live-built candles newer than the REST base's last bar.
            _rest_base = _rest_5m_cache.get(instrument)
            if _rest_base is not None and len(_rest_base) > 0:
                _last_rest_ts = _rest_base["timestamp"].max()
                _tail = live_5m[live_5m["timestamp"] > _last_rest_ts]
                frames["5"] = pd.concat([_rest_base, _tail], ignore_index=True)
            else:
                frames["5"] = live_5m
            logger.debug(f"Using live feed 5m candles ({len(live_5m)} bars)")

            # ── Freshness guard ──────────────────────────────────────────
            # If the merged frame's newest bar is older than ~7 minutes, the
            # live tail isn't contributing (feed gap / rejected ticks / bad
            # stamps). Don't wait for the next HTF cycle — force-refresh the
            # REST base NOW so signals never stall on a 25-minute cadence.
            try:
                _last5_ts = pd.to_datetime(frames["5"]["timestamp"].max())
                _now_naive = datetime.now(_IST).replace(tzinfo=None)
                _age_min = (_now_naive - _last5_ts).total_seconds() / 60.0
                if _age_min > 7:
                    logger.warning(
                        f"5m frame stale ({_age_min:.1f} min old) despite live feed — "
                        f"forcing REST refresh")
                    _from5f = (now_ist - timedelta(days=30)).strftime("%Y-%m-%d")
                    _df5f = broker.get_historical_data(instrument, "5", _from5f, today)
                    if _df5f is not None and len(_df5f) >= 30:
                        _rest_5m_cache[instrument] = _df5f
                        _last_rest_ts = _df5f["timestamp"].max()
                        _tail = live_5m[live_5m["timestamp"] > _last_rest_ts]
                        frames["5"] = pd.concat([_df5f, _tail], ignore_index=True)
            except Exception as _fg_err:
                logger.debug(f"5m freshness guard error: {_fg_err}")
        else:
            # Fallback to historical API
            from_d = (now_ist - timedelta(days=30)).strftime("%Y-%m-%d")
            df = _fetch_5m_rest_with_freshness_retry(instrument, from_d, today, now_ist)
            if df is not None and len(df) >= 30:
                frames["5"] = df
            _time_mod.sleep(0.3)

        live_1m = _feed.get_live_candles("1")
        if live_1m is not None and len(live_1m) >= 30:
            frames["1"] = live_1m
            logger.debug(f"Using live feed 1m candles ({len(live_1m)} bars)")
        else:
            from_d = (now_ist - timedelta(days=5)).strftime("%Y-%m-%d")
            df = broker.get_historical_data(instrument, "1", from_d, today)
            if df is not None and len(df) >= 30:
                frames["1"] = df
            _time_mod.sleep(0.3)
    else:
        # No live feed — fetch from historical API (original behavior)
        for tf_key, days in [("5", 30), ("1", 5)]:
            from_d = (now_ist - timedelta(days=days)).strftime("%Y-%m-%d")
            if tf_key == "5":
                df = _fetch_5m_rest_with_freshness_retry(instrument, from_d, today, now_ist)
            else:
                df = broker.get_historical_data(instrument, tf_key, from_d, today)
            if df is not None and len(df) >= 30:
                frames[tf_key] = df
            _time_mod.sleep(0.3)

    return frames


def _fetch_frames_range(instrument: str, from_date: str, to_date: str) -> dict:
    """Fetch multi-TF data for backtest date range."""
    import time
    from datetime import datetime, timedelta
    frames = {}
    
    # Add 1 day to to_date because Dhan's historical API is exclusive of the end date
    try:
        to_dt = datetime.strptime(to_date, "%Y-%m-%d")
        to_date_inclusive = (to_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        to_date_inclusive = to_date

    # Calculate daily warm-up date (1000 calendar days before from_date) to ensure
    # weekly resampled indicators (like MACD, ADX, rolling swing high/low) have enough data to calculate correctly.
    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        daily_from_date = (from_dt - timedelta(days=1000)).strftime("%Y-%m-%d")
    except Exception:
        daily_from_date = from_date

    # NOTE: "1" (1-minute) is deliberately NOT fetched here. No strategy in
    # backend/strategies/ reads frames["1"]/m1_st/m1_srsi_k/m1_srsi_d --
    # build_merged_table() already has a documented fallback for its
    # absence (base["m1_st"] = base.get("supertrend_dir", 0), etc.). For a
    # multi-month backtest range, fetching 1-minute BankNifty data can mean
    # hundreds of thousands of rows across several 90-day chunks -- this
    # was making backtest requests take 5-10+ minutes (sometimes timing
    # out) for date ranges that otherwise complete in well under a minute.
    for tf_key, tf_dhan in [("1D","DAY"),("60","60"),("15","15"),("5","5")]:
        query_from = daily_from_date if tf_key == "1D" else from_date
        logger.info(f"[Backtest] Fetching {tf_key} data: {query_from} -> {to_date_inclusive}")
        df = broker.get_historical_data(instrument, tf_dhan, query_from, to_date_inclusive)
        if df is not None and len(df) >= 30:
            frames[tf_key] = df
            logger.info(f"[Backtest] {tf_key}: {len(df)} rows, {df['timestamp'].min()} -> {df['timestamp'].max()}")
        else:
            logger.warning(f"[Backtest] {tf_key}: got {len(df) if df is not None else 0} rows (skipped)")
        time.sleep(2.5)  # Throttling to avoid Dhan rate limit (DH-904)
    return frames


def _sync_broker_sl(pos, cfg):
    """Update the Stop Loss order trigger price on Dhan when a trailing event occurs."""
    if cfg.auto_trade and broker.is_connected() and getattr(pos, "sl_order_id", None):
        try:
            is_opt = pos.trade_mode == "OPTIONS"
            if is_opt:
                new_sl_trigger = broker.calculate_option_sl_price(
                    pos.direction, pos.entry_price, pos.index_entry_price, pos.sl, pos.symbol
                )
            else:
                new_sl_trigger = pos.sl
            broker.modify_broker_sl(pos.sl_order_id, pos.qty, new_sl_trigger, is_option=is_opt)
        except Exception as err:
            logger.error(f"Failed to modify broker-side Stop Loss for {pos.symbol}: {err}")


def _execute_order(sig: dict, cfg, direction: str) -> dict:
    """Execute an order (live or paper depending on auto_trade setting) based on signal levels."""
    tm = get_trade_manager()
    meta = INSTRUMENT_META[cfg.instrument]
    
    if cfg.auto_trade:
        # Live order execution via Dhan
        result = broker.place_entry_order(
            instrument    = cfg.instrument,
            direction     = direction,
            trade_mode    = cfg.trade_mode,
            expiry        = cfg.index_expiry if cfg.trade_mode=="INDEX" else cfg.options_expiry,
            strike_type   = cfg.strike_type,
            strike_offset = cfg.strike_offset,
            lot_multiplier= cfg.lot_multiplier,
            sl_price      = sig.get("sl", 0),
            t1_price      = sig.get("target1", 0),
            t2_price      = sig.get("target2", 0),
        )
    else:
        # Paper order simulation
        lot_size = broker.get_lot_size(cfg.instrument)
        qty = lot_size * cfg.lot_multiplier
        
        # Resolve symbol name for option/index
        symbol = ""
        exchange = ""
        if cfg.trade_mode == "OPTIONS" and broker.is_connected():
            try:
                opt_symbol, opt_strike = broker.get_option_symbol(
                    cfg.instrument, direction, cfg.options_expiry, cfg.strike_type, cfg.strike_offset
                )
                symbol = opt_symbol
                exchange = meta["exchange_opt"]
            except Exception:
                pass
        
        if not symbol:
            if cfg.trade_mode == "OPTIONS":
                symbol = f"{cfg.instrument} OPTION"
                exchange = meta["exchange_opt"]
            else:
                symbol = f"{cfg.instrument} INDEX"
                exchange = "INDEX"
                
        # Resolve simulated entry price (current LTP of index/option)
        entry_price = 0.0
        if cfg.trade_mode == "OPTIONS" and broker.is_connected() and symbol:
            try:
                entry_price = broker.get_option_ltp(symbol)
            except Exception as e:
                logger.warning(f"Could not fetch option LTP for paper entry: {e}")

        if cfg.trade_mode == "OPTIONS":
            # Do NOT fall back to sig.get("entry") or index LTP here -- those
            # are index-level prices (~57000), not option premiums (~500-2000).
            # A real incident (2026-08-21) confirmed this: a paper LONG opened
            # outside market hours, when option LTP was unavailable, recorded
            # entry_price=57734.8 (the index price) and later closed against a
            # real option premium of 2180.75 -- producing a nonsensical
            # -Rs.16,66,981 "loss" that corrupted the journal and (via
            # record_cooldown_loss, which isn't gated by auto_trade) triggered
            # a real cooldown block on a live strategy. Mirrors the safe
            # fallback already used at close time in _safe_pnl_exit_price():
            # skip rather than record a price we know is in the wrong unit.
            if entry_price == 0.0:
                logger.warning(
                    f"Could not resolve option premium for paper entry ({symbol}) -- "
                    f"skipping paper entry rather than recording a corrupted price."
                )
                return {"success": False, "error": "Option premium unavailable — paper entry skipped to avoid corrupted P&L"}
        else:
            if entry_price == 0.0:
                entry_price = sig.get("entry", 0.0)
            if entry_price == 0.0:
                entry_price = broker.get_ltp(cfg.instrument) or 0.0
            
        # For OPTIONS: convert index-level SL/T1/T2 from the signal into option-premium levels.
        # The polling loop compares INDEX LTP vs pos.sl/target1/target2 for hit detection,
        # so we store index levels for INDEX mode; for OPTIONS we store index levels too
        # (allows identical hit-detection code), but use option LTP for P&L at close time.
        # SL/T1/T2 displayed in UI will be index levels which tell the user WHERE the
        # underlying needs to reach — useful context for manual traders.
        result = {
            "success": True,
            "order_id": "PAPER_" + datetime.now(_IST).strftime("%H%M%S"),
            "symbol": symbol,
            "exchange": exchange,
            "direction": direction,
            "qty": qty,
            "sl": sig.get("sl", 0),
            "t1": sig.get("target1", 0),
            "t2": sig.get("target2", 0),
            "entry_price": entry_price,
        }

    if result.get("success"):
        # Use simulated entry price if present
        entry_price = result.get("entry_price", sig.get("entry", 0))
        # Index-level entry price from strategy signal — used for display/journal
        index_entry_price = sig.get("entry", 0.0)

        pos = ActivePosition(
            instrument        = cfg.instrument,
            symbol            = result["symbol"],
            exchange          = result["exchange"],
            direction         = direction,
            entry_price       = entry_price,
            sl                = result["sl"],
            target1           = result["t1"],
            target2           = result["t2"],
            qty               = result["qty"],
            order_id          = result["order_id"],
            entry_time        = datetime.now(_IST).isoformat(),
            trade_mode        = cfg.trade_mode,
            index_entry_price = index_entry_price,
            highest_since_entry = index_entry_price if index_entry_price else entry_price,
            lowest_since_entry = index_entry_price if index_entry_price else entry_price,
            expected_entry_price = result.get("expected_price", index_entry_price),
            entry_slippage = result.get("entry_slippage", 0.0),
            entry_atr      = sig.get("atr_5m", 0.0),
            strategy       = sig.get("strategy", cfg.strategy),
        )


        if cfg.auto_trade and broker.is_connected() and not result["order_id"].startswith("PAPER_"):
            sl_success = False
            sl_order_id = None
            initial_sl_trigger = 0.0
            
            if cfg.trade_mode == "OPTIONS":
                initial_sl_trigger = broker.calculate_option_sl_price(
                    direction, entry_price, index_entry_price, result["sl"], result["symbol"]
                )
            else:
                initial_sl_trigger = result["sl"]

            # Attempt up to 3 retries for broker SL placement
            for attempt in range(1, 4):
                try:
                    sl_order_id = broker.place_broker_sl(
                        symbol=result["symbol"],
                        exchange=result["exchange"],
                        direction=direction,
                        quantity=result["qty"],
                        trigger_price=initial_sl_trigger
                    )
                    if sl_order_id:
                        sl_success = True
                        pos.sl_order_id = sl_order_id
                        logger.info(f"Broker SL placed successfully on attempt {attempt}: order_id={sl_order_id}, trigger={initial_sl_trigger}")
                        break
                except Exception as sl_err:
                    logger.warning(f"Broker SL attempt {attempt}/3 failed: {sl_err}")
                    if attempt < 3:
                        import time as _t_sl
                        _t_sl.sleep(1)

            # AUTONOMOUS RISK ACTION: If SL placement failed after 3 attempts, market-exit immediately!
            if not sl_success:
                logger.critical(f"AUTONOMOUS SAFETY ACTION: SL placement failed 3 times for {result['symbol']}. Market-exiting un-hedged position immediately!")
                try:
                    _send_telegram_alert_wrapper(
                        f"\u26a0\ufe0f AUTONOMOUS SAFETY EXIT\n"
                        f"{direction} {result['symbol']}\n"
                        f"Reason: Could not place Stop-Loss order at broker after 3 retries\n"
                        f"Action: Position closed automatically to eliminate unhedged risk!",
                        cfg.telegram_bot_token, cfg.telegram_chat_id
                    )
                except Exception:
                    pass
                # Execute instant safety exit at broker
                broker.place_exit_order(result["symbol"], result["exchange"], direction, result["qty"])
                return {"success": False, "error": "Order cancelled: Failed to place broker Stop Loss after 3 retries"}

        tm.open_position(pos)

        # Post-entry verification: confirm position exists at broker.
        # This is a "trust but verify, alert if wrong" safety check, not
        # part of the critical execution path — the order is already
        # placed and the local position already opened above. Run it in
        # a background thread instead of blocking this function's return
        # (and therefore the trade_opened broadcast / Telegram entry
        # alert the caller sends immediately after _execute_order comes
        # back) on a brief settle-time sleep.
        if cfg.auto_trade and broker.is_connected() and not result["order_id"].startswith("PAPER_"):
            def _post_entry_verify():
                try:
                    import time as _verify_time
                    _verify_time.sleep(1)  # brief wait for broker to register
                    bp = broker.sync_position_from_broker(cfg.instrument, tracked_symbol=result["symbol"])
                    if bp and bp.get("has_position"):
                        logger.info(f"Post-entry verified: {bp.get('symbol')} qty={bp.get('qty')} at broker")
                    else:
                        logger.warning(f"Post-entry check: position {result['symbol']} NOT found at broker — may need manual verification")
                        try:
                            _send_telegram_alert_wrapper(
                                f"ENTRY VERIFICATION WARNING\n"
                                f"{direction} {result['symbol']}\n"
                                f"Order filled but position NOT confirmed at broker\n"
                                f"Please verify manually!",
                                cfg.telegram_bot_token, cfg.telegram_chat_id
                            )
                        except Exception:
                            pass
                except Exception as _verify_err:
                    logger.debug(f"Post-entry verification failed: {_verify_err}")

            import threading as _threading
            _threading.Thread(target=_post_entry_verify, daemon=True, name="PostEntryVerify").start()

        # Log entry fill slippage.
        # Paper OPTIONS entries have no "expected_price" key (result comes
        # from the paper path above, not broker.place_entry_order) -- the old
        # fallback to sig.get("entry") is the INDEX-level signal price, while
        # actual_price=entry_price is the PREMIUM. Comparing them produced a
        # nonsensical logged "slippage" (confirmed live, 2026-08-26). A paper
        # fill has no real broker round-trip to slip against anyway, so the
        # honest value is 0 -- expected == actual -- same reasoning already
        # applied to the entry-price fallback itself just above (2026-08-21).
        _expected_entry_slip = result.get("expected_price")
        if _expected_entry_slip is None:
            _expected_entry_slip = entry_price if cfg.trade_mode == "OPTIONS" else sig.get("entry", 0)
        slippage_tracker.log_entry_fill(
            symbol=result["symbol"],
            direction=direction,
            expected_price=_expected_entry_slip,
            actual_price=entry_price,
            qty=result["qty"],
            order_id=result["order_id"],
            trade_mode=cfg.trade_mode,
            instrument=cfg.instrument,
        )

        # Persist the entry signal so the UI can show SL/T1/T2 while in trade
        global _active_trade_signal
        _active_trade_signal = {**sig, "recorded_at": datetime.now(_IST).isoformat()}

        # Log entry signal to chart history
        _add_signal_to_history(sig)

        # Log to signal journal (tracks strategy P&L independently of Dhan trades)
        try:
            lot_size = INSTRUMENT_META.get(cfg.instrument, {}).get("lot_size", 1)
            signal_journal_manager.open_entry(sig, cfg.instrument, lot_size)
        except Exception as _sj_err:
            logger.error(f"Signal journal open failed: {_sj_err}")

        # Add to Trading Journal (only for real broker trades, not paper trades)
        if not result["order_id"].startswith("PAPER_"):
            try:
                from journal_manager import add_journal_entry
                add_journal_entry(pos, sig)
            except Exception as e:
                logger.error(f"Failed to record journal entry: {e}")

        # Send Telegram alert
        try:
            alert_sig = _build_alert_sig(sig, cfg, result["symbol"], direction)
            send_telegram_entry_alert(alert_sig, result)
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

    return result
