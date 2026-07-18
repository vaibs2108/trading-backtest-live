"""
regime_orchestrator.py — Regime Strategy Orchestrator.

Combines all agent outputs into a final trading signal:
  1. Regime Agent determines the playbook (trend vs range)
  2. Trend or Range agent proposes the entry
  3. HTF Structure adjusts SL/T1/T2 to snap to significant levels
  4. Momentum/Volume confirms or vetoes
  5. Final signal is produced with all metadata
"""
import logging
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional

from .regime_detection import RegimeState
from .trend_entry import TrendEntryState
from .range_entry import RangeEntryState
from .htf_structure import HTFStructureState
from .momentum_volume import MomentumVolumeState

logger = logging.getLogger(__name__)


@dataclass
class RegimeSignal:
    """Final signal output — compatible with the main strategy API."""
    signal: str = "HOLD"
    time: str = ""
    entry: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    close: float = 0.0
    regime: str = "SIDEWAYS"
    regime_confidence: float = 0.0
    regime_age: int = 0
    playbook: str = ""              # "TREND" | "RANGE" | ""
    entry_quality: float = 0.0
    momentum_confirms: bool = True
    htf_trend: str = "NEUTRAL"
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0
    weighted_score: float = 0.0
    ml_prob: float = 0.0            # not used but API-compatible
    macro_bias: str = "NEUTRAL"
    atr_5m: float = 0.0
    adx_1h: float = 0.0
    h1_trend: str = "NEUTRAL"
    risk_pts: float = 0.0
    rr_t1: float = 0.0
    long_score: float = 0.0
    short_score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    veto_reasons: List[str] = field(default_factory=list)
    strategy: str = "regime_trend_range"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()
                if not k.startswith("_")}


