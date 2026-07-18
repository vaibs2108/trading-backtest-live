"""
structure_agent.py — StructureAgent: Multi-TF structure, Fibonacci, trendlines.

Identifies WHERE price is in market structure across Weekly, Daily, and 1H:
- HTF (weekly/daily) Fibonacci + pivot S/R proximity and conflict detection
- 1H dynamic S/R: EMA21, SuperTrend, pivot levels
- Trendline proximity (5m + 15m swing-based trendlines)
- EMA7->EMA21->SuperTrend cascade sequence awareness
- Chart pattern integration
"""
import numpy as np
import pandas as pd
from .base import BaseAgent, StructureState, MacroState

# Distance thresholds (in H1 ATR units) at which a HTF level is "active"
_W_CONFLICT_DIST  = 2.0   # weekly level: within 2× H1 ATR -> conflict zone (default, overridden by config)
_D1_CONFLICT_DIST = 1.5   # daily level: within 1.5× H1 ATR -> conflict zone (default, overridden by config)
_TL_NEAR_DIST     = 1.5   # trendline within 1.5× 5m ATR -> "price is at trendline"


def _get_htf_distances():
    """Read HTF conflict distances from config (allows runtime tuning)."""
    try:
        from config import get_settings
        cfg = get_settings()
        return getattr(cfg, 'htf_w_conflict_dist', _W_CONFLICT_DIST), \
               getattr(cfg, 'htf_d1_conflict_dist', _D1_CONFLICT_DIST)
    except Exception:
        return _W_CONFLICT_DIST, _D1_CONFLICT_DIST


