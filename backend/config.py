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
    strategy: str = "regime_trend_range"    # multi_agent | regime_trend_range

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
CRED_FIELDS = {"dhan_client_code", "dhan_access_token", "telegram_bot_token", "telegram_chat_id"}


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
    "NIFTY": {
        "exchange_index": "INDEX",
        "exchange_fut":   "NFO",
        "exchange_opt":   "NFO",
        "lot_size": 65,
        "strike_step": 50,
        "symbol_fut": "NIFTY",          # used in get_historical_data for INDEX
        "symbol_index": "NIFTY",
    },
    "BANKNIFTY": {
        "exchange_index": "INDEX",
        "exchange_fut":   "NFO",
        "exchange_opt":   "NFO",
        "lot_size": 30,
        "strike_step": 100,
        "symbol_fut": "BANKNIFTY",
        "symbol_index": "BANKNIFTY",
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
    "CRUDEOIL": {
        "exchange_index": "MCX",
        "exchange_fut":   "MCX",
        "exchange_opt":   "MCX",
        "lot_size": 10,
        "strike_step": 10,
        "symbol_fut": "CRUDEOIL",
        "symbol_index": "CRUDEOIL",
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
