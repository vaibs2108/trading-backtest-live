"""
capital_tracker.py — Multi-day capital & drawdown tracking.

Persists peak equity and current equity across days. Enforces an overall
drawdown limit (e.g. 10% of starting capital ~ Rs.5000-6000) that cannot
be reset by a new trading day.

Also provides an intra-trade guard: checks whether realised + unrealised
P&L would breach the daily loss limit, enabling the polling loop to
force-close a position mid-trade.
"""
import json
import logging
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_CAPITAL_PATH = Path(__file__).parent / "data" / "capital_tracker.json"

_IST = timezone(timedelta(hours=5, minutes=30))

def _ist_today() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


class CapitalTracker:
    """
    Tracks overall capital across trading days.

    Fields persisted to disk:
        starting_capital  - user-configured account size
        peak_equity       - highest equity ever reached (HWM)
        current_equity    - starting_capital + cumulative realised P&L
        max_drawdown_pct  - e.g. 10.0 means 10%
        drawdown_breached - True once drawdown limit is hit; blocks trading
        last_updated      - date string of last update
    """

    def __init__(self):
        self.starting_capital: float = 50000.0
        self.peak_equity: float = 50000.0
        self.current_equity: float = 50000.0
        self.max_drawdown_pct: float = 10.0   # default 10%
        self.drawdown_breached: bool = False
        self.last_updated: str = _ist_today()
        self._load()

    # -- Persistence -----------------------------------------------------------

    def _load(self):
        try:
            if _CAPITAL_PATH.exists():
                data = json.loads(_CAPITAL_PATH.read_text(encoding="utf-8"))
                self.starting_capital = data.get("starting_capital", self.starting_capital)
                self.peak_equity = data.get("peak_equity", self.peak_equity)
                self.current_equity = data.get("current_equity", self.current_equity)
                self.max_drawdown_pct = data.get("max_drawdown_pct", self.max_drawdown_pct)
                self.drawdown_breached = data.get("drawdown_breached", False)
                self.last_updated = data.get("last_updated", self.last_updated)
                logger.info(
                    f"Capital tracker loaded: equity={self.current_equity:,.0f}, "
                    f"peak={self.peak_equity:,.0f}, breached={self.drawdown_breached}"
                )
        except Exception as e:
            logger.warning(f"Could not load capital tracker: {e}")

    def _save(self):
        try:
            _CAPITAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CAPITAL_PATH.write_text(json.dumps({
                "starting_capital": self.starting_capital,
                "peak_equity": self.peak_equity,
                "current_equity": self.current_equity,
                "max_drawdown_pct": self.max_drawdown_pct,
                "drawdown_breached": self.drawdown_breached,
                "last_updated": self.last_updated,
            }, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save capital tracker: {e}")

    # -- Configuration ---------------------------------------------------------

    def update_config(self, starting_capital: float, max_drawdown_pct: float = None):
        """Called when settings change. Updates capital and recalculates."""
        changed = False
        if starting_capital != self.starting_capital:
            self.starting_capital = starting_capital
            if self.current_equity == 0:
                self.current_equity = starting_capital
                self.peak_equity = starting_capital
            changed = True
        if max_drawdown_pct is not None and max_drawdown_pct != self.max_drawdown_pct:
            self.max_drawdown_pct = max_drawdown_pct
            changed = True
        if changed:
            self._check_breach()
            self._save()

    # -- Trade lifecycle -------------------------------------------------------

    def record_trade_pnl(self, realised_pnl: float):
        """Called after every trade close. Updates equity and HWM."""
        self.current_equity += realised_pnl
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        self.last_updated = _ist_today()
        self._check_breach()
        self._save()
        logger.info(
            f"Capital updated: equity={self.current_equity:,.0f}, "
            f"peak={self.peak_equity:,.0f}, drawdown={self.current_drawdown:,.0f}"
        )

    # -- Drawdown checks -------------------------------------------------------

    @property
    def drawdown_limit(self) -> float:
        """Absolute drawdown amount in Rs."""
        return self.starting_capital * (self.max_drawdown_pct / 100.0)

    @property
    def current_drawdown(self) -> float:
        """Current drawdown from peak (positive number = loss from peak)."""
        return max(0.0, self.peak_equity - self.current_equity)

    @property
    def current_drawdown_pct(self) -> float:
        """Current drawdown as % of starting capital."""
        if self.starting_capital <= 0:
            return 0.0
        return (self.current_drawdown / self.starting_capital) * 100.0

    def _check_breach(self):
        """Check if drawdown limit has been breached."""
        if self.current_drawdown_pct >= self.max_drawdown_pct:
            self.drawdown_breached = True
            logger.critical(
                f"DRAWDOWN BREACH! Peak Equity={self.peak_equity:,.2f}, "
                f"Current Equity={self.current_equity:,.2f}, "
                f"Drawdown={self.current_drawdown:,.2f} ({self.current_drawdown_pct:.2f}% >= {self.max_drawdown_pct:.2f}%)"
            )

    def can_trade(self) -> tuple[bool, str]:
        """Check if overall capital drawdown allows trading."""
        if self.drawdown_breached:
            return False, f"Overall drawdown limit breached ({self.current_drawdown_pct:.1f}% >= {self.max_drawdown_pct}%)"
        return True, "OK"

    def should_force_close(self, daily_realised_pnl: float, unrealised_pnl: float,
                           max_daily_loss: float) -> tuple[bool, str]:
        """Intra-trade guard to check if daily loss limit would be breached."""
        total_pnl = daily_realised_pnl + unrealised_pnl
        if total_pnl <= -abs(max_daily_loss):
            return True, f"Intraday Daily Loss Limit hit: Rs. {total_pnl:,.2f} (limit: Rs. {max_daily_loss:,.2f})"
        return False, "OK"

    # -- Manual reset ----------------------------------------------------------

    def reset_breach(self):
        """Manually reset the drawdown breach flag. Called from API."""
        self.drawdown_breached = False
        logger.info("Drawdown breach flag manually reset")
        self._save()

    def reset_all(self, starting_capital: float = None):
        """Full reset - used when user wants to start fresh."""
        if starting_capital:
            self.starting_capital = starting_capital
        self.current_equity = self.starting_capital
        self.peak_equity = self.starting_capital
        self.drawdown_breached = False
        self.last_updated = _ist_today()
        self._save()
        logger.info(f"Capital tracker fully reset to {self.starting_capital:,.0f}")

    # -- State for API/frontend ------------------------------------------------

    def get_state(self) -> dict:
        return {
            "starting_capital": self.starting_capital,
            "peak_equity": self.peak_equity,
            "current_equity": round(self.current_equity, 2),
            "max_drawdown_pct": self.max_drawdown_pct,
            "drawdown_limit": round(self.drawdown_limit, 2),
            "current_drawdown": round(self.current_drawdown, 2),
            "current_drawdown_pct": round(self.current_drawdown_pct, 2),
            "drawdown_breached": self.drawdown_breached,
            "last_updated": self.last_updated,
        }


# -- Singleton -----------------------------------------------------------------
_tracker = CapitalTracker()


def get_capital_tracker() -> CapitalTracker:
    return _tracker
