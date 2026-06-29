import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import CooldownState, OpenPosition
from app.use_cases.trigger_engine import TriggerEngine


class TestTriggerEngineCooldown(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cooldown_repo = MagicMock()
        self.cooldown_repo.get_cooldown = AsyncMock()
        self.cooldown_repo.delete_cooldown = AsyncMock()
        self.cooldown_repo.set_cooldown = AsyncMock()

        self.token_info_service = MagicMock()
        self.token_info_service.get_token_info = AsyncMock()

        self.ml_pipeline = MagicMock()
        self.ml_pipeline.analyze_token = AsyncMock()

        self.position_repo = MagicMock()
        self.position_repo.get_position = AsyncMock()

        self.engine = TriggerEngine(
            cooldown_repo=self.cooldown_repo,
            token_info_service=self.token_info_service,
            ml_pipeline=self.ml_pipeline,
            position_repo=self.position_repo
        )

    async def test_no_cooldown_returns_false(self):
        self.cooldown_repo.get_cooldown.return_value = None
        
        is_cooldown = await self.engine._check_cooldown("whale_addr", "token_mint")
        self.assertFalse(is_cooldown)

    async def test_pending_cooldown_active_during_window(self):
        # Setup: Cooldown created 10 seconds ago, no position linked yet
        self.cooldown_repo.get_cooldown.return_value = CooldownState(
            wallet_address="whale_addr",
            token_address="token_mint",
            last_trigger_ts=datetime.now(timezone.utc) - timedelta(seconds=10),
            active_position_id=None
        )

        is_cooldown = await self.engine._check_cooldown("whale_addr", "token_mint")
        self.assertTrue(is_cooldown)

    async def test_pending_cooldown_expired_cleans_up(self):
        # Setup: Cooldown created 6 minutes ago, no position linked
        self.cooldown_repo.get_cooldown.return_value = CooldownState(
            wallet_address="whale_addr",
            token_address="token_mint",
            last_trigger_ts=datetime.now(timezone.utc) - timedelta(minutes=6),
            active_position_id=None
        )

        is_cooldown = await self.engine._check_cooldown("whale_addr", "token_mint")
        self.assertFalse(is_cooldown)
        self.cooldown_repo.delete_cooldown.assert_called_once_with("whale_addr", "token_mint")

    async def test_linked_position_active_cooldown(self):
        # Setup: Cooldown linked to pos_123
        self.cooldown_repo.get_cooldown.return_value = CooldownState(
            wallet_address="whale_addr",
            token_address="token_mint",
            last_trigger_ts=datetime.now(timezone.utc),
            active_position_id="pos_123"
        )
        # Position is OPEN
        self.position_repo.get_position.return_value = OpenPosition(
            position_id="pos_123",
            wallet_source="whale_addr",
            token_address="token_mint",
            state="OPEN",
            sl_initial=0.90,
            risk_pct=0.01,
            position_size_usd=1000.0,
            confidence_score=0.85,
            model_version="v0"
        )

        is_cooldown = await self.engine._check_cooldown("whale_addr", "token_mint")
        self.assertTrue(is_cooldown)

    async def test_linked_position_closed_cooldown_resets(self):
        # Setup: Cooldown linked to pos_123
        self.cooldown_repo.get_cooldown.return_value = CooldownState(
            wallet_address="whale_addr",
            token_address="token_mint",
            last_trigger_ts=datetime.now(timezone.utc),
            active_position_id="pos_123"
        )
        # Position is CLOSED
        self.position_repo.get_position.return_value = OpenPosition(
            position_id="pos_123",
            wallet_source="whale_addr",
            token_address="token_mint",
            state="CLOSED",
            sl_initial=0.90,
            risk_pct=0.01,
            position_size_usd=1000.0,
            confidence_score=0.85,
            model_version="v0"
        )

        is_cooldown = await self.engine._check_cooldown("whale_addr", "token_mint")
        self.assertFalse(is_cooldown)
        self.cooldown_repo.delete_cooldown.assert_called_once_with("whale_addr", "token_mint")
