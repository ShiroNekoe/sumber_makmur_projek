import os
import sys
import yaml
import unittest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.core.config import settings
from app.domain.models import PredictionResult, FeatureVector
from app.infrastructure.blockchain.token_service import SolanaTokenSafetyService
from app.use_cases.safety_check_gate import SafetyCheckGate


class TestAntiManipulationFase3(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.safety_service = SolanaTokenSafetyService()
        self.mock_log_repo = AsyncMock()
        self.gate = SafetyCheckGate(
            safety_service=self.safety_service,
            filter_log_repo=self.mock_log_repo
        )

        now = datetime.now(timezone.utc)
        self.fv = FeatureVector(
            token_address="UnsafeDeployerxxxxxxxxxxxxxxxxxxxxxx",
            wallet_source="TestWhaleWallet",
            signature="test_sig_deployer_check",
            timestamp=now,
            position_size_usd=500.0,
            token_age_minutes=15.0,
            liquidity_pool_depth=10000.0,
            slippage_actual=0.01,
            cluster_score=1.0,
            win_rate_30d=0.5,
            avg_holding_time_minutes=15.0,
            typical_trade_size_usd=500.0,
            past_exit_pattern_score=0.0,
            sol_usd_momentum=0.0,
            token_volume_liquidity_ratio=0.5,
            hour_of_day_utc=now.hour
        )

    async def test_deployer_holding_too_high_blocks_safety_gate(self):
        now = datetime.now(timezone.utc)
        pred = PredictionResult(
            direction="BUY",
            confidence_score=0.85,
            target_price_estimate=0.05,
            token_address="UnsafeDeployerxxxxxxxxxxxxxxxxxxxxxx",
            wallet_source="TestWhaleWallet",
            signature="test_sig_deployer_check",
            timestamp=now
        )

        result = await self.gate.evaluate_safety(pred, self.fv)

        self.assertFalse(result.passed)
        self.assertIn("deployer_holding_too_high", result.reason)
        self.assertAlmostEqual(result.deployer_holding_pct, 0.25)

    async def test_deployer_holding_within_limits_passes(self):
        now = datetime.now(timezone.utc)
        pred = PredictionResult(
            direction="BUY",
            confidence_score=0.85,
            target_price_estimate=0.05,
            token_address="SafeTokenxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            wallet_source="TestWhaleWallet",
            signature="test_sig_safe_token",
            timestamp=now
        )

        result = await self.gate.evaluate_safety(pred, self.fv)

        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "Passed all safety criteria")

    def test_strict_token_age_constraints_unaltered(self):
        # Verify that min_token_age_minutes and max_token_age_minutes in config.yaml
        # are strictly preserved at 2.0 and 30.0 (enforcing system owner constraint)
        config_path = os.path.abspath("backend/config.yaml")
        self.assertTrue(os.path.exists(config_path))

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        te = cfg.get("trigger_engine", {})
        self.assertEqual(float(te.get("min_token_age_minutes")), 2.0)
        self.assertEqual(float(te.get("max_token_age_minutes")), 30.0)


if __name__ == "__main__":
    unittest.main()
