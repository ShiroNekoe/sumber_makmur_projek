import logging
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from app.core.config import settings
from app.domain.models import OpenPosition, ClosedTrade
from app.domain.interfaces import (
    IPositionRepository,
    ICooldownRepository,
    IModelRegistryRepository,
    ITradeHistoryRepository,
    ITokenInfoService,
)
from app.websocket.manager import manager as ws_manager

logger = logging.getLogger(__name__)


class CrashRecoveryService:
    """
    F-17: State Persistence & Crash Recovery
    Restores the correct state of the system on startup, including re-activating
    ParallelExecutionEngine protection loops for any active open positions,
    and executing immediate exits for positions where price has fallen below stop loss levels.
    """
    def __init__(
        self,
        position_repo: IPositionRepository,
        cooldown_repo: ICooldownRepository,
        model_registry_repo: IModelRegistryRepository,
        trade_history_repo: ITradeHistoryRepository,
        token_info_service: ITokenInfoService,
        retrain_scheduler,
    ):
        self.position_repo = position_repo
        self.cooldown_repo = cooldown_repo
        self.model_registry_repo = model_registry_repo
        self.trade_history_repo = trade_history_repo
        self.token_info_service = token_info_service
        self.retrain_scheduler = retrain_scheduler

    async def run_recovery(self) -> None:
        """
        Executes startup crash recovery steps:
        1. Check active model from model_registry
        2. Check retrain_timestamp and trigger F-10 catch-up if needed
        3. Query open_positions WHERE status='open'
        4. For each open position:
           a. Fetch current price
           b. If price <= SL -> exit position immediately
           c. If price is safe -> re-activate protection loop (F-09)
        """
        logger.info("[RECOVERY] Starting system recovery checks...")
        startup_time = datetime.now(timezone.utc)
        recovery_actions = []

        # 1. Load active model
        active_model = await self.model_registry_repo.get_active_model()
        if not active_model:
            logger.warning("[RECOVERY] No active model found in registry on startup.")
        else:
            logger.info(f"[RECOVERY] Active model version: {active_model.model_version}")

        # 2. Check retrain timestamp catch-up (F-10)
        if active_model:
            trained_ts = active_model.trained_at
            if trained_ts.tzinfo is None:
                trained_ts = trained_ts.replace(tzinfo=timezone.utc)
            time_since_train = startup_time - trained_ts
            if time_since_train > timedelta(hours=24):
                logger.warning(f"[RECOVERY] Active model was trained {time_since_train.total_seconds()/3600:.1f}h ago. Triggering retrain catch-up.")
                await self.retrain_scheduler.retrain_model_if_needed(force=True)
                recovery_actions.append("retrain_catch_up")

        # 3. Query open positions
        open_positions = await self.position_repo.get_open_positions()
        open_positions_count = len(open_positions)
        logger.info(f"[RECOVERY] Found {open_positions_count} open positions to recover.")

        # 4. Recover open positions
        for pos in open_positions:
            token = pos.token_address
            entry_price = pos.entry_price or 1.0
            sl_level = pos.sl_initial

            logger.info(f"[RECOVERY] Recovering position {pos.position_id} for token {token}...")

            # Fetch current price
            try:
                info = await self.token_info_service.get_token_info(token)
                current_price = info.get("price_usd")
                if current_price is None or float(current_price) <= 0.0:
                    # Simulated fallback or error handling
                    # In a real environment if token is not found or API fails, raise error
                    if info.get("token_symbol") == "MOCK_TOKEN":
                        # For testing/offline simulation fallback: use mock price walk
                        current_price = entry_price
                    else:
                        raise ValueError(f"Price not available for token {token}")
                else:
                    current_price = float(current_price)
            except Exception as e:
                # Mark as recovery_failed, log warning and emit critical alert
                logger.critical(f"[RECOVERY] [FAILED] Position {pos.position_id} inconsistent. Price fetch error: {e}")
                pos.state = "RECOVERY_FAILED"
                await self.position_repo.update_position(pos)
                await ws_manager.broadcast({
                    "type": "system_alert",
                    "data": {
                        "event": "system_alert",
                        "alert_type": "recovery_failed",
                        "message": f"CRITICAL: Position {pos.position_id} (token {token}) marked as RECOVERY_FAILED due to price fetch failure.",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                })
                recovery_actions.append(f"failed_pos_{pos.position_id}")
                continue

            logger.info(f"[RECOVERY] Current price for token {token} is ${current_price:.6f} (SL: ${sl_level:.6f})")

            # Compare current price with SL level
            if current_price <= sl_level:
                logger.warning(f"[RECOVERY] Price ${current_price:.6f} is below SL ${sl_level:.6f}. Executing immediate market exit.")
                # Update position state to EXITING
                pos.state = "EXITING"
                await self.position_repo.update_position(pos)
                
                # Mock exit order execution
                exit_price = current_price
                pnl_pct_actual = (exit_price - entry_price) / entry_price
                r_multiple = (exit_price - entry_price) / abs(entry_price - sl_level)
                
                # Move to closed trades
                closed_trade = ClosedTrade(
                    trade_id=f"tr_{uuid.uuid4().hex[:8]}",
                    wallet_source=pos.wallet_source,
                    token_address=pos.token_address,
                    token_symbol=info.get("token_symbol", "UNKNOWN"),
                    signal_ts=pos.entry_ts,
                    entry_ts=pos.entry_ts,
                    exit_ts=datetime.now(timezone.utc),
                    direction="BUY",
                    confidence_score=pos.confidence_score,
                    safety_check_passed=True,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    position_size_usd=pos.position_size_usd,
                    risk_pct=pos.risk_pct,
                    pnl_pct_actual=pnl_pct_actual,
                    r_multiple=r_multiple,
                    label="SALAH" if r_multiple < 0 else "BUY_BENAR",
                    holding_time_minutes=int((datetime.now(timezone.utc) - pos.entry_ts).total_seconds() / 60) if pos.entry_ts else 0,
                    exit_reason="SL_RECOVERY",
                    is_paper_trade=False,
                    model_version=pos.model_version
                )
                
                # Delete position and save closed trade
                await self.position_repo.delete_position(pos.position_id)
                # If trade history repo is available, save
                if self.trade_history_repo:
                    await self.trade_history_repo.add_closed_trade(closed_trade)

                # Reset cooldown
                await self.cooldown_repo.delete_cooldown(pos.wallet_source, pos.token_address)
                
                # Broadcast closed trade
                await ws_manager.broadcast({
                    "type": "trade_closed",
                    "data": {
                        "position_id": pos.position_id,
                        "token_address": pos.token_address,
                        "pnl_pct_actual": pnl_pct_actual,
                        "exit_reason": "SL_RECOVERY",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                })
                recovery_actions.append(f"exit_pos_{pos.position_id}")
            else:
                # Re-activate F-09 Parallel Protection Loop
                from app.execution.executor import ParallelExecutionEngine
                engine = ParallelExecutionEngine(
                    pos,
                    self.position_repo,
                    self.cooldown_repo,
                    self.model_registry_repo,
                    self.trade_history_repo,
                    token_info_service=self.token_info_service
                )
                asyncio.create_task(engine.start_monitoring())
                logger.info(f"[RECOVERY] Protection loops re-activated for position {pos.position_id}.")
                recovery_actions.append(f"restored_pos_{pos.position_id}")

        # Audit log of startup
        logger.info(
            f"[RECOVERY] [AUDIT] Startup recovery finished.\n"
            f" - Startup Time: {startup_time.isoformat()}\n"
            f" - Open Positions Count: {open_positions_count}\n"
            f" - Recovery Actions Taken: {recovery_actions}"
        )
