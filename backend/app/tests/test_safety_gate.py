import unittest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from app.domain.models import FeatureVector, PredictionResult, SafetyCheckResult
from app.infrastructure.blockchain.token_service import SolanaTokenSafetyService
from app.use_cases.safety_check_gate import SafetyCheckGate
from app.websocket.manager import manager as ws_manager


class TestSafetyCheckGate(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock repositories & services
        self.mock_filter_log_repo = MagicMock()
        self.mock_filter_log_repo.add_log = AsyncMock()

        self.safety_service = SolanaTokenSafetyService()
        self.gate = SafetyCheckGate(
            safety_service=self.safety_service,
            filter_log_repo=self.mock_filter_log_repo,
            max_cache_size=3,  # small cache size to test LRU eviction
            ttl_seconds=2.0    # short TTL to test expiry
        )

        # Mock WebSocket broadcast
        self.original_broadcast = ws_manager.broadcast
        ws_manager.broadcast = AsyncMock()

        # Dummy feature vector & prediction
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

    async def test_safe_token_passes_safety_and_confidence(self):
        res = await self.gate.evaluate_safety(self.pred, self.fv)
        
        self.assertTrue(res.passed)
        self.assertEqual(res.reason, "Passed all safety criteria")
        # Verify alert broadcasted to F-07
        ws_manager.broadcast.assert_called_once()
        # Verify relevance audit log was not written with 'safety_failed'
        self.mock_filter_log_repo.add_log.assert_not_called()

    async def test_low_confidence_does_not_broadcast_alert(self):
        self.pred.confidence_score = 0.60  # below 0.75 threshold
        res = await self.gate.evaluate_safety(self.pred, self.fv)
        
        self.assertTrue(res.passed) # safety still passes
        ws_manager.broadcast.assert_not_called() # but no alert is emitted
        self.mock_filter_log_repo.add_log.assert_called_once() # logs it as low confidence

    async def test_unsafe_lp_fails_safety(self):
        self.pred.token_address = "UnsafeLPOpenxxxxxxxxxxxxxxxxxxxxxxxxx"
        res = await self.gate.evaluate_safety(self.pred, self.fv)
        
        self.assertFalse(res.passed)
        self.assertEqual(res.reason, "lp_not_locked")
        ws_manager.broadcast.assert_not_called()
        self.mock_filter_log_repo.add_log.assert_called_once()

    async def test_unsafe_contract_fails_safety(self):
        self.pred.token_address = "UnsafeContractxxxxxxxxxxxxxxxxxxxxxxx"
        res = await self.gate.evaluate_safety(self.pred, self.fv)
        
        self.assertFalse(res.passed)
        self.assertEqual(res.reason, "contract_not_verified")
        ws_manager.broadcast.assert_not_called()

    async def test_unsafe_holders_fails_safety(self):
        self.pred.token_address = "UnsafeHoldersxxxxxxxxxxxxxxxxxxxxxxxx"
        res = await self.gate.evaluate_safety(self.pred, self.fv)
        
        self.assertFalse(res.passed)
        self.assertIn("holder_concentration_too_high", res.reason)
        ws_manager.broadcast.assert_not_called()

    async def test_unsafe_mint_fails_safety(self):
        self.pred.token_address = "UnsafeMintxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        res = await self.gate.evaluate_safety(self.pred, self.fv)
        
        self.assertFalse(res.passed)
        self.assertEqual(res.reason, "mint_authority_not_revoked")
        ws_manager.broadcast.assert_not_called()

    async def test_timeout_token_triggers_fail_closed(self):
        self.pred.token_address = "TimeoutTokenxxxxxxxxxxxxxxxxxxxxxxxxx"
        # We wrapper with time measurement to make sure we don't block forever
        start_time = time.time()
        res = await self.gate.evaluate_safety(self.pred, self.fv)
        elapsed = time.time() - start_time
        
        self.assertFalse(res.passed)
        self.assertEqual(res.reason, "safety_api_failed")
        # Verify timeout is handled around 5 seconds
        self.assertTrue(4.5 <= elapsed <= 5.8)
        ws_manager.broadcast.assert_not_called()
        self.mock_filter_log_repo.add_log.assert_called_once()

    async def test_lru_cache_eviction_and_ttl(self):
        # 1. First fetch uses service
        res1 = await self.gate.evaluate_safety(self.pred, self.fv)
        self.assertTrue(res1.passed)
        
        # 2. Second fetch uses cache
        # We can verify by checking that we don't trigger new service calls
        # Let's mock safety service
        self.gate.safety_service.get_safety_info = AsyncMock(return_value={
            "liquidity_locked": True,
            "contract_verified": True,
            "top_10_holders_share": 0.12,
            "mint_authority_revoked": True
        })
        res2 = await self.gate.evaluate_safety(self.pred, self.fv)
        self.assertTrue(res2.passed)
        self.gate.safety_service.get_safety_info.assert_not_called()

        # 3. Wait for TTL to expire (2 seconds)
        await asyncio.sleep(2.1)
        res3 = await self.gate.evaluate_safety(self.pred, self.fv)
        self.assertTrue(res3.passed)
        self.gate.safety_service.get_safety_info.assert_called_once()
        
        # 4. Test LRU Eviction (cache limit = 3)
        self.gate.safety_service.get_safety_info.reset_mock()
        
        # Fill cache with 3 distinct tokens: SafeToken, token2, token3
        self.pred.token_address = "token2"
        await self.gate.evaluate_safety(self.pred, self.fv)
        self.pred.token_address = "token3"
        await self.gate.evaluate_safety(self.pred, self.fv)
        
        # Now cache has: SafeToken, token2, token3
        # Querying SafeToken should keep it in cache
        self.pred.token_address = "SafeTokenxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        await self.gate.evaluate_safety(self.pred, self.fv)
        
        # Add token4 (should evict token2 since it's the least recently used, as SafeToken was queried)
        self.pred.token_address = "token4"
        await self.gate.evaluate_safety(self.pred, self.fv)
        
        # Querying token2 should now trigger a service call (since it was evicted)
        self.gate.safety_service.get_safety_info.reset_mock()
        self.pred.token_address = "token2"
        await self.gate.evaluate_safety(self.pred, self.fv)
        self.gate.safety_service.get_safety_info.assert_called_once()
