"""
regime_detection.py — Market Regime Detection Agent.

Classifies the current market regime into:
  TRENDING_UP, TRENDING_DOWN, SIDEWAYS, TRANSITION

Uses four independent microstructure signals (no lagging indicators):
  1. Efficiency Ratio (fractal efficiency) — how straight is the price path?
  2. Bar Overlap Ratio — how much do consecutive bars overlap?
  3. Swing Structure Sequencing — HH/HL vs LH/LL vs mixed
  4. Variance Ratio — multi-scale variance comparison

Each signal votes independently; the consensus determines the regime.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List


@dataclass
class RegimeState:
    regime: str = "SIDEWAYS"       # TRENDING_UP | TRENDING_DOWN | SIDEWAYS | TRANSITION
    confidence: float = 0.0        # 0..1
    regime_age: int = 0            # bars since last regime change
    efficiency_ratio: float = 0.0  # 0..1 (1 = perfectly straight move)
    bar_overlap_ratio: float = 0.0 # 0..1 (0 = no overlap = trending)
    swing_structure: str = "MIXED" # HH_HL | LH_LL | MIXED
    variance_ratio: float = 1.0    # >1 = trending, ~1 = random, <1 = mean-reverting
    reasons: List[str] = field(default_factory=list)


class RegimeDetectionAgent:
    """
    Detect market regime from raw price action microstructure.
    Designed to be fast and almost zero-lag.
    """

    def __init__(self, lookback: int = 15, swing_order: int = 3):
        """
        lookback: bars to analyze for efficiency ratio, bar overlap, variance ratio
        swing_order: bars on each side to confirm a swing high/low
        """
        self.lookback = lookback
        self.swing_order = swing_order

    # ── 1. Efficiency Ratio ──────────────────────────────────────────────

    def _efficiency_ratio(self, closes: np.ndarray) -> float:
        """
        Net displacement / total path length.
        1.0 = price moved in a straight line (strong trend)
        ~0.0 = price went nowhere despite moving a lot (range/chop)
        """
        if len(closes) < 2:
            return 0.0
        net_move = abs(closes[-1] - closes[0])
        total_path = np.sum(np.abs(np.diff(closes)))
        if total_path == 0:
            return 0.0
        return net_move / total_path

    # ── 2. Bar Overlap Ratio ─────────────────────────────────────────────

    def _bar_overlap_ratio(self, highs: np.ndarray, lows: np.ndarray) -> float:
        """
        Average fraction of each bar's range that overlaps with the
        previous bar's range. Low overlap = directional, high = choppy.

        Computed over the last `lookback` bars.
        Returns 0..1 (0 = no overlap at all, 1 = complete overlap).
        """
        n = len(highs)
        if n < 2:
            return 0.5

        overlaps = []
        for i in range(1, n):
            cur_range = highs[i] - lows[i]
            if cur_range <= 0:
                overlaps.append(1.0)
                continue
            overlap_high = min(highs[i], highs[i - 1])
            overlap_low = max(lows[i], lows[i - 1])
            overlap = max(0, overlap_high - overlap_low)
            overlaps.append(overlap / cur_range)

        return float(np.mean(overlaps))

    # ── 3. Swing Structure ───────────────────────────────────────────────

    def _detect_swings(self, highs: np.ndarray, lows: np.ndarray):
        """
        Detect confirmed swing highs and swing lows.
        A swing high at index i is confirmed if high[i] is the max of
        high[i-order : i+order+1] and i+order bars have passed.

        Returns list of (index, price, type) where type is 'H' or 'L'.
        """
        order = self.swing_order
        n = len(highs)
        swings = []

        for i in range(order, n - order):
            # Swing High
            window_highs = highs[i - order: i + order + 1]
            if highs[i] == np.max(window_highs) and np.sum(window_highs == highs[i]) == 1:
                swings.append((i, float(highs[i]), "H"))

            # Swing Low
            window_lows = lows[i - order: i + order + 1]
            if lows[i] == np.min(window_lows) and np.sum(window_lows == lows[i]) == 1:
                swings.append((i, float(lows[i]), "L"))

        # Sort by index
        swings.sort(key=lambda x: x[0])
        return swings

    def _swing_structure(self, highs: np.ndarray, lows: np.ndarray) -> str:
        """
        Analyze the last few swing highs and lows to determine structure:
          HH_HL = uptrend (higher highs, higher lows)
          LH_LL = downtrend (lower highs, lower lows)
          MIXED = no clear pattern (sideways / transition)
        """
        swings = self._detect_swings(highs, lows)
        if len(swings) < 4:
            return "MIXED"

        # Take last 6 swings for analysis
        recent = swings[-6:]

        swing_highs = [(idx, px) for idx, px, tp in recent if tp == "H"]
        swing_lows = [(idx, px) for idx, px, tp in recent if tp == "L"]

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "MIXED"

        # Check if swing highs are rising (HH) or falling (LH)
        hh_count = sum(1 for i in range(1, len(swing_highs))
                       if swing_highs[i][1] > swing_highs[i - 1][1])
        lh_count = sum(1 for i in range(1, len(swing_highs))
                       if swing_highs[i][1] < swing_highs[i - 1][1])

        # Check if swing lows are rising (HL) or falling (LL)
        hl_count = sum(1 for i in range(1, len(swing_lows))
                       if swing_lows[i][1] > swing_lows[i - 1][1])
        ll_count = sum(1 for i in range(1, len(swing_lows))
                       if swing_lows[i][1] < swing_lows[i - 1][1])

        total_h = max(hh_count + lh_count, 1)
        total_l = max(hl_count + ll_count, 1)

        if hh_count / total_h >= 0.6 and hl_count / total_l >= 0.6:
            return "HH_HL"
        elif lh_count / total_h >= 0.6 and ll_count / total_l >= 0.6:
            return "LH_LL"
        return "MIXED"

    # ── 4. Variance Ratio ────────────────────────────────────────────────

    def _variance_ratio(self, closes: np.ndarray, short_period: int = 2,
                        long_period: int = 10) -> float:
        """
        Compare variance at two time scales.
        If VR >> 1: trending (long-scale moves are disproportionately large)
        If VR ≈ 1: random walk
        If VR << 1: mean-reverting (sideways)

        VR = Var(long_returns) / (long_period/short_period * Var(short_returns))
        """
        if len(closes) < long_period + 1:
            return 1.0

        short_returns = np.diff(closes[::short_period])  # returns at short scale
        long_returns = closes[long_period:] - closes[:-long_period]  # returns at long scale

        if len(short_returns) < 3 or len(long_returns) < 3:
            return 1.0

        var_short = np.var(short_returns)
        var_long = np.var(long_returns)

        if var_short == 0:
            return 1.0

        scale_factor = long_period / short_period
        return float(var_long / (scale_factor * var_short))

    # ── MAIN EVALUATE ────────────────────────────────────────────────────

    def evaluate(self, df: pd.DataFrame, prev_regime: str = "SIDEWAYS",
                 prev_age: int = 0) -> RegimeState:
        """
        Classify the current market regime.

        Parameters:
            df: DataFrame with at minimum 'high', 'low', 'close' columns.
                Should have at least `lookback + 2*swing_order` rows.
            prev_regime: regime from previous bar (for computing regime_age)
            prev_age: age of previous regime

        Returns RegimeState.
        """
        lb = self.lookback
        n = len(df)
        if n < lb:
            return RegimeState(regime="SIDEWAYS", confidence=0.0,
                               reasons=["Insufficient data for regime detection"])

        highs = df["high"].values[-lb * 2:].astype(float)
        lows = df["low"].values[-lb * 2:].astype(float)
        closes = df["close"].values[-lb * 2:].astype(float)

        # Recent window for efficiency and overlap
        recent_closes = closes[-lb:]
        recent_highs = highs[-lb:]
        recent_lows = lows[-lb:]

        reasons = []

        # ── Compute signals ──────────────────────────────────────────
        er = self._efficiency_ratio(recent_closes)
        bor = self._bar_overlap_ratio(recent_highs, recent_lows)
        ss = self._swing_structure(highs, lows)
        vr = self._variance_ratio(recent_closes)

        # ── Vote: each signal contributes a direction vote ───────────
        # Votes: +1 = trending_up, -1 = trending_down, 0 = sideways
        trend_votes = 0.0
        sideways_votes = 0.0

        # Efficiency Ratio: > 0.35 = trending, < 0.25 = sideways
        if er > 0.20:
            direction = 1.0 if recent_closes[-1] > recent_closes[0] else -1.0
            trend_votes += direction * min(er, 1.0)
            reasons.append(f"ER={er:.2f} (directional)")
        elif er < 0.18:
            sideways_votes += (0.18 - er) / 0.18
            reasons.append(f"ER={er:.2f} (choppy)")
        else:
            reasons.append(f"ER={er:.2f} (mixed)")

        # Bar Overlap: < 0.50 = trending, > 0.65 = range
        if bor < 0.58:
            direction = 1.0 if recent_closes[-1] > recent_closes[0] else -1.0
            trend_votes += direction * (0.58 - bor) / 0.58
            reasons.append(f"Overlap={bor:.2f} (low -> trending)")
        elif bor > 0.70:
            sideways_votes += (bor - 0.70) / 0.30
            reasons.append(f"Overlap={bor:.2f} (high -> range)")
        else:
            reasons.append(f"Overlap={bor:.2f} (moderate)")

        # Swing Structure
        if ss == "HH_HL":
            trend_votes += 1.0
            reasons.append("Swings: HH+HL (uptrend)")
        elif ss == "LH_LL":
            trend_votes -= 1.0
            reasons.append("Swings: LH+LL (downtrend)")
        else:
            sideways_votes += 0.5
            reasons.append("Swings: mixed (no clear trend)")

        # Variance Ratio: > 1.5 = trending, < 0.8 = mean-reverting
        if vr > 1.1:
            direction = 1.0 if recent_closes[-1] > recent_closes[0] else -1.0
            trend_votes += direction * min((vr - 1.0) / 2.0, 1.0)
            reasons.append(f"VR={vr:.2f} (trending)")
        elif vr < 0.8:
            sideways_votes += min((1.0 - vr) / 0.5, 1.0)
            reasons.append(f"VR={vr:.2f} (mean-reverting)")
        else:
            reasons.append(f"VR={vr:.2f} (neutral)")

        # ── Fast burst detection (5-bar short window) ──────────────
        fast_lb = 5
        if len(closes) >= fast_lb:
            fast_rc = closes[-fast_lb:]
            fast_rh = highs[-fast_lb:]
            fast_rl = lows[-fast_lb:]
            fast_net = abs(fast_rc[-1] - fast_rc[0])
            fast_path = np.sum(np.abs(np.diff(fast_rc)))
            fast_er = fast_net / fast_path if fast_path > 0 else 0
            # Fast bar overlap
            fast_overlaps = []
            for i in range(1, len(fast_rh)):
                cr = fast_rh[i] - fast_rl[i]
                if cr <= 0:
                    fast_overlaps.append(1.0)
                    continue
                oh = min(fast_rh[i], fast_rh[i - 1])
                ol = max(fast_rl[i], fast_rl[i - 1])
                fast_overlaps.append(max(0, oh - ol) / cr)
            fast_bor = float(np.mean(fast_overlaps)) if fast_overlaps else 0.5
            atr_est = float(np.mean(fast_rh - fast_rl))
            fast_move_atr = fast_net / atr_est if atr_est > 0 else 0
            if fast_er > 0.45 and fast_move_atr > 1.0 and fast_bor < 0.55:
                d = 1.0 if fast_rc[-1] > fast_rc[0] else -1.0
                trend_votes += d * 1.5
                reasons.append(f"BURST: fast_ER={fast_er:.2f}, move={fast_move_atr:.1f}ATR")

        # ── Classify regime (with stickiness) ────────────────────────
        abs_trend = abs(trend_votes)
        total_signal = abs_trend + sideways_votes

        if total_signal == 0:
            raw_regime = "SIDEWAYS"
            confidence = 0.3
        elif abs_trend > sideways_votes and abs_trend >= 0.6:
            if trend_votes > 0:
                raw_regime = "TRENDING_UP"
            else:
                raw_regime = "TRENDING_DOWN"
            confidence = min(abs_trend / (abs_trend + sideways_votes + 0.01), 0.95)
        elif sideways_votes > abs_trend and sideways_votes >= 1.2:
            raw_regime = "SIDEWAYS"
            confidence = min(sideways_votes / (abs_trend + sideways_votes + 0.01), 0.95)
        else:
            raw_regime = "TRANSITION"
            confidence = 0.3 + 0.2 * min(total_signal / 3.0, 1.0)

        # Regime stickiness: once trending, stay trending unless opposing
        # signal is strong. Prevents jitter between TRENDING and TRANSITION.
        regime = raw_regime
        if prev_regime.startswith("TRENDING") and prev_age >= 1:
            if raw_regime == "TRANSITION":
                # Stay in trend unless TRANSITION for too long or sideways is strong
                regime = prev_regime
                confidence = max(confidence - 0.1, 0.2)
                reasons.append(f"Sticky: holding {prev_regime} through TRANSITION")
            elif raw_regime == "SIDEWAYS" and sideways_votes < 1.8:
                # Weak sideways signal — hold trend
                regime = prev_regime
                confidence = max(confidence - 0.15, 0.2)
                reasons.append(f"Sticky: holding {prev_regime} through weak SIDEWAYS")

        # ── Regime age ───────────────────────────────────────────────
        if regime == prev_regime:
            regime_age = prev_age + 1
        else:
            regime_age = 1

        return RegimeState(
            regime=regime,
            confidence=round(confidence, 3),
            regime_age=regime_age,
            efficiency_ratio=round(er, 3),
            bar_overlap_ratio=round(bor, 3),
            swing_structure=ss,
            variance_ratio=round(vr, 3),
            reasons=reasons,
        )
