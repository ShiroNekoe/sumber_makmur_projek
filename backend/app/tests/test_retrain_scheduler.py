import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta

from app.domain.models import ClosedTrade, ModelRegistry
from app.use_cases.retrain_scheduler import RetrainScheduler
from app.websocket.manager import manager as ws_manager


class TestRetrainScheduler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock repositories & service
        self.mock_trade_history_repo = MagicMock()
        self.mock_trade_history_repo.get_closed_trades = AsyncMock(return_value=[])
        
        self.mock_model_registry_repo = MagicMock()
        self.mock_model_registry_repo.get_active_model = AsyncMock(return_value=None)
        self.mock_model_registry_repo.add_model_version = AsyncMock()
        self.mock_model_registry_repo.update_model_version = AsyncMock()

        self.mock_inference_engine = MagicMock()

        self.scheduler = RetrainScheduler(
            trade_history_repo=self.mock_trade_history_repo,
            model_registry_repo=self.mock_model_registry_repo,
            inference_engine=self.mock_inference_engine
        )

        self.original_broadcast = ws_manager.broadcast
        ws_manager.broadcast = AsyncMock()

    def tearDown(self):
        ws_manager.broadcast = self.original_broadcast

    async def test_retrain_skipped_due_to_insufficient_data(self):
        # 0 trades in repo -> should skip retraining
        success = await self.scheduler.retrain_model_if_needed(force=True)
        self.assertFalse(success)
        self.mock_model_registry_repo.add_model_version.assert_not_called()

    async def test_retrain_success_with_adequate_data(self):
        # Create 120 mock closed trades
        mock_trades = []
        for i in range(120):
            # Class 1: BUY_BENAR, Class 2: SALAH, Class 0: HOLD
            if i % 3 == 0:
                label = "BUY_BENAR"
                r_mult = 3.5
            elif i % 3 == 1:
                label = "SALAH"
                r_mult = -1.2
            else:
                label = "HOLD"
                r_mult = 0.5

            mock_trades.append(
                ClosedTrade(
                    trade_id=f"tr_{i}",
                    wallet_source="Wha1eA11111111111111111111111111111111111",
                    token_address=f"token_{i}xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                    token_symbol="SIM",
                    signal_ts=datetime.now(timezone.utc) - timedelta(days=10),
                    entry_ts=datetime.now(timezone.utc) - timedelta(days=10),
                    exit_ts=datetime.now(timezone.utc) - timedelta(days=10),
                    direction="BUY",
                    confidence_score=0.80,
                    safety_check_passed=True,
                    entry_price=1.0,
                    exit_price=1.0 + r_mult * 0.01,
                    position_size_usd=100.0,
                    risk_pct=0.01,
                    pnl_pct_actual=r_mult * 0.01,
                    r_multiple=r_mult,
                    label=label,
                    holding_time_minutes=20,
                    exit_reason="manual",
                    is_paper_trade=True,
                    is_bootstrap=False,
                    model_version="v0"
                )
            )

        self.mock_trade_history_repo.get_closed_trades = AsyncMock(return_value=mock_trades)
        
        # Test retrain success
        success = await self.scheduler.retrain_model_if_needed(force=True)
        self.assertTrue(success)
        self.mock_model_registry_repo.add_model_version.assert_called_once()
