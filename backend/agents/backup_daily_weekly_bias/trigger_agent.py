"""
trigger_agent.py — TriggerAgent: 5m + 1m precise entry timing.

Waits for all other agents to align, then finds the exact candle for entry.
Also handles exit signal detection.
"""
import numpy as np
import pandas as pd
from .base import BaseAgent, TriggerState, MacroState, StructureState, MomentumState


class TriggerAgent(BaseAgent):
    """
    Evaluates 5m + 1m data for precise entry/exit timing.
    Only fires when Macro + Structure + Momentum are aligned.
    """

    def evaluate(self, row: pd.Series, macro: MacroState,
                 structure: StructureState, momentum: MomentumState,
                 position: str = "NONE",
                 atr_sl_mult: float = 1.2,
                 atr_t1_mult: float = 2.5,
                 atr_t2_mult: float = 4.0) -> TriggerState:
        """Evaluate entry/exit from latest merged row."""
        reasons = []

        close = self._safe_float(self._get_val(row, "close", 0))
        atr = self._safe_float(self._get_val(row, "atr", close * 0.002))
        if atr < 1:
            atr = close * 0.002

        hour = self._safe_int(self._get_val(row, "_hour",
                              pd.to_datetime(str(self._get_val(row, "timestamp", ""))).hour
                              if "timestamp" in row else 12))
        minute = self._safe_int(self._get_val(row, "_minute", 0))

        # ── TIME QUALITY ────────────────────────────────────────────────
        if 9 <= hour < 10:
            market_phase = "OPENING"
            time_quality = 0.9  # Opening hour — high volume, good moves
        elif 10 <= hour < 14:
            market_phase = "MIDDAY"
            time_quality = 0.7
        elif hour == 14:
            market_phase = "CLOSING"
            time_quality = 0.5  # Lower quality entries near close
        else:
            market_phase = "OUTSIDE"
            time_quality = 0.0

        # Skip if outside trading window
        if hour < 9 or (hour == 9 and minute < 20) or hour >= 15:
            return TriggerState(signal="HOLD", market_phase="OUTSIDE",
                                time_quality=0.0, entry_price=close,
                                reasons=["Outside trading window"])

        # ── EXIT LOGIC (checked first) ──────────────────────────────────
        if position == "LONG":
            exit_score = 0
            exit_reasons = []

            st_dir = self._safe_int(self._get_val(row, "supertrend_dir", 0))
            macd_hist = self._safe_float(self._get_val(row, "macd_hist", 0))
            srsi_k = self._safe_float(self._get_val(row, "stochrsi_k", 50))
            srsi_phase = self._safe_int(self._get_val(row, "srsi_phase", 0))

            if st_dir == -1:
                exit_score += 1
                exit_reasons.append("5m SuperTrend flipped bearish")
            if macd_hist < 0:
                exit_score += 1
                exit_reasons.append("5m MACD histogram negative")
            if srsi_k > 80 and srsi_phase <= -2:
                exit_score += 1
                exit_reasons.append("5m StochRSI overbought and turning")
            if srsi_k > 75:
                exit_score += 0.5

            if exit_score >= 2:
                return TriggerState(
                    signal="LONG_EXIT", entry_price=close,
                    entry_quality=1.0, market_phase=market_phase,
                    time_quality=time_quality,
                    reasons=exit_reasons,
                )

        elif position == "SHORT":
            exit_score = 0
            exit_reasons = []

            st_dir = self._safe_int(self._get_val(row, "supertrend_dir", 0))
            macd_hist = self._safe_float(self._get_val(row, "macd_hist", 0))
            srsi_k = self._safe_float(self._get_val(row, "stochrsi_k", 50))
            srsi_phase = self._safe_int(self._get_val(row, "srsi_phase", 0))

            if st_dir == 1:
                exit_score += 1
                exit_reasons.append("5m SuperTrend flipped bullish")
            if macd_hist > 0:
                exit_score += 1
                exit_reasons.append("5m MACD histogram positive")
            if srsi_k < 20 and srsi_phase >= 2:
                exit_score += 1
                exit_reasons.append("5m StochRSI oversold and curling")
            if srsi_k < 25:
                exit_score += 0.5

            if exit_score >= 2:
                return TriggerState(
                    signal="SHORT_EXIT", entry_price=close,
                    entry_quality=1.0, market_phase=market_phase,
                    time_quality=time_quality,
                    reasons=exit_reasons,
                )

        # ── ENTRY GATE: TF Alignment (Macro + Swing 1H) ─────────────────
        h1_st = self._safe_int(self._get_val(row, "h1_supertrend_dir", 0))
        h1_ema = self._safe_int(self._get_val(row, "h1_ema_cross", 0))
        
        # Check if 1H swing trend is strongly established (both ST and EMA align)
        h1_strong_bull = (h1_st == 1) and (h1_ema == 1)
        h1_strong_bear = (h1_st == -1) and (h1_ema == -1)
        
        if h1_strong_bull:
            target_bias = "LONG"
            reasons.append("1H Swing trend strongly bullish (overrides macro)")
        elif h1_strong_bear:
            target_bias = "SHORT"
            reasons.append("1H Swing trend strongly bearish (overrides macro)")
        else:
            # Fall back to macro bias
            if macro.bias == "NEUTRAL":
                return TriggerState(signal="HOLD", entry_price=close,
                                    market_phase=market_phase, time_quality=time_quality,
                                    reasons=["Macro bias neutral & no strong 1H trend — no entry"])
            target_bias = macro.bias

        # ── 5m ENTRY SIGNALS ────────────────────────────────────────────
        entry_score = 0.0
        st_dir = self._safe_int(self._get_val(row, "supertrend_dir", 0))
        ema_cross = self._safe_int(self._get_val(row, "ema_cross", 0))
        st_flip_age = self._safe_int(self._get_val(row, "st_bars_since_flip", 999))
        macd_hist = self._safe_float(self._get_val(row, "macd_hist", 0))
        srsi_k = self._safe_float(self._get_val(row, "stochrsi_k", 50))
        srsi_phase = self._safe_int(self._get_val(row, "srsi_phase", 0))
        stoch_phase = self._safe_int(self._get_val(row, "stoch_phase", 0))
        engulfing = self._safe_int(self._get_val(row, "engulfing_signal", 0))
        hammer = self._safe_int(self._get_val(row, "hammer_signal", 0))

        supertrend_just_flipped = st_flip_age <= 3

        if target_bias == "LONG":
            # SuperTrend bullish
            if st_dir == 1:
                entry_score += 0.20
                reasons.append("5m SuperTrend bullish")
            if supertrend_just_flipped and st_dir == 1:
                entry_score += 0.10
                reasons.append("5m SuperTrend just flipped bullish (fresh)")

            # EMA alignment
            ema_aligned = ema_cross == 1
            if ema_aligned:
                entry_score += 0.12
                reasons.append("5m EMA7 > EMA21")

            # MACD
            if macd_hist > 0:
                entry_score += 0.08
                reasons.append("5m MACD histogram positive")

            # StochRSI phase (oversold + curling up = best)
            if srsi_phase == 3:
                entry_score += 0.20
                reasons.append("5m StochRSI oversold curling up")
            elif srsi_phase == 2:
                entry_score += 0.12
            elif srsi_k < 50 and srsi_k > srsi_k:  # mid, rising
                entry_score += 0.06

            # Stoch
            if stoch_phase == 3:
                entry_score += 0.10
                reasons.append("5m Stoch oversold curling up")
            elif stoch_phase >= 2:
                entry_score += 0.06

            # Candle patterns
            if engulfing == 1:
                entry_score += 0.10
                reasons.append("Bullish engulfing candle")
            if hammer == 1:
                entry_score += 0.08
                reasons.append("Hammer candle at support")

        else:  # SHORT
            if st_dir == -1:
                entry_score += 0.20
                reasons.append("5m SuperTrend bearish")
            if supertrend_just_flipped and st_dir == -1:
                entry_score += 0.10
                reasons.append("5m SuperTrend just flipped bearish (fresh)")

            ema_aligned = ema_cross == -1
            if ema_aligned:
                entry_score += 0.12
                reasons.append("5m EMA7 < EMA21")

            if macd_hist < 0:
                entry_score += 0.08
                reasons.append("5m MACD histogram negative")

            if srsi_phase == -3:
                entry_score += 0.20
                reasons.append("5m StochRSI overbought turning down")
            elif srsi_phase == -2:
                entry_score += 0.12

            if stoch_phase == -3:
                entry_score += 0.10
                reasons.append("5m Stoch overbought turning down")
            elif stoch_phase <= -2:
                entry_score += 0.06

            if engulfing == -1:
                entry_score += 0.10
                reasons.append("Bearish engulfing candle")
            if hammer == -1:
                entry_score += 0.08
                reasons.append("Shooting star at resistance")

        # ── 1m CONFIRMATION (bonus) ─────────────────────────────────────
        m1_st = self._safe_int(self._get_val(row, "m1_st", 0))
        m1_srsi_k = self._safe_float(self._get_val(row, "m1_srsi_k", 50))
        m1_srsi_d = self._safe_float(self._get_val(row, "m1_srsi_d", 50))

        m1_confirms_st = False
        m1_confirms_srsi = False

        if target_bias == "LONG":
            if m1_st == 1:
                entry_score += 0.08
                m1_confirms_st = True
                reasons.append("1m SuperTrend confirms LONG")
            if m1_srsi_k > m1_srsi_d:
                entry_score += 0.05
                m1_confirms_srsi = True
        else:
            if m1_st == -1:
                entry_score += 0.08
                m1_confirms_st = True
                reasons.append("1m SuperTrend confirms SHORT")
            if m1_srsi_k < m1_srsi_d:
                entry_score += 0.05
                m1_confirms_srsi = True

        entry_quality = min(1.0, entry_score)

        # ── CANDLE PATTERN DETECTION ────────────────────────────────────
        candle_pattern = "NONE"
        if target_bias == "LONG":
            if engulfing == 1:
                candle_pattern = "BULLISH_ENGULFING"
            elif hammer == 1:
                candle_pattern = "HAMMER"
        else:
            if engulfing == -1:
                candle_pattern = "BEARISH_ENGULFING"
            elif hammer == -1:
                candle_pattern = "SHOOTING_STAR"

        # ── SL & TARGET CALCULATION ─────────────────────────────────────
        if target_bias == "LONG":
            sl = round(close - atr_sl_mult * atr, 2)
            target1 = round(close + atr_t1_mult * atr, 2)
            target2 = round(close + atr_t2_mult * atr, 2)
        else:
            sl = round(close + atr_sl_mult * atr, 2)
            target1 = round(close - atr_t1_mult * atr, 2)
            target2 = round(close - atr_t2_mult * atr, 2)

        risk_atr = atr_sl_mult
        risk_pts = abs(close - sl)
        rr_ratio = round(abs(target1 - close) / max(risk_pts, 0.01), 2)

        # Determine signal based on entry quality threshold
        # (The Orchestrator will make the final decision)
        signal = target_bias if entry_quality >= 0.30 else "HOLD"

        return TriggerState(
            signal=signal,
            entry_quality=round(entry_quality, 3),
            supertrend_just_flipped=supertrend_just_flipped,
            ema_cross_aligned=ema_aligned if target_bias == "LONG" else (ema_cross == -1),
            candle_pattern=candle_pattern,
            m1_supertrend_confirms=m1_confirms_st,
            m1_srsi_confirms=m1_confirms_srsi,
            entry_price=close,
            sl=sl,
            target1=target1,
            target2=target2,
            risk_atr=risk_atr,
            rr_ratio=rr_ratio,
            market_phase=market_phase,
            time_quality=time_quality,
            reasons=reasons,
        )
