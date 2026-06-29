import asyncio
import unittest
import os
import shutil
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from app.use_cases.crash_recovery import CrashRecoveryService
from app.domain.models import OpenPosition, ClosedTrade, ModelRegistry
from app.websocket.manager import manager as ws_manager
from app.core.config import settings


class TestCrashRecoveryService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_position_repo = MagicMock()
        self.mock_cooldown_repo = MagicMock()
        self.mock_model_registry_repo = MagicMock()
        self.mock_trade_history_repo = MagicMock()
        self.mock_token_info_service = MagicMock()
        self.mock_retrain_scheduler = MagicMock()

        self.mock_cooldown_repo.delete_cooldown = AsyncMock()
        self.mock_position_repo.update_position = AsyncMock()
        self.mock_position_repo.delete_position = AsyncMock()
        self.mock_trade_history_repo.add_closed_trade = AsyncMock()
        self.mock_retrain_scheduler.retrain_model_if_needed = AsyncMock()

        # Default model registry returning active model trained just now
        self.mock_model_registry_repo.get_active_model = AsyncMock(
            return_value=ModelRegistry(
                model_version="v0",
                trained_at=datetime.now(timezone.utc),
                training_sample_count=100,
                validation_accuracy=0.80,
                expectancy_r=0.25,
                is_active=True
            )
        )

        self.service = CrashRecoveryService(
            position_repo=self.mock_position_repo,
            cooldown_repo=self.mock_cooldown_repo,
            model_registry_repo=self.mock_model_registry_repo,
            trade_history_repo=self.mock_trade_history_repo,
            token_info_service=self.mock_token_info_service,
            retrain_scheduler=self.mock_retrain_scheduler
        )

        self.original_broadcast = ws_manager.broadcast
        ws_manager.broadcast = AsyncMock()

    async def asyncTearDown(self):
        ws_manager.broadcast = self.original_broadcast

    async def test_recovery_no_open_positions(self):
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[])
        await self.service.run_recovery()
        self.mock_retrain_scheduler.retrain_model_if_needed.assert_not_called()

    async def test_recovery_triggers_retrain_catchup(self):
        # Set last trained time to 25 hours ago
        self.mock_model_registry_repo.get_active_model = AsyncMock(
            return_value=ModelRegistry(
                model_version="v0",
                trained_at=datetime.now(timezone.utc) - timedelta(hours=25),
                training_sample_count=100,
                validation_accuracy=0.80,
                expectancy_r=0.25,
                is_active=True
            )
        )
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[])
        
        await self.service.run_recovery()
        self.mock_retrain_scheduler.retrain_model_if_needed.assert_called_once_with(force=True)

    async def test_recovery_position_safe_reactivates_protection(self):
        # Entry price 1.0, SL 0.9. Current price 1.2 -> safe!
        pos = OpenPosition(
            position_id="pos_safe",
            wallet_source="whale_addr",
            token_address="token_safe",
            state="OPEN",
            entry_price=1.0,
            entry_ts=datetime.now(timezone.utc),
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=100.0,
            confidence_score=0.8,
            model_version="v0"
        )
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[pos])
        
        self.mock_token_info_service.get_token_info = AsyncMock(return_value={
            "price_usd": 1.2,
            "token_symbol": "SAFE"
        })

        with patch("app.execution.executor.ParallelExecutionEngine.start_monitoring", AsyncMock()) as mock_start:
            await self.service.run_recovery()
            
            # Position should not be deleted or exited
            self.mock_position_repo.delete_position.assert_not_called()
            # Protection loops should be re-activated
            mock_start.assert_called_once()

    async def test_recovery_position_insolvent_exits_immediately(self):
        # Entry price 1.0, SL 0.9. Current price 0.85 -> Below SL!
        pos = OpenPosition(
            position_id="pos_unsafe",
            wallet_source="whale_addr",
            token_address="token_unsafe",
            state="OPEN",
            entry_price=1.0,
            entry_ts=datetime.now(timezone.utc),
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=100.0,
            confidence_score=0.8,
            model_version="v0"
        )
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[pos])
        
        self.mock_token_info_service.get_token_info = AsyncMock(return_value={
            "price_usd": 0.85,
            "token_symbol": "UNSAFE"
        })

        await self.service.run_recovery()
        
        # Position must be updated to EXITING, deleted, and closed trade added
        self.mock_position_repo.update_position.assert_called_once_with(pos)
        self.mock_position_repo.delete_position.assert_called_once_with("pos_unsafe")
        self.mock_trade_history_repo.add_closed_trade.assert_called_once()
        self.mock_cooldown_repo.delete_cooldown.assert_called_once_with("whale_addr", "token_unsafe")
        
        # Verify websocket broadcast closed event
        ws_manager.broadcast.assert_called_once()
        called_args = ws_manager.broadcast.call_args[0][0]
        self.assertEqual(called_args["type"], "trade_closed")
        self.assertEqual(called_args["data"]["exit_reason"], "SL_RECOVERY")

    async def test_recovery_price_fetch_failure_marks_inconsistent(self):
        pos = OpenPosition(
            position_id="pos_err",
            wallet_source="whale_addr",
            token_address="token_err",
            state="OPEN",
            entry_price=1.0,
            entry_ts=datetime.now(timezone.utc),
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=100.0,
            confidence_score=0.8,
            model_version="v0"
        )
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[pos])
        
        # Simulating API failure (exception)
        self.mock_token_info_service.get_token_info = AsyncMock(side_effect=Exception("DexScreener API Timeout"))

        await self.service.run_recovery()
        
        # Position state updated to RECOVERY_FAILED
        self.mock_position_repo.update_position.assert_called_once_with(pos)
        self.assertEqual(pos.state, "RECOVERY_FAILED")
        
        # Closed trade not generated
        self.mock_trade_history_repo.add_closed_trade.assert_not_called()
        self.mock_position_repo.delete_position.assert_not_called()

        # Verify critical websocket alert
        ws_manager.broadcast.assert_called_once()
        called_args = ws_manager.broadcast.call_args[0][0]
        self.assertEqual(called_args["type"], "system_alert")
        self.assertEqual(called_args["data"]["alert_type"], "recovery_failed")
