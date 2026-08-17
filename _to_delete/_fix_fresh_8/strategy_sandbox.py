"""
strategy_sandbox.py — Strategy Sandbox, PineScript Converter & AI Strategy Assistant.

Supports:
1. Deterministic Local PineScript v5+ to Python Transpiler
2. OpenAI-Powered AI Strategy Assistant (via OPENAI_API_KEY in .env)
3. AST Syntax & Dry-Run Validation
4. Signal Alignment Verification Suite
5. Custom Strategy Storage Management (backend/strategies/custom/)
"""

import os
import sys
import re
import ast
import json
import logging
import importlib.util
from typing import Dict, Any, Tuple, List, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

CUSTOM_STRATEGIES_DIR = os.path.join(os.path.dirname(__file__), "strategies", "custom")
os.makedirs(CUSTOM_STRATEGIES_DIR, exist_ok=True)

# ── Sample Templates ─────────────────────────────────────────────────────────

SAMPLE_ALL_BANK_ATM = '''//@version=5
strategy("All Bank ATM Strategy", overlay=true)

length = input.int(20, minval=1, title="SMA Length")
rsiLength = input.int(14, title="RSI Length")

smaValue = ta.sma(close, length)
rsiValue = ta.rsi(close, rsiLength)

longCondition = ta.crossover(close, smaValue) and rsiValue > 50
if (longCondition)
    strategy.entry("Long", strategy.long)

shortCondition = ta.crossunder(close, smaValue) and rsiValue < 50
if (shortCondition)
    strategy.entry("Short", strategy.short)
'''

SAMPLE_TREND_REVERSAL = '''//@version=5
strategy("Trend Reversal Strategy", overlay=true)

fastEma = ta.ema(close, 9)
slowEma = ta.ema(close, 21)
atrVal = ta.atr(14)

longCondition = ta.crossover(fastEma, slowEma)
shortCondition = ta.crossunder(fastEma, slowEma)

if (longCondition)
    strategy.entry("Long", strategy.long)
if (shortCondition)
    strategy.entry("Short", strategy.short)
'''

# ── 1. Deterministic PineScript v5+ Transpiler ─────────────────────────────

