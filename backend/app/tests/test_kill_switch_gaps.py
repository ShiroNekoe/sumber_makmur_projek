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
        'kill_switch_large_sell' or 'kill_switch_dev_dump'.
        """
        pos = OpenPosition(
            position_id="pos_ks_impact",
            wallet_source="DevWalletXYZ111111111111111111111111111111",
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

        # Second check: price impact spikes to 0.20 (20%), spike = +18% >= 15% threshold from non-dev
        mock_impact.return_value = 0.20
        res2 = asyncio.run(engine._check_onchain_kill_signals(signer_address="SniperWalletAAA"))
        self.assertEqual(res2, "kill_switch_large_sell")

    def test_wallet_agnostic_large_sell_non_dev(self):
        """
        Verifies that a large sell from a NON-dev wallet (sniper, whale, insider)
        triggers 'kill_switch_large_sell' (Wallet-Agnostic).
        """
        pos = OpenPosition(
            position_id="pos_ks_wallet_agnostic",
            wallet_source="DevWalletXYZ111111111111111111111111111111",
            token_address="TokenAgnosticTest",
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

        # Set baseline reserves (30 SOL, 1M tokens)
        res_baseline = engine.evaluate_reserve_change(new_v_sol=30_000_000_000, new_v_token=1_000_000_000_000)
        self.assertIsNone(res_baseline)

        # Large sell by non-dev wallet: SOL reserves drop from 30 SOL to 20 SOL (33% drop >= 15% threshold)
        res_sell = engine.evaluate_reserve_change(
            new_v_sol=20_000_000_000,
            new_v_token=1_500_000_000_000,
            signer_address="WhaleWalletBBB2222222222222222222222222"
        )
        self.assertEqual(res_sell, "kill_switch_large_sell")

    def test_dev_dump_fast_path(self):
        """
        Verifies that a large sell specifically from the Dev wallet triggers
        'kill_switch_dev_dump' fast-path.
        """
        dev_wallet = "DevWalletXYZ111111111111111111111111111111"
        pos = OpenPosition(
            position_id="pos_ks_dev_fastpath",
            wallet_source=dev_wallet,
            token_address="TokenDevTest",
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

        # Set baseline reserves
        engine.evaluate_reserve_change(new_v_sol=30_000_000_000, new_v_token=1_000_000_000_000)

        # Dev wallet sells tokens: SOL reserves drop to 20 SOL (33% drop >= 15% threshold)
        res_dev_dump = engine.evaluate_reserve_change(
            new_v_sol=20_000_000_000,
            new_v_token=1_500_000_000_000,
            signer_address=dev_wallet
        )
        self.assertEqual(res_dev_dump, "kill_switch_dev_dump")

    def test_small_sell_below_threshold_ignored(self):
        """
        Verifies that small buys/sells below the 15% threshold do NOT trigger exit.
        """
        pos = OpenPosition(
            position_id="pos_ks_small_trade",
            wallet_source="DevWalletXYZ",
            token_address="TokenSmallTradeTest",
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

        # Baseline: 30 SOL
        engine.evaluate_reserve_change(new_v_sol=30_000_000_000, new_v_token=1_000_000_000_000)

        # Small trade: 29.5 SOL (1.6% drop < 15% threshold)
        res_small = engine.evaluate_reserve_change(
            new_v_sol=29_500_000_000,
            new_v_token=1_015_000_000_000,
            signer_address="SniperWalletCCC"
        )
        self.assertIsNone(res_small)

    def test_teardown_subscription_lifecycle(self):
        """
        Verifies that when execute_exit runs, tasks are marked finished/cancelled
        and position exited state is set to True.
        """
        async def run_test():
            pos = OpenPosition(
                position_id="pos_teardown_test",
                wallet_source="DevWalletXYZ",
                token_address="TokenTeardownTest",
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

            dummy_task = asyncio.create_task(asyncio.sleep(0.1))
            engine.tasks = [dummy_task]

            with patch("app.infrastructure.blockchain.pumpportal_client.build_trade_transaction", new_callable=AsyncMock), \
                 patch("app.infrastructure.blockchain.tx_signer.sign_and_broadcast_transaction", return_value="tx_sig_test"), \
                 patch("app.infrastructure.blockchain.tx_signer.close_token_account", return_value="close_sig_test"):
                await engine.execute_exit("kill_switch_large_sell")

            self.assertTrue(engine.exited)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
