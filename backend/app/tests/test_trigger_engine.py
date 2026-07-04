import unittest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
import asyncio

from app.use_cases.trigger_engine import TriggerEngine
from app.domain.models import CooldownState
from app.core.config import settings


class TestTriggerEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from app.blockchain.monitor import SolanaWebSocketMonitor
        SolanaWebSocketMonitor.degraded_mode = False
        
        # Mock repositories & services
        self.mock_cooldown_repo = MagicMock()
        self.mock_cooldown_repo.get_cooldown = AsyncMock(return_value=None)
        self.mock_cooldown_repo.set_cooldown = AsyncMock()
        self.mock_cooldown_repo.delete_cooldown = AsyncMock()
        
        self.mock_token_info_service = MagicMock()
        # Default valid token info
        self.mock_token_info_service.get_token_info = AsyncMock(return_value={
            "age_minutes": 120.0,
            "liquidity_usd": 15000.0,
            "token_symbol": "TEST_COIN"
        })
        
        self.mock_ml_pipeline = MagicMock()
        self.mock_ml_pipeline.analyze_token = AsyncMock()
        
        self.engine = TriggerEngine(
            cooldown_repo=self.mock_cooldown_repo,
            token_info_service=self.mock_token_info_service,
            ml_pipeline=self.mock_ml_pipeline
        )

        # Force some settings
        settings.TRIGGER_WINDOW_MINUTES = 5
        settings.MIN_TOKEN_AGE_MINUTES = 60
        settings.MIN_LIQUIDITY_USD = 5000.0
        settings.COOLDOWN_SECONDS = 3600

    async def test_hard_filter_token_age(self):
        # Set token info as too new (10 minutes old)
        self.mock_token_info_service.get_token_info.return_value = {
            "age_minutes": 10.0,
            "liquidity_usd": 15000.0,
            "token_symbol": "NEW_COIN"
        }
        
        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "token_mint": "NewCoinAddressxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "signature": "sig_too_new",
            "timestamp_utc": datetime.now(timezone.utc)
        }
        
        await self.engine.trigger_event(event)
        
        self.mock_ml_pipeline.analyze_token.assert_not_called()
        self.mock_cooldown_repo.set_cooldown.assert_not_called()

    async def test_hard_filter_liquidity(self):
        # Set low liquidity pool depth ($1,500 USD)
        self.mock_token_info_service.get_token_info.return_value = {
            "age_minutes": 120.0,
            "liquidity_usd": 1500.0,
            "token_symbol": "LOW_LIQ_COIN"
        }
        
        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "token_mint": "LowLiqCoinAddressxxxxxxxxxxxxxxxxxxxxxxxx",
            "signature": "sig_low_liq",
            "timestamp_utc": datetime.now(timezone.utc)
        }
        
        await self.engine.trigger_event(event)
        
        self.mock_ml_pipeline.analyze_token.assert_not_called()
        self.mock_cooldown_repo.set_cooldown.assert_not_called()

    async def test_cooldown_active(self):
        # Setup cooldown repository to return an active cooldown
        self.mock_cooldown_repo.get_cooldown.return_value = CooldownState(
            wallet_address="Wha1eA11111111111111111111111111111111111",
            token_address="SomeCoinAddressxxxxxxxxxxxxxxxxxxxxxxxxx",
            last_trigger_ts=datetime.now(timezone.utc) - timedelta(minutes=2), # 2m ago (within 5m pending window)
            active_position_id=None
        )

        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "token_mint": "SomeCoinAddressxxxxxxxxxxxxxxxxxxxxxxxxx",
            "signature": "sig_cooldown",
            "timestamp_utc": datetime.now(timezone.utc)
        }
        
        await self.engine.trigger_event(event)
        
        self.mock_ml_pipeline.analyze_token.assert_not_called()
        self.mock_cooldown_repo.set_cooldown.assert_not_called()

    async def test_trigger_or_mode(self):
        settings.TRIGGER_MODE = "OR"
        
        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "token_mint": "TokenAddressxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "signature": "sig_or_mode",
            "timestamp_utc": datetime.now(timezone.utc)
        }
        
        await self.engine.trigger_event(event)
        
        # In OR mode, single event triggers analysis with confidence_boost=False
        self.mock_ml_pipeline.analyze_token.assert_called_with(
            token_address="TokenAddressxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            wallet_source="Wha1eA11111111111111111111111111111111111",
            confidence_boost=False,
            signature="sig_or_mode",
            timestamp=event["timestamp_utc"]
        )
        self.mock_cooldown_repo.set_cooldown.assert_called_once()

    async def test_trigger_and_mode(self):
        settings.TRIGGER_MODE = "AND"
        token = "TokenAddressANDxxxxxxxxxxxxxxxxxxxxxxxxx"
        
        # 1. First event from Whale A -> should NOT trigger yet
        event_a = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "token_mint": token,
            "signature": "sig_and_1",
            "timestamp_utc": datetime.now(timezone.utc)
        }
        await self.engine.trigger_event(event_a)
        self.mock_ml_pipeline.analyze_token.assert_not_called()
        
        # 2. Second event from Whale B on same token within 5m -> should trigger with boost!
        event_b = {
            "wallet_address": "Wha1eB22222222222222222222222222222222222",
            "token_mint": token,
            "signature": "sig_and_2",
            "timestamp_utc": datetime.now(timezone.utc)
        }
        await self.engine.trigger_event(event_b)
        self.mock_ml_pipeline.analyze_token.assert_called_with(
            token_address=token,
            wallet_source="Wha1eB22222222222222222222222222222222222",
            confidence_boost=True,
            signature="sig_and_2",
            timestamp=event_b["timestamp_utc"]
        )


    async def test_trigger_and_mode_timeout(self):
        settings.TRIGGER_MODE = "AND"
        token = "TokenAddressTimeoutxxxxxxxxxxxxxxxxxxxxxx"
        
        # 1. First event from Whale A -> 10 minutes ago
        event_a = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "token_mint": token,
            "signature": "sig_expired",
            "timestamp_utc": datetime.now(timezone.utc) - timedelta(minutes=10)
        }
        await self.engine.trigger_event(event_a)
        
        # 2. Second event from Whale B -> now (10 minutes later, outside 5m window) -> should NOT trigger
        event_b = {
            "wallet_address": "Wha1eB22222222222222222222222222222222222",
            "token_mint": token,
            "signature": "sig_now",
            "timestamp_utc": datetime.now(timezone.utc)
        }
        await self.engine.trigger_event(event_b)
        self.mock_ml_pipeline.analyze_token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