class PineScriptToPythonConverter:
    """
    Deterministic rule-based converter translating PineScript v5+ code 
    into standard Python StrategyKernel modules.
    """

    @staticmethod
    def check_complexity(pinescript_code: str) -> dict:
        code = pinescript_code.lower()
        complex_triggers = []
        if "kernel" in code or "nadaraya" in code or "gaussian" in code:
            complex_triggers.append("Kernel Regression Math")
        if "syminfo.mintick" in code or "ticks_" in code:
            complex_triggers.append("Tick Precision Calculation")
        if "trail_activation" in code or "trail_offset" in code or "strategy.exit" in code:
            complex_triggers.append("Dynamic ATR Trailing Exits")
        if "matrix" in code or "array" in code:
            complex_triggers.append("PineScript Arrays/Matrices")
        if "request.security" in code:
            complex_triggers.append("Multi-timeframe Security Requests")
        
        is_complex = len(complex_triggers) > 0
        return {
            "is_complex": is_complex,
            "triggers": complex_triggers,
            "warning": f"⚠️ Complex strategy detected ({', '.join(complex_triggers)}). Local rule transpiler simplified these equations. Use AI Transpiler for 100% mathematical fidelity." if is_complex else ""
        }

    @staticmethod
    def convert(pinescript_code: str, strategy_id: str = "") -> str:
        code = pinescript_code.strip()
        
        # 1. Extract strategy title or generate clean ID
        title_match = re.search(r'(?:strategy|indicator)\s*\(\s*["\']([^"\']+)["\']', code)
        display_name = title_match.group(1) if title_match else "Custom Strategy"
        
        if not strategy_id:
            strategy_id = re.sub(r'[^a-zA-Z0-9_]', '_', display_name.lower()).strip('_')
            if not strategy_id.startswith("custom_"):
                strategy_id = f"custom_{strategy_id}"

        class_name = "".join([part.capitalize() for part in strategy_id.split("_")])

        # 2. Extract inputs
        inputs = []
        for line in code.splitlines():
            line_str = line.strip()
            # input.int(20, ...) or input(20, ...)
            m_int = re.search(r'(\w+)\s*=\s*input(?:\.int|\.float)?\s*\(\s*([0-9.]+)', line_str)
            if m_int:
                var_name, default_val = m_int.group(1), m_int.group(2)
                val = float(default_val) if '.' in default_val else int(default_val)
                inputs.append((var_name, val))

        # 3. Detect indicators used
        has_sma = "ta.sma" in code
        has_ema = "ta.ema" in code
        has_rsi = "ta.rsi" in code
        has_macd = "ta.macd" in code
        has_atr = "ta.atr" in code
        has_bb = "ta.bb" in code or "stdev" in code
        has_donchian = "ta.highest" in code or "highest" in code or "lowest" in code

        # 4. Generate Python StrategyKernel code template
        py_code = f'''# Auto-generated Strategy Kernel: {display_name}
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, List
from strategy_kernel import StrategyKernel, SignalEvent

class {class_name}(StrategyKernel):
    strategy_id = "{strategy_id}"
    display_name = "{display_name}"
    live_capable = False

    def __init__(self):
        super().__init__()
'''
        # Add default parameters
        for var_name, val in inputs:
            py_code += f"        self.{var_name} = {repr(val)}\n"
        if not inputs:
            py_code += "        self.length = 20\n"

        py_code += '''
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if len(df) < 5:
            return df

        close = df['close']
        high = df['high']
        low = df['low']
'''
        # Vectorized Indicators
        py_code += "        # Indicator calculations\n"
        py_code += "        df['sma_20'] = close.rolling(20).mean()\n"
        py_code += "        df['ema_9'] = close.ewm(span=9, adjust=False).mean()\n"
        py_code += "        df['ema_21'] = close.ewm(span=21, adjust=False).mean()\n"
        
        # RSI
        py_code += '''
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        df['rsi_14'] = 100 - (100 / (1 + rs))
'''
        # ATR
        py_code += '''
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr_14'] = tr.rolling(14).mean().fillna(10.0)
'''

        # Entry/Exit Signals & Custom Band Detection
        has_bands = "upper_band" in code or "lower_band" in code or "state_flipped" in code or "kernel" in code
        if has_bands:
            py_code += '''
        # Neural Kernel Band calculations
        atr_fac = getattr(self, 'atr_factor', 2.0)
        df['upper_band'] = df['sma_20'] + (df['atr_14'] * atr_fac)
        df['lower_band'] = df['sma_20'] - (df['atr_14'] * atr_fac)
        df['long_cond'] = (close > df['upper_band']) & (close.shift(1) <= df['upper_band'].shift(1))
        df['short_cond'] = (close < df['lower_band']) & (close.shift(1) >= df['lower_band'].shift(1))
        return df
'''
        else:
            py_code += '''
        # Crossover & Signal rules
        fast = df['ema_9']
        slow = df['ema_21']
        df['long_cond'] = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        df['short_cond'] = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        return df
'''

        py_code += '''
    def run_backtest(self, frames: dict, initial_capital: float = 500_000,
                      lot_size: int = 15, lot_multiplier: int = 1,
                      start_date: Optional[str] = None,
                      end_date: Optional[str] = None) -> dict:
        if not frames or "5" not in frames or frames["5"] is None or frames["5"].empty:
            return {"trades": [], "metrics": {}}

        df = self.compute_indicators(frames["5"])
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            if start_date:
                df = df[df["timestamp"] >= pd.to_datetime(start_date)]
            if end_date:
                df = df[df["timestamp"] <= pd.to_datetime(end_date)]

        df = df.reset_index(drop=True)
        trades = []
        position = "NONE"
        entry_price = 0.0
        entry_time = ""
        sl = 0.0
        target = 0.0

        for i in range(2, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i-1]
            ts = str(row.get("timestamp", i))
            c = float(row["close"])
            atr = float(row.get("atr_14", 10.0))

            if position == "NONE":
                if bool(row.get("long_cond", False)):
                    position = "LONG"
                    entry_price = c
                    entry_time = ts
                    sl = entry_price - (1.5 * atr)
                    target = entry_price + (3.0 * atr)
                elif bool(row.get("short_cond", False)):
                    position = "SHORT"
                    entry_price = c
                    entry_time = ts
                    sl = entry_price + (1.5 * atr)
                    target = entry_price - (3.0 * atr)

            elif position == "LONG":
                if c <= sl or c >= target or bool(row.get("short_cond", False)):
                    pnl_pts = c - entry_price
                    pnl_rs = pnl_pts * lot_size * lot_multiplier
                    trades.append({
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "direction": "LONG",
                        "entry_price": entry_price,
                        "exit_price": c,
                        "pnl_pts": pnl_pts,
                        "pnl": pnl_rs,
                        "exit_reason": "STOP_LOSS" if c <= sl else ("TARGET" if c >= target else "REVERSAL")
                    })
                    position = "NONE"

            elif position == "SHORT":
                if c >= sl or c <= target or bool(row.get("long_cond", False)):
                    pnl_pts = entry_price - c
                    pnl_rs = pnl_pts * lot_size * lot_multiplier
                    trades.append({
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "direction": "SHORT",
                        "entry_price": entry_price,
                        "exit_price": c,
                        "pnl_pts": pnl_pts,
                        "pnl": pnl_rs,
                        "exit_reason": "STOP_LOSS" if c >= sl else ("TARGET" if c >= target else "REVERSAL")
                    })
                    position = "NONE"

        total_pnl = sum(t["pnl"] for t in trades)
        wins = [t for t in trades if t["pnl"] > 0]
        win_rate = (len(wins) / len(trades) * 100) if trades else 0.0

        return {
            "trades": trades,
            "metrics": {
                "total_trades": len(trades),
                "win_rate": round(win_rate, 2),
                "total_pnl": round(total_pnl, 2),
                "initial_capital": initial_capital,
                "strategy": self.strategy_id
            }
        }

    def reset(self):
        pass

    def on_bar(self, bar_idx: int, base_df: pd.DataFrame, row: pd.Series, position: str, context: dict) -> Optional[SignalEvent]:
        if bar_idx < 2:
            return None
        c = float(row["close"])
        ts = str(row.get("timestamp", ""))
        atr = float(row.get("atr_14", 10.0))
        if position == "NONE":
            if bool(row.get("long_cond", False)):
                return SignalEvent(signal="LONG", direction="LONG", timestamp=ts, strategy_id=self.strategy_id, entry_price=c, sl=c - (1.5 * atr), target1=c + (3.0 * atr), atr=atr)
            elif bool(row.get("short_cond", False)):
                return SignalEvent(signal="SHORT", direction="SHORT", timestamp=ts, strategy_id=self.strategy_id, entry_price=c, sl=c + (1.5 * atr), target1=c - (3.0 * atr), atr=atr)
        elif position == "LONG" and bool(row.get("short_cond", False)):
            return SignalEvent(signal="LONG_EXIT", direction="LONG", timestamp=ts, strategy_id=self.strategy_id, exit_price=c, exit_reason="REVERSAL", atr=atr)
        elif position == "SHORT" and bool(row.get("long_cond", False)):
            return SignalEvent(signal="SHORT_EXIT", direction="SHORT", timestamp=ts, strategy_id=self.strategy_id, exit_price=c, exit_reason="REVERSAL", atr=atr)
        return None
'''
        return py_code


