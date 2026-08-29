"""
config.py — App-wide settings, loaded from .env or overridden at runtime.
"""
import os
import json
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional

BASE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    # ── Dhan Credentials (updated daily by user) ──────────────────────────
    dhan_client_code: str = ""
    dhan_access_token: str = ""

    # ── Strategy Selection ─────────────────────────────────────────────────
    strategy: str = "custom_alpha_combo_cusum125"    # default live strategy (see live_bar_processor.py _proc_list for all 4 live-capable options)

    # ── Instrument Settings ────────────────────────────────────────────────
    instrument: str = "BANKNIFTY"          # NIFTY | BANKNIFTY | SENSEX
    trade_mode: str = "OPTIONS"            # INDEX | OPTIONS
    index_expiry: int = 0                  # 0=current, 1=next, 2=far
    options_expiry: int = 0
    strike_type: str = "ITM"              # ATM | ITM | OTM
    strike_offset: int = -4              # +N for OTM, -N for ITM (from ATM)

    # ── Trading Parameters ─────────────────────────────────────────────────
    lot_multiplier: int = 1              # how many lots to trade
    max_daily_loss: float = 5000.0       # Rs. stop trading for the day
    max_daily_profit: float = 15000.0    # Rs. stop trading after this profit
    auto_trade: bool = False             # master switch for live execution
    product_type: str = "NRML"           # NRML | MIS (Carry Forward vs. Intraday)
    position_hold_mode: str = "INTRADAY"  # INTRADAY | CARRY_FORWARD — backtest engine
                                           # EOD handling (per-backtest-request override
                                           # from the Backtest page; distinct from the
                                           # live-order product_type above)
    confirm_signals: bool = False        # whether to require 2 consecutive polls before entry
    auto_square_off_minutes: int = 10    # minutes before market close to auto square-off
    auto_kill_switch: bool = False       # auto-disable after consecutive failures
    auto_kill_switch_max_failures: int = 3  # failures before kill switch triggers

    # ── Capital Protection ────────────────────────────────────────────────
    starting_capital: float = 30000.0    # total account capital (Rs.)
    data_stale_threshold_min: int = 10   # minutes after which candle data is considered stale

    # ── Regime Strategy Parameters ──────────────────────────────────────────
    regime_trail_mult: float = 1.5       # trailing distance = ATR * this
    regime_trail_activation: float = 0.3 # min profit (in ATR) before trailing starts (0 = immediate)
    regime_be_trigger: float = 0.3       # profit (in ATR) to lock breakeven (0 = disabled)
    regime_be_buffer: float = 0.4        # buffer above entry for breakeven lock (in ATR)

    # ── Strategy Parameters ───────────────────────────────────────────────
    ml_threshold: float = 0.0           # disabled — ML model not reliable yet
    atr_sl_mult: float = 1.8
    atr_t1_mult: float = 3.0
    atr_t2_mult: float = 5.0
    max_hold_bars: int = 18              # 5min bars = 90 min max hold
    min_adx: float = 18.0

    # ── Agent Engine ──────────────────────────────────────────────────────
    agent_weight_macro: float = 0.25
    agent_weight_structure: float = 0.20
    agent_weight_momentum: float = 0.20
    agent_weight_trigger: float = 0.15
    agent_weight_volume: float = 0.10
    agent_weight_memory: float = 0.10
    min_orchestrator_score: float = 0.30  # minimum weighted score for entry
    min_trigger_quality: float = 0.50     # minimum trigger quality for entry (to reduce chart noise/overtrading)
    min_rr: float = 0.60                  # minimum risk-to-reward ratio for Target 1 (to filter out low reward entries like 0.28x)
    max_rr: float = 10.0                  # maximum R:R ratio — reject entries where SL is too tight (T1/SL > 10x)
    block_short_oversold: bool = False    # block SHORT entries when StochRSI K < 20 (counter-trend into oversold)

    # ── Trailing Stop Loss ────────────────────────────────────────────────
    trailing_be_trigger_atr: float = 1.0  # move SL to breakeven after +1.0 ATR profit
    trailing_start_atr: float = 1.0       # start trailing after +1.0 ATR peak profit
    trailing_offset_atr: float = 0.3      # trail 0.3 ATR behind the peak price

    # ── Entry Filters ─────────────────────────────────────────────────────
    cooldown_bars: int = 3                # block same-direction re-entry for N bars (15m) after a loss
    sl_level_buffer_atr: float = 0.0      # buffer behind key level for SL placement (0.0 = exact level)

    # ── HTF Conflict Distances (Structure Agent) ─────────────────────────
    htf_w_conflict_dist: float = 1.0     # weekly level: within N× H1 ATR -> conflict zone
    htf_d1_conflict_dist: float = 0.8    # daily level: within N× H1 ATR -> conflict zone

    # ── Pattern Memory ────────────────────────────────────────────────────
    pattern_memory_enabled: bool = True   # enabled since FAISS index is built
    pattern_memory_k: int = 20            # top-K neighbors to retrieve
    pattern_memory_min_samples: int = 5   # minimum analogs for confidence

    # ── Chart Patterns ────────────────────────────────────────────────────
    chart_patterns_enabled: bool = True

    # ── Chart ─────────────────────────────────────────────────────────────
    chart_timeframe: str = "5"           # 1|5|15|25|60|DAY

    # ── Paths ─────────────────────────────────────────────────────────────
    model_path: str = str(BASE_DIR / "models" / "ml_model.pkl")
    data_dir: str = str(BASE_DIR / "data")
    log_dir: str = str(BASE_DIR / "logs")
    memory_index_dir: str = str(BASE_DIR / "memory")

    # ── Telegram (optional) ───────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── CAS Scanner (backend/cas_scanner.py) ────────────────────────────────
    # Dedicated Dhan credentials, isolated from the primary ones above so the
    # scanner's own API usage never contends with the live trading engine's
    # rate budget. Sourced from .env only (DHAN_CAS_CLIENT_CODE/DHAN_CAS_TOKEN_ID),
    # same as the primary credentials -- see CRED_FIELDS below.
    dhan_cas_client_code: str = ""
    dhan_cas_access_token: str = ""
    cas_scanner_enabled: bool = True
    cas_premium_floor: float = 5.0          # Mode A: premium below this counts as "near-worthless"
    cas_max_days_to_expiry: int = 3         # Mode A: shortlist strikes expiring within N sessions (0 = today)
    cas_spike_multiple: float = 5.0         # Mode A: alert when price >= this x its recent rolling minimum
    cas_lookback_minutes: int = 10          # Mode A: rolling-minimum window for spike detection
    # Mode B redesign (2026-08-26): a single-sweep ratio snapshot fired on
    # anything that crossed 3x even once, flooding 12k+ Telegram alerts in one
    # day -- mostly noise from a fresh-expiry-cycle's universally-low starting
    # OI. Researched real practice (VPIN, gamma/strike-clustering literature)
    # rather than just raising the threshold: genuine activity accumulates
    # volume over time and clusters across neighboring strikes, so a single
    # instantaneous ratio can't tell that apart from a one-off print. Redesign:
    # entry_ratio is a loose bar to START tracking a strike's history;
    # cas_undercurrent_ratio is now the ACCUMULATED volume (since first
    # tracked) vs baseline OI, not an instantaneous snapshot; a candidate must
    # also persist for confirm_minutes before it's eligible to alert.
    cas_undercurrent_entry_ratio: float = 1.5   # Mode B: loose bar to start tracking a strike at all
    cas_undercurrent_ratio: float = 3.0         # Mode B: accumulated volume vs baseline-OI threshold to confirm
    cas_undercurrent_confirm_minutes: float = 30.0  # Mode B: must persist this long before it can alert
    cas_undercurrent_top_n: int = 15            # Mode B: cap on ranked alerts, PER SIDE (bull/bear separately)
    # Per-candidate Telegram alerts (2026-08-27): even after the accumulation-gate
    # redesign, individual per-candidate messages still meant 45 separate
    # Telegram pings once the ranked list filled up -- "so many messages which
    # is not making sense" per the user. Replaced with one periodic digest
    # (top-N-per-side, ranked) instead of firing per candidate.
    cas_digest_interval_minutes: float = 30.0   # Mode B: how often to send the consolidated top-N-per-side digest
    cas_digest_top_n: int = 10                  # Mode B: how many per side the digest itself shows (tighter than cas_undercurrent_top_n's tracking cap)
    cas_atm_band_pct: float = 5.0           # both modes: "near spot" band for OI-concentration scoring

    # Mode C -- Index Burst (2026-08-27): confirmed live that Mode B's 30-min
    # gate structurally cannot catch a fast index move -- SENSEX 77300/77400
    # PE spiked 500%+ and mostly reversed within ~7-9 minutes on their own
    # 0-DTE expiry day, faster than even one full stock sweep cycle. Indices
    # (only 3: NIFTY/BANKNIFTY/SENSEX) get their own fast, independent
    # polling loop, decoupled from the 208-stock sweep, with a much shorter
    # confirm window and immediate per-alert Telegram (not digest-batched).
    cas_index_sweep_seconds: float = 30.0       # Mode C: how often the 3 indices are freshly polled
    cas_index_entry_ratio: float = 2.0          # Mode C: loose bar to start tracking (higher than stocks' 1.5x -- indices have naturally huge absolute volume)
    cas_index_confirm_minutes: float = 5.0      # Mode C: must persist this long before it can alert (vs Mode B's 30 -- justified by the ~7-9 min SENSEX move)
    # Killed live (2026-08-28, 09:20 IST, ~5min after market open): flooded
    # 20+ Telegram alerts across SENSEX/BANKNIFTY strikes within 90 seconds.
    # Root cause: every strike with zero prior-day OI (deep OTM) confirms as
    # an "infinite ratio" the instant any fresh volume trades against it --
    # and at real market open, dozens of such strikes get their first
    # opening-range volume near-simultaneously (normal price discovery, not
    # genuine unusual activity). Since the scanner had been running since
    # 09:00:45 pre-market, many strikes had already cleared the 5-min
    # confirm window by 09:20, all at once.
    # Re-enabled 2026-08-28 with two fixes: (1) cas_index_opening_range_minutes
    # keeps Mode C fully idle (no fetch, no tracking) for the first N minutes
    # after the 09:15 open, so tracking only ever starts from a genuinely
    # settled baseline -- suppressing just the ALERT during this window
    # wouldn't work, since the same strikes would still all become
    # "confirmed but held" together and fire in one batch the moment
    # suppression lifted; (2) cas_index_min_accumulated_volume gates the
    # zero-baseline-OI ("infinite ratio") path specifically, since that path
    # has no natural signal-strength floor otherwise -- a handful of
    # contracts against zero OI shouldn't confirm as strongly as a real burst.
    cas_mode_c_enabled: bool = True
    cas_index_opening_range_minutes: float = 20.0   # Mode C: stays fully idle this long after the 09:15 open
    cas_index_min_accumulated_volume: float = 500.0  # Mode C: floor on the zero-baseline-OI path only -- a first estimate, not benchmarked, tune from real data

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


