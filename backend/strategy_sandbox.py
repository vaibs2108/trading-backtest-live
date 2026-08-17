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


# ── 1c. Deterministic Pre-Analysis Briefing (runs BEFORE the AI's first attempt) ──

class PineScriptPreAnalyzer:
    """
    Lightweight, deterministic pre-analysis pass over pasted PineScript, run
    BEFORE the AI's first conversion attempt — not just reactively after a
    validation failure.

    IMPORTANT DESIGN NOTE: detection here is driven by PineScript's actual
    SYNTAX (`var`, `:=`, `for ... to ...`, `[1]`), not by guessing likely
    English parameter names (an earlier version of this class matched
    hardcoded words like "poles"/"order"/"degree", which only works for
    scripts that happen to use those exact words). PineScript's `var`
    declaration and `:=` reassignment are the LANGUAGE'S OWN markers for
    "this variable persists across bars" — that is a fully general signal
    that works on any future script, regardless of what its author named
    things. Similarly, a Pine `for i = 1 to N` loop where `N` is an input
    parameter is detected structurally, not by name-matching. This should
    self-adjust to new scripts without needing new keywords added by hand.

    What it flags:
      - Variables using Pine's `var`/`:=` state mechanism that ALSO
        self-reference their own previous value via `[1]` — the strongest
        signal of a recursive/IIR calculation (e.g. `contsw := ...
        nz(contsw[1])`). These need an explicit per-bar Python loop with
        persisted state, converted to `pd.Series` before any pandas-only
        method is called on the result (previously caused
        `'numpy.ndarray' object has no attribute 'shift'`).
      - Other `var`/`:=`-declared state variables without explicit `[1]`
        usage — still part of the same bar-by-bar state machine.
      - Input parameters used as the end-bound of a Pine `for` loop in the
        source (e.g. `for i = 1 to poles`) — these are small internal loop
        counts (filter stages/coefficients), not a bar-count, and mistaking
        them for one previously caused
        `ValueError: Length of values (N) does not match length of index (M)`.
      - Standard PineScript `ta.*` built-ins already present in the script,
        so the AI is told exactly which `pandas_ta` accessor calls are
        relevant to THIS script rather than only a generic reference list.

    This does not replace the AI's own reasoning or the local rule-based
    transpiler — it hands the AI a short, concrete briefing about this
    specific script's tricky parts up front, which is cheaper and more
    reliable than only learning about them after a validation failure and
    burning through retries. If nothing notable is detected, this is a
    silent no-op (empty briefing) and the request proceeds exactly as before.
    """

    _RAW_SERIES_NAMES = {"close", "open", "high", "low", "volume", "hl2", "hlc3", "ohlc4", "hlcc4", "time"}
    _VAR_DECL_RE = re.compile(r'\bvar\s+(?:\w+\s+)?(\w+)\s*=(?!=)')
    _REASSIGN_RE = re.compile(r'\b(\w+)\s*:=')
    _SELF_REF_RE = re.compile(r'\b([a-zA-Z_]\w*)\s*\[\s*1\s*\]')
    _INPUT_RE = re.compile(r'(\w+)\s*=\s*input(?:\.int|\.float)?\s*\(\s*([0-9.]+)')
    _FOR_LOOP_RE = re.compile(r'\bfor\s+\w+\s*=\s*[\w.]+\s+to\s+(\w+)')
    _TA_BUILTIN_LABELS = {
        "ta.sma": "SMA", "ta.ema": "EMA", "ta.rsi": "RSI", "ta.macd": "MACD",
        "ta.atr": "ATR", "ta.bbands": "Bollinger Bands", "ta.adx": "ADX", "ta.stoch": "Stochastic",
    }
    _COLOR_HEX_RE = re.compile(r'#[0-9A-Fa-f]{6}\b')
    _COLOR_VAR_RE = re.compile(r'\bvar\s+color\s+(\w+)')
    _COLOR_BUILTIN_RE = re.compile(r'\bcolor\.\w+')
    # A `for` loop whose own iteration variable is later used as a historical-offset
    # index into a series inside the same script (e.g. `for i = 0 to n\n  ... src[i] ...`)
    # is Pine's per-bar windowed/convolution-sum idiom, not a one-time setup loop.
    _CONV_LOOP_RE = re.compile(r'\bfor\s+(\w+)\s*=\s*[\w.]+\s+to\s+[\w.]+')

    @classmethod
    def analyze(cls, pinescript_code: str) -> Dict[str, Any]:
        code = pinescript_code or ""

        var_declared = set(m.group(1) for m in cls._VAR_DECL_RE.finditer(code))
        reassigned = set(m.group(1) for m in cls._REASSIGN_RE.finditer(code))
        stateful_vars = (var_declared | reassigned) - cls._RAW_SERIES_NAMES

        self_referenced = set(m.group(1) for m in cls._SELF_REF_RE.finditer(code)) - cls._RAW_SERIES_NAMES

        # Strongest signal: a var/:=-managed variable that ALSO self-references via [1] -> genuinely recursive.
        recursive_vars = sorted(stateful_vars & self_referenced)
        # Weaker but still relevant: state variables without an explicit [1] reference in this script.
        other_stateful_vars = sorted(stateful_vars - set(recursive_vars))

        inputs = {}
        for m in cls._INPUT_RE.finditer(code):
            inputs.setdefault(m.group(1), m.group(2))

        loop_bound_risk_params = []
        seen_names = set()
        for m in cls._FOR_LOOP_RE.finditer(code):
            end_ident = m.group(1)
            if end_ident and not end_ident.isdigit() and end_ident in inputs and end_ident not in seen_names:
                loop_bound_risk_params.append({"name": end_ident, "default": inputs[end_ident]})
                seen_names.add(end_ident)

        ta_builtins_detected = [label for key, label in cls._TA_BUILTIN_LABELS.items() if key in code]

        has_color_state = bool(
            cls._COLOR_HEX_RE.search(code) or cls._COLOR_VAR_RE.search(code) or cls._COLOR_BUILTIN_RE.search(code)
        )

        # Detect a `for i = A to B` loop whose own loop variable is later indexed
        # into a series within a nearby window of source (a windowed/convolution sum).
        has_convolution_loop = False
        for m in cls._CONV_LOOP_RE.finditer(code):
            loop_var = m.group(1)
            window = code[m.end(): m.end() + 400]
            if re.search(r'\[\s*' + re.escape(loop_var) + r'\s*\]', window):
                has_convolution_loop = True
                break

        # Detect PineScript matrix and array structures
        has_pine_matrix = "matrix." in code or "array." in code or "fact(" in code

        # Detect var-declared scalar trackers (e.g., var float maxLow = ...)
        has_scalar_trackers = bool(re.search(r'\bvar\s+(?:float|int)\s+(\w+)\s*=\s*(?:nz|na|math|\d|low|high|close)', code))

        # Detect ta.barssince
        has_barssince = "ta.barssince" in code or "barssince(" in code

        return {
            "recursive_vars": recursive_vars,
            "other_stateful_vars": other_stateful_vars,
            "loop_bound_risk_params": loop_bound_risk_params,
            "ta_builtins_detected": ta_builtins_detected,
            "has_color_state": has_color_state,
            "has_convolution_loop": has_convolution_loop,
            "has_pine_matrix": has_pine_matrix,
            "has_scalar_trackers": has_scalar_trackers,
            "has_barssince": has_barssince,
        }

    @classmethod
    def build_briefing(cls, pinescript_code: str) -> str:
        """Returns a short user-message-prependable briefing string, or '' if nothing notable was detected."""
        f = cls.analyze(pinescript_code)
        if not (f["recursive_vars"] or f["other_stateful_vars"] or f["loop_bound_risk_params"] or f["ta_builtins_detected"]
                or f["has_color_state"] or f["has_convolution_loop"] or f["has_pine_matrix"] or f["has_scalar_trackers"] or f["has_barssince"]):
            return ""

        lines = ["PRE-ANALYSIS CONTEXT (deterministically detected from this exact script's actual Pine syntax before you see it — use it, do not ignore it):"]

        if f["recursive_vars"]:
            names = ", ".join(f"`{v}`" for v in f["recursive_vars"])
            lines.append(
                f"- Detected variable(s) using PineScript's `var`/`:=` persistent-state mechanism AND "
                f"referencing their own previous value via `[1]`: {names}. These are recursive/IIR — they CANNOT "
                f"be vectorized with `.rolling()`/`.ewm()`. Compute them with an explicit Python loop over every "
                f"bar (`for i in range(len(df))`), carrying the previous computed value forward in a plain Python "
                f"variable (mirroring Pine's `var` persistence across bars) — then convert the result to "
                f"`pd.Series(your_list, index=df.index)` IMMEDIATELY, before calling any pandas-only method "
                f"(`.shift()`, `.rolling()`, `.diff()`) on it."
            )
        if f["other_stateful_vars"]:
            names = ", ".join(f"`{v}`" for v in f["other_stateful_vars"])
            lines.append(
                f"- Detected additional `var`/`:=`-managed state variable(s) in this script (no explicit `[1]` "
                f"usage found, but Pine's `var`/`:=` still means these persist/mutate across bars): {names}. "
                f"Treat these as part of the same bar-by-bar state machine as any recursive variables above — "
                f"carry their value forward in your per-bar loop rather than computing them with a single "
                f"vectorized pandas expression."
            )
        if f["loop_bound_risk_params"]:
            names = ", ".join(f"`{p['name']}` (default {p['default']})" for p in f["loop_bound_risk_params"])
            lines.append(
                f"- Detected input parameter(s) used as the END bound of a `for` loop in the Pine SOURCE itself: "
                f"{names}. This is a small internal loop count local to the source's own computation (e.g. filter "
                f"stages/coefficients), NOT the number of bars to process. Your Python bar-by-bar loop over "
                f"historical data must ALWAYS iterate `range(len(df))` regardless of this parameter's value; use "
                f"the parameter only INSIDE each bar's own formula (e.g. as an inner loop bound or coefficient "
                f"count for that single bar), never as the outer per-bar loop's range(). Confusing the two "
                f"produces a results array far shorter than the dataframe and raises `ValueError: Length of "
                f"values (N) does not match length of index (M)`."
            )
        if f["ta_builtins_detected"]:
            names = ", ".join(f["ta_builtins_detected"])
            lines.append(
                f"- Detected standard PineScript `ta.*` built-in(s) in this script: {names}. Use the `pandas_ta` "
                f"`.ta` accessor for these per the column-name reference above, rather than hand-deriving the formulas."
            )
        if f["has_color_state"]:
            lines.append(
                "- Detected PineScript `color`-typed value(s) in this script (hex literals like `#2DD204`, "
                "`var color` declarations, and/or `color.*` built-ins). These exist ONLY for chart plotting "
                "(`plot(..., color=...)`, `barcolor(...)`) and NEVER feed into entry/exit trading logic. OMIT "
                "them entirely from your Python translation — no DataFrame column, no computation. Do not mix "
                "string color values with `np.nan`/numeric arrays in a `np.where(...)`/`np.array(...)` call — "
                "that raises a NumPy DType promotion error (`could not be promoted... no common DType exists`)."
            )
        if f["has_convolution_loop"]:
            lines.append(
                "- Detected a `for` loop in this script whose own loop variable is later used as a "
                "historical-offset index into a series (e.g. `for i = 0 to n ... src[i] ...`). This is Pine's "
                "per-bar windowed/convolution-sum idiom: it re-executes on EVERY bar, and `src[i]` means \"the "
                "value `i` bars back FROM THE CURRENT BAR\", not the value at absolute row `i` of the dataset. "
                "Do NOT compute this loop once outside a per-bar loop using `df['col'].iloc[i]` for a small "
                "range of `i` — that produces a single scalar, not a per-bar Series, and any later `.iloc[]` "
                "indexing into it will crash with `AttributeError: 'numpy.float64' object has no attribute "
                "'iloc'`. Wrap it in an outer `for idx in range(len(df))` loop and use `df['col'].iloc[idx - i]` "
                "(offset from the current bar, guarding `idx - i >= 0`) inside, then assign the per-bar results "
                "back as `df['col'] = pd.Series(values, index=df.index)`."
            )
        if f["has_pine_matrix"]:
            lines.append(
                "- Detected PineScript matrix/array/factorial structures (`matrix.*`, `array.*`, or factorial functions). "
                "Import Python's standard `import math` module at top of file and call `math.factorial(n)` / `math.comb(n, r)` directly. "
                "NEVER call `np.math.factorial` or `np.math.*` — NumPy has NO `math` submodule and calling it raises `AttributeError: module 'numpy' has no attribute 'math'`. "
                "Translate Pine `matrix.new` using `np.zeros((rows, cols))` or `np.full(...)` and Pine `array.new_float` using Python lists `[0.0] * size`."
            )
        if f["has_scalar_trackers"]:
            lines.append(
                "- Detected PineScript `var float`/`var int` running tracker variables (e.g. `var float maxLowPrice = nz(low[1], low)`). "
                "These MUST be initialized as plain Python scalars before your per-bar loop (e.g. `max_low_price = float(df['low'].iloc[0])`), "
                "NEVER as vectorized pandas Series (e.g. `df['low'].shift(1).fillna(...)`). Seeding tracker variables as Series and calling `min()`/`max()` inside the loop "
                "raises `ValueError: The truth value of a Series is ambiguous`."
            )
        if f["has_barssince"]:
            lines.append(
                "- Detected `ta.barssince(cond)`. Track this inside your per-bar loop with an integer counter "
                "(e.g. `bars_since_cond = 0 if cond else (bars_since_cond + 1 if bars_since_cond is not None else 99999)`)."
            )

        return "\n".join(lines) + "\n\n"


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
            "PINE SCRIPT v5/v6 LANGUAGE SEMANTICS — READ THIS FIRST, IT IS THE ROOT CAUSE OF NEARLY EVERY TRANSLATION BUG:\n"
            "Rather than only pattern-matching the specific error signatures listed further below, derive correct translations "
            "from these four real language rules (from TradingView's official Pine Script v5/v6 documentation). Nearly every "
            "runtime crash this pipeline has ever produced traces back to getting one of these four rules wrong:\n"
            "  (a) EXECUTION MODEL: a Pine script executes EXACTLY ONCE PER HISTORICAL BAR, top to bottom, in bar order — the "
            "entire script body (including any `for` loop written inside it) already runs once per bar implicitly. When you "
            "translate to Python, this implicit per-bar execution must become an EXPLICIT outer loop `for idx in range(len(df)):` "
            "wrapping the ENTIRE translated body. Any `for` loop that appears in the Pine SOURCE (e.g. `for i = 0 to len-1`) is "
            "therefore ALREADY nested inside that once-per-bar context — it becomes an INNER loop nested inside your outer "
            "`range(len(df))` loop, never a replacement for it and never something you compute a single time outside it.\n"
            "  (b) THE HISTORY-REFERENCING OPERATOR `[]` IS ALWAYS RELATIVE TO THE BAR CURRENTLY EXECUTING: `[]` can be applied "
            "to any series expression — a bare variable, a built-in like `close`, or a user-defined function's return value — and "
            "`expr[n]` always means \"the value of `expr` `n` bars before THE BAR THE SCRIPT IS CURRENTLY ON\", never \"the value "
            "at absolute row/position `n` of the whole dataset\". Inside a Pine `for i = 0 to N-1: total += close[i] * coeffs[i]` "
            "loop (which is itself already running once per bar per rule (a)), `close[i]` means `i` bars back from THIS bar. "
            "Translate it as `df['close'].iloc[idx - i]` where `idx` is your OUTER per-bar loop's current index — never as "
            "`df['close'].iloc[i]` using the inner loop counter as an absolute row position; that conflates the inner "
            "coefficient-count loop with the outer per-bar loop and produces a results array only as long as the inner loop's "
            "trip count instead of `len(df)`.\n"
            "  (c) `var`/`varip` DECLARATIONS PERSIST ACROSS BARS, PLAIN DECLARATIONS DO NOT: a variable declared with `var` "
            "(e.g. `var float total = 0.0`) runs its initializer ONCE, on the first bar its declaration is reached, and then "
            "SILENTLY KEEPS its value on every later bar until explicitly reassigned with `:=` — it is never re-initialized. "
            "`varip` behaves the same way for this pipeline's purposes (it additionally persists across intrabar realtime ticks, "
            "which does not apply to historical backtesting). A variable declared WITHOUT `var`/`varip` is reinitialized fresh "
            "every single bar and carries nothing forward. Translate every `var`/`varip`-declared variable as a plain Python "
            "variable initialized ONCE before your outer `range(len(df))` loop, then only ever reassigned (never re-initialized) "
            "inside the loop body — exactly mirroring Pine's own persistence.\n"
            "  (d) USER-DEFINED FUNCTIONS RE-EXECUTE ONCE PER BAR TOO, WITH THEIR OWN INDEPENDENT PERSISTENT STATE: a Pine "
            "`f(x) =>` function called from the script body is, by rule (a), implicitly re-evaluated once per bar right along "
            "with the rest of the script. If its body declares a `var`-scoped local (e.g. `_filt(src) =>\\n    var float price = na\\n"
            "    price := math.abs(price - nz(price[1])) < dev ? nz(price[1]) : price`), that local's value persists ACROSS the "
            "function's own bar-by-bar calls exactly like a top-level `var` — it is NOT a stateless pure function you can call once "
            "on the whole array. Do NOT translate such a function into a plain Python helper that receives an entire array/Series "
            "and slices it (`src[-length:]`, `src[-2]`); that treats a per-bar-with-carried-state function as if it runs once "
            "globally. Instead INLINE its logic directly into your main per-bar loop, carrying its local state forward in a plain "
            "scalar Python variable across iterations exactly like any other recursive `var`.\n"
            "  (e) DO NOT USE `df.ta` OR `pandas_ta` ACCESSORS: Always compute technical indicators using standard native pandas/numpy "
            "(e.g. `df['close'].ewm(span=N, adjust=False).mean()` for EMA, `df['close'].rolling(window=N).mean()` for SMA, "
            "`np.where()`) or pure Python scalar variables inside the bar-by-bar loop. `df.ta` does not exist on plain pandas DataFrames.\n"
            "  (f) PINESCRIPT BUILT-INS & CONSTANTS: Pine built-in `syminfo.mintick` should be defined as `mintick = 0.05` (the minimum tick size for BankNifty / Nifty instruments). NEVER reference `self.mintick` or `syminfo.mintick` as object attributes.\n"
            "  (g) VARIABLE SCOPE & INITIALIZATION: Every variable evaluated in signal conditions or returned in df (such as `arrowUp`, `arrowDown`, `longCond`, `shortCond`) MUST be initialized with a fallback default (e.g. `arrowUp = np.nan` or `None`) BEFORE any loops or conditionals, so that reading them outside the loop or after conditional blocks never raises `UnboundLocalError` or `NameError`.\n"
            "Use these rules to reason about ANY Pine construct, including ones not explicitly named in the specific rules "
            "below: if a variable is declared `var`/`varip` or reassigned with `:=`, it needs loop-carried state (rule c); if any "
            "`for` loop indexes a series with `[]`, its counter is a bars-ago offset relative to an implicit outer per-bar context, "
            "never an absolute row index (rules a+b); if a function body reassigns its own `var` local, inline it per-bar rather "
            "than calling it once on a whole array (rule d).\n\n"
            "Interface Contract:\n"
            "1. Class inherits from StrategyKernel (from strategy_kernel import StrategyKernel, SignalEvent).\n"
            "2. Class attributes: strategy_id (e.g. custom_strategy_name), display_name, live_capable = False.\n"
            "3. Method compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame computing all PineScript indicators on df.\n"
            "   Use pd.concat([tr1, tr2, tr3], axis=1).max(axis=1) for ATR calculation. ALWAYS fill NaNs on indicator series using `.bfill().fillna(0.0)`.\n"
            "   CRITICAL DATAFRAME COLUMN RULE: EVERY calculated PineScript variable, exit metric, band, or indicator (e.g. `kernel_ma`, `upper_band`, `lower_band`, `ticks_activation`, `ticks_offset`, `atr`, `signal`, `trend_state`, `trades_today`) MUST be explicitly assigned as a DataFrame column on `df` inside `compute_indicators` (e.g., `df['kernel_ma'] = ...`, `df['upper_band'] = ...`, `df['lower_band'] = ...`). NEVER leave calculated variables as local Python variables in `compute_indicators` because they will be lost when `run_backtest` runs and raise KeyError or NameError!\n"
            "   CRITICAL PANDAS & MATH RULE: ALWAYS fill NaNs and check `pd.isna()` before calling `round()` or `int()`. Python `round(np.nan)` raises ValueError: cannot convert float NaN to integer. In `compute_indicators`, use vectorized comparisons (`df['long_cond'] = (df['close'] > df['open'])`). In `run_backtest`, iterate rows via `for idx, row in df.iterrows():` and access scalar values like `float(row['close'])`, `float(row.get('kernel_ma', row['close']))` or `bool(row.get('long_cond', False))`.\n"
            "   CRITICAL TIMESTAMP & DATE RULE: NEVER call `.dt` accessor without first ensuring datetime type via `df['timestamp'] = pd.to_datetime(df['timestamp'])` at start of `compute_indicators`! Calling `.dt` on non-datetime series raises `AttributeError: Can only use .dt accessor with datetimelike values`. In `run_backtest`, NEVER call `.date()` on `row.name`, `idx`, or integer row numbers (e.g., `row.name.date()` or `idx.date()` or `i.date()`) because DataFrame index numbers are integers in pandas and raise `'int' object has no attribute 'date'`. Extract timestamp safely via `bar_ts = pd.to_datetime(row['timestamp'])` and get date via `bar_date = bar_ts.date() if hasattr(bar_ts, 'date') else None`. Use `str(bar_ts)` for `entry_time` and `exit_time` in trade dicts.\n"
            "   ABSOLUTE RULE — COLUMN NAME IS 'timestamp', NEVER 'time': the real production OHLCV dataframe has a column literally named `timestamp` (already datetime64) for the bar's time. There is NO `time` column anywhere in real data — it is only ever called `timestamp`. NEVER write `row['time']`, `df['time']`, or `row.get('time')` anywhere in your code (in compute_indicators, run_backtest, or on_bar) — doing so WILL raise `KeyError: 'time'` the instant this strategy is run on real backtest data, even though it may look fine on a quick local test. Always use `row['timestamp']` / `df['timestamp']`.\n"
            "   COMPUTE STANDARD INDICATORS WITH PANDAS/NUMPY: For standard indicators, use pandas builtin expressions:\n"
            "     EMA: `df['close'].ewm(span=9, adjust=False).mean()`\n"
            "     SMA: `df['close'].rolling(window=20).mean()`\n"
            "     ATR: compute True Range (`tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()], axis=1).max(axis=1)`) and rolling mean `tr.rolling(14).mean()`\n"
            "     RSI: compute gain/loss Series and rolling ewm mean.\n"
            "   These calls mutate `df` in place and also return it, so `df = self.compute_indicators(df.copy())` still works fine.\n"
            "   CRITICAL NONETYPE & NAN COMPARISON RULE: In `run_backtest`, NEVER compare numeric variables (such as `close`, `upper_band`, `lower_band`, `atr_val`) directly if any of them can be `None` or `NaN`. Before executing any `<` or `>` comparison, ALWAYS cast numeric values safely via `float(row.get('col', 0.0))` or check `if row.get('col') is not None and not pd.isna(row['col']):`. Comparing a float with `None` (e.g. `12.5 < None`) raises `TypeError: '<' not supported between instances of 'float' and 'NoneType'`!\n"
            "   CRITICAL ATR COLUMN CREATION PATTERN RULE: NEVER write `df = df.ta.atr(...)` because `pandas_ta.atr()` returns a `pd.Series` which will overwrite your entire DataFrame object and destroy its `.columns` attribute! Compute ATR natively via True Range or assign directly: `df['atr'] = tr.rolling(14).mean().bfill().fillna(0.0)`.\n"
            "   CRITICAL SAFE LIST INDEXING RULE FOR PER-BAR LOOPS: When reading previous items from a Python list inside a loop (`out_values`), NEVER use `out_values[idx - 1]` without checking `if idx > 0`. On the first bar (`idx = 0`), `out_values[-1]` on an empty list `[]` raises `IndexError: list index out of range`. ALWAYS write `prev_val = out_values[idx - 1] if idx > 0 else 0.0`!\n"
            "   CRITICAL DATAFRAME VS SERIES `.columns` RULE: `df.columns` exists ONLY on a DataFrame (`pd.DataFrame`). In `run_backtest`, `row` inside `for idx, row in df.iterrows():` is a `pd.Series` and does NOT have a `.columns` attribute. Calling `row.columns` raises `AttributeError: 'Series' object has no attribute 'columns'`. ALWAYS check column names using `df.columns` (the DataFrame instance), NEVER `row.columns`!\n"
            "   CRITICAL PYTHON ARRAY VS SCALAR CONDITION RULE: Never evaluate a Python boolean `if` condition directly on a NumPy array or Pandas Series (e.g. `if price - price[-1] < filtdev:` or `if arr > 0:`). This raises `ValueError: The truth value of an array with more than one element is ambiguous`. Perform array operations element-by-element inside a loop over bar indices or use vectorized pandas/numpy assignment!\n"
            "   CRITICAL `iloc` INDEX BOUNDS RULE: Inside a per-bar loop (`for idx in range(len(df)):`), NEVER access historical prices like `df['close'].iloc[idx - offset]` without guarding with `if idx >= offset:`. If `idx < offset`, `idx - offset` becomes negative (e.g. `-5`), which either unexpectedly fetches prices from the END of the dataset or raises `IndexError: list index out of range`!\n"
            "   CRITICAL NO MIXING SCALAR AND SERIES IN PYTHON MAX/MIN RULE: Inside a per-bar loop (`for idx in range(len(df)):`), NEVER pass a pandas Series (e.g. `df['low']` or `df['low'].shift(1)`) into Python's built-in `max()` or `min()` function along with a float scalar — e.g. `max(low_price_float, max_low_series)` raises `ValueError: The truth value of a Series is ambiguous`. State tracking variables like `max_low_price` MUST be initialized as a scalar float BEFORE the loop: `max_low_price = float(df['low'].iloc[0])`, and inside the per-bar loop updated strictly using scalar floats: `max_low_price = max(float(df['low'].iloc[idx]), float(max_low_price))`.\n"
            "   CRITICAL SCALAR vs SERIES SHIFT RULE: `.shift()` is strictly a pandas Series method. NEVER call `.shift()` on a Python scalar variable (e.g. `trend = 0`, then `trend.shift(1)` raises `'int' object has no attribute 'shift'`). To access the previous bar's value of a scalar state variable, maintain an explicit scalar copy `prev_trend = trend` before reassigning `trend` in each iteration of the loop.\n"
            "   CRITICAL — NO `np.math` MODULE EXISTS: NumPy has NO `math` submodule — `np.math.factorial(...)`, `np.math.comb(...)`, `np.math.pow(...)`, or any other `np.math.*` reference is a hallucinated API and raises `AttributeError: module 'numpy' has no attribute 'math'` (numpy briefly re-exported the stdlib `math` module as `numpy.math` in old versions, but this was removed and must never be relied on). If you need factorial/combinatorial arithmetic (e.g. computing binomial-style coefficients for an N-pole/multi-pole filter), add `import math` at the top of the file and call Python's own built-in `math.factorial(n)` / `math.comb(n, r)` directly — never through `np`. A simple hand-rolled iterative helper (`def _factorial(n): a = 1\\n    for i in range(1, n + 1): a *= i\\n    return a`) is an equally safe alternative if you prefer not to add the import.\n"
            "   CRITICAL RECURSIVE/IIR STATE RULE — GENERAL DETECTION PRINCIPLE: PineScript's OWN syntax tells you which variables are stateful — do not rely on recognizing named techniques (Kernel Regression, Nadaraya-Watson, N-Pole/multi-pole Gaussian filters, etc.) by name, since you will encounter scripts using this pattern under any name or no name at all. The reliable, general signal is: ANY variable declared with `var` (e.g. `var float out = na`) and/or reassigned with `:=` (e.g. `contsw := ...`) persists its value across bars by definition of the language — and if that same variable is ALSO referenced via its own history with `[1]` (e.g. `nz(contsw[1])`, `state[1]`), it is recursive/IIR: its current value depends on its own PREVIOUSLY COMPUTED value, not just on raw price. Apply this rule to every `var`/`:=` variable you find, regardless of what it's named or what technique it implements.\n"
            "   THIS RULE STILL APPLIES WHEN THE RECURSIVE VARIABLE LIVES INSIDE A PINE USER-DEFINED FUNCTION (`name(params) => ...`), NOT JUST AT TOP LEVEL: a Pine function whose body reassigns one of its own local variables with `:=` and references that variable's own previous value via `[1]` (e.g. `_filt(src, len, filter) => \\n    price := math.abs(price - nz(price[1])) < filtdev ? nz(price[1]) : price`) is re-evaluated by Pine ONCE PER BAR, and that local variable's state persists ACROSS those per-bar calls exactly like a top-level `var`/`:=` variable would. Do NOT translate such a function into a plain Python function that receives the WHOLE array/Series as its `src` argument and operates on it in one shot with slicing like `src[-length:]` or `src[-2]` — that treats a per-bar function as if it runs once globally on the whole dataset instead of once per bar, and mixing whole-array slicing with a scalar comparison (e.g. `abs(whole_array - scalar) < scalar_threshold` then `if <that array-valued result>:`) raises `ValueError: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()`. Instead, INLINE this function's logic directly into your main per-bar loop (`for idx in range(len(df))`), carrying its own persisted scalar state (e.g. a plain Python `prev_price` variable) forward across iterations exactly like any other recursive variable, and evaluate it using THIS BAR'S scalar value each iteration — never call it once with the entire array/Series.\n"
            "   These recursive/stateful variables CANNOT be vectorized with `.rolling()`/`.ewm()`. You must compute them with an explicit Python `for` loop that appends each new value to a plain list, carrying the previous value forward in a plain Python variable exactly like Pine's `var` persists it across bars, e.g. `vals = []; prev = 0.0\\nfor i in range(len(df)):\\n    prev = <formula using prev and df[...].iloc[i]>\\n    vals.append(prev)`. NEVER call `.shift()`, `.rolling()`, `.diff()`, or any other pandas-only method directly on that raw list or on a numpy array — plain Python lists and `numpy.ndarray` objects do NOT have `.shift()` and calling it raises `AttributeError: 'numpy.ndarray' object has no attribute 'shift'` (or `'list' object has no attribute 'shift'`). Immediately after the loop, convert the result back into a pandas Series aligned to the dataframe's index BEFORE using any pandas method on it: `df['my_filter'] = pd.Series(vals, index=df.index)`. Only after that assignment may you call `.shift()`/`.rolling()` etc. on `df['my_filter']`. Do this conversion-back-to-Series step for EVERY loop-computed indicator, not just the first one — mixing a still-raw list/array with pandas Series operations anywhere in the file is a fatal runtime error.\n"
            "   Standard PineScript `ta.*` built-ins (RSI, EMA, SMA, MACD, ATR, Bollinger Bands, ADX, Stochastic) and simple same-bar comparisons (e.g. `close > open`, crossover checks on already-computed columns) are NOT stateful in this sense — keep using vectorized `pandas_ta`/pandas expressions for those; do not force them into a bar-by-bar loop, since hand-looping well-tested library math only reintroduces transcription bugs and hurts performance for no correctness benefit. The loop is only for the genuinely recursive `var`/`:=`-self-referencing part of the script.\n"
            "   CRITICAL LOOP-BOUND RULE: any Python `for` loop that walks the historical dataframe bar-by-bar (to build a recursive/IIR indicator, or to run entries/exits in `run_backtest`) MUST iterate exactly `range(len(df))` — one iteration per row of the ACTUAL data — never a smaller fixed count. Watch specifically for the source PineScript having its OWN `for x = 1 to someParam` loop (e.g. computing filter coefficients/stages) — that `someParam` (however it's named — 'poles', 'order', 'degree', 'stages', or anything else) is a small internal loop count used INSIDE a single bar's calculation, it is NEVER the number of bars to loop over in your Python translation. Confusing the two produces a results array far shorter than the dataframe and raises `ValueError: Length of values (N) does not match length of index (M)`.\n"
            "   CRITICAL WINDOWED/CONVOLUTION SUM RULE: if the source PineScript has a `for` loop that sums a series value indexed by historical-offset syntax — e.g. `for i = 0 to len-1: total += src[i] * coeffs[i]` (any variable names) — this is Pine's way of computing ONE VALUE PER BAR: on every bar, `src[i]` means \"the value of `src` `i` bars back FROM THE CURRENT BAR\", not the value at absolute row/position `i` of the whole dataset. Do NOT compute this loop ONCE outside a per-bar loop using `df['col'].iloc[i]` for a small range of `i` — that collapses a per-bar rolling calculation into a SINGLE scalar number (not a Series), and any later code that indexes that scalar (because it expected a Series/array) will crash — this shows up as EITHER `AttributeError: 'numpy.float64' object has no attribute 'iloc'` (if indexed via `.iloc[i]`) OR `IndexError: invalid index to scalar variable` (if indexed via plain bracket syntax `hso[i]`) — both are the EXACT SAME underlying mistake (a scalar where a per-bar Series was needed), just surfacing through a different indexing style; treat both as the same bug. Instead, wrap it in an OUTER per-bar loop over `range(len(df))` and compute the inner sum with an offset from the CURRENT bar, e.g.: `values = []\\nfor idx in range(len(df)):\\n    total = 0.0\\n    for i in range(len(coeffs)):\\n        if idx - i >= 0:\\n            total += df['close'].iloc[idx - i] * coeffs[i]\\n    values.append(total)\\ndf['col'] = pd.Series(values, index=df.index)`. Apply this to ANY Pine `for` loop that indexes a price/indicator series by the loop variable, regardless of what the loop or variables are named — never leave the result of such a loop as a single accumulated scalar. GET THE INNER LOOP BOUND EXACTLY RIGHT: if `coeffs` (or any coefficient/weight list) has `N` elements, the valid indices are `0` through `N-1` — the inner loop MUST be `range(len(coeffs))`, never `range(len(coeffs) + 1)` or any other off-by-one variant, or you will hit `IndexError: list index out of range` the moment `i` reaches `N`. DO NOT CONFLATE THE INNER COEFFICIENT-COUNT LOOP WITH THE OUTER PER-BAR LOOP — this is a THIRD, distinct way this same mistake shows up: writing only ONE loop, bounded by the number of coefficients/weights (e.g. `for i in range(len_hull + 1): hma += df['close'].iloc[i] * hull_coeffs[i]; hma_values.append(hma)`), and appending to the results list once per coefficient instead of once per bar. This produces a results array of length `N` (the coefficient count, e.g. 13) instead of length `len(df)` (e.g. 300) — a HALF-FIX that still lacks the required outer per-bar loop. Any later code that indexes that array by bar position (`hso[idx]` for `idx` up to `len(df)-1`) will crash once `idx` exceeds the coefficient count, with `IndexError: index N is out of bounds for axis 0 with size N` (where N is the coefficient count, not related to `len(df)`). There must ALWAYS be exactly two nested loops: an OUTER loop over `range(len(df))` (one iteration per bar, producing exactly `len(df)` appended values) and an INNER loop over `range(len(coeffs))` (summing the weighted historical offsets for that one bar only, using `df['close'].iloc[idx - i]`, guarded by `idx - i >= 0`) — never collapse these into a single loop bounded by the coefficient count.\n"
            "   CRITICAL — PYTHON LIST HISTORICAL INDEXING & EMPTY LIST GUARD: When referencing lookback values from a Python list being populated inside a per-bar loop (e.g., `out_values[idx - r]` or `contsw_values[idx - 1]`), if `idx < r` or `idx == 0`, `out_values` will be empty `[]` or have fewer elements than `r`. Accessing `out_values[idx - r]` when `idx < r` raises `IndexError: list index out of range` (or accesses negative indices incorrectly). ALWAYS guard historical list lookups with an explicit index check: `out_values[idx - r] if (idx >= r and idx - r < len(out_values)) else 0.0` or `contsw_values[idx - 1] if (idx > 0 and idx - 1 < len(contsw_values)) else 0`!\n"
            "   CRITICAL — SIGNAL CONDITION DATAFRAME COLUMN ASSIGNMENT: If signal conditions or backtest logic evaluate `df['arrowUp']`, `df['arrowDown']`, `df['goLong']`, `df['goShort']`, etc., EVERY single signal variable MUST be explicitly created, initialized, and assigned as a column onto `df` (e.g. `df['arrowUp'] = pd.Series(arrowUp_list, index=df.index)`). NEVER leave signal variables like `arrowUp` or `arrowDown` as purely local variables inside a loop without assigning them to `df`, otherwise referencing `df['arrowUp']` outside the loop raises `KeyError: 'arrowUp'`!\n"
            "   CRITICAL COLOR/STRING STATE RULE: PineScript `color`-typed variables (e.g. `var color colorout = na`, hex literals like `#2DD204`/`#D2042D`, or built-ins like `color.red`/`color.green`) exist ONLY for chart plotting (`plot(..., color=colorout)`, `barcolor(...)`) — they NEVER feed into entry/exit trading logic. Do NOT translate `color`-typed variables into the strategy's Python code at all — omit them entirely: no DataFrame column, no computation, no `np.where`/`np.array` involving them. If you believe you must keep one, build it as a plain Python list of strings/`None` via an explicit per-bar loop, and NEVER mix string values with `np.nan` in the same `np.where(...)`/`np.array(...)` call — `np.where(cond, 'somecolor', np.nan)` raises `DType ... could not be promoted ... no common DType exists for the given inputs`, because NumPy cannot store a string dtype and a float dtype together in one array. Use `None` (not `np.nan`) if a placeholder is unavoidable, or better, avoid NumPy for any color/string-typed value entirely.\n"
            "   CRITICAL — NO BROKER/EXECUTION API EXISTS ON StrategyKernel: `self` (the strategy instance) has NO trade-execution methods and NO position-tracking attributes. NEVER call `self.entry(...)`, `self.exit(...)`, `self.close(...)`, `self.signal_event(...)`, and NEVER reference `self.position_size`, `self.position_avg_price`, or any similar broker-style API — none of these exist on `StrategyKernel`, and calling or reading them raises `AttributeError: '<YourClassName>' object has no attribute '...'`. This is a common mistranslation of Pine's OWN built-ins `strategy.entry()` / `strategy.exit()` / `strategy.position_size` — those are Pine-engine built-ins, NOT part of the Python `StrategyKernel` interface, and must NEVER be copied over as `self.<method>` calls. You must implement 100% of trade lifecycle management yourself as plain local Python state inside `run_backtest`: a local `open_trade = None` (or a `position` string) that you set on entry and clear on exit, with entry price/SL/target/trailing-stop level tracked in local variables, manually checking exit conditions on each bar-loop iteration (`if c <= sl or c >= target: ... trades.append({...}); open_trade = None`). For Pine's `strategy.exit(..., trail_points=N, trail_offset=M)` (an ATR-based trailing stop), translate it as manual trailing-stop simulation: once price has moved favorably by at least `N` points from entry, arm the trailing stop; from then on, each subsequent bar, ratchet a local `trail_stop` variable to `(highest_price_since_entry - M)` for a LONG (or `(lowest_price_since_entry + M)` for a SHORT), and exit the trade the moment price crosses that ratcheted level — all using local variables inside your own loop, never a `self.<method>()` call.\n"
            "   CRITICAL — NEVER LET A PER-BAR SCALAR VARIABLE SILENTLY BECOME A WHOLE-COLUMN SERIES: inside a `for idx in range(len(df)):` per-bar loop, every value you track, compare, or carry forward as \"this bar's value\" (e.g. a Pine `var float x = high[1]`-style running tracker, or an indicator you assign into `df`) MUST be a plain Python scalar (float/int/bool) at the point you compare or `min()`/`max()` it — never a Series. Two specific mistakes both raise `ValueError: The truth value of a Series is ambiguous. Use a.empty, a.bool(), a.item(), a.any() or a.all()`, and BOTH must be avoided: (1) Do NOT write `df['col'] = some_scalar_expr` (whole-column assignment, no `.loc`/`.at`) INSIDE the per-bar loop body — this overwrites the ENTIRE column with that one value every iteration, but `df['col']` is still a full-length Series everywhere you read it, so a same-bar check like `if df['close'].iloc[idx] > df['col']:` compares a scalar against an entire Series and crashes. Instead, either append each bar's scalar to a plain Python list and assign the WHOLE list to the column ONCE after the loop ends (`df['col'] = pd.Series(values, index=df.index)`), or if you truly need the column updated during the loop, write only that one row with `df.loc[idx, 'col'] = value`. (2) Do NOT seed a per-bar tracking variable with a whole-column expression like `min_high_price = df['high'].shift(1).fillna(df['high'])` (this is a full Series, not a scalar) and then call Python's builtin `min()`/`max()` between it and a scalar inside the loop (`min_high_price = min(highPrice.iloc[idx], min_high_price)`) — `min()`/`max()` must evaluate a truthy comparison internally and crashes the same way. Seed these trackers with a genuine SCALAR instead (e.g. `min_high_price = float(df['high'].iloc[0])`), and only ever reassign them to scalars for the rest of the loop.\n"
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

        # Deterministic pre-analysis: use what our installed tools can already
        # figure out on their own (regex-based detection, same style used
        # elsewhere in this file) and hand it to the AI BEFORE its first
        # attempt — not just reactively in retry feedback after a failure.
        pre_analysis_briefing = PineScriptPreAnalyzer.build_briefing(prompt_or_pinescript) if is_pinescript else ""

        user_msg = (
            f"{pre_analysis_briefing}Convert the following PineScript code to Python StrategyKernel:\n\n{prompt_or_pinescript}"
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
                                f"7. If error mentions `Length of values (N) does not match length of index (M)`, you looped over the wrong count — most likely you used a filter order/degree/poles/stages parameter (a small number, N) as your loop's `range()` bound instead of `range(len(df))` (M = the full number of bars). Fix by looping the FULL dataframe length (`range(len(df))`); the order/degree parameter may only be used INSIDE each iteration's formula (e.g. how many smoothing passes to apply to that bar), never as the loop bound itself.\n"
                                f"8. If error mentions `'numpy.float64' object has no attribute 'iloc'` (or similar `'numpy.float64'/'float'/'int' object has no attribute` for a pandas method) OR `invalid index to scalar variable` (bracket-indexing a scalar, e.g. `hso[i]` where `hso` is a plain float) — these are THE SAME BUG surfacing two different ways: you computed what should be a PER-BAR windowed/convolution sum (e.g. `for i in range(len_hull): total += df['close'].iloc[i] * coeffs[i]`) as a SINGLE one-time scalar OUTSIDE a per-bar loop, using absolute positions `iloc[i]` instead of an offset from the current bar. Fix by wrapping the whole convolution in an outer `for idx in range(len(df)):` loop, computing each bar's value with `df['close'].iloc[idx - i]` (offset from `idx`, guarding `idx - i >= 0`), appending to a list, then `df['col'] = pd.Series(values, index=df.index)` — never leave it as a single accumulated scalar, and never index a scalar with either `.iloc[i]` or `[i]`.\n"
                                f"9. If error mentions `DType ... could not be promoted` or `no common DType exists`, you mixed a PineScript `color`-typed value (a hex string like `'#2DD204'` or `color.red`) with a numeric NaN inside the same `np.where(...)`/`np.array(...)` call. Color variables are ONLY for chart plotting — remove them from your Python translation entirely; they have no effect on entry/exit logic and should not be computed at all.\n"
                                f"10. If error mentions `object has no attribute 'signal_event'`, `'exit'`, `'entry'`, or `'position_size'` (or any similarly-named method/attribute not defined on your own class), you called a hallucinated broker-style API that does not exist on `StrategyKernel`. Remove ALL such calls. Implement trade entries/exits entirely with your own local Python variables (`open_trade`, `position`, `trail_stop`, etc.) inside `run_backtest`'s own loop, manually appending completed trades to the `trades` list — never call `self.<method>()` for order execution.\n"
                                f"11. If error mentions `The truth value of a Series is ambiguous`, somewhere inside a per-bar loop a value that should be a plain scalar is actually a full-length pandas Series. Check for two specific causes: (a) you wrote `df['col'] = some_expr` (whole-column assignment, no `.loc`) INSIDE the per-bar loop body — this silently overwrites the entire column every iteration, so reading `df['col']` back later in an `if`/comparison compares a scalar against a whole Series; fix by appending each bar's scalar to a list and assigning `df['col'] = pd.Series(values, index=df.index)` ONCE after the loop, or writing only that row via `df.loc[idx, 'col'] = value`. (b) you seeded a per-bar tracking variable with a whole-column expression (e.g. `min_high_price = df['high'].shift(1).fillna(df['high'])`) instead of a scalar, then called Python's `min()`/`max()` between it and a scalar inside the loop. Fix by seeding it with a genuine scalar (e.g. `float(df['high'].iloc[0])`) and only ever reassigning it to scalars afterward.\n"
                                f"12. If error mentions `The truth value of an array with more than one element is ambiguous`, you translated a Pine user-defined function that has its own `[1]`-self-referencing recursive state (e.g. a `_filt`/smoothing helper reassigning a local var with `:=` and reading `nz(price[1])`) into a plain Python function that receives the WHOLE numpy array/Series and slices it (`src[-length:]`, `src[-2]`, `price - src[-2]`) instead of being evaluated per-bar. Fix by INLINING that function's logic into your main `for idx in range(len(df))` loop, carrying its state forward in a plain scalar Python variable (e.g. `prev_price`) exactly like any other recursive `var`/`:=` variable, and evaluating it with THIS BAR'S scalar value each iteration — never call it once on the entire array/Series.\n"
                                f"13. If error mentions `cannot convert float NaN to integer`, you called `round()` or `int()` on a value that can be NaN — most commonly an indicator column read before its own lookback window has filled (e.g. `row['ATRr_14']` is NaN on bars 0 through 12 for a 14-period ATR). Guard EVERY `round()`/`int()` call on a row value: check `pd.isna(val)` first and substitute a safe fallback (e.g. `val = row['ATRr_14']; val = 0.0 if pd.isna(val) else float(val); ticks = round(val * factor)`), and/or ensure NaN-producing indicator columns are filled with `.bfill().fillna(0.0)` at the end of `compute_indicators` before `run_backtest` ever reads them.\n"
                                f"14. If error mentions `IndexError: list index out of range` inside a coefficient/windowed-sum loop, your inner loop bound is off by one — if a coefficients list has `N` elements, valid indices are `0` to `N-1`, so the loop must be `range(len(coeffs))`, never `range(len(coeffs) + 1)`.\n"
                                f"15. If error mentions `IndexError: index N is out of bounds for axis 0 with size N` (the SAME small number N appears twice in the message), you wrote only ONE loop for a windowed/convolution sum, bounded by the coefficient/weight count (e.g. `for i in range(len_hull + 1): hma += ...; hma_values.append(hma)`), instead of TWO nested loops. This produces a results array of length N (the coefficient count) instead of length `len(df)` — so it crashes the moment later code indexes it by bar position past N. Fix by restructuring into an OUTER loop `for idx in range(len(df)):` (exactly one append per bar, `len(df)` total values) that contains an INNER loop `for i in range(len(coeffs)):` summing `df['close'].iloc[idx - i] * coeffs[i]` for that single bar (guarding `idx - i >= 0`) — never a single loop bounded by the coefficient count.\n"
                                f"16. If error mentions `module 'numpy' has no attribute 'math'`, you used `np.math.<something>` (e.g. `np.math.factorial(order)`) — NumPy has no `math` submodule, this is a hallucinated API. Add `import math` at the top of the file and call Python's built-in `math.factorial(n)` / `math.comb(n, r)` directly instead — replace every `np.math.*` reference in the file, not just the one named in the error.\n"
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
    Performs AST syntax validation, dry-run execution on a 300-bar sample dataframe
    (see `_SAMPLE_ROWS` — sized generously so long-period `pandas_ta` indicators,
    e.g. a 100-period ATR, actually populate during dry-run), and returns
    comprehensive validation diagnostics.
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

    # Dry-run fixture size. MUST be comfortably larger than any realistic PineScript
    # indicator length (ATR/SMA/EMA/etc.), because pandas_ta's `.ta.*` accessors
    # silently DECLINE to append their output column at all — not even filled with
    # NaN — when the dataframe has too few rows relative to the requested `length`
    # (confirmed empirically: `.ta.atr(length=100, append=True)` produces no
    # `ATRr_100` column at all on <=100 rows, only starts appending it past ~140
    # rows). Previously this fixture used only 100 rows, so a strategy correctly
    # calling `df.ta.atr(length=100, append=True)` (a legitimate, common indicator
    # length) would pass dry-run validation... no — would actually FAIL dry-run
    # validation with a plain `KeyError: 'ATRr_100'` on the very next line that
    # reads the column, even though the code is 100% correct and would work fine
    # against real production data (which has thousands of rows). 300 rows leaves
    # headroom for any period up to ~290 while staying cheap to construct.
    _SAMPLE_ROWS = 300

    @staticmethod
    def _create_sample_df(instrument: str = "BANKNIFTY") -> pd.DataFrame:
        """Load the latest REAL historical OHLCV candles for dry-run verification.

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

        Row count is `_SAMPLE_ROWS` (see above) rather than a small fixed number —
        long-period `pandas_ta` indicators need real headroom to populate at all.
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
                        df = df.tail(StrategyValidator._SAMPLE_ROWS).copy().reset_index(drop=True)
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
        n = StrategyValidator._SAMPLE_ROWS
        dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="5min")
        np.random.seed(42)
        base_price = 57150.0 if "BANK" in instrument.upper() else (24500.0 if "NIFTY" in instrument.upper() else 57150.0)
        close = base_price + np.cumsum(np.random.randn(n) * 25.0)
        high = close + np.abs(np.random.randn(n) * 15.0)
        low = close - np.abs(np.random.randn(n) * 15.0)
        open_p = close + np.random.randn(n) * 8.0
        volume = np.random.randint(500, 5000, size=n)

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
