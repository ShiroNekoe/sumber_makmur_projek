import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from app.domain.models import FeatureVector, PredictionResult, OpenPosition, ModelRegistry
from app.use_cases.auto_trade_executor import AutoTradeExecutor
from app.websocket.manager import manager as ws_manager


class TestAutoTradeExecutor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock repositories
        self.mock_position_repo = MagicMock()
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[])
        self.mock_position_repo.add_position = AsyncMock()

        self.mock_cooldown_repo = MagicMock()
        self.mock_cooldown_repo.get_cooldown = AsyncMock(return_value=None)
        self.mock_cooldown_repo.set_cooldown = AsyncMock()

        self.mock_model_registry_repo = MagicMock()
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

        self.executor = AutoTradeExecutor(
            position_repo=self.mock_position_repo,
            cooldown_repo=self.mock_cooldown_repo,
            model_registry_repo=self.mock_model_registry_repo
        )

        # Mock WebSocket broadcast
        self.original_broadcast = ws_manager.broadcast
        ws_manager.broadcast = AsyncMock()

        self.fv = FeatureVector(
            token_address="SafeTokenxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            wallet_source="Wha1eA11111111111111111111111111111111111",
            signature="sig_1",
            timestamp=datetime.now(timezone.utc),
            position_size_usd=150.0,
            token_age_minutes=100.0,
            liquidity_pool_depth=25000.0,
            slippage_actual=0.01,
            cluster_score=0.0,
            win_rate_30d=0.5,
            avg_holding_time_minutes=20.0,
            typical_trade_size_usd=500.0,
            past_exit_pattern_score=0.0,
            sol_usd_momentum=0.0,
            token_volume_liquidity_ratio=0.2,
            hour_of_day_utc=12
        )

        self.pred = PredictionResult(
            direction="BUY",
            confidence_score=0.85,
            target_price_estimate=0.50,
            token_address="SafeTokenxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            wallet_source="Wha1eA11111111111111111111111111111111111",
            signature="sig_1",
            timestamp=datetime.now(timezone.utc)
        )

    def tearDown(self):
        ws_manager.broadcast = self.original_broadcast

    async def test_successful_execution(self):
        open_pos = await self.executor.execute_trade(self.pred, self.fv)
        self.assertIsNotNone(open_pos)
        self.assertEqual(open_pos.state, "OPEN")
        self.assertEqual(open_pos.position_size_usd, 1000.0) # Sizing = (10000 * 1%) / 10% SL = $1000
        self.mock_position_repo.add_position.assert_called_once()
        self.mock_cooldown_repo.set_cooldown.assert_called_once()

    async def test_correlation_cap_blocked(self):
        # Mock settings to have custom cap of 2 positions
        from app.core.config import settings
        original_cap = settings.RISK_MAX_CONCURRENT_POSITIONS
        settings.RISK_MAX_CONCURRENT_POSITIONS = 2
        
        try:
            # Mock 2 open positions (cap reached)
            self.mock_position_repo.get_open_positions = AsyncMock(return_value=[
                MagicMock(spec=OpenPosition), MagicMock(spec=OpenPosition)
            ])
            open_pos = await self.executor.execute_trade(self.pred, self.fv)
            self.assertIsNone(open_pos)
            self.mock_position_repo.add_position.assert_not_called()

            # Verify WebSocket broadcast was called with formatted envelope
            ws_manager.broadcast.assert_called_once()
            called_args = ws_manager.broadcast.call_args[0][0]
            self.assertEqual(called_args["type"], "position_cap_reached")
            self.assertEqual(called_args["data"]["open_count"], 2)
            self.assertEqual(called_args["data"]["max_count"], 2)
            self.assertEqual(called_args["data"]["event"], "POSITION_CAP_REACHED")
        finally:
            settings.RISK_MAX_CONCURRENT_POSITIONS = original_cap

    async def test_correlation_cap_database_failure_failsafe(self):
        # Mock database query failure
        self.mock_position_repo.get_open_positions = AsyncMock(side_effect=Exception("SQLite connection timeout"))
        
        # Should return None (block trade) as fail-safe
        open_pos = await self.executor.execute_trade(self.pred, self.fv)
        self.assertIsNone(open_pos)
        self.mock_position_repo.add_position.assert_not_called()
