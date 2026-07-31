import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any, List

from app.core.config import settings
from app.domain.interfaces import IPositionRepository, ITradeHistoryRepository
from app.websocket.manager import manager as ws_manager

logger = logging.getLogger(__name__)


class PortfolioRiskGuard:
    """
    FASE 1: Portfolio Circuit Breaker & Risk Guard Service.
    Enforces daily and weekly portfolio loss limits without touching token selection criteria.
    Fails closed when daily/weekly loss thresholds are breached.
    """
    def __init__(
        self,
        position_repo: Optional[IPositionRepository] = None,
        trade_history_repo: Optional[ITradeHistoryRepository] = None,
        portfolio_service = None,
        db_session = None,
        pnl_calculator = None,
        token_info_service = None
    ):
        self.position_repo = position_repo
        self.trade_history_repo = trade_history_repo
        self.portfolio_service = portfolio_service
        self.db_session = db_session
        self.pnl_calculator = pnl_calculator
        self.token_info_service = token_info_service

    async def is_trading_allowed(self) -> Tuple[bool, str]:
        """
        Evaluates current daily and weekly realized + unrealized portfolio loss
        against starting period equity.
        
        Returns:
            (is_allowed: bool, reason: str)
        """
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

        max_daily_loss_pct = getattr(settings, "RISK_MAX_DAILY_LOSS_PCT", 0.05)
        max_weekly_loss_pct = getattr(settings, "RISK_MAX_WEEKLY_LOSS_PCT", 0.15)

        # 1. Compute realized PnL since start of day and week
        daily_realized_pnl = 0.0
        weekly_realized_pnl = 0.0

        if self.trade_history_repo:
            try:
                closed_trades = await self.trade_history_repo.get_closed_trades(limit=500, exclude_bootstrap=True)
                for t in closed_trades:
                    if getattr(t, "is_paper_trade", False):
                        continue
                    
                    exit_ts = getattr(t, "exit_ts", None)
                    if not exit_ts:
                        continue
                    if exit_ts.tzinfo is None:
                        exit_ts = exit_ts.replace(tzinfo=timezone.utc)

                    pnl_usd = (getattr(t, "position_size_usd", 0.0) or 0.0) * (getattr(t, "pnl_pct_actual", 0.0) or 0.0)
                    
                    if exit_ts >= start_of_day:
                        daily_realized_pnl += pnl_usd
                    if exit_ts >= start_of_week:
                        weekly_realized_pnl += pnl_usd
            except Exception as e:
                logger.error(f"[RISK GUARD] Error querying closed trades for PnL calculation: {e}")

        # 2. Compute unrealized PnL from open positions
        unrealized_pnl = 0.0
        if self.position_repo:
            try:
                open_positions = await self.position_repo.get_open_positions()
                for pos in open_positions:
                    unrealized_pnl += getattr(pos, "unrealized_pnl_usd", 0.0) or 0.0
            except Exception as e:
                logger.error(f"[RISK GUARD] Error querying open positions for unrealized PnL: {e}")

        # 3. Obtain baseline starting equity for day and week
        day_baseline_equity = await self._get_baseline_equity(start_of_day)
        week_baseline_equity = await self._get_baseline_equity(start_of_week)

        # Prevent division by zero
        if day_baseline_equity <= 0.0:
            day_baseline_equity = 1000.0
        if week_baseline_equity <= 0.0:
            week_baseline_equity = 1000.0

        # 4. Calculate total period PnL and loss ratios
        daily_total_pnl = daily_realized_pnl + unrealized_pnl
        weekly_total_pnl = weekly_realized_pnl + unrealized_pnl

        daily_loss_pct = daily_total_pnl / day_baseline_equity
        weekly_loss_pct = weekly_total_pnl / week_baseline_equity

        # 5. Evaluate Daily Circuit Breaker
        if daily_total_pnl < 0 and abs(daily_loss_pct) >= max_daily_loss_pct:
            reason = (
                f"Circuit Breaker Triggered: Daily loss ({abs(daily_loss_pct):.2%}) "
                f"exceeds maximum allowed limit of {max_daily_loss_pct:.2%}. "
                f"Trading suspended until {getattr(settings, 'RISK_CIRCUIT_BREAKER_RESET_UTC', '00:00')} UTC."
            )
            logger.critical(f"[RISK GUARD] [CIRCUIT BREAKER] {reason}")
            
            await self._broadcast_circuit_breaker_event(
                period="daily",
                loss_pct=abs(daily_loss_pct),
                limit_pct=max_daily_loss_pct,
                reason=reason
            )
            return False, reason

        # 6. Evaluate Weekly Circuit Breaker
        if weekly_total_pnl < 0 and abs(weekly_loss_pct) >= max_weekly_loss_pct:
            reason = (
                f"Circuit Breaker Triggered: Weekly loss ({abs(weekly_loss_pct):.2%}) "
                f"exceeds maximum allowed limit of {max_weekly_loss_pct:.2%}."
            )
            logger.critical(f"[RISK GUARD] [CIRCUIT BREAKER] {reason}")

            await self._broadcast_circuit_breaker_event(
                period="weekly",
                loss_pct=abs(weekly_loss_pct),
                limit_pct=max_weekly_loss_pct,
                reason=reason
            )
            return False, reason

        return True, "Trading allowed."

    async def _get_baseline_equity(self, cutoff_time: datetime) -> float:
        """
        Helper method to retrieve starting equity snapshot for a period cutoff.
        """
        db = self.db_session or (getattr(self.pnl_calculator, "db", None) if self.pnl_calculator else None)
        if db:
            try:
                from app.infrastructure.database.models import EquitySnapshotORM
                snapshot = db.query(EquitySnapshotORM).filter(
                    EquitySnapshotORM.timestamp <= cutoff_time
                ).order_by(EquitySnapshotORM.timestamp.desc()).first()

                if snapshot and snapshot.portfolio_value_usd > 0:
                    return snapshot.portfolio_value_usd
            except Exception as e:
                logger.debug(f"[RISK GUARD] Could not fetch baseline equity snapshot: {e}")

        return 1000.0  # Safe default baseline equity

    async def _broadcast_circuit_breaker_event(
        self,
        period: str,
        loss_pct: float,
        limit_pct: float,
        reason: str
    ) -> None:
        try:
            event_payload = {
                "type": "circuit_breaker_triggered",
                "data": {
                    "event": "CIRCUIT_BREAKER_TRIGGERED",
                    "period": period,
                    "loss_pct": loss_pct,
                    "limit_pct": limit_pct,
                    "reason": reason,
                    "reset_utc": getattr(settings, "RISK_CIRCUIT_BREAKER_RESET_UTC", "00:00"),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            }
            await ws_manager.broadcast(event_payload)
        except Exception as e:
            logger.error(f"[RISK GUARD] Failed to broadcast circuit breaker WS event: {e}")