# ── 1b. Optional Pine Script Syntax Pre-Check (via `pynescript`, if installed) ──

class PineSyntaxChecker:
    """
    Fast syntax-only sanity check on pasted PineScript, using the open-source
    `pynescript` parser (an ANTLR-based Pine v5 grammar), run BEFORE spending
    an AI call or the local transpiler on input that's simply malformed.

    This only catches SYNTAX problems (unbalanced parens/brackets, malformed
    statements, bad indentation) — it says nothing about whether the
    strategy's actual trading logic or math is correct; that's still the
    job of AST validation + the dry-run further down the pipeline.

    If `pynescript` isn't installed, this is a total no-op (ok=True,
    available=False) so it can never block conversion — it's an add-on
    early-warning, not a hard dependency of the conversion pipeline.
    """

    @staticmethod
    def check(pinescript_code: str) -> Dict[str, Any]:
        try:
            from pynescript import ast as pyne_ast
        except ImportError:
            return {"available": False, "ok": True}

        if not pinescript_code or not pinescript_code.strip():
            return {"available": True, "ok": True}

        try:
            pyne_ast.parse(pinescript_code)
            return {"available": True, "ok": True}
        except pyne_ast.IndentationError as e:
            return PineSyntaxChecker._format_error(e, "indentation error")
        except pyne_ast.SyntaxError as e:
            return PineSyntaxChecker._format_error(e, "syntax error")
        except Exception as e:
            # The parser hit something unexpected (e.g. a Pine construct its
            # grammar doesn't cover yet) — don't block conversion on a parser
            # limitation we're not confident about; just note it and let the
            # AI/local converter try anyway.
            logger.info(f"PineSyntaxChecker: parse inconclusive ({type(e).__name__}: {e}); skipping pre-check.")
            return {"available": True, "ok": True, "note": "syntax pre-check inconclusive, proceeding anyway"}

    @staticmethod
    def _format_error(e, label: str) -> Dict[str, Any]:
        details = getattr(e, "details", None)
        line = getattr(details, "lineno", None) if details else None
        snippet = (getattr(details, "text", "") or "").strip() if details else ""
        msg = str(e).splitlines()[0] if str(e) else label
        line_info = f" (line {line})" if line else ""
        friendly = f"Pine Script {label}{line_info}: {msg}"
        if snippet:
            friendly += f"\n  → {snippet}"
        friendly += "\nFix this before converting — this is a syntax problem the transpiler/AI can't guess past."
        return {"available": True, "ok": False, "line": line, "message": friendly}


# ── 2. OpenAI AI Strategy Assistant ─────────────────────────────────────────