class RegimeOrchestrator:
    """Final decision maker for the regime strategy."""

    def __init__(self, min_quality: float = 0.25, min_rr: float = 0.0, min_regime_age: int = 1):
        self.min_quality = min_quality
        self.min_rr = min_rr
        self.min_regime_age = min_regime_age

    def evaluate(self, row: pd.Series,
                 regime: RegimeState,
                 trend_entry: TrendEntryState,
                 range_entry: RangeEntryState,
                 htf: HTFStructureState,
                 momentum: MomentumVolumeState,
                 position: str = "NONE") -> RegimeSignal:
        """
        Combine all agent outputs into a final signal.
        """
        close = float(row.get("close", 0))
        atr = float(row.get("atr", close * 0.002))
        if pd.isna(atr) or atr < 1:
            atr = close * 0.002

        ts_val = row.get("timestamp", "")
        try:
            ts = pd.Timestamp(ts_val).tz_localize("Asia/Kolkata").isoformat()
        except Exception:
            ts = str(ts_val)

        all_reasons = []
        veto_reasons = []

        # Base signal object
        sig = RegimeSignal(
            time=ts, close=close, atr_5m=round(atr, 2),
            regime=regime.regime, regime_confidence=regime.confidence,
            regime_age=regime.regime_age,
            htf_trend=htf.htf_trend,
            nearest_support=htf.nearest_support,
            nearest_resistance=htf.nearest_resistance,
            macro_bias=htf.htf_trend,
            h1_trend=htf.htf_trend,
        )

        # ── SELECT PLAYBOOK based on regime ──────────────────────────
        active_entry = None
        playbook = ""

        if regime.regime in ("TRENDING_UP", "TRENDING_DOWN"):
            active_entry = trend_entry
            playbook = "TREND"
            all_reasons.append(f"Regime: {regime.regime} (conf={regime.confidence:.0%}, age={regime.regime_age})")
            all_reasons.extend(regime.reasons[:2])

        elif regime.regime == "SIDEWAYS":
            active_entry = range_entry
            playbook = "RANGE"
            all_reasons.append(f"Regime: SIDEWAYS (conf={regime.confidence:.0%}, age={regime.regime_age})")
            all_reasons.extend(regime.reasons[:2])

        elif regime.regime == "TRANSITION":
            sig.signal = "HOLD"
            sig.reasons = [
                f"Regime TRANSITION — no trade (conf={regime.confidence:.0%})",
                *regime.reasons[:2]
            ]
            return sig

        sig.playbook = playbook

        # ── HANDLE EXIT SIGNALS ──────────────────────────────────────
        # Check both agents for exit signals
        for entry_state in [trend_entry, range_entry]:
            if entry_state.signal in ("LONG_EXIT", "SHORT_EXIT"):
                sig.signal = entry_state.signal
                sig.entry = close
                sig.reasons = entry_state.reasons
                return sig

        # ── NO ACTIVE ENTRY SIGNAL ───────────────────────────────────
        if active_entry is None or active_entry.signal not in ("LONG", "SHORT"):
            sig.signal = "HOLD"
            hold_reasons = []
            if active_entry:
                hold_reasons = active_entry.reasons
            sig.reasons = hold_reasons + all_reasons
            return sig

        # ── VETO CHECKS & PRE-COMPUTATION ───────────────────────────
        entry_px = active_entry.entry_price
        sl_px = active_entry.sl
        t1_px = active_entry.target1
        t2_px = active_entry.target2

        # Snap SL/TARGETS to HTF levels if they're nearby
        for level in htf.all_levels:
            lp = level.price
            # Snap SL to nearest HTF level if close
            if abs(lp - sl_px) < atr * 0.5:
                if active_entry.signal == "LONG" and lp < sl_px:
                    sl_px = lp - atr * 0.1  # just below the level
                elif active_entry.signal == "SHORT" and lp > sl_px:
                    sl_px = lp + atr * 0.1  # just above the level

            # Snap T1 to nearest HTF level
            if abs(lp - t1_px) < atr * 0.8:
                t1_px = lp

        # Risk-reward
        risk = abs(entry_px - sl_px)
        rr_t1 = abs(t1_px - entry_px) / max(risk, 0.01)

        # Expose candidate setup details to UI even if vetoed / held
        sig.entry = round(entry_px, 2)
        sig.sl = round(sl_px, 2)
        sig.target1 = round(t1_px, 2)
        sig.target2 = round(t2_px, 2)
        sig.risk_pts = round(risk, 2)
        sig.rr_t1 = round(rr_t1, 2)

        # 1. Momentum/Volume veto
        if momentum.veto:
            veto_reasons.append(f"Momentum VETO: {'; '.join(momentum.reasons[:2])}")

        # 2. HTF conflict: don't go LONG at HTF resistance in a range trade
        if playbook == "RANGE" and active_entry.signal == "LONG" and htf.at_htf_resistance:
            veto_reasons.append("Range LONG at HTF resistance — conflict")

        if playbook == "RANGE" and active_entry.signal == "SHORT" and htf.at_htf_support:
            veto_reasons.append("Range SHORT at HTF support — conflict")

        # 3. Trend trade against HTF trend
        if playbook == "TREND":
            if active_entry.signal == "LONG" and htf.htf_trend == "BEARISH":
                # Not a hard veto, but reduce quality
                all_reasons.append("Warning: LONG trend trade against bearish HTF")
            if active_entry.signal == "SHORT" and htf.htf_trend == "BULLISH":
                all_reasons.append("Warning: SHORT trend trade against bullish HTF")

        # 4. Regime too young — not established enough
        if regime.regime_age < self.min_regime_age:
            veto_reasons.append(
                f"Regime age {regime.regime_age} < min {self.min_regime_age} bars"
            )

        # 5. Entry quality too low
        if active_entry.entry_quality < self.min_quality:
            veto_reasons.append(
                f"Entry quality {active_entry.entry_quality:.2f} < min {self.min_quality}"
            )

        # 6. Exhaustion detected
        if momentum.exhaustion_detected:
            veto_reasons.append("Momentum exhaustion detected")

        if veto_reasons:
            sig.signal = "HOLD"
            sig.veto_reasons = veto_reasons
            sig.reasons = all_reasons + active_entry.reasons
            return sig

        # 7. R:R too low — don't take trades where reward < min_rr * risk
        if rr_t1 < self.min_rr:
            sig.signal = "HOLD"
            sig.veto_reasons = [
                f"R:R too low: {rr_t1:.2f}x (need {self.min_rr}x). "
                f"Risk={risk:.0f} pts, Reward={abs(t1_px - entry_px):.0f} pts"
            ]
            sig.reasons = all_reasons + active_entry.reasons
            return sig

        # Weighted score (for compatibility and ranking)
        quality = active_entry.entry_quality
        regime_score = regime.confidence
        momentum_score = momentum.confidence if momentum.confirms else 0.2
        htf_bonus = 0.1 if (
            (active_entry.signal == "LONG" and htf.at_htf_support) or
            (active_entry.signal == "SHORT" and htf.at_htf_resistance)
        ) else 0.0

        weighted_score = (
            0.30 * regime_score +
            0.30 * quality +
            0.25 * momentum_score +
            0.15 * htf.confidence +
            htf_bonus
        )

        all_reasons.extend(active_entry.reasons)
        all_reasons.extend(momentum.reasons[:2])
        all_reasons.extend(htf.reasons[:2])

        sig.signal = active_entry.signal
        sig.entry_quality = round(quality, 3)
        sig.momentum_confirms = momentum.confirms
        sig.weighted_score = round(weighted_score, 3)
        sig.long_score = round(weighted_score if active_entry.signal == "LONG" else 0, 3)
        sig.short_score = round(weighted_score if active_entry.signal == "SHORT" else 0, 3)
        sig.reasons = all_reasons
        sig.veto_reasons = []

        return sig
