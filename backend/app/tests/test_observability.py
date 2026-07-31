import os
import sys
import unittest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.core.config import settings
from app.domain.models import OpenPosition
from app.use_cases.dashboard_query import DashboardQueryService, append_signal_event


class TestObservabilityFase4(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_trade_repo = AsyncMock()
        self.mock_wallet_repo = AsyncMock()
        self.mock_pos_repo = AsyncMock()
        self.mock_model_repo = AsyncMock()

        self.service = DashboardQueryService(
            trade_history_repo=self.mock_trade_repo,
            wallet_repo=self.mock_wallet_repo,
            position_repo=self.mock_pos_repo,
            model_registry_repo=self.mock_model_repo
        )

    async def test_get_dashboard_stats_observability_fields(self):
        now = datetime.now(timezone.utc)
        open_pos1 = OpenPosition(
            position_id="pos1",
            token_address="Token1111111111111111111111111111111111",
            wallet_source="w1",
            state="OPEN",
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=1200.0,
            confidence_score=0.8,
            model_version="v0"
        )
        self.mock_pos_repo.get_open_positions = AsyncMock(return_value=[open_pos1])
        self.mock_trade_repo.get_closed_trades = AsyncMock(return_value=[])

        # Append a deployer block signal to test deployer_blocks_24h counter
        append_signal_event({
            "event": "LOG_ONLY",
            "reason": "safety_failed: deployer_holding_too_high: deployer owns 25.00%",
            "token_address": "TokenDeployerBlocked11111111111111111111",
            "timestamp": now.isoformat()
        })

        stats = await self.service.get_stats()

        self.assertIn("current_exposure_usd", stats)
        self.assertIn("max_exposure_usd", stats)
        self.assertIn("circuit_breaker_active", stats)
        self.assertIn("deployer_blocks_24h", stats)

        self.assertEqual(stats["current_exposure_usd"], 1200.0)
        self.assertEqual(stats["max_exposure_usd"], settings.RISK_MAX_TOTAL_EXPOSURE_USD)
        self.assertGreaterEqual(stats["deployer_blocks_24h"], 1)

    async def test_get_system_status_response(self):
        status = await self.service.get_system_status()
        self.assertIn("overall_status", status)
        self.assertIn("rpc_status", status)
        self.assertIn("components", status)


if __name__ == "__main__":
    unittest.main()