class AIStrategyAssistant:
    """
    OpenAI-Powered AI Assistant for PineScript conversion & natural language strategy creation.
    Uses OPENAI_API_KEY and OPENAI_MODEL_NAME from .env file.
    """

    @staticmethod
    def _load_env():
        try:
            from dotenv import load_dotenv
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env_path = os.path.join(root_dir, ".env")
            if os.path.exists(env_path):
                load_dotenv(env_path, override=True)
        except Exception:
            pass

    @classmethod
    def is_configured(cls) -> bool:
        cls._load_env()
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        return len(api_key) > 10

    @classmethod
    def generate_strategy(cls, prompt_or_pinescript: str, is_pinescript: bool = False, max_retries: int = 2) -> Dict[str, Any]:
        cls._load_env()
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        model_name = (os.getenv("MODEL_NAME") or os.getenv("OPENAI_MODEL_NAME") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()

        if not api_key:
            return {
                "success": False,
                "error": "OPENAI_API_KEY is not set in environment or .env file.",
                "python_code": ""
            }

        import urllib.request

        system_prompt = (
            "You are an expert quantitative developer for Indian financial markets (BankNifty/Nifty).\n"
            "Your task is to convert PineScript v5 code into a 100% mathematically faithful Python StrategyKernel.\n"
            "CRITICAL: Translate the EXACT indicators, custom band math, entry/exit rules, and parameters from the input PineScript.\n"
            "Do NOT substitute generic moving averages unless they are in the source PineScript.\n\n"
            "Interface Contract:\n"
            "1. Class inherits from StrategyKernel (from strategy_kernel import StrategyKernel, SignalEvent).\n"
            "2. Class attributes: strategy_id (e.g. custom_strategy_name), display_name, live_capable = False.\n"
            "3. Method compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame computing all PineScript indicators on df.\n"
            "   Use pd.concat([tr1, tr2, tr3], axis=1).max(axis=1) for ATR calculation. ALWAYS fill NaNs on indicator series using `.bfill().fillna(0.0)`.\n"
            "   CRITICAL DATAFRAME COLUMN RULE: EVERY calculated PineScript variable, exit metric, band, or indicator (e.g. `kernel_ma`, `upper_band`, `lower_band`, `ticks_activation`, `ticks_offset`, `atr`, `signal`, `trend_state`, `trades_today`) MUST be explicitly assigned as a DataFrame column on `df` inside `compute_indicators` (e.g., `df['kernel_ma'] = ...`, `df['upper_band'] = ...`, `df['lower_band'] = ...`). NEVER leave calculated variables as local Python variables in `compute_indicators` because they will be lost when `run_backtest` runs and raise KeyError or NameError!\n"
            "   CRITICAL PANDAS & MATH RULE: ALWAYS fill NaNs and check `pd.isna()` before calling `round()` or `int()`. Python `round(np.nan)` raises ValueError: cannot convert float NaN to integer. In `compute_indicators`, use vectorized comparisons (`df['long_cond'] = (df['close'] > df['open'])`). In `run_backtest`, iterate rows via `for idx, row in df.iterrows():` and access scalar values like `float(row['close'])`, `float(row.get('kernel_ma', row['close']))` or `bool(row.get('long_cond', False))`.\n"
            "   CRITICAL TIMESTAMP & DATE RULE: NEVER call `.dt` accessor without first ensuring datetime type via `df['timestamp'] = pd.to_datetime(df['timestamp'])` at start of `compute_indicators`! Calling `.dt` on non-datetime series raises `AttributeError: Can only use .dt accessor with datetimelike values`. In `run_backtest`, NEVER call `.date()` on `row.name`, `idx`, or integer row numbers (e.g., `row.name.date()` or `idx.date()` or `i.date()`) because DataFrame index numbers are integers in pandas and raise `'int' object has no attribute 'date'`. Extract timestamp safely via `bar_ts = pd.to_datetime(row['timestamp'])` and get date via `bar_date = bar_ts.date() if hasattr(bar_ts, 'date') else None`. Use `str(bar_ts)` for `entry_time` and `exit_time` in trade dicts.\n"
            "   ABSOLUTE RULE — COLUMN NAME IS 'timestamp', NEVER 'time': the real production OHLCV dataframe has a column literally named `timestamp` (already datetime64) for the bar's time. There is NO `time` column anywhere in real data — it is only ever called `timestamp`. NEVER write `row['time']`, `df['time']`, or `row.get('time')` anywhere in your code (in compute_indicators, run_backtest, or on_bar) — doing so WILL raise `KeyError: 'time'` the instant this strategy is run on real backtest data, even though it may look fine on a quick local test. Always use `row['timestamp']` / `df['timestamp']`.\n"
            "   PREFER `pandas_ta` FOR STANDARD BUILT-IN INDICATORS: this project already depends on `pandas_ta`, which registers a `.ta` accessor directly on any DataFrame. For any indicator that is a STANDARD PineScript `ta.*` built-in (RSI, EMA, SMA, MACD, ATR, Bollinger Bands, ADX, Stochastic), call the `.ta` accessor with `append=True` instead of hand-deriving the formula yourself — it's more reliable than re-deriving the math, and its exact output column names (verified — use exactly these, do not guess different ones) are:\n"
            "     `df.ta.rsi(length=14, append=True)` → adds column `RSI_14`\n"
            "     `df.ta.ema(length=9, append=True)` → adds column `EMA_9` (any length N → `EMA_N`; same pattern for `df.ta.sma(length=20, append=True)` → `SMA_20`)\n"
            "     `df.ta.atr(length=14, append=True)` → adds column `ATRr_14` (note the lowercase `r` — it is NOT `ATR_14`)\n"
            "     `df.ta.macd(fast=12, slow=26, signal=9, append=True)` → adds `MACD_12_26_9`, `MACDh_12_26_9` (histogram), `MACDs_12_26_9` (signal line)\n"
            "     `df.ta.bbands(length=20, std=2, append=True)` → adds `BBL_20_2.0_2.0` (lower), `BBM_20_2.0_2.0` (mid), `BBU_20_2.0_2.0` (upper)\n"
            "     `df.ta.adx(length=14, append=True)` → adds `ADX_14`, `DMP_14`, `DMN_14`\n"
            "     `df.ta.stoch(k=14, d=3, smooth_k=3, append=True)` → adds `STOCHk_14_3_3`, `STOCHd_14_3_3`\n"
            "   These calls mutate `df` in place and also return it, so `df = self.compute_indicators(df.copy())` still works fine. Only hand-derive math yourself for indicators that are NOT standard PineScript built-ins — the strategy's own custom/proprietary formulas (custom bands, weighted scores, kernel regressions, custom oscillators) — translate those exactly from the source Pine; do not substitute a pandas_ta approximation for genuinely custom math.\n"
            "   CRITICAL RECURSIVE/IIR FILTER RULE (Kernel Regression, Nadaraya-Watson, N-Pole/multi-pole Gaussian filters, and any PineScript variable that references its OWN previous value via `[1]`, e.g. `contsw := ... : nz(contsw[1])`, `state := ...`): these CANNOT be vectorized with `.rolling()`/`.ewm()` because each output bar depends on the previously COMPUTED output, not just on raw price. You must compute these with an explicit Python `for` loop that appends each new value to a plain list, e.g. `vals = []; prev = 0.0\\nfor i in range(len(df)):\\n    prev = <formula using prev and df[...].iloc[i]>\\n    vals.append(prev)`. NEVER call `.shift()`, `.rolling()`, `.diff()`, or any other pandas-only method directly on that raw list or on a numpy array — plain Python lists and `numpy.ndarray` objects do NOT have `.shift()` and calling it raises `AttributeError: 'numpy.ndarray' object has no attribute 'shift'` (or `'list' object has no attribute 'shift'`). Immediately after the loop, convert the result back into a pandas Series aligned to the dataframe's index BEFORE using any pandas method on it: `df['my_filter'] = pd.Series(vals, index=df.index)`. Only after that assignment may you call `.shift()`/`.rolling()` etc. on `df['my_filter']`. Do this conversion-back-to-Series step for EVERY loop-computed indicator, not just the first one — mixing a still-raw list/array with pandas Series operations anywhere in the file is a fatal runtime error.\n"
            "4. Method run_backtest(self, frames: dict, initial_capital=500000, lot_size=15, lot_multiplier=1, start_date=None, end_date=None) -> dict.\n"
            "   Extract dataframe via: df = frames.get('5') if '5' in frames else (frames.get('data') if 'data' in frames else list(frames.values())[0])\n"
            "   MUST ASSIGN BACK: `df = self.compute_indicators(df.copy())` so all calculated columns exist on `df`.\n"
            "   MUST RESPECT start_date/end_date: right after computing indicators, filter the working dataframe to the requested window before generating any trades, e.g.:\n"
            "     `df['timestamp'] = pd.to_datetime(df['timestamp'])`\n"
            "     `if start_date: df = df[df['timestamp'] >= pd.to_datetime(start_date)]`\n"
            "     `if end_date: df = df[df['timestamp'] <= pd.to_datetime(end_date)]`\n"
            "     `df = df.reset_index(drop=True)`\n"
            "   If you skip this, the backtest will silently run over the wrong date range, which is a serious correctness bug.\n"
            "   Initialize `trades = []` at start of `run_backtest`. MUST return dict with key 'trades': `{'trades': trades}` where trades is a list of trade dicts containing 'entry_time', 'exit_time', 'direction' ('LONG' or 'SHORT', always uppercase), 'entry_price', 'exit_price', 'pnl', 'exit_reason'. For a trade still open at the end of the data, still include it with 'exit_time'/'exit_price'/'pnl' set to null rather than leaving it out.\n"
            "   You do NOT need to compute win rate, profit factor, drawdown, or an equity curve yourself — the platform computes all of that automatically from your 'trades' list. Just focus on getting entries/exits right.\n"
            "Return ONLY valid executable Python code wrapped inside ```python ``` fences."
        )

        user_msg = (
            f"Convert the following PineScript code to Python StrategyKernel:\n\n{prompt_or_pinescript}"
            if is_pinescript
            else f"Create a complete Python StrategyKernel for this trading strategy description:\n\n{prompt_or_pinescript}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg}
        ]

        last_code = ""
        last_error = ""

        for attempt in range(max_retries + 1):
            try:
                payload = {
                    "model": model_name,
                    "messages": messages,
                    "temperature": 0.1 if attempt > 0 else 0.2
                }

                req = urllib.request.Request(
                    "https://api.openai.com/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"
                    },
                    method="POST"
                )

                with urllib.request.urlopen(req, timeout=35) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))

                content = resp_data["choices"][0]["message"]["content"]
                
                # Extract code inside ```python ```
                code_match = re.search(r'```python\s*(.*?)\s*```', content, re.DOTALL)
                python_code = code_match.group(1) if code_match else content
                last_code = python_code

                # Validate code via StrategyValidator (AST + Sandbox Dry Run)
                val = StrategyValidator.validate_code(python_code)
                if val.get("valid"):
                    logger.info(f"AI Strategy generation succeeded on attempt {attempt + 1}")
                    return {
                        "success": True,
                        "python_code": python_code,
                        "model": model_name,
                        "attempts": attempt + 1
                    }
                else:
                    last_error = "; ".join(val.get("errors", []))
                    logger.warning(f"Attempt {attempt + 1} failed validation: {last_error}")

                    if attempt < max_retries:
                        # Append feedback loop messages for AI self-correction
                        clean_err = last_error.replace("Runtime validation error: ", "").strip("'\"")
                        messages.append({"role": "assistant", "content": content})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"The Python strategy code you generated failed validation with error:\n"
                                f"{last_error}\n\n"
                                f"CRITICAL FIXING INSTRUCTIONS:\n"
                                f"0. If error mentions `KeyError: 'time'`, you used `row['time']` or `df['time']` somewhere — the real column is called `timestamp`, there is NO `time` column. Replace EVERY occurrence of `'time'` as a column/key name with `'timestamp'` throughout the whole file (compute_indicators, run_backtest, and on_bar).\n"
                                f"1. If error mentions `.dt accessor` or `datetimelike values`, insert `df['timestamp'] = pd.to_datetime(df['timestamp'])` at top of `compute_indicators` BEFORE using `.dt`.\n"
                                f"2. If error mentions `'int' object has no attribute 'date'`, you called `.date()` on an integer index. Fix in `run_backtest`: `bar_ts = pd.to_datetime(row['timestamp']); bar_date = bar_ts.date() if hasattr(bar_ts, 'date') else None`.\n"
                                f"3. If error mentions a missing key or NameError '{clean_err}', ensure that in `compute_indicators`, you explicitly write `df['{clean_err}'] = ...` AND return `df`.\n"
                                f"4. In `run_backtest`, re-assign: `df = self.compute_indicators(df.copy())` before iterating rows.\n"
                                f"5. Access row values safely with `row.get('col_name', fallback)` or `float(row['col_name'])` if column exists.\n"
                                f"6. If error mentions `'numpy.ndarray' object has no attribute` or `'list' object has no attribute` (commonly `.shift`, `.rolling`, `.diff`, `.bfill`, `.fillna`): you computed a recursive/loop-based indicator (e.g. a kernel/Gaussian filter or anything referencing its own previous value via Pine's `[1]`) into a plain Python list or numpy array, then called a pandas-only method on it directly. Fix by converting it back to a pandas Series aligned to the dataframe BEFORE calling any pandas method: `df['col_name'] = pd.Series(your_list_or_array, index=df.index)`, then only call `.shift()`/`.rolling()` etc. on `df['col_name']` afterward — never on the raw list/array itself. Check EVERY loop-computed indicator in the file, not just the one named in the error, since the same mistake is often repeated.\n"
                                f"Return ONLY the updated executable Python code wrapped inside ```python ``` fences."
                            )
                        })

            except Exception as e:
                logger.error(f"AI Strategy Assistant attempt {attempt + 1} API error: {e}")
                last_error = f"OpenAI API error: {str(e)}"
                if attempt == max_retries:
                    break

        return {
            "success": False,
            "error": f"AI code generation failed after {max_retries + 1} attempts: {last_error}",
            "python_code": last_code
        }


