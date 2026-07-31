import os
import sys
import unittest
import numpy as np
import pandas as pd
from unittest.mock import AsyncMock, MagicMock

from app.ml_pipeline.training_utils import stratified_train_test_split, compute_class_sample_weights
from scripts.walk_forward_eval import run_walk_forward_eval


class TestModelValidityFase2(unittest.TestCase):
    def test_stratified_train_test_split_proportions(self):
        # Create synthetic imbalanced dataset: 100 samples
        # Class 0 (HOLD): 20 samples (20%)
        # Class 1 (BUY_BENAR): 30 samples (30%)
        # Class 2 (SALAH): 50 samples (50%)
        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = np.array([0] * 20 + [1] * 30 + [2] * 50)

        X_train, X_val, y_train, y_val = stratified_train_test_split(X, y, test_size=0.20, random_state=42)

        # Check total sizes
        self.assertEqual(len(y_train), 80)
        self.assertEqual(len(y_val), 20)

        # Check class ratios in val set:
        # 20% of 20 = 4 HOLD
        # 20% of 30 = 6 BUY_BENAR
        # 20% of 50 = 10 SALAH
        self.assertEqual(np.sum(y_val == 0), 4)
        self.assertEqual(np.sum(y_val == 1), 6)
        self.assertEqual(np.sum(y_val == 2), 10)

        # Check class ratios in train set:
        self.assertEqual(np.sum(y_train == 0), 16)
        self.assertEqual(np.sum(y_train == 1), 24)
        self.assertEqual(np.sum(y_train == 2), 40)

    def test_compute_class_sample_weights(self):
        labels = np.array([0, 1, 1, 2, 2, 2])
        weights = compute_class_sample_weights(labels, num_class=3)

        self.assertEqual(len(weights), 6)
        # Class 0 frequency = 1/6, Class 1 = 2/6, Class 2 = 3/6
        # Class weights = Total / (num_class * count)
        # Class 0 weight = 6 / (3 * 1) = 2.0
        # Class 1 weight = 6 / (3 * 2) = 1.0
        # Class 2 weight = 6 / (3 * 3) = 0.666...
        self.assertAlmostEqual(weights[0], 2.0)
        self.assertAlmostEqual(weights[1], 1.0)
        self.assertAlmostEqual(weights[3], 2/3)

    def test_cluster_score_multi_wallet_detection(self):
        from datetime import datetime, timezone, timedelta
        from app.ml_pipeline.bootstrap import ReconstructedPosition, TokenMarketSnapshot, HistoricalModelBootstrapService

        now = datetime.now(timezone.utc)
        snap = TokenMarketSnapshot(price_usd=1.0, liquidity_usd=10000.0, volume_24h=2000.0)

        # Position 1: Wallet A buys Token XYZ at T
        pos1 = ReconstructedPosition(
            wallet_address="WalletA1111111111111111111111111111111111",
            token_mint="TokenXYZ1111111111111111111111111111111111",
            entry_signature="sigA",
            exit_signature="sigA_exit",
            entry_ts=now,
            exit_ts=now + timedelta(minutes=20),
            amount_token=100.0,
            entry_snapshot=snap,
            exit_snapshot=snap
        )

        # Position 2: Wallet B (DIFFERENT wallet) buys SAME Token XYZ at T + 2 minutes
        pos2 = ReconstructedPosition(
            wallet_address="WalletB2222222222222222222222222222222222",
            token_mint="TokenXYZ1111111111111111111111111111111111",
            entry_signature="sigB",
            exit_signature="sigB_exit",
            entry_ts=now + timedelta(minutes=2),
            exit_ts=now + timedelta(minutes=22),
            amount_token=100.0,
            entry_snapshot=snap,
            exit_snapshot=snap
        )

        svc = HistoricalModelBootstrapService()

        # Test with both positions in all_positions -> cluster_score MUST BE 1.0
        feat1 = svc._feature_row(pos1, entry_value=500.0, holding_minutes=20, prior_trades=[], all_positions=[pos1, pos2])
        self.assertEqual(feat1["cluster_score"], 1.0)

        # Test with single position in all_positions -> cluster_score MUST BE 0.0
        feat_single = svc._feature_row(pos1, entry_value=500.0, holding_minutes=20, prior_trades=[], all_positions=[pos1])
        self.assertEqual(feat_single["cluster_score"], 0.0)

    def test_walk_forward_eval_execution(self):
        """
        Verifies end-to-end walk-forward evaluation logic across multi-window out-of-time folds.
        Mocks trade history repository with 25 realistic ClosedTrade objects spanning 21 days.
        """
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch, AsyncMock
        from app.domain.models import ClosedTrade

        base_time = datetime.now(timezone.utc) - timedelta(days=21)
        mock_trades = []
        for i in range(25):
            t_entry = base_time + timedelta(days=i * 0.8)
            mock_trades.append(
                ClosedTrade(
                    trade_id=f"wf_trade_{i}",
                    wallet_source=f"Wallet_{i % 3}",
                    token_address=f"TokenMint_{i % 4}",
                    token_symbol="PUMP",
                    signal_ts=t_entry,
                    entry_ts=t_entry,
                    exit_ts=t_entry + timedelta(minutes=15),
                    direction="BUY",
                    confidence_score=0.85,
                    safety_check_passed=True,
                    entry_price=1.0,
                    exit_price=1.2 if i % 2 == 0 else 0.8,
                    position_size_usd=100.0,
                    risk_pct=0.01,
                    pnl_pct_actual=0.20 if i % 2 == 0 else -0.20,
                    r_multiple=2.0 if i % 2 == 0 else -2.0,
                    label="BUY_BENAR" if i % 2 == 0 else "SALAH",
                    holding_time_minutes=15,
                    exit_reason="tp_target",
                    is_paper_trade=True,
                    is_bootstrap=True,
                    model_version="v0"
                )
            )

        with patch("app.infrastructure.database.repository.SQLAlchemyTradeHistoryRepository.get_closed_trades", new=AsyncMock(return_value=mock_trades)):
            trades_result = run_walk_forward_eval(window_days=14, step_days=7)
            self.assertIsNotNone(trades_result)
            self.assertGreaterEqual(len(trades_result), 20)


if __name__ == "__main__":
    unittest.main()
