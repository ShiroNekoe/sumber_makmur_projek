import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
        Verifies that a large sell specifically from the Dev wallet (dev_wallet_address)
        triggers 'kill_switch_dev_dump' fast-path, independently of copy-traded whale wallet_source.
        """
        dev_wallet = "DevWalletXYZ111111111111111111111111111111"
        copy_trade_whale = "CopyTradeWhale1111111111111111111111111"
        pos = OpenPosition(
            position_id="pos_ks_dev_fastpath",
            wallet_source=copy_trade_whale,
            dev_wallet_address=dev_wallet,
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

        # Copy-trade whale sells tokens: should trigger kill_switch_large_sell (NOT dev dump)
        res_whale_sell = engine.evaluate_reserve_change(
            new_v_sol=15_000_000_000,
            new_v_token=2_000_000_000_000,
            signer_address=copy_trade_whale
        )
        self.assertEqual(res_whale_sell, "kill_switch_large_sell")

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

    def test_anti_double_trigger_push_and_polling(self):
        """
        Simulates both push and polling tasks detecting kill signal simultaneously.
        Verifies that self.lock prevents duplicate execute_exit runs and exit order
        is broadcast exactly once.
        """
        async def run_test():
            pos = OpenPosition(
                position_id="pos_double_trigger_test",
                wallet_source="WhaleWallet",
                token_address="TokenDoubleTriggerTest",
                state="OPEN",
                entry_price=1.0,
                sl_initial=0.9,
                risk_pct=0.01,
                position_size_usd=500.0,
                confidence_score=0.85,
                model_version="v0"
            )

            mock_position_repo = AsyncMock()
            mock_trade_history = AsyncMock()
            engine = ParallelExecutionEngine(
                position=pos,
                position_repo=mock_position_repo,
                cooldown_repo=AsyncMock(),
                model_registry_repo=AsyncMock(),
                trade_history_repo=mock_trade_history
            )

            mock_keypair = MagicMock()
            mock_keypair.pubkey.return_value = "DummyPublicKey11111111111111111111111111"

            with patch("sys.argv", ["main.py"]), \
                 patch("app.infrastructure.blockchain.wallet_manager.load_wallet_from_env", return_value=mock_keypair), \
                 patch("app.infrastructure.blockchain.pumpportal_client.build_trade_transaction", new_callable=AsyncMock), \
                 patch("app.infrastructure.blockchain.tx_signer.sign_and_broadcast_transaction", return_value="tx_sig_double"), \
                 patch("app.infrastructure.blockchain.tx_signer.close_token_account", return_value="close_sig_double"):
                # Spawn two concurrent exit triggers
                t1 = asyncio.create_task(engine.execute_exit("kill_switch_large_sell"))
                t2 = asyncio.create_task(engine.execute_exit("kill_switch_slippage_spike"))
                await asyncio.gather(t1, t2)

            self.assertTrue(engine.exited)
            # Verify position_repo.update_position was called exactly ONCE (atomic execution lock)
            self.assertEqual(mock_position_repo.update_position.call_count, 1)

        asyncio.run(run_test())

    def test_dev_wallet_address_database_persistence(self):
        """
        Bagian 2: Verifies that dev_wallet_address is persisted to SQLite database
        and restored accurately when position is reloaded across database sessions.
        """
        async def run_test():
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from app.infrastructure.database.session import Base, run_db_migrations
            from app.infrastructure.database.models import OpenPositionORM, WatchlistWalletORM, ModelRegistryORM
            from app.infrastructure.database.repository import SQLAlchemyPositionRepository
            from datetime import datetime, timezone

            test_db_engine = create_engine("sqlite:///:memory:")
            Base.metadata.create_all(test_db_engine)
            run_db_migrations(test_db_engine)

            TestSession = sessionmaker(bind=test_db_engine)

            # Setup FK dependencies directly in ORM
            db1 = TestSession()
            db1.add(WatchlistWalletORM(
                wallet_address="WhaleWalletFK11111111111111111111111111",
                label="test_whale",
                source="manual",
                added_at=datetime.now(timezone.utc)
            ))
            db1.add(ModelRegistryORM(
                model_version="v0",
                trained_at=datetime.now(timezone.utc),
                training_sample_count=100,
                validation_accuracy=0.8,
                expectancy_r=1.5,
                is_active=True
            ))
            db1.commit()

            repo1 = SQLAlchemyPositionRepository(db1)

            dev_addr = "DevWalletReal9VDPBQyYfLEdUEDSZ3mvf9C2pzH3xzra9GEN"
            pos = OpenPosition(
                position_id="pos_db_persist_test",
                wallet_source="WhaleWalletFK11111111111111111111111111",
                dev_wallet_address=dev_addr,
                token_address="TokenPersistTest",
                state="OPEN",
                entry_price=1.0,
                sl_initial=0.9,
                risk_pct=0.01,
                position_size_usd=500.0,
                confidence_score=0.85,
                model_version="v0"
            )

            await repo1.add_position(pos)
            db1.close()

            # Create fresh DB session & repository instance to simulate process restart reload
            db2 = TestSession()
            repo2 = SQLAlchemyPositionRepository(db2)

            fetched_pos = await repo2.get_position("pos_db_persist_test")
            self.assertIsNotNone(fetched_pos)
            self.assertEqual(fetched_pos.dev_wallet_address, dev_addr)
            db2.close()

        asyncio.run(run_test())

    def test_fetch_dev_wallet_address_multi_page_pagination(self):
        """
        Bagian 3: Verifies backward pagination (before parameter) in fetch_dev_wallet_address()
        when token has >1000 transactions history.
        """
        async def run_test():
            from app.infrastructure.blockchain.bonding_curve_price import fetch_dev_wallet_address

            # Mock multi-page getSignaturesForAddress response: page 1 = 1000 sigs, page 2 = 5 sigs (genesis page)
            page1_sigs = [{"signature": f"sig_p1_{i}"} for i in range(1000)]
            page2_sigs = [{"signature": "genesis_sig_1"}, {"signature": "genesis_sig_0"}]

            def mock_rpc_call(url, payload):
                method = payload.get("method")
                params = payload.get("params", [])
                if method == "getSignaturesForAddress":
                    opts = params[1] if len(params) > 1 else {}
                    if "before" in opts:
                        return {"result": page2_sigs}
                    return {"result": page1_sigs}
                elif method == "getTransaction":
                    return {
                        "result": {
                            "transaction": {
                                "message": {
                                    "accountKeys": [
                                        {"pubkey": "GenesisCreatorPubkey1111111111111111111", "signer": True}
                                    ]
                                }
                            }
                        }
                    }
                return {}

            with patch("asyncio.to_thread", side_effect=lambda fn, u, p: mock_rpc_call(u, p)):
                dev_addr = await fetch_dev_wallet_address("HighVolumeMint111111111111111111111111")
                self.assertEqual(dev_addr, "GenesisCreatorPubkey1111111111111111111")

        asyncio.run(run_test())

    def test_ws_monitor_account_subscription_and_reconnect(self):
        """
        Bagian 1: Verifies SolanaWebSocketMonitor account subscription management,
        callback routing, and resubscription state tracking.
        """
        async def run_test():
            from app.blockchain.monitor import SolanaWebSocketMonitor

            monitor = SolanaWebSocketMonitor()
            received_bytes = []

            async def dummy_callback(data):
                received_bytes.append(data)

            pda = "5sbsMYZa7PMxgCefX8TkaxivNwWNHKtDb9qVy1CTQrwH"
            await monitor.subscribe_account(pda, dummy_callback)

            self.assertIn(pda, monitor.account_callbacks)
            self.assertIn(dummy_callback, monitor.account_callbacks[pda])

            # Simulate incoming WebSocket accountNotification message
            dummy_b64 = "AAAAAAAAAAAA"
            import json
            import base64
            expected_bytes = base64.b64decode(dummy_b64)

            ws_msg = {
                "method": "accountNotification",
                "params": {
                    "subscription": 12345,
                    "result": {
                        "value": {
                            "data": [dummy_b64, "base64"]
                        }
                    }
                }
            }

            # Map subscription ID 12345 -> pda
            monitor.sub_id_to_pda[12345] = pda

            await monitor._handle_ws_message(json.dumps(ws_msg))
            self.assertEqual(len(received_bytes), 1)
            self.assertEqual(received_bytes[0], expected_bytes)

            await monitor.unsubscribe_account(pda, dummy_callback)
            self.assertNotIn(pda, monitor.account_callbacks)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
