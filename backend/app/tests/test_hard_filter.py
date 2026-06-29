import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import HardFilterAuditLog
from app.use_cases.hard_filter import TokenAgeLiquidityHardFilter


class TestTokenAgeLiquidityHardFilter(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token_info_service = MagicMock()
        self.token_info_service.get_token_info = AsyncMock()

        self.trigger_engine = MagicMock()
        self.trigger_engine.trigger_event = AsyncMock()

        self.hard_filter_log_repo = MagicMock()
        self.hard_filter_log_repo.add_hard_filter_log = AsyncMock()

        self.hard_filter = TokenAgeLiquidityHardFilter(
            token_info_service=self.token_info_service,
            trigger_engine=self.trigger_engine,
            hard_filter_log_repo=self.hard_filter_log_repo
        )

        # Configured defaults in test environment settings:
        # min_token_age_minutes: 60
        # min_liquidity_usd: 5000.0

    async def test_token_passes_filter(self):
        # Setup: Token is 2 hours old and has $15k liquidity (passes both checks)
        self.token_info_service.get_token_info.return_value = {
            "age_minutes": 120.0,
            "liquidity_usd": 15000.0,
            "volume_24h": 3000.0,
            "token_symbol": "PASS"
        }

        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "swap",
            "token_mint": "EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
            "amount_usd": 1000.0,
            "signature": "sig_passed"
        }

        await self.hard_filter.process_event(event)

        # Assert: Passed and forwarded to trigger engine
        self.trigger_engine.trigger_event.assert_called_once_with(event)
        self.assertEqual(event["token_age_minutes"], 120.0)
        self.assertEqual(event["liquidity_pool_depth"], 15000.0)

        # Assert: Decision audited
        self.hard_filter_log_repo.add_hard_filter_log.assert_called_once()
        args, _ = self.hard_filter_log_repo.add_hard_filter_log.call_args
        log = args[0]
        self.assertIsInstance(log, HardFilterAuditLog)
        self.assertTrue(log.passed)
        self.assertIsNone(log.reason)

    async def test_token_fails_age(self):
        # Setup: Token is 30m old (fails age check)
        self.token_info_service.get_token_info.return_value = {
            "age_minutes": 30.0,
            "liquidity_usd": 15000.0,
            "volume_24h": 3000.0,
            "token_symbol": "FAIL_AGE"
        }

        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "swap",
            "token_mint": "EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
            "amount_usd": 1000.0,
            "signature": "sig_fail_age"
        }

        await self.hard_filter.process_event(event)

        # Assert: Discarded (not forwarded)
        self.trigger_engine.trigger_event.assert_not_called()

        # Assert: Decision audited
        self.hard_filter_log_repo.add_hard_filter_log.assert_called_once()
        args, _ = self.hard_filter_log_repo.add_hard_filter_log.call_args
        log = args[0]
        self.assertFalse(log.passed)
        self.assertIn("age_too_low", log.reason)

    async def test_token_fails_liquidity(self):
        # Setup: Token has $3k liquidity (fails liquidity check)
        self.token_info_service.get_token_info.return_value = {
            "age_minutes": 120.0,
            "liquidity_usd": 3000.0,
            "volume_24h": 3000.0,
            "token_symbol": "FAIL_LIQ"
        }

        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "swap",
            "token_mint": "EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
            "amount_usd": 1000.0,
            "signature": "sig_fail_liq"
        }

        await self.hard_filter.process_event(event)

        # Assert: Discarded (not forwarded)
        self.trigger_engine.trigger_event.assert_not_called()

        # Assert: Decision audited
        self.hard_filter_log_repo.add_hard_filter_log.assert_called_once()
        args, _ = self.hard_filter_log_repo.add_hard_filter_log.call_args
        log = args[0]
        self.assertFalse(log.passed)
        self.assertIn("liquidity_too_low", log.reason)

    async def test_token_info_service_failed_discard(self):
        # Setup: Service returns None (API failure)
        self.token_info_service.get_token_info.return_value = None

        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "swap",
            "token_mint": "EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
            "amount_usd": 1000.0,
            "signature": "sig_api_fail"
        }

        await self.hard_filter.process_event(event)

        # Assert: Discarded
        self.trigger_engine.trigger_event.assert_not_called()

        # Assert: Logged as not found
        self.hard_filter_log_repo.add_hard_filter_log.assert_called_once()
        args, _ = self.hard_filter_log_repo.add_hard_filter_log.call_args
        log = args[0]
        self.assertFalse(log.passed)
        self.assertEqual(log.reason, "token_not_found")

    async def test_cache_hits(self):
        # Setup: Returns successful info
        self.token_info_service.get_token_info.return_value = {
            "age_minutes": 120.0,
            "liquidity_usd": 15000.0,
            "volume_24h": 3000.0,
            "token_symbol": "CACHE_TEST"
        }

        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "swap",
            "token_mint": "EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
            "amount_usd": 1000.0,
            "signature": "sig_1"
        }

        # Process twice
        await self.hard_filter.process_event(event)
        
        event2 = dict(event, signature="sig_2")
        await self.hard_filter.process_event(event2)

        # Assert: API called only once because of 5-min TTL cache
        self.token_info_service.get_token_info.assert_called_once_with("EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584")
        self.assertEqual(self.trigger_engine.trigger_event.call_count, 2)
