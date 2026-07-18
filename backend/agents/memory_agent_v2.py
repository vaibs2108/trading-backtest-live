"""
memory_agent_v2.py — FAISS Pattern Memory v2: Structural context vectors.

v1 encoded raw indicator values (StochRSI K=25).
v2 encodes structural context: "price at 1H EMA21 after cascade descent,
15m MACD expanding bear, volume climax."

This captures WHAT HAPPENED structurally, not just indicator thresholds.

Two modes:
  1. Query mode — given current setup + scenario type, find similar historical
     setups and return win rate / avg move.
  2. Build mode — construct FAISS index from backtest trades.
"""
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE VECTOR DEFINITION
# ═══════════════════════════════════════════════════════════════════════════════

# v2 feature names — structural context, NOT raw indicator values
MEMORY_FEATURES_V2 = [
    # ── EMA Cascade State (ordinal per TF) ──
    # Encodes WHERE price sits in the structure: 0=BELOW_ST, 1=BELOW_21_ABOVE_ST,
    # 2=BETWEEN_7_21, 3=ABOVE_ALL (for bullish structure; inverse for bearish)
    "h1_cascade_encoded",       # 0-4
    "d1_cascade_encoded",       # 0-4

    # ── Price position relative to key levels (ATR-normalized) ──
    "price_to_h1_ema7_atr",
    "price_to_h1_ema21_atr",
    "price_to_h1_st_atr",
    "price_to_d1_ema7_atr",
    "price_to_d1_ema21_atr",
    "price_to_nearest_res_atr",  # nearest resistance from LevelMap
    "price_to_nearest_sup_atr",  # nearest support from LevelMap

    # ── Journey state ──
    "cascade_direction",        # -1 descending, 0 stalling, +1 ascending
    "bars_in_journey",
    "journey_completion_pct",   # 0-1 (how close to target)

    # ── Momentum context (trajectory, not threshold) ──
    "m15_macd_slope_norm",      # Normalized MACD histogram slope
    "m15_momentum_traj",        # -2 to +2 (ACCELERATING to REVERSING)
    "m5_macd_slope_norm",

    # ── Volume context ──
    "volume_relative_5m",       # Volume / SMA20
    "volume_state_encoded",     # 0=NORMAL, 1=DRYING, 2=EXPANDING, 3=CLIMAX, -1=DIVERGENT

    # ── HTF alignment ──
    "weekly_daily_alignment",   # -1 (both bear), 0 (mixed), +1 (both bull)
    "daily_h1_alignment",       # Same encoding

    # ── Time context ──
    "session_phase",            # 0=opening, 1=midday, 2=closing

    # ── Oscillator context (v3 trend-aware) ──
    "oscillator_context_encoded",  # -2=OB_downtrend, -1=OB_uptrend, 0=neutral, 1=OS_uptrend, 2=OS_downtrend

    # ── Scenario type (for filtered search) ──
    "scenario_type_encoded",    # 0=NONE, 1=EMA_CASCADE, 2=HTF_LEVEL, 3=TREND_CONT, 4=BREAKOUT
]

VECTOR_DIM = len(MEMORY_FEATURES_V2)

# Scenario type encoding map
SCENARIO_TYPE_MAP = {
    "NONE": 0,
    "EMA_CASCADE": 1,
    "HTF_LEVEL_BOUNCE": 2,
    "TREND_CONTINUATION": 3,
    "BREAKOUT": 4,
    "REVERSAL": 5,
}

# Cascade state encoding (ordinal: worse → better for the given bias)
CASCADE_ENCODE = {
    "BELOW_ST": 0, "BELOW_ALL": 0,
    "BELOW_21_ABOVE_ST": 1, "ABOVE_21_BELOW_ST": 1,
    "BETWEEN_7_21": 2, "BETWEEN_21_7": 2,
    "ABOVE_ALL": 3, "ABOVE_ST": 3,
    "UNKNOWN": 2,  # neutral
}

# Volume state encoding
VOLUME_STATE_MAP = {
    "NORMAL": 0, "DRYING": 1, "EXPANDING": 2, "CLIMAX": 3, "DIVERGENT": -1,
}

# Oscillator context encoding
OSC_CONTEXT_MAP = {
    "OVERBOUGHT_IN_DOWNTREND": -2,
    "OVERBOUGHT_IN_UPTREND": -1,
    "NEUTRAL": 0,
    "OVERSOLD_IN_UPTREND": 1,
    "OVERSOLD_IN_DOWNTREND": 2,
}