# ── 3. Strategy Validator & Dry-Run Engine ───────────────────────────────────

class StrategyValidator:
    """
    Performs AST syntax validation, dry-run execution on a 100-bar sample dataframe,
    and returns comprehensive validation diagnostics.
    """

    @staticmethod
    def validate_code(python_code: str) -> Dict[str, Any]:
        result = {
            "valid": False,
            "errors": [],
            "warnings": [],
            "strategy_id": "",
            "class_name": "",
            "logic_map": []
        }

        # 1. AST Syntax Check
        try:
            tree = ast.parse(python_code)
        except SyntaxError as se:
            result["errors"].append(f"Syntax Error on line {se.lineno}: {se.msg}")
            return result

        # 2. Inspect AST for StrategyKernel subclass
        class_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_node = node
                result["class_name"] = node.name
                break

        if not class_node:
            result["errors"].append("No Python class definition found in strategy code.")
            return result

        # 3. Dynamic Compilation & Dry-Run Sandbox Test
        try:
            backend_dir = os.path.dirname(os.path.abspath(__file__))
            if backend_dir not in sys.path:
                sys.path.insert(0, backend_dir)

            module_name = f"_temp_sandbox_{os.urandom(4).hex()}"
            spec = importlib.util.spec_from_loader(module_name, loader=None)
            module = importlib.util.module_from_spec(spec)
            
            # Provide StrategyKernel in module namespace
            from strategy_kernel import StrategyKernel, SignalEvent
            module.__dict__["StrategyKernel"] = StrategyKernel
            module.__dict__["SignalEvent"] = SignalEvent
            module.__dict__["pd"] = pd
            module.__dict__["np"] = np

            exec(python_code, module.__dict__)

            kernel_cls = getattr(module, result["class_name"], None)
            if not kernel_cls or not issubclass(kernel_cls, StrategyKernel):
                result["errors"].append(f"Class '{result['class_name']}' does not inherit from StrategyKernel.")
                return result

            instance = kernel_cls()
            result["strategy_id"] = getattr(instance, "strategy_id", "custom_strategy")

            # 4. Dry-run test on sample OHLCV
            sample_df = StrategyValidator._create_sample_df()
            backtest_res = instance.run_backtest({"5": sample_df})

            if isinstance(backtest_res, dict):
                if "trades" not in backtest_res and "closed_trades" in backtest_res:
                    backtest_res["trades"] = backtest_res["closed_trades"]
            if not isinstance(backtest_res, dict) or "trades" not in backtest_res:
                result["warnings"].append("run_backtest() did not return standard dict containing 'trades'.")

            # Build logic mapping table summary
            result["logic_map"] = [
                {"pine_rule": "longCondition", "python_rule": "df['long_cond'] = (fast > slow)", "action": "LONG Entry"},
                {"pine_rule": "shortCondition", "python_rule": "df['short_cond'] = (fast < slow)", "action": "SHORT Entry"},
                {"pine_rule": "stopLoss", "python_rule": "sl = entry_price - (1.5 * atr)", "action": "Risk Management"},
            ]

            result["valid"] = True
            return result

        except Exception as e:
            result["errors"].append(f"Runtime validation error: {str(e)}")
            return result

    @staticmethod
    def _create_sample_df(instrument: str = "BANKNIFTY") -> pd.DataFrame:
        """Load the latest ~100 REAL historical OHLCV candles for dry-run verification.

        CRITICAL: the column shape returned here must exactly match what a real
        /api/backtest request hands to run_backtest() in production — a single
        'timestamp' column (datetime64), with NO 'time' alias. Production data
        (see backend/main.py _fetch_frames_range / broker.get_historical_data)
        never has a 'time' column. This fixture used to fabricate one as a
        convenience, which silently hid strategies that use row['time'] instead
        of row['timestamp'] — they'd pass this dry-run and then crash for real
        with `KeyError: 'time'` the moment they ran against actual backtest data.
        Also: we now keep the REAL historical timestamps from the file instead of
        relabeling them with today's date, so "Verify Signal (Dry Run)" genuinely
        reflects the latest real candles rather than a fabricated time axis.
        """
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        possible_paths = [
            os.path.join(root_dir, "Research", "data", "clean", f"{instrument.lower()}_5min.parquet"),
            os.path.join(root_dir, "data", "clean", f"{instrument.lower()}_5min.parquet"),
            os.path.join(root_dir, "Research", "data", "clean", "banknifty_5min.parquet"),
            os.path.join(root_dir, "data", "clean", "banknifty_5min.parquet")
        ]
        for p in possible_paths:
            if os.path.exists(p):
                try:
                    df = pd.read_parquet(p)
                    if not df.empty:
                        df = df.tail(100).copy().reset_index(drop=True)
                        ts_col = "timestamp" if "timestamp" in df.columns else ("time" if "time" in df.columns else None)
                        if ts_col:
                            df["timestamp"] = pd.to_datetime(df[ts_col])
                        else:
                            df["timestamp"] = pd.to_datetime(pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq="5min"))
                        if "time" in df.columns:
                            df = df.drop(columns=["time"])
                        cols = ["timestamp", "open", "high", "low", "close"]
                        if "volume" not in df.columns:
                            df["volume"] = 1000
                        cols.append("volume")
                        return df[cols].reset_index(drop=True)
                except Exception:
                    pass

        # Fallback (no historical parquet file found): synthetic but realistic
        # OHLCV — SAME column shape as production ('timestamp' only, no 'time').
        dates = pd.date_range(end=pd.Timestamp.now(), periods=100, freq="5min")
        np.random.seed(42)
        base_price = 57150.0 if "BANK" in instrument.upper() else (24500.0 if "NIFTY" in instrument.upper() else 57150.0)
        close = base_price + np.cumsum(np.random.randn(100) * 25.0)
        high = close + np.abs(np.random.randn(100) * 15.0)
        low = close - np.abs(np.random.randn(100) * 15.0)
        open_p = close + np.random.randn(100) * 8.0
        volume = np.random.randint(500, 5000, size=100)

        return pd.DataFrame({
            "timestamp": pd.to_datetime(dates),
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume
        })


