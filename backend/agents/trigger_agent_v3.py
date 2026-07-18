"""
trigger_agent_v3.py — Trigger Agent v3: 5m scenario-driven entries with LevelMap SL/targets.

Key changes from v2:
- SL and targets from LevelMap levels, not ATR multiples
- Entry types are scenario-driven (PULLBACK_TO_LEVEL, BREAKOUT, etc.)
- No additive scoring — 5m structure either confirms the scenario or doesn't
- Exit logic preserved but enhanced with LevelMap awareness
"""
import logging
import numpy as np
import pandas as pd
from .base import BaseAgent
from .base_v3 import TriggerScenario, SwingScenario, FlowScenario, LevelMap, PriceLevel
from config import get_settings, INSTRUMENT_META

logger = logging.getLogger(__name__)


class TriggerAgentV3(BaseAgent):
    """5m execution: scenario-driven entries with LevelMap-based risk."""

    def evaluate(self, row: pd.Series, swing: SwingScenario, flow: FlowScenario,
                 level_map: LevelMap, position: str = "NONE") -> TriggerScenario:
        reasons = []

        close = self._safe_float(self._get_val(row, "close", 0))
        atr = self._safe_float(self._get_val(row, "atr", close * 0.002))
        if atr < 1:
            atr = close * 0.002
        high = self._safe_float(self._get_val(row, "high", close))
        low = self._safe_float(self._get_val(row, "low", close))

        hour = self._safe_int(self._get_val(row, "_hour", 12))
        minute = self._safe_int(self._get_val(row, "_minute", 0))

        # ── Session window ──────────────────────────────────────────────
        cfg = get_settings()
        exch = INSTRUMENT_META.get(cfg.instrument, {}).get("exchange_index", "INDEX")
        mins = hour * 60 + minute
        if exch == "MCX":
            session_start, session_end = 9 * 60, 23 * 60 + 30
        else:
            session_start, session_end = 9 * 60 + 20, 15 * 60 + 20

        # Time quality
        if 9 <= hour < 10:
            market_phase, time_quality = "OPENING", 0.9
        elif 10 <= hour < 14:
            market_phase, time_quality = "MIDDAY", 0.7
        elif hour >= 14:
            market_phase, time_quality = "CLOSING", 0.5
        else:
            market_phase, time_quality = "OUTSIDE", 0.0

        if not (session_start <= mins < session_end):
            return TriggerScenario(signal="HOLD", market_phase="OUTSIDE",
                                   time_quality=0.0, entry_price=close,
                                   reasons=["Outside trading window"])

        # ── 5m indicators ───────────────────────────────────────────────
        st_dir = self._safe_int(self._get_val(row, "supertrend_dir", 0))
        ema_cross = self._safe_int(self._get_val(row, "ema_cross", 0))
        ema7 = self._safe_float(self._get_val(row, "ema7", 0))
        ema21 = self._safe_float(self._get_val(row, "ema21", 0))
        st_val = self._safe_float(self._get_val(row, "supertrend", 0))
        st_flip_age = self._safe_int(self._get_val(row, "st_bars_since_flip", 999))
        macd_hist = self._safe_float(self._get_val(row, "macd_hist", 0))
        srsi_k = self._safe_float(self._get_val(row, "stochrsi_k", 50))
        srsi_phase = self._safe_int(self._get_val(row, "srsi_phase", 0))
        engulfing = self._safe_int(self._get_val(row, "engulfing_signal", 0))
        hammer = self._safe_int(self._get_val(row, "hammer_signal", 0))

        # 5m EMA cascade (same logic as SwingAgent but on 5m)
        if ema7 > 0 and ema21 > 0:
            ema5_bull = ema7 > ema21
            if ema5_bull:
                if close > ema7:
                    ema_cascade_5m = "ABOVE_ALL"
                elif close > ema21:
                    ema_cascade_5m = "BETWEEN_7_21"
                elif st_val > 0 and close > st_val:
                    ema_cascade_5m = "BELOW_21_ABOVE_ST"
                else:
                    ema_cascade_5m = "BELOW_ST"
            else:
                if close < ema7:
                    ema_cascade_5m = "BELOW_ALL"
                elif close < ema21:
                    ema_cascade_5m = "BETWEEN_21_7"
                elif st_val > 0 and close < st_val:
                    ema_cascade_5m = "ABOVE_21_BELOW_ST"
                else:
                    ema_cascade_5m = "ABOVE_ST"
        else:
            ema_cascade_5m = "UNKNOWN"

        # ── EXIT LOGIC (checked first) ──────────────────────────────────
        if position == "LONG":
            exit_score = 0
            exit_reasons = []
            if st_dir == -1:
                exit_score += 1
                exit_reasons.append("5m SuperTrend flipped bearish")
            if macd_hist < 0:
                exit_score += 1
                exit_reasons.append("5m MACD histogram negative")
            if srsi_k > 80 and srsi_phase <= -2:
                exit_score += 1
                exit_reasons.append("5m StochRSI overbought turning")
            if srsi_k > 75:
                exit_score += 0.5
            # v3: also exit if swing cascade changed against us
            if swing.trade_bias == "SHORT":
                exit_score += 1
                exit_reasons.append("Swing bias flipped to SHORT")

            if exit_score >= 2:
                return TriggerScenario(
                    signal="LONG_EXIT", entry_price=close,
                    entry_quality=1.0, market_phase=market_phase,
                    time_quality=time_quality, reasons=exit_reasons,
                )

        elif position == "SHORT":
            exit_score = 0
            exit_reasons = []
            if st_dir == 1:
                exit_score += 1
                exit_reasons.append("5m SuperTrend flipped bullish")
            if macd_hist > 0:
                exit_score += 1
                exit_reasons.append("5m MACD histogram positive")
            if srsi_k < 20 and srsi_phase >= 2:
                exit_score += 1
                exit_reasons.append("5m StochRSI oversold curling")
            if srsi_k < 25:
                exit_score += 0.5
            if swing.trade_bias == "LONG":
                exit_score += 1
                exit_reasons.append("Swing bias flipped to LONG")

            if exit_score >= 2:
                return TriggerScenario(
                    signal="SHORT_EXIT", entry_price=close,
                    entry_quality=1.0, market_phase=market_phase,
                    time_quality=time_quality, reasons=exit_reasons,
                )

        if position != "NONE":
            return TriggerScenario(
                signal=position, entry_price=close,
                market_phase=market_phase, time_quality=time_quality,
                reasons=[f"Holding {position} position — no exit triggered"]
            )

        # ── ENTRY GATE ──────────────────────────────────────────────────
        target_bias = swing.trade_bias
        if target_bias == "NEUTRAL":
            return TriggerScenario(signal="HOLD", entry_price=close,
                                   market_phase=market_phase, time_quality=time_quality,
                                   ema_cascade_5m=ema_cascade_5m,
                                   reasons=["Swing bias neutral — no entry"])

        # ── ENTRY QUALITY — does 5m confirm the scenario? ───────────────
        entry_quality = 0.0
        entry_type = "NONE"
        entry_level = None

        if target_bias == "LONG":
            # 5m SuperTrend bullish
            if st_dir == 1:
                entry_quality += 0.25
                reasons.append("5m SuperTrend bullish")
            if st_flip_age <= 3 and st_dir == 1:
                entry_quality += 0.10
                reasons.append("5m ST just flipped bullish")
            # 5m EMA confirms
            if ema_cross == 1:
                entry_quality += 0.15
                reasons.append("5m EMA7 > EMA21")
            # MACD confirms
            if macd_hist > 0:
                entry_quality += 0.10
            # Candle pattern
            if engulfing == 1:
                entry_quality += 0.10
                reasons.append("Bullish engulfing")
            if hammer == 1:
                entry_quality += 0.08
                reasons.append("Hammer candle")
            # Flow agent supports
            if flow.supports_direction == "LONG":
                entry_quality += 0.15
            # Oscillator context bonus
            if flow.oscillator_context == "OVERSOLD_IN_UPTREND":
                entry_quality += 0.10
                reasons.append("Oversold in uptrend — bounce setup")

            # Determine entry type
            if swing.ema_cascade in ("BETWEEN_21_7", "ABOVE_21_BELOW_ST", "ABOVE_ST"):
                entry_type = "BOUNCE_AT_SUPPORT"
            elif st_flip_age <= 3 and st_dir == 1:
                entry_type = "BREAKOUT"
            else:
                entry_type = "TREND_CONTINUATION"

        else:  # SHORT
            if st_dir == -1:
                entry_quality += 0.25
                reasons.append("5m SuperTrend bearish")
            if st_flip_age <= 3 and st_dir == -1:
                entry_quality += 0.10
                reasons.append("5m ST just flipped bearish")
            if ema_cross == -1:
                entry_quality += 0.15
                reasons.append("5m EMA7 < EMA21")
            if macd_hist < 0:
                entry_quality += 0.10
            if engulfing == -1:
                entry_quality += 0.10
                reasons.append("Bearish engulfing")
            if hammer == -1:
                entry_quality += 0.08
                reasons.append("Shooting star")
            if flow.supports_direction == "SHORT":
                entry_quality += 0.15
            if flow.oscillator_context == "OVERBOUGHT_IN_DOWNTREND":
                entry_quality += 0.10
                reasons.append("Overbought in downtrend — SHORT opportunity")

            if swing.ema_cascade in ("BETWEEN_7_21", "BELOW_21_ABOVE_ST", "BELOW_ST"):
                entry_type = "REJECTION_AT_RESISTANCE"
            elif st_flip_age <= 3 and st_dir == -1:
                entry_type = "BREAKOUT"
            else:
                entry_type = "TREND_CONTINUATION"

        entry_quality = min(1.0, entry_quality)

        # ── Candle pattern ──────────────────────────────────────────────
        candle_pattern = "NONE"
        if target_bias == "LONG":
            if engulfing == 1: candle_pattern = "ENGULFING"
            elif hammer == 1: candle_pattern = "HAMMER"
        else:
            if engulfing == -1: candle_pattern = "ENGULFING"
            elif hammer == -1: candle_pattern = "SHOOTING_STAR"

        # ── SL & TARGET from LevelMap ───────────────────────────────────
        sl, target1, target2 = self._compute_risk_levels(
            target_bias, close, atr, level_map, swing
        )

        risk_pts = abs(close - sl)
        risk_reward = round(abs(target1 - close) / max(risk_pts, 0.01), 2) if risk_pts > 0 else 0

        # ── Signal decision ─────────────────────────────────────────────
        signal = target_bias if entry_quality >= 0.30 else "HOLD"
        if signal == "HOLD":
            reasons.append(f"Entry quality {entry_quality:.2f} < 0.30")

        return TriggerScenario(
            signal=signal,
            entry_type=entry_type,
            entry_level=entry_level,
            ema_cascade_5m=ema_cascade_5m,
            supertrend_5m=st_dir,
            bars_since_5m_flip=st_flip_age,
            candle_pattern=candle_pattern,
            entry_price=close,
            sl=sl,
            target1=target1,
            target2=target2,
            risk_reward=risk_reward,
            entry_quality=round(entry_quality, 3),
            time_quality=time_quality,
            market_phase=market_phase,
            reasons=reasons,
        )

    def _compute_risk_levels(self, direction: str, close: float, atr: float,
                              level_map: LevelMap, swing: SwingScenario):
        """Compute SL, T1, T2 from LevelMap levels (not ATR multiples)."""
        cfg = get_settings()

        if direction == "LONG":
            # SL: nearest level below with decent strength, or ATR fallback
            sl_candidates = [lv for lv in level_map.levels_below
                           if lv.strength >= 0.25 and (close - lv.price) / max(atr, 1) < 3]
            if sl_candidates:
                # First strong level below
                sl_level = sl_candidates[0]
                sl = round(sl_level.price - cfg.sl_level_buffer_atr * atr, 2)  # buffer below level
            else:
                sl = round(close - cfg.atr_sl_mult * atr, 2)

            # T1: swing journey target or next level above
            if swing.journey_target > close:
                target1 = round(swing.journey_target, 2)
            elif level_map.next_target_up:
                target1 = round(level_map.next_target_up.price, 2)
            else:
                target1 = round(close + cfg.atr_t1_mult * atr, 2)

            # T2: next HTF level above or further target
            if level_map.nearest_htf_resistance and level_map.nearest_htf_resistance.price > target1:
                target2 = round(level_map.nearest_htf_resistance.price, 2)
            else:
                target2 = round(close + cfg.atr_t2_mult * atr, 2)

        else:  # SHORT
            # SL: nearest level above with decent strength
            sl_candidates = [lv for lv in level_map.levels_above
                           if lv.strength >= 0.25 and (lv.price - close) / max(atr, 1) < 3]
            if sl_candidates:
                sl_level = sl_candidates[0]
                sl = round(sl_level.price + cfg.sl_level_buffer_atr * atr, 2)
            else:
                sl = round(close + cfg.atr_sl_mult * atr, 2)

            # T1: swing journey target or next level below
            if swing.journey_target > 0 and swing.journey_target < close:
                target1 = round(swing.journey_target, 2)
            elif level_map.next_target_down:
                target1 = round(level_map.next_target_down.price, 2)
            else:
                target1 = round(close - cfg.atr_t1_mult * atr, 2)

            # T2: next HTF level below
            if level_map.nearest_htf_support and level_map.nearest_htf_support.price < target1:
                target2 = round(level_map.nearest_htf_support.price, 2)
            else:
                target2 = round(close - cfg.atr_t2_mult * atr, 2)

        return sl, target1, target2
