import asyncio
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np

from app.domain.models import ClosedTrade
from app.ml_pipeline.bootstrap import FEATURE_COLUMNS, TokenMarketSnapshot
from app.ml_pipeline.new_token_discovery_service import NewTokenDiscoveryService


class TestNewTokenDiscoveryService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.trade_repo = MagicMock()
        self.trade_repo.get_closed_trades = AsyncMock(return_value=[])
        self.token_service = MagicMock()
        self.model_repo = MagicMock()

        self.service = NewTokenDiscoveryService(
            trade_history_repo=self.trade_repo,
            token_info_service=self.token_service,
            model_registry_repo=self.model_repo,
        )

    def test_wss_fallback_chain_conversion(self):
        self.service.rpc_url = "https://my-custom-rpc.com/v1"
        chain = self.service._get_wss_fallback_chain()
        self.assertTrue(chain[0].startswith("wss://my-custom-rpc.com/v1"))
        self.assertTrue(all(url.startswith("wss://") or url.startswith("ws://") for url in chain))

    def test_passes_filters(self):
        now = datetime.now(timezone.utc)
        valid_snapshot = TokenMarketSnapshot(
            price_usd=1.25,
            liquidity_usd=10000.0,
            volume_24h=5000.0,
            pair_created_at=now - timedelta(minutes=15),
        )
        self.assertTrue(self.service._passes_filters(valid_snapshot))

        low_liq_snapshot = TokenMarketSnapshot(
            price_usd=1.25,
            liquidity_usd=1000.0,  # Below $6000 threshold
            volume_24h=5000.0,
            pair_created_at=now - timedelta(minutes=10),
        )
        self.assertFalse(self.service._passes_filters(low_liq_snapshot))

    def test_extract_mints_from_transaction(self):
        tx = {
            "meta": {
                "postTokenBalances": [
                    {"mint": "So11111111111111111111111111111111111111112"},  # WSOL - excluded
                    {"mint": "NewTokenMint111111111111111111111111111111"},
                ],
                "preTokenBalances": [
                    {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},  # USDC - excluded
                ],
            }
        }
        mints = self.service._extract_mints_from_transaction(tx)
        self.assertEqual(mints, ["NewTokenMint111111111111111111111111111111"])

    def test_calculate_sol_usd_momentum(self):
        now = datetime.now(timezone.utc)
        self.service._sol_price_history = [
            (now - timedelta(minutes=5), 140.0),
            (now, 147.0),  # +5% gain
        ]
        momentum = self.service._calculate_sol_usd_momentum()
        self.assertAlmostEqual(momentum, 0.05, places=3)

    async def test_extract_features_structure_and_wallet_stats(self):
        now = datetime.now(timezone.utc)
        snapshot = TokenMarketSnapshot(
            price_usd=0.05,
            liquidity_usd=15000.0,
            volume_24h=30000.0,
            pair_created_at=now - timedelta(minutes=30),
        )

        mock_trades = [
            ClosedTrade(
                trade_id="t1",
                wallet_source="w1",
                token_address="m1",
                token_symbol="M1",
                signal_ts=now - timedelta(days=2),
                entry_ts=now - timedelta(days=2),
                exit_ts=now - timedelta(days=2, minutes=-15),
                direction="BUY",
                confidence_score=0.8,
                safety_check_passed=True,
                entry_price=1.0,
                exit_price=1.5,
                position_size_usd=500.0,
                risk_pct=0.01,
                pnl_pct_actual=0.50,
                r_multiple=3.5,
                label="BUY_BENAR",
                holding_time_minutes=15,
                exit_reason="tp_target",
                is_paper_trade=True,
                model_version="v0",
            ),
            ClosedTrade(
                trade_id="t2",
                wallet_source="w1",
                token_address="m2",
                token_symbol="M2",
                signal_ts=now - timedelta(days=5),
                entry_ts=now - timedelta(days=5),
                exit_ts=now - timedelta(days=5, minutes=-10),
                direction="BUY",
                confidence_score=0.8,
                safety_check_passed=True,
                entry_price=1.0,
                exit_price=0.8,
                position_size_usd=500.0,
                risk_pct=0.01,
                pnl_pct_actual=-0.20,
                r_multiple=-1.5,
                label="SALAH",
                holding_time_minutes=10,
                exit_reason="kill_switch_dev_sell",
                is_paper_trade=True,
                model_version="v0",
            ),
        ]
        self.trade_repo.get_closed_trades = AsyncMock(return_value=mock_trades)

        features = await self.service._extract_features("CandidateMint123", snapshot)
        self.assertIsInstance(features, np.ndarray)
        self.assertEqual(features.shape, (1, 12))

        # Check calculated values in row:
        # win_rate_30d: 1 win / 2 trades = 0.50
        self.assertAlmostEqual(features[0][5], 0.50)
        # avg_holding_time_minutes: (15 + 10) / 2 = 12.5
        self.assertAlmostEqual(features[0][6], 12.5)
        # typical_trade_size_usd: 500.0
        self.assertAlmostEqual(features[0][7], 500.0)
        # past_exit_pattern_score: 1 kill_switch / 2 trades = 0.50
        self.assertAlmostEqual(features[0][8], 0.50)

    async def test_active_trading_trigger(self):
        # Setup mock safety check gate
        mock_gate = MagicMock()
        mock_gate.evaluate_safety = AsyncMock()
        self.service.safety_check_gate = mock_gate

        # Mock candidate pairs and snapshot
        now = datetime.now(timezone.utc)
        self.service._fetch_candidate_pairs = AsyncMock(return_value=["ActiveCandidateMint11111111111111"])
        self.service._fetch_pair_snapshot = AsyncMock(return_value=TokenMarketSnapshot(
            price_usd=1.25,
            liquidity_usd=10000.0,
            volume_24h=5000.0,
            pair_created_at=now - timedelta(minutes=15),
        ))
        
        # Mock ML scoring to be above confidence threshold (0.50)
        self.service._score = AsyncMock(return_value=0.85)

        # Force trade enabled setting
        from app.core.config import settings
        orig_enabled = settings.DISCOVERY_TRADE_ENABLED
        settings.DISCOVERY_TRADE_ENABLED = True

        try:
            # Trigger scan
            opps = await self.service.scan_once()
            self.assertEqual(len(opps), 1)
            
            # Verify that evaluate_safety was triggered as a background task
            await asyncio.sleep(0.1) # Yield execution to let background tasks run
            mock_gate.evaluate_safety.assert_called_once()
            
            # Check arguments passed to evaluate_safety
            args, _ = mock_gate.evaluate_safety.call_args
            prediction, fv = args[0], args[1]
            self.assertEqual(prediction.token_address, "ActiveCandidateMint11111111111111")
            self.assertEqual(prediction.confidence_score, 0.85)
            self.assertEqual(prediction.wallet_source, "new_token_discovery")
            self.assertEqual(fv.token_address, "ActiveCandidateMint11111111111111")
            self.assertEqual(fv.wallet_source, "new_token_discovery")
            
        finally:
            settings.DISCOVERY_TRADE_ENABLED = orig_enabled


if __name__ == "__main__":
    unittest.main()
