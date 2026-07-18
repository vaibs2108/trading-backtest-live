"""
memory_agent.py — PatternMemoryAgent: FAISS vector similarity search.

"Has this setup happened before? What was the outcome?"
Uses a FAISS index of historical setups for nearest-neighbor search.
"""
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional
from .base import BaseAgent, PatternMemoryState

logger = logging.getLogger(__name__)

# Key features used for vector encoding
MEMORY_FEATURES = [
    # SuperTrend state
    "st_bars_since_flip", "st_price_dist_atr", "st_touch_count_10",
    # EMA state
    "ema_bars_since_cross", "ema_gap_atr", "ema_price_zone",
    # StochRSI state
    "srsi_phase", "srsi_trajectory_5bar", "srsi_bars_in_oversold", "srsi_bars_in_overbought",
    # ADX state
    "adx_regime", "adx_slope_5bar", "di_spread",
    # MACD state
    "macd_hist_slope_3bar", "macd_hist_peak_ratio", "macd_zero_cross_bars",
    # Stoch state
    "stoch_phase",
    # Multi-TF alignment
    "tf_st_alignment_net", "tf_ema_alignment_net", "tf_momentum_alignment",
    # Structure
    "fib_zone", "fib_nearest_dist_atr",
    "sr_nearest_support_atr", "sr_nearest_resistance_atr",
    # Volume
    "vol_relative", "vol_price_divergence",
    # Higher TF context
    "h1_srsi_phase", "h1_adx_regime", "h1_macd_hist_slope_3bar",
    "m15_srsi_phase",
    # Pattern
    "pattern_encoded", "pattern_completion",
    # Weekly / Daily outermost direction (NEW — captures HTF context)
    "w_supertrend_dir", "w_ema_cross",
    "d1_supertrend_dir",
    # EMA21 cascade sequence (NEW — how long price has been resting at EMA21)
    "h1_bars_near_ema21",
    # Trendline context (NEW — are we at a trendline?)
    "trendline_res_slope", "trendline_sup_slope",
]