import threading

# Singleton
_settings: Optional[Settings] = None
_settings_file = BASE_DIR / "settings.json"
_settings_lock = threading.Lock()


def get_settings() -> Settings:
    global _settings
    with _settings_lock:
        if _settings is None:
            _settings = _load_settings()
        return _settings


# Credential fields that must ONLY come from .env (never saved to settings.json)
CRED_FIELDS = {"dhan_client_code", "dhan_access_token", "telegram_bot_token", "telegram_chat_id",
               "dhan_cas_client_code", "dhan_cas_access_token"}


def _load_settings() -> Settings:
    """Load from .env first, then apply non-credential JSON overrides.
    
    Credentials (Dhan + Telegram) are ALWAYS sourced from .env and never
    read from or written to settings.json to prevent accidental leakage.
    """
    import os
    from dotenv import load_dotenv
    # Ensure env variables are loaded from the .env file in base directory
    env_path = BASE_DIR.parent / ".env"
    load_dotenv(dotenv_path=env_path)
    
    # Initialize Settings. Pydantic Settings will automatically map matching env variables (case-insensitive)
    settings = Settings()
    
    # Check if we have overrides in settings.json
    overrides = {}
    if _settings_file.exists():
        try:
            overrides = json.loads(_settings_file.read_text())
        except Exception:
            pass
            
    # Apply overrides — skip credential fields (they come from .env only)
    for k, v in overrides.items():
        if hasattr(settings, k) and k not in CRED_FIELDS:
            setattr(settings, k, v)
            
    # Strip whitespace from credential fields
    for field in CRED_FIELDS:
        val = getattr(settings, field)
        if isinstance(val, str):
            setattr(settings, field, val.strip())
            
    # Explicit fallback mapping: DHAN_TOKEN_ID -> dhan_access_token
    if not settings.dhan_access_token:
        token_id = os.environ.get("DHAN_TOKEN_ID", "").strip()
        if token_id:
            settings.dhan_access_token = token_id
            
    # Explicit fallback mapping: DHAN_CLIENT_CODE -> dhan_client_code
    if not settings.dhan_client_code:
        client_code = os.environ.get("DHAN_CLIENT_CODE", "").strip()
        if client_code:
            settings.dhan_client_code = client_code

    # Same fallback mapping for the CAS scanner's dedicated, isolated credentials
    if not settings.dhan_cas_access_token:
        cas_token = os.environ.get("DHAN_CAS_TOKEN_ID", "").strip()
        if cas_token:
            settings.dhan_cas_access_token = cas_token
    if not settings.dhan_cas_client_code:
        cas_client = os.environ.get("DHAN_CAS_CLIENT_CODE", "").strip()
        if cas_client:
            settings.dhan_cas_client_code = cas_client

    return settings