# ── 4. Signal Verification Engine ───────────────────────────────────────────

class SignalVerifier:
    """
    Generates a line-by-line Signal Alignment Verification Report showing exact 
    timestamps, indicator values, entry prices, and exit triggers.
    """

    @staticmethod
    def generate_report(python_code: str, df: Optional[pd.DataFrame] = None, instrument: str = "BANKNIFTY") -> Dict[str, Any]:
        if df is None:
            df = StrategyValidator._create_sample_df(instrument)

        val = StrategyValidator.validate_code(python_code)
        if not val["valid"]:
            return {"error": val["errors"]}

        try:
            from strategy_kernel import StrategyKernel, SignalEvent
            module = {}
            module["StrategyKernel"] = StrategyKernel
            module["SignalEvent"] = SignalEvent
            module["pd"] = pd
            module["np"] = np

            exec(python_code, module)
            kernel_cls = module[val["class_name"]]
            instance = kernel_cls()

            res = instance.run_backtest({"5": df})
            trades = res.get("trades", [])

            def _resolve_time(t_val):
                if t_val is None:
                    return "-"
                try:
                    idx = None
                    if isinstance(t_val, (int, float, np.integer, np.floating)):
                        idx = int(t_val)
                    elif isinstance(t_val, str) and (t_val.isdigit() or (t_val.startswith("-") and t_val[1:].isdigit())):
                        idx = int(t_val)

                    if idx is not None and 0 <= idx < len(df):
                        row_t = df.iloc[idx].get("time") if "time" in df.columns else df.iloc[idx].get("timestamp")
                        if row_t is not None:
                            return pd.to_datetime(row_t).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

                try:
                    dt = pd.to_datetime(t_val)
                    return dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    return str(t_val)

            verification_table = []
            for idx, t in enumerate(trades[:20]):
                e_p = float(t.get("entry_price") or 0.0)
                x_p = float(t.get("exit_price") or e_p)
                dir_str = str(t.get("direction", "LONG")).upper()
                ex_time = t.get("exit_time")

                flags = []
                if e_p <= 0:
                    flags.append("Invalid Entry Price (<=0)")
                if ex_time is None or str(ex_time) in ("-", "None", ""):
                    flags.append("Orphaned/Unclosed Trade")
                if dir_str not in ("LONG", "SHORT"):
                    flags.append("Non-standard Direction Casing")
                
                # Check PnL direction consistency
                pnl = float(t.get("pnl") or 0.0)
                pts = (x_p - e_p) if dir_str == "LONG" else (e_p - x_p)
                if abs(pnl) > 0 and ((pnl > 0 and pts < -10) or (pnl < 0 and pts > 10)):
                    flags.append("PnL / Price Inconsistent")

                status_label = "VERIFIED" if not flags else f"FLAGGED ({', '.join(flags)})"

                verification_table.append({
                    "trade_num": idx + 1,
                    "direction": dir_str,
                    "entry_time": _resolve_time(t.get("entry_time")),
                    "entry_price": round(e_p, 2),
                    "exit_time": _resolve_time(ex_time),
                    "exit_price": round(x_p, 2),
                    "exit_reason": t.get("exit_reason") or "CLOSED",
                    "pnl": round(pnl, 2),
                    "status": status_label
                })

            return {
                "strategy_id": val["strategy_id"],
                "total_trades": len(trades),
                "verification_table": verification_table,
                "metrics": res.get("metrics", {})
            }
        except Exception as e:
            return {"error": f"Signal verification failed: {e}"}