class PatternMemoryAgent(BaseAgent):
    """
    Uses FAISS vector search to find similar past setups and check outcomes.
    Provides historical win rate and confidence based on analogs.
    """

    def __init__(self):
        super().__init__()
        self._index = None
        self._outcomes = None  # Array of (pnl, win_flag, hold_bars, date_str)
        self._loaded = False

    def load_index(self, index_path: str):
        """Load the pre-built FAISS index and outcomes."""
        try:
            import faiss

            vec_file = Path(index_path) / "vectors.npz"
            idx_file = Path(index_path) / "faiss_index.bin"
            out_file = Path(index_path) / "outcomes.npz"

            if not out_file.exists():
                logger.warning(f"Pattern memory outcomes not found at {index_path}")
                return

            # Check if portable vectors.npz exists to rebuild the index cross-platform
            if vec_file.exists():
                logger.info("Loading cross-platform vectors.npz to build FAISS index...")
                vec_data = np.load(str(vec_file))
                vectors_np = vec_data["vectors"]
                dim = vectors_np.shape[1]
                self._index = faiss.IndexFlatL2(dim)
                self._index.add(vectors_np)
            elif idx_file.exists():
                logger.info("Loading faiss_index.bin fallback...")
                self._index = faiss.read_index(str(idx_file))
            else:
                logger.warning(f"Pattern memory vectors/index not found at {index_path}")
                return

            data = np.load(str(out_file), allow_pickle=True)
            self._outcomes = data["outcomes"]  # (N, 4): pnl_atr, win_flag, hold_bars, date_str
            self._loaded = True
            logger.info(f"Pattern memory loaded: {self._index.ntotal} setups indexed")

        except ImportError:
            logger.warning("FAISS not installed. Pattern memory disabled. "
                           "Install with: pip install faiss-cpu")
        except Exception as e:
            logger.warning(f"Failed to load pattern memory: {e}")

    def evaluate(self, row: pd.Series, k: int = 20,
                 min_samples: int = 5) -> PatternMemoryState:
        """
        Find similar past setups and compute outcome statistics.

        Args:
            row: Latest merged row with all behavioral features
            k: Number of nearest neighbors to retrieve
            min_samples: Minimum analogs needed for confidence
        """
        if not self._loaded or self._index is None:
            return PatternMemoryState(
                reasons=["Pattern memory not loaded"]
            )

        # Encode current setup as vector
        vector = self._encode_setup(row)
        if vector is None:
            return PatternMemoryState(
                reasons=["Could not encode current setup"]
            )

        # Search
        try:
            import faiss
            vector_2d = vector.reshape(1, -1).astype(np.float32)
            distances, indices = self._index.search(vector_2d, k)

            # Filter valid indices
            valid = indices[0][indices[0] >= 0]
            if len(valid) < min_samples:
                return PatternMemoryState(
                    similar_setups_found=len(valid),
                    confidence=0.0,
                    reasons=[f"Only {len(valid)} analogs found (need {min_samples})"]
                )

            # Get outcomes for these neighbors
            outcomes = self._outcomes[valid]
            pnl_atrs = outcomes[:, 0].astype(float)
            win_flags = outcomes[:, 1].astype(float)
            hold_bars = outcomes[:, 2].astype(float)

            win_rate = win_flags.mean()
            avg_pnl = pnl_atrs.mean()
            avg_hold = hold_bars.mean()

            # Consistency: are the outcomes consistent?
            # (all wins = 1.0, mixed = 0.5, all losses = 1.0 but negative)
            consistency = 1 - win_flags.std() * 2  # Lower std = more consistent
            consistency = max(0, min(1, consistency))

            # Best analog
            best_idx = valid[np.argmax(pnl_atrs)]
            best_date = str(self._outcomes[best_idx, 3]) if self._outcomes.shape[1] > 3 else ""

            # Confidence: based on sample size + consistency + win rate
            confidence = 0.0
            if len(valid) >= min_samples:
                confidence = 0.3  # Base for having enough samples
                confidence += consistency * 0.3
                confidence += max(0, (win_rate - 0.5)) * 0.4  # Bonus for high win rate
            confidence = min(1.0, confidence)

            reasons = [
                f"Found {len(valid)} similar past setups",
                f"Historical win rate: {win_rate:.0%}",
                f"Avg P&L: {avg_pnl:.1f} ATR",
                f"Avg hold: {avg_hold:.0f} bars",
            ]

            return PatternMemoryState(
                similar_setups_found=len(valid),
                historical_win_rate=round(win_rate, 3),
                historical_avg_pnl_atr=round(avg_pnl, 2),
                historical_avg_hold_bars=int(avg_hold),
                best_analog_date=best_date,
                sample_consistency=round(consistency, 3),
                confidence=round(confidence, 3),
                reasons=reasons,
            )

        except Exception as e:
            logger.warning(f"Pattern memory search failed: {e}")
            return PatternMemoryState(reasons=[f"Search failed: {e}"])

    def _encode_setup(self, row: pd.Series) -> Optional[np.ndarray]:
        """Encode a trading setup as a normalized vector."""
        values = []
        for feat in MEMORY_FEATURES:
            val = row.get(feat, 0)
            if isinstance(val, float) and np.isnan(val):
                val = 0.0
            values.append(float(val))

        vec = np.array(values, dtype=np.float32)

        # L2 normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return vec

    @staticmethod
    def build_index(df: pd.DataFrame, outcomes_df: pd.DataFrame,
                    output_dir: str):
        """
        Build the FAISS index from historical data.
        Called by training pipeline.

        Args:
            df: Merged DataFrame with all behavioral features (signal bars only)
            outcomes_df: DataFrame with columns [timestamp, pnl_atr, win, hold_bars]
            output_dir: Directory to save index files
        """
        try:
            import faiss
        except ImportError:
            logger.error("FAISS not installed. Cannot build index.")
            return

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Encode all setups
        vectors = []
        valid_outcomes = []

        for idx, row in df.iterrows():
            values = []
            for feat in MEMORY_FEATURES:
                val = row.get(feat, 0)
                if isinstance(val, float) and np.isnan(val):
                    val = 0.0
                values.append(float(val))
            vec = np.array(values, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            vectors.append(vec)

            # Get outcome for this bar
            ts = row.get("timestamp", "")
            outcome_row = outcomes_df[outcomes_df["timestamp"] == ts]
            if not outcome_row.empty:
                valid_outcomes.append([
                    float(outcome_row.iloc[0].get("pnl_atr", 0)),
                    float(outcome_row.iloc[0].get("win", 0)),
                    float(outcome_row.iloc[0].get("hold_bars", 0)),
                    str(ts),
                ])
            else:
                valid_outcomes.append([0, 0, 0, str(ts)])

        vectors_np = np.array(vectors, dtype=np.float32)
        outcomes_np = np.array(valid_outcomes, dtype=object)

        # Build FAISS index (L2 distance, flat for accuracy)
        dim = vectors_np.shape[1]
        index = faiss.IndexFlatL2(dim)
        index.add(vectors_np)

        # Save
        faiss.write_index(index, str(output_path / "faiss_index.bin"))
        np.savez(str(output_path / "vectors.npz"), vectors=vectors_np)
        np.savez(str(output_path / "outcomes.npz"), outcomes=outcomes_np)

        logger.info(f"Pattern memory index built: {index.ntotal} setups, "
                    f"dim={dim}, saved to {output_dir}")
