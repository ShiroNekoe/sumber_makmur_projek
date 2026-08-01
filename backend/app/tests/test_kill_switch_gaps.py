import unittest
import asyncio
from unittest.mock import AsyncMock, patch

from app.domain.models import OpenPosition
from app.execution.executor import ParallelExecutionEngine


class TestKillSwitchGaps(unittest.TestCase):
    @patch("app.infrastructure.blockchain.bonding_curve_price.estimate_bonding_curve_price_impact")
    def test_kill_switch_triggers_on_price_impact_spike(self, mock_impact):
        """
        Verifies that an on-chain price impact spike over baseline triggers
        'kill_switch_slippage_spike'.
        """
        pos = OpenPosition(
            position_id="pos_ks_impact",
            wallet_source="TestWallet",
            token_address="TokenSpikeTest",
            state="OPEN",
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=500.0,
            confidence_score=0.85,
            model_version="v0"
        )

        engine = ParallelExecutionEngine(
            position=pos,
            position_repo=AsyncMock(),
            cooldown_repo=AsyncMock(),
            model_registry_repo=AsyncMock(),
            trade_history_repo=AsyncMock()
        )

        # First check: baseline impact = 0.02 (2%)
        mock_impact.return_value = 0.02
        res1 = asyncio.run(engine._check_onchain_kill_signals())
        self.assertIsNone(res1)
        self.assertEqual(engine._baseline_price_impact, 0.02)

        # Second check: price impact spikes to 0.20 (20%), spike = +18% >= 15% threshold
        mock_impact.return_value = 0.20
        res2 = asyncio.run(engine._check_onchain_kill_signals())
        self.assertEqual(res2, "kill_switch_slippage_spike")

    def test_kill_switch_triggers_on_dev_dump(self):
        """
        Verifies that a holder concentration shift / dev wallet sell triggers
        'kill_switch_dev_dump'.
        """
        pos = OpenPosition(
            position_id="pos_ks_dev",
            wallet_source="TestWallet",
            token_address="TokenDevDumpTest",
            state="OPEN",
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=500.0,
            confidence_score=0.85,
            model_version="v0"
        )

        mock_safety_svc = AsyncMock()
        mock_safety_svc.get_safety_info.side_effect = [
            {"top_10_holders_share": 0.20}, # baseline: 20%
            {"top_10_holders_share": 0.35}  # shift: +15% >= dev dump threshold
        ]

        engine = ParallelExecutionEngine(
            position=pos,
            position_repo=AsyncMock(),
            cooldown_repo=AsyncMock(),
            model_registry_repo=AsyncMock(),
            trade_history_repo=AsyncMock(),
            token_safety_service=mock_safety_svc
        )

        # First check establishes baseline
        with patch("app.infrastructure.blockchain.bonding_curve_price.estimate_bonding_curve_price_impact", new_callable=AsyncMock(return_value=None)):
            res1 = asyncio.run(engine._check_onchain_kill_signals())
            self.assertIsNone(res1)

            # Second check triggers dev dump
            res2 = asyncio.run(engine._check_onchain_kill_signals())
            self.assertEqual(res2, "kill_switch_dev_dump")


if __name__ == "__main__":
    unittest.main()