# Momentum trajectory encoding
MOM_TRAJ_MAP = {
    "REVERSING": -2, "DECELERATING": -1, "STEADY": 0,
    "ACCELERATING": 1,
}


# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MemoryResult:
    """Result from FAISS similarity search."""
    similar_setups_found: int = 0
    historical_win_rate: float = 0.5
    historical_avg_pnl_atr: float = 0.0
    historical_avg_hold_bars: int = 0
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN MEMORY AGENT v2
# ═══════════════════════════════════════════════════════════════════════════════

class PatternMemoryAgentV2:
    """
    FAISS-based pattern memory using structural context vectors.

    Modes:
      - load_index() + query() for live/backtest inference
      - collect_setup() + build_and_save_index() for training
    """

    def __init__(self):
        self._index = None
        self._outcomes = None       # (N, 5): pnl_atr, win, hold_bars, scenario_type, date
        self._scenario_types = None # (N,): scenario type codes for filtered search
        self._loaded = False

        # Collector for building index during backtest
        self._vectors_buffer: List[np.ndarray] = []
        self._outcomes_buffer: List[list] = []

    # ── LOADING ─────────────────────────────────────────────────────────────

    def load_index(self, index_dir: str) -> bool:
        """Load pre-built FAISS index from disk."""
        try:
            import faiss
        except ImportError:
            logger.warning("faiss-cpu not installed. Pattern memory disabled.")
            return False

        idx_file = Path(index_dir) / "faiss_v2_index.bin"
        out_file = Path(index_dir) / "outcomes_v2.npz"

        if not idx_file.exists() or not out_file.exists():
            logger.info(f"FAISS v2 index not found at {index_dir} — memory disabled")
            return False

        try:
            self._index = faiss.read_index(str(idx_file))
            data = np.load(str(out_file), allow_pickle=True)
            self._outcomes = data["outcomes"]
            self._scenario_types = data.get("scenario_types", None)
            if self._scenario_types is None:
                self._scenario_types = np.zeros(self._outcomes.shape[0], dtype=np.int32)
            self._loaded = True
            logger.info(f"FAISS v2 loaded: {self._index.ntotal} setups, dim={VECTOR_DIM}")
            return True
        except Exception as e:
            logger.warning(f"Failed to load FAISS v2: {e}")
            return False

    # ── QUERY ───────────────────────────────────────────────────────────────

    def query(self, vector: np.ndarray, scenario_type: str = "NONE",
              k: int = 30, min_samples: int = 5) -> MemoryResult:
        """
        Find similar historical setups and compute outcome stats.

        Args:
            vector: Encoded feature vector (from encode_setup)
            scenario_type: Filter to same scenario type for better relevance
            k: Number of nearest neighbors to fetch (pre-filter)
            min_samples: Minimum analogs for confident result
        """
        if not self._loaded or self._index is None:
            return MemoryResult(reasons=["FAISS not loaded"])

        try:
            import faiss
            vec_2d = vector.reshape(1, -1).astype(np.float32)

            # Fetch more than k to allow scenario-type filtering
            fetch_k = min(k * 3, self._index.ntotal)
            distances, indices = self._index.search(vec_2d, fetch_k)

            valid_idx = indices[0][indices[0] >= 0]
            if len(valid_idx) == 0:
                return MemoryResult(reasons=["No neighbors found"])

            # Filter by scenario type if requested
            sc_code = SCENARIO_TYPE_MAP.get(scenario_type, 0)
            if sc_code > 0 and self._scenario_types is not None:
                type_mask = self._scenario_types[valid_idx] == sc_code
                if type_mask.sum() >= min_samples:
                    valid_idx = valid_idx[type_mask]
                # If not enough same-type matches, use all (graceful degradation)

            # Trim to k
            valid_idx = valid_idx[:k]

            if len(valid_idx) < min_samples:
                return MemoryResult(
                    similar_setups_found=len(valid_idx),
                    confidence=0.0,
                    reasons=[f"Only {len(valid_idx)} analogs (need {min_samples})"],
                )

            # Compute stats
            outcomes = self._outcomes[valid_idx]
            pnl_atrs = outcomes[:, 0].astype(float)
            win_flags = outcomes[:, 1].astype(float)
            hold_bars = outcomes[:, 2].astype(float)

            win_rate = float(win_flags.mean())
            avg_pnl = float(pnl_atrs.mean())
            avg_hold = float(hold_bars.mean())

            # Consistency: low std = consistent outcomes
            consistency = max(0, 1 - win_flags.std() * 2)

            # Confidence: sample size + consistency + win rate clarity
            confidence = 0.0
            if len(valid_idx) >= min_samples:
                confidence = 0.30                                     # base
                confidence += consistency * 0.25                      # consistent outcomes
                confidence += abs(win_rate - 0.5) * 0.45             # clear win/loss signal
                # More samples = more confidence
                sample_bonus = min(0.15, len(valid_idx) / 100 * 0.15)
                confidence += sample_bonus
            confidence = min(1.0, max(0.0, confidence))

            reasons = [
                f"{len(valid_idx)} similar setups found",
                f"Win rate: {win_rate:.0%}",
                f"Avg PnL: {avg_pnl:+.1f} ATR",
            ]

            return MemoryResult(
                similar_setups_found=len(valid_idx),
                historical_win_rate=round(win_rate, 3),
                historical_avg_pnl_atr=round(avg_pnl, 2),
                historical_avg_hold_bars=int(avg_hold),
                confidence=round(confidence, 3),
                reasons=reasons,
            )

        except Exception as e:
            logger.warning(f"FAISS query failed: {e}")
            return MemoryResult(reasons=[f"Query error: {e}"])

    # ── FEATURE ENCODING ────────────────────────────────────────────────────

    @staticmethod
    def encode_setup(row: pd.Series, swing=None, flow=None, htf=None,
                     level_map=None, scenario_type: str = "NONE") -> np.ndarray:
        """
        Encode a trading setup into a v2 structural context vector.

        Can work in two modes:
          1. With agent outputs (swing, flow, htf, level_map) — richest encoding
          2. From raw merged row only — derives values from columns (for index building)
        """
        def safe(val, default=0.0):
            if val is None or (isinstance(val, float) and np.isnan(val)):
                return default
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        close = safe(row.get("close", 0))
        atr = safe(row.get("atr", close * 0.002))
        if atr < 1:
            atr = close * 0.002

        h1_atr = safe(row.get("h1_atr", atr * 2))
        if h1_atr < 1:
            h1_atr = atr * 2

        features = {}

        # ── EMA cascade state ──────────────────────────────────────────
        if swing is not None:
            features["h1_cascade_encoded"] = CASCADE_ENCODE.get(swing.ema_cascade, 2)
        else:
            # Derive from raw columns
            h1_ema7 = safe(row.get("h1_ema7", 0))
            h1_ema21 = safe(row.get("h1_ema21", 0))
            h1_st = safe(row.get("h1_supertrend", 0))
            if h1_ema7 > 0 and h1_ema21 > 0:
                if close > h1_ema7 and close > h1_ema21:
                    features["h1_cascade_encoded"] = 3  # ABOVE_ALL
                elif close > h1_ema21:
                    features["h1_cascade_encoded"] = 2  # BETWEEN
                elif close > h1_st:
                    features["h1_cascade_encoded"] = 1  # BELOW_21_ABOVE_ST
                else:
                    features["h1_cascade_encoded"] = 0  # BELOW_ST
            else:
                features["h1_cascade_encoded"] = 2

        # Daily cascade
        d1_ema7 = safe(row.get("d1_ema7", 0))
        d1_ema21 = safe(row.get("d1_ema21", 0))
        d1_st = safe(row.get("d1_supertrend", 0))
        if d1_ema7 > 0 and d1_ema21 > 0:
            if close > d1_ema7 and close > d1_ema21:
                features["d1_cascade_encoded"] = 3
            elif close > d1_ema21:
                features["d1_cascade_encoded"] = 2
            elif close > d1_st:
                features["d1_cascade_encoded"] = 1
            else:
                features["d1_cascade_encoded"] = 0
        else:
            features["d1_cascade_encoded"] = 2

        # ── Price-to-level distances (ATR normalized) ──────────────────
        h1_ema7 = safe(row.get("h1_ema7", close))
        h1_ema21 = safe(row.get("h1_ema21", close))
        h1_st_price = safe(row.get("h1_supertrend", close))
        d1_ema7_v = safe(row.get("d1_ema7", close))
        d1_ema21_v = safe(row.get("d1_ema21", close))

        features["price_to_h1_ema7_atr"] = np.clip((close - h1_ema7) / h1_atr, -5, 5)
        features["price_to_h1_ema21_atr"] = np.clip((close - h1_ema21) / h1_atr, -5, 5)
        features["price_to_h1_st_atr"] = np.clip((close - h1_st_price) / h1_atr, -5, 5)
        features["price_to_d1_ema7_atr"] = np.clip((close - d1_ema7_v) / h1_atr, -5, 5)
        features["price_to_d1_ema21_atr"] = np.clip((close - d1_ema21_v) / h1_atr, -5, 5)

        # Nearest resistance/support from LevelMap
        if level_map is not None:
            if level_map.levels_above:
                features["price_to_nearest_res_atr"] = np.clip(
                    (level_map.levels_above[0].price - close) / h1_atr, 0, 10)
            else:
                features["price_to_nearest_res_atr"] = 10.0
            if level_map.levels_below:
                features["price_to_nearest_sup_atr"] = np.clip(
                    (close - level_map.levels_below[0].price) / h1_atr, 0, 10)
            else:
                features["price_to_nearest_sup_atr"] = 10.0
        else:
            # Estimate from row columns
            h1_res = safe(row.get("h1_res1", close + 3 * h1_atr))
            h1_sup = safe(row.get("h1_sup1", close - 3 * h1_atr))
            features["price_to_nearest_res_atr"] = np.clip((h1_res - close) / h1_atr, 0, 10)
            features["price_to_nearest_sup_atr"] = np.clip((close - h1_sup) / h1_atr, 0, 10)

        # ── Journey state ──────────────────────────────────────────────
        if swing is not None:
            dir_map = {"DESCENDING": -1, "ASCENDING": 1, "STALLING": 0}
            features["cascade_direction"] = dir_map.get(swing.ema_cascade_direction, 0)
            features["bars_in_journey"] = min(swing.bars_in_journey, 50) / 50.0  # normalize
            # Journey completion
            if swing.journey_target > 0 and swing.journey_invalidation > 0:
                total_dist = abs(swing.journey_target - swing.journey_invalidation)
                if total_dist > 0:
                    progress = abs(close - swing.journey_invalidation) / total_dist
                    features["journey_completion_pct"] = np.clip(progress, 0, 1)
                else:
                    features["journey_completion_pct"] = 0.5
            else:
                features["journey_completion_pct"] = 0.5
        else:
            features["cascade_direction"] = 0
            features["bars_in_journey"] = 0
            features["journey_completion_pct"] = 0.5

        # ── Momentum context ───────────────────────────────────────────
        m15_macd_slope = safe(row.get("m15_macd_hist_slope_3bar", 0))
        m5_macd_slope = safe(row.get("macd_hist_slope_3bar", 0))
        features["m15_macd_slope_norm"] = np.clip(m15_macd_slope / max(atr * 0.1, 1), -3, 3)
        features["m5_macd_slope_norm"] = np.clip(m5_macd_slope / max(atr * 0.1, 1), -3, 3)

        if flow is not None:
            features["m15_momentum_traj"] = MOM_TRAJ_MAP.get(flow.momentum_trajectory, 0)
        else:
            # Estimate from raw
            if m15_macd_slope > atr * 0.05:
                features["m15_momentum_traj"] = 1
            elif m15_macd_slope < -atr * 0.05:
                features["m15_momentum_traj"] = -1
            else:
                features["m15_momentum_traj"] = 0

        # ── Volume context ─────────────────────────────────────────────
        vol = safe(row.get("volume", 0))
        vol_sma = safe(row.get("m15_volume_sma20", vol))
        if vol_sma > 0:
            features["volume_relative_5m"] = np.clip(vol / vol_sma, 0, 5)
        else:
            features["volume_relative_5m"] = 1.0

        if flow is not None:
            features["volume_state_encoded"] = VOLUME_STATE_MAP.get(flow.volume_state, 0)
        else:
            rv = features["volume_relative_5m"]
            if rv > 2.5:
                features["volume_state_encoded"] = 3
            elif rv > 1.5:
                features["volume_state_encoded"] = 2
            elif rv < 0.5:
                features["volume_state_encoded"] = 1
            else:
                features["volume_state_encoded"] = 0

        # ── HTF alignment ──────────────────────────────────────────────
        w_st = safe(row.get("w_supertrend_dir", 0))
        d_st = safe(row.get("d1_supertrend_dir", 0))
        h1_st_dir = safe(row.get("h1_supertrend_dir", 0))

        if w_st == d_st and w_st != 0:
            features["weekly_daily_alignment"] = w_st  # +1 or -1
        else:
            features["weekly_daily_alignment"] = 0

        if d_st == h1_st_dir and d_st != 0:
            features["daily_h1_alignment"] = d_st
        else:
            features["daily_h1_alignment"] = 0

        # ── Time context ───────────────────────────────────────────────
        hour = safe(row.get("_hour", 12))
        if hour < 10:
            features["session_phase"] = 0  # opening
        elif hour < 14:
            features["session_phase"] = 1  # midday
        else:
            features["session_phase"] = 2  # closing

        # ── Oscillator context (trend-aware) ───────────────────────────
        if flow is not None:
            features["oscillator_context_encoded"] = OSC_CONTEXT_MAP.get(
                flow.oscillator_context, 0)
        else:
            features["oscillator_context_encoded"] = 0

        # ── Scenario type ──────────────────────────────────────────────
        features["scenario_type_encoded"] = SCENARIO_TYPE_MAP.get(scenario_type, 0)

        # ── Build vector ───────────────────────────────────────────────
        vec = np.array([features.get(f, 0.0) for f in MEMORY_FEATURES_V2], dtype=np.float32)

        # L2 normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return vec

    # ── COLLECTING (during backtest) ────────────────────────────────────────

    def collect_setup(self, vector: np.ndarray, pnl_atr: float, win: bool,
                      hold_bars: int, scenario_type: str, date_str: str):
        """Collect a completed trade's setup vector + outcome for later index building."""
        self._vectors_buffer.append(vector)
        self._outcomes_buffer.append([
            pnl_atr,
            1.0 if win else 0.0,
            float(hold_bars),
            float(SCENARIO_TYPE_MAP.get(scenario_type, 0)),
            date_str,
        ])

    def reset_collector(self):
        """Clear the collector buffer."""
        self._vectors_buffer = []
        self._outcomes_buffer = []

    def build_and_save_index(self, output_dir: str) -> int:
        """Build FAISS index from collected setups and save to disk."""
        if not self._vectors_buffer:
            logger.warning("No setups collected — cannot build FAISS index")
            return 0

        try:
            import faiss
        except ImportError:
            logger.error("faiss-cpu not installed. Cannot build index.")
            return 0

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        vectors_np = np.array(self._vectors_buffer, dtype=np.float32)
        outcomes_np = np.array([[o[0], o[1], o[2], o[3]] for o in self._outcomes_buffer],
                               dtype=np.float32)
        scenario_types_np = np.array([int(o[3]) for o in self._outcomes_buffer], dtype=np.int32)

        # Build index (L2 flat — exact search, fine for <100k setups)
        dim = vectors_np.shape[1]
        index = faiss.IndexFlatL2(dim)
        index.add(vectors_np)

        # Save
        faiss.write_index(index, str(output_path / "faiss_v2_index.bin"))
        np.savez(
            str(output_path / "outcomes_v2.npz"),
            outcomes=outcomes_np,
            scenario_types=scenario_types_np,
        )

        n = index.ntotal
        logger.info(f"FAISS v2 index built: {n} setups, dim={dim}, saved to {output_dir}")
        return n

    # ── STATIC BUILD FROM DATAFRAME (for ml_training.py compat) ─────────────

    @staticmethod
    def build_index_from_trades(trades_df: pd.DataFrame, merged_df: pd.DataFrame,
                                output_dir: str) -> int:
        """
        Build FAISS v2 index from completed backtest trades.

        Args:
            trades_df: DataFrame with columns [entry_bar_idx, exit_bar_idx, direction,
                       entry_price, exit_price, pnl, scenario_type, entry_time]
            merged_df: Full merged table (to extract features from entry bars)
            output_dir: Directory to save index files

        Returns: number of setups indexed
        """
        agent = PatternMemoryAgentV2()
        agent.reset_collector()

        for _, trade in trades_df.iterrows():
            bar_idx = int(trade.get("entry_bar_idx", -1))
            if bar_idx < 0 or bar_idx >= len(merged_df):
                continue

            row = merged_df.iloc[bar_idx]
            atr = float(row.get("atr", 1))
            if atr < 1:
                atr = float(row.get("close", 50000)) * 0.002

            pnl_pts = float(trade.get("pnl", 0))
            direction = trade.get("direction", "LONG")
            # Normalize PnL by ATR and direction
            qty = 1  # per-unit PnL
            pnl_atr = pnl_pts / (atr * 15)  # rough: pnl / (atr * lot_size)
            win = pnl_pts > 0

            hold_bars = int(trade.get("hold_bars", 0))
            sc_type = str(trade.get("scenario_type", "NONE"))
            entry_time = str(trade.get("entry_time", ""))

            vector = PatternMemoryAgentV2.encode_setup(
                row, scenario_type=sc_type
            )

            agent.collect_setup(vector, pnl_atr, win, hold_bars, sc_type, entry_time)

        return agent.build_and_save_index(output_dir)