class StructureAgent(BaseAgent):
    """
    Evaluates market structure from Weekly, Daily, and 1H data.
    Key responsibility: detect HTF level conflicts that override a bullish/bearish 1H bias.
    """

    def evaluate(self, row: pd.Series, macro: MacroState) -> StructureState:
        reasons = []
        w_conflict_dist, d1_conflict_dist = _get_htf_distances()

        close    = self._safe_float(self._get_val(row, "close", 0))
        atr_5m   = self._safe_float(self._get_val(row, "atr", close * 0.002))
        h1_atr   = self._safe_float(self._get_val(row, "h1_atr", atr_5m * 3))
        if h1_atr < 1:
            h1_atr = atr_5m * 3

        # ── WEEKLY LEVELS ──────────────────────────────────────────────────
        w_fib_618    = self._safe_float(self._get_val(row, "w_fib_618", 0))
        w_fib_500    = self._safe_float(self._get_val(row, "w_fib_500", 0))
        w_fib_382    = self._safe_float(self._get_val(row, "w_fib_382", 0))
        w_res1       = self._safe_float(self._get_val(row, "w_res1", 0))
        w_sup1       = self._safe_float(self._get_val(row, "w_sup1", 0))
        w_swing_high = self._safe_float(self._get_val(row, "w_swing_high", 0))
        w_swing_low  = self._safe_float(self._get_val(row, "w_swing_low", 0))

        # ── DAILY LEVELS ───────────────────────────────────────────────────
        d1_fib_618   = self._safe_float(self._get_val(row, "d1_fib_618", 0))
        d1_fib_500   = self._safe_float(self._get_val(row, "d1_fib_500", 0))
        d1_fib_382   = self._safe_float(self._get_val(row, "d1_fib_382", 0))
        d1_res1      = self._safe_float(self._get_val(row, "d1_res1", 0))
        d1_sup1      = self._safe_float(self._get_val(row, "d1_sup1", 0))
        d1_swing_high = self._safe_float(self._get_val(row, "d1_swing_high", 0))
        d1_swing_low  = self._safe_float(self._get_val(row, "d1_swing_low", 0))

        # ── 1H LEVELS ─────────────────────────────────────────────────────
        h1_sup1      = self._safe_float(self._get_val(row, "h1_sup1", 0))
        h1_res1      = self._safe_float(self._get_val(row, "h1_res1", 0))
        h1_fib_618   = self._safe_float(self._get_val(row, "h1_fib_618", 0))
        h1_fib_500   = self._safe_float(self._get_val(row, "h1_fib_500", 0))
        h1_fib_382   = self._safe_float(self._get_val(row, "h1_fib_382", 0))
        h1_st_val    = self._safe_float(self._get_val(row, "h1_supertrend", 0))

        # ── HTF RESISTANCE / SUPPORT PROXIMITY ────────────────────────────
        # Classify each level as resistance (above price) or support (below price).
        # Use H1 ATR as the unit of measure for all HTF distances.

        weekly_resistance = []   # (label, price, dist_h1_atr)
        weekly_support    = []
        daily_resistance  = []
        daily_support     = []

        for label, val in [("W_SWING_HIGH", w_swing_high), ("W_FIB_382", w_fib_382),
                            ("W_FIB_500", w_fib_500),       ("W_FIB_618", w_fib_618),
                            ("W_RES1", w_res1)]:
            if val > 0 and val > close:
                weekly_resistance.append((label, val, (val - close) / h1_atr))
            elif val > 0 and val < close:
                weekly_support.append((label, val, (close - val) / h1_atr))

        # Weekly swing low is always the key support floor
        if w_swing_low > 0 and w_swing_low < close:
            weekly_support.append(("W_SWING_LOW", w_swing_low, (close - w_swing_low) / h1_atr))

        for label, val in [("D1_SWING_HIGH", d1_swing_high), ("D1_FIB_382", d1_fib_382),
                            ("D1_FIB_500", d1_fib_500),       ("D1_FIB_618", d1_fib_618),
                            ("D1_RES1", d1_res1)]:
            if val > 0 and val > close:
                daily_resistance.append((label, val, (val - close) / h1_atr))
            elif val > 0 and val < close:
                daily_support.append((label, val, (close - val) / h1_atr))

        if d1_swing_low > 0 and d1_swing_low < close:
            daily_support.append(("D1_SWING_LOW", d1_swing_low, (close - d1_swing_low) / h1_atr))
        if d1_sup1 > 0 and d1_sup1 < close:
            daily_support.append(("D1_SUP1", d1_sup1, (close - d1_sup1) / h1_atr))

        # Sort by proximity
        weekly_resistance.sort(key=lambda x: x[2])
        weekly_support.sort(key=lambda x: x[2])
        daily_resistance.sort(key=lambda x: x[2])
        daily_support.sort(key=lambda x: x[2])

        w_nearest_res_dist  = weekly_resistance[0][2]  if weekly_resistance  else 999.0
        w_nearest_sup_dist  = weekly_support[0][2]     if weekly_support     else 999.0
        d1_nearest_res_dist = daily_resistance[0][2]   if daily_resistance   else 999.0
        d1_nearest_sup_dist = daily_support[0][2]      if daily_support      else 999.0

        # ── HTF CONFLICT DETECTION ─────────────────────────────────────────
        # A conflict means price is approaching a major HTF level that opposes the
        # intended trade direction. This becomes a hard veto in the Orchestrator.
        htf_resistance_conflict = False
        htf_support_conflict    = False
        htf_confirms_bias       = False
        htf_conflict_reason     = ""

        if macro.bias == "LONG":
            # Check: are we walking into weekly/daily resistance?
            if weekly_resistance and weekly_resistance[0][2] < w_conflict_dist:
                label, price, dist = weekly_resistance[0]
                htf_resistance_conflict = True
                htf_conflict_reason = f"{label} @ {price:.0f} ({dist:.1f} H1 ATR above)"
                reasons.append(f"CAUTION: {label} resistance {dist:.1f} H1 ATR above — LONG risky")
            elif daily_resistance and daily_resistance[0][2] < d1_conflict_dist:
                label, price, dist = daily_resistance[0]
                htf_resistance_conflict = True
                htf_conflict_reason = f"{label} @ {price:.0f} ({dist:.1f} H1 ATR above)"
                reasons.append(f"CAUTION: {label} resistance {dist:.1f} H1 ATR above")

            # Check: is a weekly/daily support level confirming the LONG (nearby below)?
            if weekly_support and weekly_support[0][2] < w_conflict_dist:
                label, price, dist = weekly_support[0]
                htf_confirms_bias = True
                reasons.append(f"HTF support: {label} @ {price:.0f} ({dist:.1f} H1 ATR below) — confirms LONG")
            elif daily_support and daily_support[0][2] < d1_conflict_dist:
                label, price, dist = daily_support[0]
                htf_confirms_bias = True
                reasons.append(f"HTF support: {label} @ {price:.0f} ({dist:.1f} H1 ATR below) — confirms LONG")

        elif macro.bias == "SHORT":
            # Check: are we walking into weekly/daily support (bad for SHORT)?
            if weekly_support and weekly_support[0][2] < w_conflict_dist:
                label, price, dist = weekly_support[0]
                htf_support_conflict = True
                htf_conflict_reason = f"{label} @ {price:.0f} ({dist:.1f} H1 ATR below)"
                reasons.append(f"CAUTION: {label} support {dist:.1f} H1 ATR below — SHORT risky")
            elif daily_support and daily_support[0][2] < d1_conflict_dist:
                label, price, dist = daily_support[0]
                htf_support_conflict = True
                htf_conflict_reason = f"{label} @ {price:.0f} ({dist:.1f} H1 ATR below)"
                reasons.append(f"CAUTION: {label} support {dist:.1f} H1 ATR below")

            # Weekly/daily resistance confirms SHORT (nearby above)
            if weekly_resistance and weekly_resistance[0][2] < w_conflict_dist:
                label, price, dist = weekly_resistance[0]
                htf_confirms_bias = True
                reasons.append(f"HTF resistance: {label} @ {price:.0f} ({dist:.1f} H1 ATR above) — confirms SHORT")
            elif daily_resistance and daily_resistance[0][2] < d1_conflict_dist:
                label, price, dist = daily_resistance[0]
                htf_confirms_bias = True
                reasons.append(f"HTF resistance: {label} @ {price:.0f} ({dist:.1f} H1 ATR above) — confirms SHORT")

        # ── 1H S/R LEVELS ──────────────────────────────────────────────────
        support_candidates = []
        for name, val in [("PIVOT_S1", h1_sup1), ("FIB_618", h1_fib_618),
                          ("FIB_500", h1_fib_500), ("SUPERTREND", h1_st_val)]:
            if val > 0 and val < close:
                support_candidates.append((name, val, close - val))

        if support_candidates:
            support_candidates.sort(key=lambda x: x[2])
            support_type, nearest_support, sup_dist = support_candidates[0]
            price_to_support_atr = sup_dist / max(atr_5m, 1)
        else:
            support_type     = "NONE"
            nearest_support  = close * 0.99
            price_to_support_atr = 1.0

        resist_candidates = []
        for name, val in [("PIVOT_R1", h1_res1), ("FIB_382", h1_fib_382),
                          ("FIB_500", h1_fib_500), ("SUPERTREND", h1_st_val)]:
            if val > 0 and val > close:
                resist_candidates.append((name, val, val - close))

        if resist_candidates:
            resist_candidates.sort(key=lambda x: x[2])
            _, nearest_resistance, res_dist = resist_candidates[0]
            price_to_resistance_atr = res_dist / max(atr_5m, 1)
        else:
            nearest_resistance      = close * 1.01
            price_to_resistance_atr = 1.0

        # ── 1H SUPPORT STRENGTH ─────────────────────────────────────────────
        h1_support_touches  = self._safe_int(self._get_val(row, "h1_sr_support_touches_20", 0))
        h1_fib_618_touches  = self._safe_int(self._get_val(row, "h1_fib_618_touches", 0))
        support_test_count  = max(h1_support_touches, h1_fib_618_touches)
        support_strength    = min(1.0, support_test_count * 0.25)

        if support_test_count >= 3:
            reasons.append(f"1H support tested {support_test_count}x — strong level")
            support_strength = min(1.0, support_strength + 0.15)
        elif support_test_count >= 2:
            reasons.append(f"1H support tested {support_test_count}x")

        # ── DYNAMIC S/R (EMA21 + SuperTrend cascade) ───────────────────────
        h1_st_dir       = self._safe_int(self._get_val(row, "h1_supertrend_dir", 0))
        h1_st_dist_atr  = self._safe_float(self._get_val(row, "h1_st_price_dist_atr", 5))
        h1_st_touches   = self._safe_int(self._get_val(row, "h1_st_touch_count_20", 0))
        h1_ema_zone     = self._safe_int(self._get_val(row, "h1_ema_price_zone", 2))
        h1_ema21_dist   = self._safe_float(self._get_val(row, "h1_ema_price_to_ema21_atr", 5))
        h1_ema7_dist    = self._safe_float(self._get_val(row, "h1_ema_price_to_ema7_atr", 0))
        bars_at_ema21   = self._safe_int(self._get_val(row, "h1_bars_near_ema21", 0))

        supertrend_as_support = (h1_st_dir == 1 and 0 < h1_st_dist_atr < 1.0
                                 and h1_st_touches >= 2)
        if supertrend_as_support:
            reasons.append("1H SuperTrend acting as dynamic support")
            support_strength = min(1.0, support_strength + 0.2)

        ema21_as_support = (h1_ema_zone >= 1 and abs(h1_ema21_dist) < 0.8
                            and macro.bias == "LONG")
        if ema21_as_support:
            if bars_at_ema21 >= 3:
                reasons.append(f"Price testing 1H EMA21 support ({bars_at_ema21} bars) — high conviction bounce zone")
            else:
                reasons.append("Price near 1H EMA21 support")

        ema7_broken_traveling = (h1_ema_zone == 1 and abs(h1_ema7_dist) > 0.3)
        if ema7_broken_traveling:
            reasons.append("Price broke 1H EMA7, heading toward EMA21 — watch for EMA21 bounce")

        # ── TRENDLINES ─────────────────────────────────────────────────────
        # 5m trendlines
        tl_res_val   = self._safe_float(self._get_val(row, "trendline_resistance", 0))
        tl_sup_val   = self._safe_float(self._get_val(row, "trendline_support", 0))
        tl_res_slope = self._safe_float(self._get_val(row, "trendline_res_slope", 0))
        tl_sup_slope = self._safe_float(self._get_val(row, "trendline_sup_slope", 0))

        # 15m trendlines (structural context)
        m15_tl_res   = self._safe_float(self._get_val(row, "m15_trendline_resistance", 0))
        m15_tl_sup   = self._safe_float(self._get_val(row, "m15_trendline_support", 0))
        m15_tl_res_slope = self._safe_float(self._get_val(row, "m15_trendline_res_slope", 0))
        m15_tl_sup_slope = self._safe_float(self._get_val(row, "m15_trendline_sup_slope", 0))

        # Distance from price to trendlines (in 5m ATR)
        tl_res_dist = ((tl_res_val - close) / max(atr_5m, 1)) if tl_res_val > 0 else 999.0
        tl_sup_dist = ((close - tl_sup_val) / max(atr_5m, 1)) if tl_sup_val > 0 else 999.0

        # Use 15m trendlines if more informative (take closer of the two)
        if m15_tl_res > 0:
            m15_res_dist = (m15_tl_res - close) / max(atr_5m, 1)
            if abs(m15_res_dist) < abs(tl_res_dist):
                tl_res_dist  = m15_res_dist
                tl_res_slope = m15_tl_res_slope
        if m15_tl_sup > 0:
            m15_sup_dist = (close - m15_tl_sup) / max(atr_5m, 1)
            if abs(m15_sup_dist) < abs(tl_sup_dist):
                tl_sup_dist  = m15_sup_dist
                tl_sup_slope = m15_tl_sup_slope

        # Trendline signals
        # Ascending support trendline nearby from below: confirm LONG
        if 0 < tl_sup_dist < _TL_NEAR_DIST and tl_sup_slope > 0:
            reasons.append(f"Ascending support trendline {tl_sup_dist:.1f} ATR below (slope +{tl_sup_slope:.2f})")
        # Descending resistance trendline nearby above: warn LONG
        if 0 < tl_res_dist < _TL_NEAR_DIST and tl_res_slope < 0:
            reasons.append(f"Descending resistance trendline {tl_res_dist:.1f} ATR above (slope {tl_res_slope:.2f})")
        # Price just broke above descending trendline (tl_res_dist < 0): bullish breakout
        if tl_res_val > 0 and tl_res_dist < -0.3 and tl_res_slope < 0:
            reasons.append("Broke above descending resistance trendline — bullish breakout")

        # ── CHART PATTERNS ──────────────────────────────────────────────────
        pattern_name       = str(self._get_val(row, "pattern_name", "NONE"))
        pattern_signal     = str(self._get_val(row, "pattern_signal", "NONE"))
        pattern_completion = self._safe_float(self._get_val(row, "pattern_completion", 0))
        pattern_breakout   = self._safe_float(self._get_val(row, "pattern_breakout_dist_atr", 0))
        pattern_target     = self._safe_float(self._get_val(row, "pattern_target_atr", 0))

        if pattern_name != "NONE" and pattern_completion > 0.5:
            reasons.append(f"Chart pattern: {pattern_name} ({pattern_completion:.0%} complete)")

        # ── FIBONACCI LEVEL PROXIMITY (1H) ──────────────────────────────────
        h1_fib_zone  = self._safe_int(self._get_val(row, "h1_fib_zone", 2))
        h1_fib_dist  = self._safe_float(self._get_val(row, "h1_fib_nearest_dist_atr", 5))

        if h1_fib_dist < 0.5:
            reasons.append(f"Price at 1H Fibonacci level ({h1_fib_dist:.1f} ATR away)")

        # ── CONFIDENCE ──────────────────────────────────────────────────────
        confidence = 0.0

        # Near strong 1H support + macro agrees
        if price_to_support_atr < 1.0 and support_strength > 0.5 and macro.bias == "LONG":
            confidence += 0.35
            reasons.append("Near strong 1H support + macro bullish")
        elif price_to_resistance_atr < 1.0 and macro.bias == "SHORT":
            confidence += 0.35
            reasons.append("Near 1H resistance + macro bearish")

        # Dynamic S/R adds confidence
        if supertrend_as_support and macro.bias == "LONG":
            confidence += 0.20
        if ema21_as_support:
            confidence += 0.12
            if bars_at_ema21 >= 3:
                confidence += 0.08  # extra for extended support test

        # HTF alignment bonus: weekly/daily level CONFIRMS the direction
        if htf_confirms_bias:
            confidence += 0.20

        # HTF conflict warning: reduce confidence even without a veto
        # (veto in orchestrator is the hard block; this softens confidence)
        if htf_resistance_conflict and macro.bias == "LONG":
            confidence -= 0.25
        if htf_support_conflict and macro.bias == "SHORT":
            confidence -= 0.25

        # Trendline alignment
        if 0 < tl_sup_dist < _TL_NEAR_DIST and tl_sup_slope > 0 and macro.bias == "LONG":
            confidence += 0.10   # ascending trendline support confirms LONG
        if 0 < tl_res_dist < _TL_NEAR_DIST and tl_res_slope < 0 and macro.bias == "LONG":
            confidence -= 0.10   # descending trendline resistance warns against LONG

        # Chart pattern aligned with macro
        if pattern_signal == macro.bias and pattern_completion > 0.6:
            confidence += 0.15

        # 1H Fibonacci zone alignment
        if (macro.bias == "LONG" and h1_fib_zone >= 2) or \
           (macro.bias == "SHORT" and h1_fib_zone <= 2):
            confidence += 0.10

        confidence = min(1.0, max(0.0, confidence))

        return StructureState(
            nearest_support=round(nearest_support, 2),
            nearest_resistance=round(nearest_resistance, 2),
            support_type=support_type,
            support_strength=round(support_strength, 3),
            support_test_count=support_test_count,
            price_to_support_atr=round(price_to_support_atr, 2),
            price_to_resistance_atr=round(price_to_resistance_atr, 2),
            ema21_as_support=ema21_as_support,
            supertrend_as_support=supertrend_as_support,
            ema7_broken_traveling_to_21=ema7_broken_traveling,
            bars_at_ema21=bars_at_ema21,
            pattern_name=pattern_name,
            pattern_signal=pattern_signal,
            pattern_completion=round(pattern_completion, 2),
            pattern_breakout_dist_atr=round(pattern_breakout, 2),
            pattern_target_atr=round(pattern_target, 2),
            htf_resistance_conflict=htf_resistance_conflict,
            htf_support_conflict=htf_support_conflict,
            htf_conflict_reason=htf_conflict_reason,
            htf_confirms_bias=htf_confirms_bias,
            w_nearest_res_dist_atr=round(w_nearest_res_dist, 2),
            w_nearest_sup_dist_atr=round(w_nearest_sup_dist, 2),
            d1_nearest_res_dist_atr=round(d1_nearest_res_dist, 2),
            d1_nearest_sup_dist_atr=round(d1_nearest_sup_dist, 2),
            trendline_res_dist_atr=round(tl_res_dist, 2),
            trendline_sup_dist_atr=round(tl_sup_dist, 2),
            trendline_res_slope=round(tl_res_slope, 4),
            trendline_sup_slope=round(tl_sup_slope, 4),
            confidence=round(confidence, 3),
            reasons=reasons,
        )
