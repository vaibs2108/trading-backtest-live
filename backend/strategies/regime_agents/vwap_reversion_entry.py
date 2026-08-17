"""
vwap_reversion_entry.py — Session-anchored VWAP + standard-deviation-band
mean reversion, an alternative range definition to both RangeEntryAgent
(percentile of highs/lows) and VolumeProfileEntryAgent (POC/VAH/VAL).

VWAP resets at the start of each trading day (session-anchored, per
standard intraday futures practice) rather than using a rolling N-bar
lookback that ignores day boundaries. Entries fade price extended beyond
N standard deviations of the VWAP; target is VWAP itself.

Scratch-only. Not wired into any live path.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List


@dataclass
class VWAPReversionState:
    signal: str = "HOLD"
    entry_price: float = 0.0
    sl: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    vwap: float = 0.0
    std_dev: float = 0.0
    entry_quality: float = 0.0
    reasons: List[str] = field(default_factory=list)


class VWAPReversionAgent:
    def __init__(self, entry_std: float = 2.0, min_std_atr: float = 0.5,
                 session_start_minute: int = 9 * 60 + 20):
        self.entry_std = entry_std
        self.min_std_atr = min_std_atr
        self.session_start_minute = session_start_minute

    def _session_vwap(self, df: pd.DataFrame, row: pd.Series):
        """Cumulative VWAP from the start of the current trading session."""
        cur_date = pd.Timestamp(row["timestamp"]).date()
        day_df = df[df["timestamp"].dt.date == cur_date]
        if len(day_df) < 3 or "volume" not in day_df.columns:
            return None, None
        closes = day_df["close"].values.astype(float)
        vols = day_df["volume"].values.astype(float)
        vols = np.where(vols <= 0, 1.0, vols)
        cum_pv = np.cumsum(closes * vols)
        cum_v = np.cumsum(vols)
        vwap_series = cum_pv / cum_v
        vwap = vwap_series[-1]
        dev = closes - vwap_series
        std = float(np.std(dev)) if len(dev) > 3 else 0.0
        return vwap, std

    def evaluate(self, df: pd.DataFrame, row: pd.Series, regime: str,
                 regime_confidence: float, position: str = "NONE",
                 atr: float = 0.0) -> VWAPReversionState:
        close = float(row.get("close", 0))
        if regime != "SIDEWAYS":
            return VWAPReversionState(signal="HOLD", reasons=["Not SIDEWAYS"])

        if atr <= 0:
            atr = close * 0.002

        vwap, std = self._session_vwap(df, row)
        if vwap is None or std <= 0:
            return VWAPReversionState(signal="HOLD", reasons=["Insufficient session data"])

        std_atr = std / atr if atr > 0 else 0
        base = VWAPReversionState(vwap=round(vwap, 2), std_dev=round(std, 2))

        if std_atr < self.min_std_atr:
            base.signal = "HOLD"
            base.reasons = [f"VWAP std too tight: {std_atr:.2f} ATR"]
            return base

        z = (close - vwap) / std if std > 0 else 0

        if position != "NONE":
            if position == "LONG" and close >= vwap:
                base.signal = "LONG_EXIT"
                base.reasons = [f"Price reached VWAP {vwap:.0f} -- exit LONG"]
            elif position == "SHORT" and close <= vwap:
                base.signal = "SHORT_EXIT"
                base.reasons = [f"Price reached VWAP {vwap:.0f} -- exit SHORT"]
            else:
                base.signal = "HOLD"
            return base

        o, h, l = float(row.get("open", 0)), float(row.get("high", close)), float(row.get("low", close))
        bar_range = h - l

        if z <= -self.entry_std:
            body = close - o
            if bar_range > 0 and body > 0 and body / bar_range > 0.40:
                sl = close - atr * 1.0
                quality = round(0.5 * min(abs(z) / 3.0, 1.0) + 0.5 * regime_confidence, 3)
                base.signal = "LONG"
                base.entry_price = close
                base.sl = round(sl, 2)
                base.target1 = round(vwap, 2)
                base.target2 = round(vwap + std, 2)
                base.entry_quality = quality
                base.reasons = [f"z={z:.2f} below -{self.entry_std}, targeting VWAP {vwap:.0f}"]
                return base

        if z >= self.entry_std:
            body = o - close
            if bar_range > 0 and body > 0 and body / bar_range > 0.40:
                sl = close + atr * 1.0
                quality = round(0.5 * min(abs(z) / 3.0, 1.0) + 0.5 * regime_confidence, 3)
                base.signal = "SHORT"
                base.entry_price = close
                base.sl = round(sl, 2)
                base.target1 = round(vwap, 2)
                base.target2 = round(vwap - std, 2)
                base.entry_quality = quality
                base.reasons = [f"z={z:.2f} above +{self.entry_std}, targeting VWAP {vwap:.0f}"]
                return base

        base.signal = "HOLD"
        base.reasons = [f"z={z:.2f}, waiting for extension"]
        return base
