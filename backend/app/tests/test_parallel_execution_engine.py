import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from app.domain.models import OpenPosition
from app.execution.executor import ParallelExecutionEngine
from app.websocket.manager import manager as ws_manager


class TestParallelExecutionEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock repositories
        self.mock_position_repo = MagicMock()
        self.mock_position_repo.update_position = AsyncMock()

        self.mock_cooldown_repo = MagicMock()
        self.mock_cooldown_repo.delete_cooldown = AsyncMock()

        self.mock_model_registry_repo = MagicMock()
        self.mock_trade_history_repo = MagicMock()
        self.mock_trade_history_repo.add_closed_trade = AsyncMock()

        self.position = OpenPosition(
            position_id="pos_1",
            wallet_source="Wha1eA11111111111111111111111111111111111",
            token_address="SafeTokenxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            state="OPEN",
            entry_price=1.0,
            entry_ts=datetime.now(timezone.utc),
            sl_initial=0.90, # 10% distance
            risk_pct=0.01,
            position_size_usd=1000.0,
            trailing_active=False,
            trailing_level=None,
            peak_r_multiple=0.0,
            confidence_score=0.85,
            model_version="v0"
        )

        self.engine = ParallelExecutionEngine(
            position=self.position,
            position_repo=self.mock_position_repo,
            cooldown_repo=self.mock_cooldown_repo,
            model_registry_repo=self.mock_model_registry_repo,
            trade_history_repo=self.mock_trade_history_repo
        )

        # Mock WebSocket broadcast
        self.original_broadcast = ws_manager.broadcast
        ws_manager.broadcast = AsyncMock()

    def tearDown(self):
        ws_manager.broadcast = self.original_broadcast

    async def test_atomic_exit_logic(self):
        # Verify that execute_exit can only run once
        await self.engine.execute_exit("SL")
        self.assertTrue(self.engine.exited)
        self.assertEqual(self.position.state, "CLOSED")
        
        self.mock_trade_history_repo.add_closed_trade.assert_called_once()
        self.mock_cooldown_repo.delete_cooldown.assert_called_once()

        # Execute again, should not call repositories again
        await self.engine.execute_exit("trailing_tp")
        self.mock_trade_history_repo.add_closed_trade.assert_called_once()
