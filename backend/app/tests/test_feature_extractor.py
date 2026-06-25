import unittest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
import asyncio
import time

from app.domain.models import ClosedTrade, FeatureVector
from app.ml_pipeline.inference import FeatureExtractor
from app.infrastructure.blockchain.token_service import SolanaTokenInfoService


class TestFeatureExtractor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock repositories & services
        self.mock_trade_history_repo = MagicMock()
        self.mock_trade_history_repo.get_closed_trades = AsyncMock(return_value=[])
        
        self.mock_token_info_service = MagicMock()
        self.mock_token_info_service.get_token_info = AsyncMock(return_value={
            "age_minutes": 100.0,
            "liquidity_usd": 25000.0,
            "volume_24h": 5000.0,
            "token_symbol": "DUMMY"
        })
        
        self.extractor = FeatureExtractor(
            trade_history_repo=self.mock_trade_history_repo,
            token_info_service=self.mock_token_info_service
        )

    async def test_fallback_to_prior_defaults_when_db_empty(self):
        trigger_event = {
            "token_address": "TokenAddressxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "signature": "sig_test_1",
            "amount_usd": 150.0,
            "confidence_boost": False,
            "timestamp_utc": datetime.now(timezone.utc)
        }
        
        fv = await self.extractor.extract_features(trigger_event)
        
        self.assertIsNotNone(fv)
        # Check prior defaults are applied
        self.assertEqual(fv.win_rate_30d, self.extractor.prior_win_rate)
        self.assertEqual(fv.avg_holding_time_minutes, self.extractor.prior_holding_time_minutes)
        self.assertEqual(fv.typical_trade_size_usd, self.extractor.prior_trade_size_usd)
        
        # Check on-chain features
        self.assertEqual(fv.position_size_usd, 150.0)
        self.assertEqual(fv.token_age_minutes, 100.0)
        self.assertEqual(fv.liquidity_pool_depth, 25000.0)
        self.assertEqual(fv.token_volume_liquidity_ratio, 5000.0 / 25000.0)

    async def test_calculates_historical_metrics_correctly(self):
        # Setup dummy historical closed trades
        dummy_trades = [
            ClosedTrade(
                trade_id="t1",
                wallet_source="Wha1eA11111111111111111111111111111111111",
                token_address="token1",
                token_symbol="T1",
                signal_ts=datetime.now(timezone.utc),
                entry_ts=datetime.now(timezone.utc),
                exit_ts=datetime.now(timezone.utc) - timedelta(days=5),
                direction="BUY",
                confidence_score=0.8,
                safety_check_passed=True,
                entry_price=10.0,
                exit_price=13.0,
                position_size_usd=100.0,
                risk_pct=0.01,
                pnl_pct_actual=0.3,
                r_multiple=3.0,
                label="BUY_BENAR", # winner
                holding_time_minutes=30,
                exit_reason="trailing_tp",
                is_paper_trade=False,
                model_version="v0"
            ),
            ClosedTrade(
                trade_id="t2",
                wallet_source="Wha1eA11111111111111111111111111111111111",
                token_address="token2",
                token_symbol="T2",
                signal_ts=datetime.now(timezone.utc),
                entry_ts=datetime.now(timezone.utc),
                exit_ts=datetime.now(timezone.utc) - timedelta(days=10),
                direction="BUY",
                confidence_score=0.85,
                safety_check_passed=True,
                entry_price=10.0,
                exit_price=9.0,
                position_size_usd=200.0,
                risk_pct=0.01,
                pnl_pct_actual=-0.1,
                r_multiple=-1.0,
                label="SALAH", # loser
                holding_time_minutes=10,
                exit_reason="SL",
                is_paper_trade=False,
                model_version="v0"
            )
        ]
        self.mock_trade_history_repo.get_closed_trades.return_value = dummy_trades
        
        trigger_event = {
            "token_address": "TokenAddressxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "wallet_address": "Wha1eA11111111111111111111111111111111111",
            "signature": "sig_test_2",
            "amount_usd": 150.0,
            "confidence_boost": True, # will trigger cluster_score = 1.0
            "timestamp_utc": datetime.now(timezone.utc)
        }
        
        fv = await self.extractor.extract_features(trigger_event)
        
        self.assertIsNotNone(fv)
        # 1 winner, 1 loser -> win rate = 0.5
        self.assertEqual(fv.win_rate_30d, 0.5)
        # avg holding time -> (30 + 10) / 2 = 20.0
        self.assertEqual(fv.avg_holding_time_minutes, 20.0)
        # typical size -> (100 + 200) / 2 = 150.0
        self.assertEqual(fv.typical_trade_size_usd, 150.0)
        # exit pattern score -> no kill_switch exits -> 0.0
        self.assertEqual(fv.past_exit_pattern_score, 0.0)
        # cluster score (AND boost is True) -> 1.0
        self.assertEqual(fv.cluster_score, 1.0)


class TestTokenServiceCache(unittest.IsolatedAsyncioTestCase):
    async def test_dexscreener_cache_hits(self):
        token_service = SolanaTokenInfoService()
        token = "FakeTokenAddressForCachingTest1234567890"

        # Mock dexscreener API method to track how many times it was called
        token_service._fetch_from_dexscreener = AsyncMock(return_value={
            "age_minutes": 150.0,
            "liquidity_usd": 30000.0,
            "volume_24h": 1000.0,
            "token_symbol": "CACHE_COIN"
        })

        # 1. First call: Cache miss, should fetch
        info1 = await token_service.get_token_info(token)
        self.assertEqual(info1["token_symbol"], "CACHE_COIN")
        token_service._fetch_from_dexscreener.assert_called_once_with(token)

        # Reset mock call count
        token_service._fetch_from_dexscreener.reset_mock()

        # 2. Second call: Cache hit, should use cache and NOT fetch
        info2 = await token_service.get_token_info(token)
        self.assertEqual(info2["token_symbol"], "CACHE_COIN")
        token_service._fetch_from_dexscreener.assert_not_called()

        # 3. Modify cache time to simulate expiration (>60s TTL)
        token_service.cache[token] = (time.time() - 70.0, info2)

        # 4. Third call: Expired cache, should fetch again
        info3 = await token_service.get_token_info(token)
        self.assertEqual(info3["token_symbol"], "CACHE_COIN")
        token_service._fetch_from_dexscreener.assert_called_once_with(token)


if __name__ == "__main__":
    unittest.main()
