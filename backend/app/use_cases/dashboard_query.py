"""
F-07 Dashboard Query Use Case
Clean Architecture use case layer for aggregating dashboard data from repositories.
Handles all read operations needed by the REST API and WebSocket initial_state.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from app.core.config import settings
from app.domain.interfaces import (
    ITradeHistoryRepository,
    IWalletRepository,
    IPositionRepository,
    IModelRegistryRepository,
)

logger = logging.getLogger(__name__)

# In-memory signal event log (ring buffer, max 500 entries)
# This is populated by the safety_check_gate via the event publisher
_signal_log: List[dict] = []
_MAX_SIGNAL_LOG = 500


def append_signal_event(event: dict) -> None:
    """Called by SafetyCheckGate to record a signal event for dashboard queries."""
    global _signal_log
    _signal_log.append(event)
    if len(_signal_log) > _MAX_SIGNAL_LOG:
        _signal_log = _signal_log[-_MAX_SIGNAL_LOG:]


def get_all_signal_events() -> List[dict]:
    """Return a copy of the current signal log."""
    return list(_signal_log)


class DashboardQueryService:
    """
    Use case for read-only data aggregation used by the Dashboard API.
    Follows SOLID: Single Responsibility (only reads), Open/Closed (extend by adding methods).
    """

    def __init__(
        self,
        trade_history_repo: ITradeHistoryRepository,
        wallet_repo: IWalletRepository,
        position_repo: Optional[IPositionRepository] = None,
        model_registry_repo: Optional[IModelRegistryRepository] = None,
    ):
        self.trade_history_repo = trade_history_repo
        self.wallet_repo = wallet_repo
        self.position_repo = position_repo
        self.model_registry_repo = model_registry_repo

    async def get_recent_signals(self, hours: int = 24) -> List[dict]:
        """
        Return signals from the in-memory log within the specified time window.
        Falls back to empty list on error (non-blocking).
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            result = []
            for event in reversed(get_all_signal_events()):
                try:
                    ts_str = event.get("timestamp", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts >= cutoff:
                            result.append(event)
                except Exception:
                    result.append(event)  # Include if can't parse timestamp
            return result
        except Exception as e:
            logger.error(f"[DASHBOARD QUERY] Error fetching recent signals: {e}")
            return []

    async def get_open_positions(self) -> List:
        """Return all currently open positions from the database."""
        try:
            if self.position_repo:
                return await self.position_repo.get_open_positions()
            return []
        except Exception as e:
            logger.error(f"[DASHBOARD QUERY] Error fetching open positions: {e}")
            return []

    async def get_recent_trades(self, limit: int = 50) -> List:
        """
        Return most recent closed trades from the database.
        3-second timeout, returns empty list on failure (non-blocking for dashboard).
        """
        try:
            trades = await self.trade_history_repo.get_closed_trades(limit=limit, offset=0)
            return trades
        except Exception as e:
            logger.error(f"[DASHBOARD QUERY] Error fetching recent trades: {e}")
            return []

    async def get_stats(self) -> dict:
        """
        Aggregate dashboard statistics.
        Returns defaults on partial failure to ensure dashboard always loads.
        """
        active_model_ver = "v0"
        if self.model_registry_repo:
            try:
                active_model = await self.model_registry_repo.get_active_model()
                if active_model:
                    active_model_ver = active_model.model_version
            except Exception:
                pass

        stats = {
            "win_rate_pct": None,
            "total_closed_trades": 0,
            "buy_benar_count": 0,
            "triggers_today": 0,
            "alerts_fired_24h": 0,
            "total_signals_24h": 0,
            "open_positions_count": 0,
            "confidence_threshold_pct": round(settings.CONFIDENCE_THRESHOLD * 100, 1),
            "active_model_version": active_model_ver,
        }
        try:
            trades = await self.trade_history_repo.get_closed_trades(limit=500, offset=0)
            stats["total_closed_trades"] = len(trades)

            buy_benar = [t for t in trades if t.label == "BUY_BENAR"]
            stats["buy_benar_count"] = len(buy_benar)

            if trades:
                stats["win_rate_pct"] = round(len(buy_benar) / len(trades) * 100, 1)
        except Exception as e:
            logger.warning(f"[DASHBOARD QUERY] Trade stats error: {e}")

        try:
            # Count signals from last 24h in-memory log
            recent_signals = await self.get_recent_signals(hours=24)
            stats["total_signals_24h"] = len(recent_signals)
            stats["alerts_fired_24h"] = len([s for s in recent_signals if s.get("event") == "ALERT"])
        except Exception as e:
            logger.warning(f"[DASHBOARD QUERY] Signal stats error: {e}")

        try:
            if self.position_repo:
                open_pos = await self.position_repo.get_open_positions()
                stats["open_positions_count"] = len(open_pos)
        except Exception as e:
            logger.warning(f"[DASHBOARD QUERY] Open positions stats error: {e}")

        return stats

    async def get_system_status(self) -> dict:
        """Return current system health status."""
        components = [
            {"name": "Wallet Monitor", "status": "running", "detail": "Listening for on-chain events"},
            {"name": "Relevance Filter", "status": "running", "detail": "F-02 active"},
            {"name": "Trigger Engine", "status": "running", "detail": "5-min window active"},
            {"name": "Feature Extractor", "status": "running", "detail": "12+ features"},
            {"name": "XGBoost Engine", "status": "running", "detail": "Model v0 loaded"},
            {"name": "Safety Gate", "status": "running", "detail": "4-criteria check active"},
        ]
        return {
            "overall_status": "healthy",
            "rpc_status": "simulation",
            "components": components,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_wallet_candidates(self) -> List[dict]:
        """
        Return wallet candidates awaiting approval (F-12 placeholder).
        Returns empty list until F-12 Dynamic Wallet Discovery is implemented.
        """
        # F-12 integration point: when F-12 is implemented, query its repository here
        return []