def save_settings(data: dict) -> Settings:
    """Persist non-credential settings to JSON file and reload.
    
    Credential fields are stripped before saving — they are always
    sourced from .env at load time.
    """
    global _settings
    with _settings_lock:
        existing = {}
        if _settings_file.exists():
            try:
                existing = json.loads(_settings_file.read_text())
            except Exception:
                pass
        # Strip credential fields before persisting
        safe_data = {k: v for k, v in data.items() if k not in CRED_FIELDS}
        existing = {k: v for k, v in existing.items() if k not in CRED_FIELDS}
        existing.update(safe_data)
        _settings_file.write_text(json.dumps(existing, indent=2))
        # Reload from scratch so .env credentials are picked up
        _settings = _load_settings()
        return _settings


# ── Instrument metadata ───────────────────────────────────────────────────────
# NOTE: lot_size values are FALLBACKS only. At runtime, broker.get_lot_size()
# fetches the actual lot size from Dhan's instrument file via tsl.get_lot_size().

INSTRUMENT_META = {
    "BANKNIFTY": {
        "exchange_index": "INDEX",
        "exchange_fut":   "NFO",
        "exchange_opt":   "NFO",
        "lot_size": 30,
        "strike_step": 100,
        "symbol_fut": "BANKNIFTY",
        "symbol_index": "BANKNIFTY",
    },
    "NIFTY": {
        "exchange_index": "INDEX",
        "exchange_fut":   "NFO",
        "exchange_opt":   "NFO",
        "lot_size": 75,
        "strike_step": 50,
        "symbol_fut": "NIFTY",
        "symbol_index": "NIFTY",
    },
    "FINNIFTY": {
        "exchange_index": "INDEX",
        "exchange_fut":   "NFO",
        "exchange_opt":   "NFO",
        "lot_size": 65,
        "strike_step": 50,
        "symbol_fut": "FINNIFTY",
        "symbol_index": "FINNIFTY",
    },
    "MIDCPNIFTY": {
        "exchange_index": "INDEX",
        "exchange_fut":   "NFO",
        "exchange_opt":   "NFO",
        "lot_size": 120,
        "strike_step": 25,
        "symbol_fut": "MIDCPNIFTY",
        "symbol_index": "MIDCPNIFTY",
    },
    "SENSEX": {
        "exchange_index": "INDEX",
        "exchange_fut":   "BFO",
        "exchange_opt":   "BFO",
        "lot_size": 10,
        "strike_step": 100,
        "symbol_fut": "SENSEX",
        "symbol_index": "SENSEX",
    },
    "BANKEX": {
        "exchange_index": "INDEX",
        "exchange_fut":   "BFO",
        "exchange_opt":   "BFO",
        "lot_size": 15,
        "strike_step": 100,
        "symbol_fut": "BANKEX",
        "symbol_index": "BANKEX",
    },
    "CRUDEOIL": {
        "exchange_index": "MCX",
        "exchange_fut":   "MCX",
        "exchange_opt":   "MCX",
        "lot_size": 100,
        "strike_step": 50,
        "symbol_fut": "CRUDEOIL",
        "symbol_index": "CRUDEOIL",
    },
    "CRUDEOILM": {
        "exchange_index": "MCX",
        "exchange_fut":   "MCX",
        "exchange_opt":   "MCX",
        "lot_size": 10,
        "strike_step": 50,
        "symbol_fut": "CRUDEOILM",
        "symbol_index": "CRUDEOILM",
    },
    "NATURALGAS": {
        "exchange_index": "MCX",
        "exchange_fut":   "MCX",
        "exchange_opt":   "MCX",
        "lot_size": 1250,
        "strike_step": 5,
        "symbol_fut": "NATURALGAS",
        "symbol_index": "NATURALGAS",
    },
    "GOLD": {
        "exchange_index": "MCX",
        "exchange_fut":   "MCX",
        "exchange_opt":   "MCX",
        "lot_size": 100,
        "strike_step": 100,
        "symbol_fut": "GOLD",
        "symbol_index": "GOLD",
    },
    "GOLDM": {
        "exchange_index": "MCX",
        "exchange_fut":   "MCX",
        "exchange_opt":   "MCX",
        "lot_size": 10,
        "strike_step": 100,
        "symbol_fut": "GOLDM",
        "symbol_index": "GOLDM",
    },
    "SILVER": {
        "exchange_index": "MCX",
        "exchange_fut":   "MCX",
        "exchange_opt":   "MCX",
        "lot_size": 30,
        "strike_step": 500,
        "symbol_fut": "SILVER",
        "symbol_index": "SILVER",
    },
    "SILVERM": {
        "exchange_index": "MCX",
        "exchange_fut":   "MCX",
        "exchange_opt":   "MCX",
        "lot_size": 5,
        "strike_step": 500,
        "symbol_fut": "SILVERM",
        "symbol_index": "SILVERM",
    },
}

# Market trading hours by exchange (IST)
# Each entry: (start_hour, start_minute, end_hour, end_minute)
MARKET_HOURS = {
    "INDEX": (9, 15, 15, 30),   # NSE/BSE: 9:15 AM - 3:30 PM
    "NFO":   (9, 15, 15, 30),
    "BFO":   (9, 15, 15, 30),
    "MCX":   (9, 0, 23, 30),    # MCX: 9:00 AM - 11:30 PM
}

TIMEFRAME_LABELS = {
    "1": "1 Min", "5": "5 Min", "15": "15 Min",
    "25": "25 Min", "60": "1 Hour", "DAY": "Daily"
}
