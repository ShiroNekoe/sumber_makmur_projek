import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from app.domain.models import OpenPosition
from app.execution.executor import ParallelExecutionEngine
from app.websocket.manager import manager as ws_manager


class TestParallelExecutionEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock repositories
        self.mock_position_repo = MagicMock()
        self.mock_position_repo.update_position = AsyncMock()

        self.mock_cooldown_repo = MagicMock()
        self.mock_cooldown_repo.delete_cooldown = AsyncMock()

        self.mock_model_registry_repo = MagicMock()
        self.mock_trade_history_repo = MagicMock()
        self.mock_trade_history_repo.add_closed_trade = AsyncMock()

        self.position = OpenPosition(
            position_id="pos_1",
            wallet_source="Wha1eA11111111111111111111111111111111111",
            token_address="SafeTokenxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            state="OPEN",
            entry_price=1.0,
            entry_ts=datetime.now(timezone.utc),
            sl_initial=0.90, # 10% distance
            risk_pct=0.01,
            position_size_usd=1000.0,
            trailing_active=False,
            trailing_level=None,
            peak_r_multiple=0.0,
            confidence_score=0.85,
            model_version="v0"
        )

        self.engine = ParallelExecutionEngine(
            position=self.position,
            position_repo=self.mock_position_repo,
            cooldown_repo=self.mock_cooldown_repo,
            model_registry_repo=self.mock_model_registry_repo,
            trade_history_repo=self.mock_trade_history_repo
        )

        # Mock WebSocket broadcast
        self.original_broadcast = ws_manager.broadcast
        ws_manager.broadcast = AsyncMock()

    def tearDown(self):
        ws_manager.broadcast = self.original_broadcast

    async def test_atomic_exit_logic(self):
        # Verify that execute_exit can only run once
        await self.engine.execute_exit("SL")
        self.assertTrue(self.engine.exited)
        self.assertEqual(self.position.state, "CLOSED")
        
        self.mock_trade_history_repo.add_closed_trade.assert_called_once()
        self.mock_cooldown_repo.delete_cooldown.assert_called_once()

        # Execute again, should not call repositories again
        await self.engine.execute_exit("trailing_tp")
        self.mock_trade_history_repo.add_closed_trade.assert_called_once()

    async def test_start_monitoring_spawns_two_independent_tasks(self):
        """
        Perbaikan: tiga lapis proteksi harus berjalan paralel (dokumen
        sumber, 06 - Eksekusi Otomatis: "tiga lapis proteksi berjalan
        paralel, bukan berurutan"). Sebelumnya start_monitoring hanya
        membuat SATU task gabungan. Sekarang harus ada dua task asyncio
        independen: satu untuk Lapis 1&2 (price-based), satu untuk Lapis 3
        (kill-switch) -- dan keduanya benar-benar berjalan sebagai
        asyncio.Task terpisah, bukan dipanggil berurutan dalam satu task.
        """
        await self.engine.start_monitoring()
        try:
            self.assertEqual(len(self.engine.tasks), 3)
            for task in self.engine.tasks:
                self.assertIsInstance(task, asyncio.Task)
                self.assertFalse(task.done())
        finally:
            for task in self.engine.tasks:
                task.cancel()
            await asyncio.gather(*self.engine.tasks, return_exceptions=True)

    async def test_kill_switch_detects_liquidity_drop_not_random(self):
        """
        Perbaikan: Lapis 3 (kill-switch) sebelumnya disimulasikan dengan
        `random.random() < 0.01` -- tidak event-driven dan tidak terkait
        data on-chain sungguhan. Sekarang harus polling
        ITokenInfoService.get_token_info() dan mendeteksi penurunan tajam
        liquidity_pool_depth sebagai sinyal LP removal (kill_switch_lp),
        sesuai threshold KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT di
        config.yaml.
        """
        mock_token_info_service = MagicMock()
        # Baseline tinggi, lalu drop drastis di pembacaan kedua (LP pulled)
        mock_token_info_service.get_token_info = AsyncMock(
            side_effect=[
                {"liquidity_usd": 50000.0, "age_minutes": 100.0, "volume_24h": 1000.0},
                {"liquidity_usd": 2000.0, "age_minutes": 100.0, "volume_24h": 1000.0},  # drop 96%
            ]
        )
        mock_token_safety_service = MagicMock()
        mock_token_safety_service.get_safety_info = AsyncMock(
            return_value={
                "liquidity_locked": True,
                "contract_verified": True,
                "top_10_holders_share": 0.12,
                "mint_authority_revoked": True,
            }
        )

        engine = ParallelExecutionEngine(
            position=self.position,
            position_repo=self.mock_position_repo,
            cooldown_repo=self.mock_cooldown_repo,
            model_registry_repo=self.mock_model_registry_repo,
            trade_history_repo=self.mock_trade_history_repo,
            token_info_service=mock_token_info_service,
            token_safety_service=mock_token_safety_service,
        )

        # Panggilan pertama -> set baseline, belum trigger
        reason_first = await engine._check_onchain_kill_signals()
        self.assertIsNone(reason_first)

        # Panggilan kedua -> liquidity drop drastis -> harus trigger
        reason_second = await engine._check_onchain_kill_signals()
        self.assertEqual(reason_second, "kill_switch_lp")

    async def test_kill_switch_detects_holder_concentration_shift(self):
        """Lapis 3 juga harus mendeteksi holder concentration shift
        (top_10_holders_share melonjak) sebagai sinyal dev/creator wallet
        dump, sesuai KILL_SWITCH_DEV_WALLET_SELL_THRESHOLD_PCT."""
        mock_token_info_service = MagicMock()
        mock_token_info_service.get_token_info = AsyncMock(
            return_value={"liquidity_usd": 50000.0, "age_minutes": 100.0, "volume_24h": 1000.0}
        )
        mock_token_safety_service = MagicMock()
        mock_token_safety_service.get_safety_info = AsyncMock(
            side_effect=[
                {"liquidity_locked": True, "contract_verified": True,
                 "top_10_holders_share": 0.15, "mint_authority_revoked": True},
                {"liquidity_locked": True, "contract_verified": True,
                 "top_10_holders_share": 0.45, "mint_authority_revoked": True},  # +30pp shift
            ]
        )

        engine = ParallelExecutionEngine(
            position=self.position,
            position_repo=self.mock_position_repo,
            cooldown_repo=self.mock_cooldown_repo,
            model_registry_repo=self.mock_model_registry_repo,
            trade_history_repo=self.mock_trade_history_repo,
            token_info_service=mock_token_info_service,
            token_safety_service=mock_token_safety_service,
        )

        reason_first = await engine._check_onchain_kill_signals()
        self.assertIsNone(reason_first)

        reason_second = await engine._check_onchain_kill_signals()
        self.assertEqual(reason_second, "kill_switch_dev_dump")

    async def test_kill_switch_fail_open_when_services_not_injected(self):
        """Backward-compatibility: jika token_info_service/token_safety_service
        tidak disuntikkan (None, seperti pemanggilan lama / test lama),
        kill-switch tidak boleh error -- harus fail-open (return None)."""
        reason = await self.engine._check_onchain_kill_signals()
        self.assertIsNone(reason)

    async def test_execute_exit_cancels_other_running_tasks(self):
        """
        Begitu salah satu lapis trigger exit, task lapis lain yang belum
        trigger harus di-cancel -- tidak boleh terus polling tanpa guna
        setelah posisi closed.
        """
        await self.engine.start_monitoring()

        await self.engine.execute_exit("kill_switch_lp")

        # Beri kesempatan event loop memproses pembatalan task
        await asyncio.sleep(0)
        for task in self.engine.tasks:
            self.assertTrue(task.cancelled() or task.done())

    async def test_two_layers_trigger_concurrently_only_one_exit_recorded(self):
        """
        Simulasi dua lapis trigger nyaris bersamaan (race condition yang
        jadi alasan utama kebutuhan lock atomic di dokumen sumber).
        Walau keduanya memanggil execute_exit() secara konkuren, hanya
        SATU closed_trade yang boleh tercatat.
        """
        results = await asyncio.gather(
            self.engine.execute_exit("SL"),
            self.engine.execute_exit("kill_switch_lp"),
        )
        self.mock_trade_history_repo.add_closed_trade.assert_called_once()
        self.assertEqual(self.position.state, "CLOSED")