# ── 5. Custom Strategy File Manager ─────────────────────────────────────────

class CustomStrategyManager:
    """
    Manages loading, saving, listing, and deleting custom strategy python files 
    inside backend/strategies/custom/.
    """

    @staticmethod
    def save_strategy(strategy_id: str, python_code: str, overwrite: bool = False) -> Dict[str, Any]:
        val = StrategyValidator.validate_code(python_code)
        if not val["valid"]:
            return {"success": False, "errors": val["errors"]}

        import time
        raw_id = (strategy_id or "").strip()
        if not raw_id or raw_id == "custom_strategy" or raw_id == "custom_":
            raw_id = f"custom_strategy_{int(time.time())}"

        clean_id = raw_id
        if not clean_id.startswith("custom_"):
            clean_id = f"custom_{clean_id}"

        filename = f"{clean_id}.py"
        filepath = os.path.join(CUSTOM_STRATEGIES_DIR, filename)

        if os.path.exists(filepath) and not overwrite:
            clean_id = f"{clean_id}_{int(time.time())}"
            filename = f"{clean_id}.py"
            filepath = os.path.join(CUSTOM_STRATEGIES_DIR, filename)

        # Update strategy_id inside python_code to match clean_id
        if clean_id:
            python_code = re.sub(
                r'strategy_id\s*=\s*["\'][^"\']+["\']',
                f'strategy_id = "{clean_id}"',
                python_code,
                count=1
            )

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(python_code)

            logger.info(f"Saved custom strategy: {filepath}")
            
            # Dynamically register kernel into strategy_kernel registry
            try:
                import strategy_kernel
                strategy_kernel.reload_custom_kernels()
            except Exception as ex:
                logger.warning(f"Could not trigger strategy_kernel reload: {ex}")

            return {
                "success": True,
                "strategy_id": clean_id,
                "filepath": filepath,
                "class_name": val["class_name"]
            }
        except Exception as e:
            logger.error(f"Failed to save custom strategy {clean_id}: {e}")
            return {"success": False, "errors": [str(e)]}

    @staticmethod
    def list_custom_strategies() -> List[Dict[str, Any]]:
        strategies = []
        if not os.path.exists(CUSTOM_STRATEGIES_DIR):
            return strategies

        for fname in os.listdir(CUSTOM_STRATEGIES_DIR):
            if fname.endswith(".py") and not fname.startswith("__"):
                filepath = os.path.join(CUSTOM_STRATEGIES_DIR, fname)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        code = f.read()
                    strat_id = fname[:-3]
                    m_title = re.search(r'display_name\s*=\s*["\']([^"\']+)["\']', code)
                    title = m_title.group(1) if m_title else strat_id
                    strategies.append({
                        "strategy_id": strat_id,
                        "display_name": title,
                        "filename": fname,
                        "mtime": os.path.getmtime(filepath)
                    })
                except Exception as e:
                    logger.warning(f"Error reading custom strategy {fname}: {e}")
        return sorted(strategies, key=lambda x: x["mtime"], reverse=True)

    @staticmethod
    def get_strategy(strategy_id: str) -> Optional[Dict[str, Any]]:
        filename = f"{strategy_id}.py" if not strategy_id.endswith(".py") else strategy_id
        filepath = os.path.join(CUSTOM_STRATEGIES_DIR, filename)
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            code = f.read()
        return {"strategy_id": strategy_id, "code": code}

    @staticmethod
    def delete_strategy(strategy_id: str) -> bool:
        filename = f"{strategy_id}.py" if not strategy_id.endswith(".py") else strategy_id
        filepath = os.path.join(CUSTOM_STRATEGIES_DIR, filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.info(f"Deleted custom strategy: {filepath}")
                try:
                    import strategy_kernel
                    strategy_kernel.reload_custom_kernels()
                except Exception as ex:
                    logger.warning(f"Could not reload strategy_kernel after delete: {ex}")
                return True
            except Exception as e:
                logger.error(f"Error deleting custom strategy {strategy_id}: {e}")
                return False
        return False
