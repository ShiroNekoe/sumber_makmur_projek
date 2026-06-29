import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.core.config import settings
from app.ml_pipeline.bootstrap import (
    BootstrapDataUnavailable,
    HistoricalModelBootstrapService,
    HistoricalSwapEvent,
    TokenMarketSnapshot,
)


class FakeHistoricalTransactionSource:
    def __init__(self, events=None, fail=False):
        self.events = events or []
        self.fail = fail

    async def fetch_wallet_events(self, wallet_address: str, history_days: int):
        if self.fail:
            raise BootstrapDataUnavailable("rpc down")
        return [event for event in self.events if event.wallet_address == wallet_address]


class FakeHistoricalPriceProvider:
    def __init__(self, snapshots):
        self.snapshots = snapshots

    async def get_snapshot(self, token_mint: str, timestamp: datetime):
        return self.snapshots.get((token_mint, timestamp))


class TestHistoricalModelBootstrapService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_target_wallets = settings.TARGET_WALLETS
        self.old_risk = settings.RISK_PCT_PER_TRADE
        settings.TARGET_WALLETS = ["wallet_a"]
        settings.RISK_PCT_PER_TRADE = 0.01

        self.registry_repo = MagicMock()
        self.registry_repo.get_model_version = AsyncMock(return_value=None)
        self.registry_repo.add_model_version = AsyncMock()
        self.registry_repo.update_model_version = AsyncMock()

        self.trade_repo = MagicMock()
        self.trade_repo.add_closed_trade = AsyncMock()

    def tearDown(self):
        settings.TARGET_WALLETS = self.old_target_wallets
        settings.RISK_PCT_PER_TRADE = self.old_risk
        shutil.rmtree(self.test_dir)

    async def test_bootstrap_reconstructs_historical_trades_and_persists_v0(self):
        base = datetime(2026, 6, 1, tzinfo=timezone.utc)
        events = [
            HistoricalSwapEvent("wallet_a", "sig_buy_win", "token_win", "BUY", 10.0, base),
            HistoricalSwapEvent("wallet_a", "sig_sell_win", "token_win", "SELL", 10.0, base + timedelta(hours=2)),
            HistoricalSwapEvent("wallet_a", "sig_buy_loss", "token_loss", "BUY", 5.0, base + timedelta(hours=3)),
            HistoricalSwapEvent("wallet_a", "sig_sell_loss", "token_loss", "SELL", 5.0, base + timedelta(hours=5)),
            HistoricalSwapEvent("wallet_a", "sig_buy_hold", "token_hold", "BUY", 8.0, base + timedelta(hours=6)),
            HistoricalSwapEvent("wallet_a", "sig_sell_hold", "token_hold", "SELL", 8.0, base + timedelta(hours=7)),
        ]
        snapshots = {
            ("token_win", events[0].timestamp): TokenMarketSnapshot(1.00, 10000.0, 2000.0, base - timedelta(days=5)),
            ("token_win", events[1].timestamp): TokenMarketSnapshot(1.05, 10000.0, 2000.0, base - timedelta(days=5)),
            ("token_loss", events[2].timestamp): TokenMarketSnapshot(1.00, 9000.0, 1200.0, base - timedelta(days=3)),
            ("token_loss", events[3].timestamp): TokenMarketSnapshot(0.98, 9000.0, 1200.0, base - timedelta(days=3)),
            ("token_hold", events[4].timestamp): TokenMarketSnapshot(1.00, 8000.0, 1000.0, base - timedelta(days=2)),
            ("token_hold", events[5].timestamp): TokenMarketSnapshot(1.005, 8000.0, 1000.0, base - timedelta(days=2)),
        }

        service = HistoricalModelBootstrapService(
            transaction_source=FakeHistoricalTransactionSource(events),
            price_provider=FakeHistoricalPriceProvider(snapshots),
            history_days=30,
            min_trades_warning=50,
        )

        success = await service.bootstrap_model_v0(
            models_dir=self.test_dir,
            model_registry_repo=self.registry_repo,
            trade_history_repo=self.trade_repo,
        )

        self.assertTrue(success)
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "v0.json")))
        self.registry_repo.add_model_version.assert_called_once()
        registry_entry = self.registry_repo.add_model_version.call_args.args[0]
        self.assertEqual(registry_entry.model_version, "v0")
        self.assertTrue(registry_entry.is_active)
        self.assertEqual(registry_entry.training_sample_count, 3)

        self.assertEqual(self.trade_repo.add_closed_trade.await_count, 3)
        trades = [call.args[0] for call in self.trade_repo.add_closed_trade.await_args_list]
        self.assertTrue(all(trade.is_bootstrap for trade in trades))
        self.assertEqual({trade.label for trade in trades}, {"BUY_BENAR", "SALAH", "HOLD"})
        self.assertTrue(all(trade.exit_reason == "bootstrap_reconstructed" for trade in trades))

    async def test_bootstrap_returns_false_when_block_explorer_unavailable(self):
        service = HistoricalModelBootstrapService(
            transaction_source=FakeHistoricalTransactionSource(fail=True),
            price_provider=FakeHistoricalPriceProvider({}),
        )

        success = await service.bootstrap_model_v0(
            models_dir=self.test_dir,
            model_registry_repo=self.registry_repo,
            trade_history_repo=self.trade_repo,
        )

        self.assertFalse(success)
        self.registry_repo.add_model_version.assert_not_called()
        self.trade_repo.add_closed_trade.assert_not_called()
