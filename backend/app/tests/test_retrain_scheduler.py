import unittest
import asyncio
import numpy as np
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta

from app.domain.models import ClosedTrade, ModelRegistry
from app.use_cases.retrain_scheduler import RetrainScheduler
from app.ml_pipeline.inference import compute_class_sample_weights
from app.websocket.manager import manager as ws_manager


class TestRetrainScheduler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock repositories & service
        self.mock_trade_history_repo = MagicMock()
        self.mock_trade_history_repo.get_closed_trades = AsyncMock(return_value=[])
        
        self.mock_model_registry_repo = MagicMock()
        self.mock_model_registry_repo.get_active_model = AsyncMock(return_value=None)
        self.mock_model_registry_repo.add_model_version = AsyncMock()
        self.mock_model_registry_repo.update_model_version = AsyncMock()

        self.mock_inference_engine = MagicMock()

        self.scheduler = RetrainScheduler(
            trade_history_repo=self.mock_trade_history_repo,
            model_registry_repo=self.mock_model_registry_repo,
            inference_engine=self.mock_inference_engine
        )

        self.original_broadcast = ws_manager.broadcast
        ws_manager.broadcast = AsyncMock()

    def tearDown(self):
        ws_manager.broadcast = self.original_broadcast

    async def test_retrain_skipped_due_to_insufficient_data(self):
        # 0 trades in repo -> should skip retraining
        success = await self.scheduler.retrain_model_if_needed(force=True)
        self.assertFalse(success)
        self.mock_model_registry_repo.add_model_version.assert_not_called()

    async def test_retrain_success_with_adequate_data(self):
        # Create 120 mock closed trades
        mock_trades = []
        for i in range(120):
            # Class 1: BUY_BENAR, Class 2: SALAH, Class 0: HOLD
            if i % 3 == 0:
                label = "BUY_BENAR"
                r_mult = 3.5
            elif i % 3 == 1:
                label = "SALAH"
                r_mult = -1.2
            else:
                label = "HOLD"
                r_mult = 0.5

            mock_trades.append(
                ClosedTrade(
                    trade_id=f"tr_{i}",
                    wallet_source="Wha1eA11111111111111111111111111111111111",
                    token_address=f"token_{i}xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                    token_symbol="SIM",
                    signal_ts=datetime.now(timezone.utc) - timedelta(days=10),
                    entry_ts=datetime.now(timezone.utc) - timedelta(days=10),
                    exit_ts=datetime.now(timezone.utc) - timedelta(days=10),
                    direction="BUY",
                    confidence_score=0.80,
                    safety_check_passed=True,
                    entry_price=1.0,
                    exit_price=1.0 + r_mult * 0.01,
                    position_size_usd=100.0,
                    risk_pct=0.01,
                    pnl_pct_actual=r_mult * 0.01,
                    r_multiple=r_mult,
                    label=label,
                    holding_time_minutes=20,
                    exit_reason="manual",
                    is_paper_trade=True,
                    is_bootstrap=False,
                    model_version="v0"
                )
            )

        self.mock_trade_history_repo.get_closed_trades = AsyncMock(return_value=mock_trades)
        
        # Test retrain success
        success = await self.scheduler.retrain_model_if_needed(force=True)
        self.assertTrue(success)
        self.mock_model_registry_repo.add_model_version.assert_called_once()

    def test_compute_class_sample_weights_inverse_frequency(self):
        """
        Perbaikan: class imbalance handling untuk dataset multi-class
        (sesuai 03 - Pipeline AI, dokumen sumber: 'scale_pos_weight agar
        model tak hanya aman memprediksi SALAH/HOLD').
        """
        # 80 SALAH (kelas 2), 10 BUY_BENAR (kelas 1), 10 HOLD (kelas 0)
        # -- merepresentasikan win rate rendah by design seperti disebut
        # dokumen sumber.
        labels = np.array([2] * 80 + [1] * 10 + [0] * 10)
        weights = compute_class_sample_weights(labels, num_class=3)

        weight_hold = weights[labels == 0][0]
        weight_buy_benar = weights[labels == 1][0]
        weight_salah = weights[labels == 2][0]

        # Kelas minoritas (BUY_BENAR, HOLD) harus mendapat weight lebih
        # besar dari kelas mayoritas (SALAH) -- inilah efek yang membuat
        # model tidak "aman" hanya memprediksi SALAH/HOLD.
        self.assertGreater(weight_buy_benar, weight_salah)
        self.assertGreater(weight_hold, weight_salah)
        # BUY_BENAR dan HOLD punya jumlah sampel sama -> weight harus sama
        self.assertAlmostEqual(weight_buy_benar, weight_hold)
        # Rata-rata weight = 1.0 (normalisasi) -> skala loss keseluruhan
        # tidak berubah drastis dibanding training tanpa weight.
        self.assertAlmostEqual(float(weights.mean()), 1.0, places=6)

    def test_compute_class_sample_weights_balanced_dataset_is_uniform(self):
        """Dataset yang sudah balanced (seperti dataset bootstrap v0 40/40/40)
        seharusnya menghasilkan weight uniform 1.0 untuk semua sampel --
        artinya penambahan sample weight ini tidak mengubah perilaku
        bootstrap v0 yang sudah ada dan sudah diuji terpisah."""
        labels = np.array([0] * 40 + [1] * 40 + [2] * 40)
        weights = compute_class_sample_weights(labels, num_class=3)
        self.assertTrue(np.allclose(weights, 1.0))

    async def test_retrain_succeeds_with_extreme_class_imbalance(self):
        """
        Dataset closed trades realistis: SALAH mendominasi (sesuai 'win
        rate rendah by design' di dokumen sumber), BUY_BENAR hanya sedikit
        di atas ambang minimum (min_buy_benar_in_alt=15). Memverifikasi
        retraining tetap berhasil dan tidak error walau distribusi label
        sangat tidak seimbang -- bukan sekadar memverifikasi nilai weight
        secara terisolasi.
        """
        mock_trades = []
        # 90 SALAH, 15 BUY_BENAR, 15 HOLD = 120 total (>= min_closed_trades_first)
        label_plan = (["SALAH"] * 90) + (["BUY_BENAR"] * 15) + (["HOLD"] * 15)
        for i, label in enumerate(label_plan):
            r_mult = 3.5 if label == "BUY_BENAR" else (-1.2 if label == "SALAH" else 0.5)
            mock_trades.append(
                ClosedTrade(
                    trade_id=f"tr_imb_{i}",
                    wallet_source="Wha1eA11111111111111111111111111111111111",
                    token_address=f"token_imb_{i}xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                    token_symbol="SIM",
                    signal_ts=datetime.now(timezone.utc) - timedelta(days=10),
                    entry_ts=datetime.now(timezone.utc) - timedelta(days=10),
                    exit_ts=datetime.now(timezone.utc) - timedelta(days=10),
                    direction="BUY",
                    confidence_score=0.80,
                    safety_check_passed=True,
                    entry_price=1.0,
                    exit_price=1.0 + r_mult * 0.01,
                    position_size_usd=100.0,
                    risk_pct=0.01,
                    pnl_pct_actual=r_mult * 0.01,
                    r_multiple=r_mult,
                    label=label,
                    holding_time_minutes=20,
                    exit_reason="manual",
                    is_paper_trade=True,
                    is_bootstrap=False,
                    model_version="v0"
                )
            )

        self.mock_trade_history_repo.get_closed_trades = AsyncMock(return_value=mock_trades)

        success = await self.scheduler.retrain_model_if_needed(force=True)
        self.assertTrue(success)
        self.mock_model_registry_repo.add_model_version.assert_called_once()

        # Validasi val_accuracy yang teregistrasi tetap berada di rentang
        # probabilitas yang valid (sanity check bahwa weighted training
        # tidak menghasilkan model yang corrupt/divergen).
        args, _ = self.mock_model_registry_repo.add_model_version.call_args
        registry_entry = args[0]
        self.assertGreaterEqual(registry_entry.validation_accuracy, 0.0)
        self.assertLessEqual(registry_entry.validation_accuracy, 1.0)