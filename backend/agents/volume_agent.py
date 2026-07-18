"""
volume_agent.py — VolumeAgent: Volume confirmation/veto.

Confirms or VETOES setups from other agents based on volume dynamics.
Has veto power — can override other agents if volume strongly contradicts.
"""
import numpy as np
import pandas as pd
from .base import BaseAgent, VolumeState, MacroState, StructureState


class VolumeAgent(BaseAgent):
    """
    Evaluates volume dynamics to confirm or deny setups.
    Can veto trades when volume strongly contradicts the setup.
    """

    def evaluate(self, row: pd.Series, macro: MacroState,
                 structure: StructureState) -> VolumeState:
        """Evaluate volume from the latest merged row."""
        reasons = []

        # ── 5m VOLUME ───────────────────────────────────────────────────
        vol_relative = self._safe_float(self._get_val(row, "vol_relative", 1.0))
        vol_trend_5m = self._safe_float(self._get_val(row, "vol_trend_5bar", 0))
        vol_divergence = self._safe_int(self._get_val(row, "vol_price_divergence", 0))
        vol_spike_ago = self._safe_int(self._get_val(row, "vol_spike_bars_ago", 50))
        vol_accum = self._safe_float(self._get_val(row, "vol_accum_dist_5bar", 0))

        # ── 1H VOLUME (from merged columns) ─────────────────────────────
        h1_vol_relative = self._safe_float(self._get_val(row, "h1_vol_relative", 1.0))
        h1_vol_trend = self._safe_float(self._get_val(row, "h1_vol_trend_5bar", 0))

        # ── VOLUME AT SUPPORT/RESISTANCE ────────────────────────────────
        volume_at_support = "NEUTRAL"
        volume_at_resistance = "NEUTRAL"

        if structure.price_to_support_atr < 0.8:  # Near support
            if vol_relative > 1.5:
                volume_at_support = "HIGH_SELLING"
                reasons.append("High volume selling at support (bearish)")
            elif vol_relative < 0.6:
                volume_at_support = "LOW_SELLING"
                reasons.append("Low volume at support (drying up = bullish)")
            else:
                volume_at_support = "MODERATE"

        if structure.price_to_resistance_atr < 0.8:  # Near resistance
            if vol_relative > 1.5:
                volume_at_resistance = "HIGH_BUYING"
                reasons.append("High volume at resistance (trying to break)")
            elif vol_relative < 0.6:
                volume_at_resistance = "LOW_BUYING"
                reasons.append("Low volume at resistance (likely rejection)")

        # ── VOLUME TRENDS ───────────────────────────────────────────────
        volume_trend_5m = "FLAT"
        if vol_trend_5m > 0.15:
            volume_trend_5m = "INCREASING"
        elif vol_trend_5m < -0.15:
            volume_trend_5m = "DECREASING"

        volume_trend_1h = "FLAT"
        if h1_vol_trend > 0.15:
            volume_trend_1h = "INCREASING"
        elif h1_vol_trend < -0.15:
            volume_trend_1h = "DECREASING"

        # ── BREAKOUT VOLUME ─────────────────────────────────────────────
        breakout_volume = vol_relative > 1.5
        if breakout_volume:
            reasons.append(f"Volume {vol_relative:.1f}x average — breakout level")

        # ── VOLUME DIVERGENCE ───────────────────────────────────────────
        vol_div_map = {
            0: "NONE",
            1: "PRICE_DOWN_VOL_DOWN",    # weak selling = bullish
            -1: "PRICE_UP_VOL_DOWN",     # weak rally = bearish
            2: "PRICE_UP_VOL_UP",        # strong rally
            -2: "PRICE_DOWN_VOL_UP",     # strong selling
        }
        volume_divergence = vol_div_map.get(vol_divergence, "NONE")

        if volume_divergence == "PRICE_UP_VOL_DOWN":
            reasons.append("Price rising on declining volume — weak rally")
        elif volume_divergence == "PRICE_DOWN_VOL_DOWN":
            reasons.append("Price falling on declining volume — weak selloff (bullish)")
        elif volume_divergence == "PRICE_UP_VOL_UP":
            reasons.append("Strong buying — price + volume both rising")
        elif volume_divergence == "PRICE_DOWN_VOL_UP":
            reasons.append("Heavy selling — price down + volume up")

        # ── ACCUMULATION / DISTRIBUTION ─────────────────────────────────
        accumulation = vol_accum > 0.5 and structure.price_to_support_atr < 1.5
        distribution = vol_accum < -0.5 and structure.price_to_resistance_atr < 1.5

        if accumulation:
            reasons.append("Accumulation signal — buying at support")
        if distribution:
            reasons.append("Distribution signal — selling at resistance")

        # ── VETO LOGIC ──────────────────────────────────────────────────
        veto_signal = False
        veto_reason = ""

        # Veto long: massive selling at support (support likely breaks)
        if macro.bias == "LONG" and volume_at_support == "HIGH_SELLING" and vol_relative > 2.0:
            veto_signal = True
            veto_reason = f"Massive selling ({vol_relative:.1f}x) at support — likely breakdown"

        # Veto short: massive buying at resistance (resistance likely breaks)
        if macro.bias == "SHORT" and volume_at_resistance == "HIGH_BUYING" and vol_relative > 2.0:
            veto_signal = True
            veto_reason = f"Massive buying ({vol_relative:.1f}x) at resistance — likely breakout"

        # Veto: price rising but volume collapsing (unsustainable)
        if macro.bias == "LONG" and volume_divergence == "PRICE_UP_VOL_DOWN" and vol_relative < 0.4:
            veto_signal = True
            veto_reason = "Rally on extremely low volume — unsustainable"

        if macro.bias == "SHORT" and volume_divergence == "PRICE_DOWN_VOL_DOWN" and vol_relative < 0.4:
            # Actually, this is bullish for shorts being squeezed
            pass

        if veto_signal:
            reasons.append(f"VETO: {veto_reason}")

        # ── CONFIDENCE ──────────────────────────────────────────────────
        confidence = 0.5  # Neutral baseline

        if macro.bias == "LONG":
            if volume_at_support == "LOW_SELLING":
                confidence += 0.2  # Drying volume at support = bullish
            if accumulation:
                confidence += 0.15
            if volume_divergence == "PRICE_DOWN_VOL_DOWN":
                confidence += 0.1  # Weak selling = bullish
            if volume_divergence == "PRICE_UP_VOL_UP":
                confidence += 0.15
        else:
            if volume_at_resistance == "LOW_BUYING":
                confidence += 0.2
            if distribution:
                confidence += 0.15
            if volume_divergence == "PRICE_DOWN_VOL_UP":
                confidence += 0.15

        if veto_signal:
            confidence = 0.0

        confidence = min(1.0, max(0.0, confidence))

        return VolumeState(
            volume_at_support=volume_at_support,
            volume_at_resistance=volume_at_resistance,
            volume_trend_1h=volume_trend_1h,
            volume_trend_5m=volume_trend_5m,
            relative_volume_5m=round(vol_relative, 2),
            breakout_volume=breakout_volume,
            volume_divergence=volume_divergence,
            accumulation=accumulation,
            distribution=distribution,
            veto_signal=veto_signal,
            veto_reason=veto_reason,
            confidence=round(confidence, 3),
            reasons=reasons,
        )
