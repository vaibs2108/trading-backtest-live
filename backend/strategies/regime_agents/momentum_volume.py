"""
momentum_volume.py — Momentum & Volume Quality Agent.

Validates signals from the Trend/Range agents by checking:
  1. Volume confirmation — is volume supporting the direction?
  2. Rate of change acceleration — is momentum building or fading?
  3. Exhaustion detection — new price extremes on declining momentum
  4. Accumulation/distribution — smart money flow

This agent DOES NOT generate signals — it provides a quality filter
(confirm / neutral / veto) for signals from other agents.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List


@dataclass
class MomentumVolumeState:
    confirms: bool = True         # True = confirms the trade direction
    veto: bool = False            # True = veto the trade
    confidence: float = 0.5       # 0..1
    volume_trend: str = "NEUTRAL"  # RISING | FALLING | NEUTRAL
    momentum_state: str = "NEUTRAL"  # ACCELERATING | DECELERATING | DIVERGING | NEUTRAL
    exhaustion_detected: bool = False
    reasons: List[str] = field(default_factory=list)


class MomentumVolumeAgent:
    """
    Quality filter for trade signals.
    Uses raw price & volume data — no derived indicators.
    """

    def __init__(self, lookback: int = 15):
        self.lookback = lookback

    def _volume_trend(self, volumes: np.ndarray, direction: str) -> tuple:
        """
        Check if volume is confirming the price direction.

        In an uptrend: rising volume on up-bars, declining on down-bars = bullish
        In a downtrend: rising volume on down-bars = bearish
        In a range: volume should be declining (quiet market)
        """
        if len(volumes) < 5 or np.sum(volumes) == 0:
            return "NEUTRAL", 0.5, "Volume data insufficient"

        recent_vol = volumes[-5:]
        prior_vol = volumes[-10:-5] if len(volumes) >= 10 else volumes[:5]

        avg_recent = np.mean(recent_vol)
        avg_prior = np.mean(prior_vol)

        if avg_prior == 0:
            return "NEUTRAL", 0.5, "No prior volume for comparison"

        vol_ratio = avg_recent / avg_prior

        if vol_ratio > 1.2:
            return "RISING", min(vol_ratio / 2.0, 1.0), f"Volume rising ({vol_ratio:.1f}x prior)"
        elif vol_ratio < 0.8:
            return "FALLING", min(1.0 / vol_ratio / 2.0, 1.0), f"Volume falling ({vol_ratio:.1f}x prior)"
        return "NEUTRAL", 0.5, f"Volume steady ({vol_ratio:.1f}x prior)"

    def _rate_of_change_accel(self, closes: np.ndarray) -> tuple:
        """
        Compute rate of change acceleration.
        ROC = (close - close_n) / close_n
        Acceleration = ROC_recent - ROC_prior
        Positive acceleration = momentum building. Negative = fading.
        """
        if len(closes) < 10:
            return "NEUTRAL", 0.5, "Insufficient data for ROC"

        roc_5 = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] != 0 else 0
        roc_5_prior = (closes[-5] - closes[-10]) / closes[-10] if closes[-10] != 0 else 0

        accel = roc_5 - roc_5_prior

        if abs(accel) < 0.001:
            return "NEUTRAL", 0.5, f"Momentum flat (ROC accel: {accel:.4f})"
        elif accel > 0.001:
            return "ACCELERATING", min(0.5 + abs(accel) * 50, 1.0), f"Momentum building (accel: {accel:.4f})"
        else:
            return "DECELERATING", min(0.5 + abs(accel) * 50, 1.0), f"Momentum fading (accel: {accel:.4f})"

    def _detect_exhaustion(self, highs: np.ndarray, lows: np.ndarray,
                           closes: np.ndarray, volumes: np.ndarray,
                           direction: str) -> tuple:
        """
        Detect exhaustion: price making new extremes on declining volume/momentum.
        This is a bearish signal for LONG entries, bullish for SHORT exits.
        """
        if len(closes) < 10 or np.sum(volumes) == 0:
            return False, "No exhaustion data"

        if direction in ("LONG", "TRENDING_UP"):
            # Check: new highs on declining volume?
            recent_high = np.max(highs[-3:])
            prior_high = np.max(highs[-10:-3]) if len(highs) >= 10 else np.max(highs[:-3])

            if recent_high >= prior_high:
                vol_at_highs = np.mean(volumes[-3:])
                vol_prior = np.mean(volumes[-10:-3]) if len(volumes) >= 10 else np.mean(volumes[:-3])
                if vol_prior > 0 and vol_at_highs < vol_prior * 0.7:
                    return True, "New highs on declining volume — exhaustion"

        elif direction in ("SHORT", "TRENDING_DOWN"):
            recent_low = np.min(lows[-3:])
            prior_low = np.min(lows[-10:-3]) if len(lows) >= 10 else np.min(lows[:-3])

            if recent_low <= prior_low:
                vol_at_lows = np.mean(volumes[-3:])
                vol_prior = np.mean(volumes[-10:-3]) if len(volumes) >= 10 else np.mean(volumes[:-3])
                if vol_prior > 0 and vol_at_lows < vol_prior * 0.7:
                    return True, "New lows on declining volume — exhaustion"

        return False, "No exhaustion pattern"

    def evaluate(self, df: pd.DataFrame, signal_direction: str,
                 regime: str) -> MomentumVolumeState:
        """
        Evaluate momentum/volume quality for a proposed signal.

        Parameters:
            df: recent price history with 'high', 'low', 'close', 'volume'
            signal_direction: LONG | SHORT | HOLD
            regime: TRENDING_UP | TRENDING_DOWN | SIDEWAYS | TRANSITION

        Returns MomentumVolumeState with confirms/veto flags.
        """
        if signal_direction == "HOLD":
            return MomentumVolumeState(confirms=True, reasons=["No signal to validate"])

        n = len(df)
        lb = min(self.lookback, n)
        if lb < 8:
            return MomentumVolumeState(confirms=True,
                                       reasons=["Insufficient data — passing through"])

        closes = df["close"].values[-lb:].astype(float)
        highs = df["high"].values[-lb:].astype(float)
        lows = df["low"].values[-lb:].astype(float)

        has_volume = "volume" in df.columns
        volumes = df["volume"].values[-lb:].astype(float) if has_volume else np.ones(lb)

        reasons = []
        confirm_score = 0.0
        veto_score = 0.0

        # ── 1. Volume Trend ──────────────────────────────────────────
        vol_trend, vol_conf, vol_reason = self._volume_trend(volumes, signal_direction)
        reasons.append(vol_reason)

        if regime.startswith("TRENDING"):
            # Trending: want rising volume in trade direction
            if vol_trend == "RISING":
                confirm_score += 0.3
            elif vol_trend == "FALLING":
                veto_score += 0.15
        else:
            # Sideways: declining volume is actually ok (quiet market for range trades)
            if vol_trend == "FALLING":
                confirm_score += 0.1

        # ── 2. Rate of Change Acceleration ───────────────────────────
        mom_state, mom_conf, mom_reason = self._rate_of_change_accel(closes)
        reasons.append(mom_reason)

        if signal_direction == "LONG":
            if mom_state == "ACCELERATING" and closes[-1] > closes[-5]:
                confirm_score += 0.3
            elif mom_state == "DECELERATING" and closes[-1] > closes[-5]:
                veto_score += 0.2
                reasons.append("LONG signal but momentum decelerating")
        elif signal_direction == "SHORT":
            if mom_state == "ACCELERATING" and closes[-1] < closes[-5]:
                confirm_score += 0.3
            elif mom_state == "DECELERATING" and closes[-1] < closes[-5]:
                veto_score += 0.2
                reasons.append("SHORT signal but downward momentum fading")

        # ── 3. Exhaustion Detection ──────────────────────────────────
        exhausted, exh_reason = self._detect_exhaustion(
            highs, lows, closes, volumes, signal_direction
        )
        if exhausted:
            veto_score += 0.4
            reasons.append(exh_reason)

        # ── Final decision ───────────────────────────────────────────
        confirms = confirm_score >= veto_score
        veto = veto_score >= 0.5
        confidence = max(confirm_score - veto_score + 0.5, 0.1)
        confidence = min(confidence, 1.0)

        return MomentumVolumeState(
            confirms=confirms and not veto,
            veto=veto,
            confidence=round(confidence, 3),
            volume_trend=vol_trend,
            momentum_state=mom_state,
            exhaustion_detected=exhausted,
            reasons=reasons,
        )
