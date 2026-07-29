import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import WatchlistWallet
from app.use_cases.wallet_discovery import WalletDiscoveryService


class TestWalletDiscoveryService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Mock repositories and dependency services
        self.wallet_repo = MagicMock()
        self.wallet_repo.get_wallet = AsyncMock(return_value=None)
        self.wallet_repo.add_wallet = AsyncMock()
        self.wallet_repo.get_all_wallets = AsyncMock(return_value=[])

        self.token_info_service = MagicMock()
        # Mock name containing "Simulator" to trigger simulation logic
        type(self.token_info_service).__name__ = "TokenInfoSimulator"

        self.discovery_service = WalletDiscoveryService(
            wallet_repo=self.wallet_repo,
            token_info_service=self.token_info_service
        )

    async def asyncTearDown(self):
        await self.discovery_service.stop()

    async def test_queue_and_analyze_flow_simulation(self):
        # Start background service worker
        await self.discovery_service.start()

        event = {
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "event_type": "swap",
            "token_mint": "EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
            "amount_usd": 1500.00,
            "signature": "sim_signature_123",
            "timestamp_utc": datetime.now(timezone.utc),
            "token_age_minutes": 15.0  # Within valid window (2-30m in default test config)
        }

        # Queue occurrences 3 times to trigger registration threshold
        await self.discovery_service.discover_wallets(event)
        
        # Second transaction (different signature to avoid dedup)
        event2 = dict(event, signature="sim_signature_456")
        await self.discovery_service.discover_wallets(event2)

        # Third transaction (triggers registration)
        event3 = dict(event, signature="sim_signature_789")
        await self.discovery_service.discover_wallets(event3)

        # Wait briefly for background loop to pick it up and process mock logic
        await asyncio.sleep(1.2)

        # Verify wallet candidate was registered via add_wallet repo
        self.wallet_repo.add_wallet.assert_called()
        args, _ = self.wallet_repo.add_wallet.call_args
        registered_wallet = args[0]
        
        self.assertIsInstance(registered_wallet, WatchlistWallet)
        self.assertEqual(registered_wallet.source, "auto_discovered")
        self.assertEqual(registered_wallet.active, False)
        self.assertEqual(registered_wallet.status, "pending")
        self.assertTrue(registered_wallet.wallet_address.startswith("DiscovWhale"))
