import unittest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
import asyncio

from app.use_cases.relevance_filter import RelevanceFilter
from app.domain.models import WatchlistWallet, FilterAuditLog
from app.core.config import settings


class TestRelevanceFilter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock repositories & trigger engine
        self.mock_log_repo = MagicMock()
        self.mock_log_repo.add_log = AsyncMock()
        
        self.mock_trigger_engine = MagicMock()
        self.mock_trigger_engine.trigger_event = AsyncMock()
        
        self.mock_wallet_repo = MagicMock()
        self.mock_wallet_repo.get_wallet = AsyncMock(return_value=None)
        
        # Instantiate Relevance Filter
        self.filter = RelevanceFilter(
            filter_log_repo=self.mock_log_repo,
            trigger_engine=self.mock_trigger_engine,
            wallet_repo=self.mock_wallet_repo
        )

        # Force some settings for deterministic testing
        self.orig_routers = settings.DEX_ROUTERS
        self.orig_custodials = settings.CUSTODIAL_EXCHANGES
        self.orig_min_swap = settings.MIN_SWAP_AMOUNT_USD
        self.orig_min_lp = settings.MIN_LP_CHANGE_USD
        
        settings.DEX_ROUTERS = ["6EF8rrect3EDQS425286575m1111111111111111"]
        settings.CUSTODIAL_EXCHANGES = ["BinanceCustodianAddress1111111111111111"]
        settings.MIN_SWAP_AMOUNT_USD = 10.0
        settings.MIN_LP_CHANGE_USD = 1000.0

    def tearDown(self):
        settings.DEX_ROUTERS = self.orig_routers
        settings.CUSTODIAL_EXCHANGES = self.orig_custodials
        settings.MIN_SWAP_AMOUNT_USD = self.orig_min_swap
        settings.MIN_LP_CHANGE_USD = self.orig_min_lp

    async def test_rule1_dex_router_check(self):
        # 1. Event with valid DEX router should pass Rule 1
        event_valid = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "swap",
            "amount_usd": 150.0,
            "token_mint": "BONKxxxxxxxxx",
            "signature": "sig_valid_dex",
            "program_id": "6EF8rrect3EDQS425286575m1111111111111111"
        }
        await self.filter.process_event(event_valid)
        self.mock_trigger_engine.trigger_event.assert_called_with(event_valid)
        
        # Verify that add_log was called with is_relevant = True
        log_arg = self.mock_log_repo.add_log.call_args[0][0]
        self.assertTrue(log_arg.is_relevant)
        self.assertEqual(log_arg.signature, "sig_valid_dex")

        # Reset mocks
        self.mock_trigger_engine.trigger_event.reset_mock()
        self.mock_log_repo.add_log.reset_mock()

        # 2. Event with unknown/non-DEX router should fail Rule 1
        event_invalid = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "swap",
            "amount_usd": 150.0,
            "token_mint": "BONKxxxxxxxxx",
            "signature": "sig_invalid_dex",
            "program_id": "UnknownRouterAddress1111111111111111111"
        }
        await self.filter.process_event(event_invalid)
        self.mock_trigger_engine.trigger_event.assert_not_called()
        
        # Verify that add_log was called with is_relevant = False
        log_arg = self.mock_log_repo.add_log.call_args[0][0]
        self.assertFalse(log_arg.is_relevant)
        self.assertEqual(log_arg.reason, "irrelevant_non_dex")

    async def test_rule2_low_usd_value(self):
        event_low_val = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "swap",
            "amount_usd": 2.5, # Below $10 threshold
            "token_mint": "BONKxxxxxxxxx",
            "signature": "sig_low_value",
            "program_id": "6EF8rrect3EDQS425286575m1111111111111111"
        }
        await self.filter.process_event(event_low_val)
        self.mock_trigger_engine.trigger_event.assert_not_called()
        
        log_arg = self.mock_log_repo.add_log.call_args[0][0]
        self.assertFalse(log_arg.is_relevant)
        self.assertIn("irrelevant_low_value", log_arg.reason)

    async def test_rule3_self_transfer(self):
        # Setup receiver wallet to exist in watchlist
        receiver = "Wha1eB22222222222222222222222222222222222"
        self.mock_wallet_repo.get_wallet.return_value = WatchlistWallet(
            wallet_address=receiver,
            label="Whale B",
            source="manual",
            added_at=datetime.utcnow(),
            active=True
        )

        event_self_tx = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "transfer",
            "amount_usd": 2000.0,
            "token_mint": "WSOLxxxxxxxxx",
            "signature": "sig_self_tx",
            "receiver_address": receiver
        }
        await self.filter.process_event(event_self_tx)
        self.mock_trigger_engine.trigger_event.assert_not_called()
        
        log_arg = self.mock_log_repo.add_log.call_args[0][0]
        self.assertFalse(log_arg.is_relevant)
        self.assertEqual(log_arg.reason, "self_transfer")

    async def test_rule4_custodial_exchange(self):
        receiver = "BinanceCustodianAddress1111111111111111"
        event_custodial = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "transfer",
            "amount_usd": 5000.0,
            "token_mint": "WSOLxxxxxxxxx",
            "signature": "sig_custodial",
            "receiver_address": receiver
        }
        await self.filter.process_event(event_custodial)
        self.mock_trigger_engine.trigger_event.assert_not_called()
        
        log_arg = self.mock_log_repo.add_log.call_args[0][0]
        self.assertFalse(log_arg.is_relevant)
        self.assertEqual(log_arg.reason, "custodial_deposit")

    async def test_rule5_lp_change_significance(self):
        # 1. Significant LP Change should pass
        event_lp_sig = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "lp_change",
            "amount_usd": 5000.0, # Above $1000 threshold
            "token_mint": "LP_TOKENxxxxx",
            "signature": "sig_lp_sig",
            "program_id": "6EF8rrect3EDQS425286575m1111111111111111"
        }
        await self.filter.process_event(event_lp_sig)
        self.mock_trigger_engine.trigger_event.assert_called_with(event_lp_sig)
        
        self.mock_trigger_engine.trigger_event.reset_mock()
        self.mock_log_repo.add_log.reset_mock()

        # 2. Insignificant LP Change should fail
        event_lp_insig = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "lp_change",
            "amount_usd": 200.0, # Below $1000 threshold
            "token_mint": "LP_TOKENxxxxx",
            "signature": "sig_lp_insig",
            "program_id": "6EF8rrect3EDQS425286575m1111111111111111"
        }
        await self.filter.process_event(event_lp_insig)
        self.mock_trigger_engine.trigger_event.assert_not_called()
        
        log_arg = self.mock_log_repo.add_log.call_args[0][0]
        self.assertFalse(log_arg.is_relevant)
        self.assertIn("insignificant_lp_change", log_arg.reason)


if __name__ == "__main__":
    unittest.main()
