import unittest
from datetime import datetime, timezone, timedelta
from app.core.config import settings
from app.domain.cluster_logic import compute_cluster_score, GenericTradeEvent
from app.ml_pipeline.bootstrap import ReconstructedPosition, TokenMarketSnapshot


class TestClusterLogicSharedFunction(unittest.TestCase):
    def test_shared_cluster_score_parity_between_train_and_serve(self):
        """
        Verifies that training-path (bootstrap ReconstructedPosition events) and
        serving-path (inference / trigger events) produce 100% IDENTICAL outputs
        when evaluated against equivalent trade inputs.
        """
        now = datetime.now(timezone.utc)
        target_wallet = "WalletWhaleA11111111111111111111111111111"
        other_wallet = "WalletWhaleB22222222222222222222222222222"
        target_token = "MemeTokenABC11111111111111111111111111111"

        snap = TokenMarketSnapshot(price_usd=1.0, liquidity_usd=10000.0, volume_24h=2000.0)

        # 1. Training-path data structure: ReconstructedPosition
        train_event_1 = ReconstructedPosition(
            wallet_address=target_wallet,
            token_mint=target_token,
            entry_signature="sigA",
            exit_signature="sigA_exit",
            entry_ts=now,
            exit_ts=now + timedelta(minutes=15),
            amount_token=100.0,
            entry_snapshot=snap,
            exit_snapshot=snap
        )
        train_event_2 = ReconstructedPosition(
            wallet_address=other_wallet,
            token_mint=target_token,
            entry_signature="sigB",
            exit_signature="sigB_exit",
            entry_ts=now + timedelta(minutes=3),  # 3 mins later (within window)
            exit_ts=now + timedelta(minutes=18),
            amount_token=100.0,
            entry_snapshot=snap,
            exit_snapshot=snap
        )

        train_score = compute_cluster_score(
            target_wallet=target_wallet,
            target_token=target_token,
            target_timestamp=now,
            events=[train_event_1, train_event_2],
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        # 2. Serving-path data structure: GenericTradeEvent / Dict payload
        serve_event_1 = {
            "wallet_address": target_wallet,
            "token_address": target_token,
            "timestamp": now
        }
        serve_event_2 = {
            "wallet_address": other_wallet,
            "token_address": target_token,
            "timestamp": now + timedelta(minutes=3)
        }

        serve_score = compute_cluster_score(
            target_wallet=target_wallet,
            target_token=target_token,
            target_timestamp=now,
            events=[serve_event_1, serve_event_2],
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        # PARITY CHECK: Both MUST yield 1.0 (multi-wallet cluster detected)
        self.assertEqual(train_score, 1.0)
        self.assertEqual(serve_score, 1.0)
        self.assertEqual(train_score, serve_score)

    def test_single_wallet_returns_zero_on_both_paths(self):
        now = datetime.now(timezone.utc)
        target_wallet = "WalletWhaleA11111111111111111111111111111"
        target_token = "SingleTokenXYZ11111111111111111111111111"

        snap = TokenMarketSnapshot(price_usd=1.0, liquidity_usd=10000.0, volume_24h=2000.0)

        train_single = ReconstructedPosition(
            wallet_address=target_wallet,
            token_mint=target_token,
            entry_signature="sigA",
            exit_signature="sigA_exit",
            entry_ts=now,
            exit_ts=now + timedelta(minutes=15),
            amount_token=100.0,
            entry_snapshot=snap,
            exit_snapshot=snap
        )

        train_score = compute_cluster_score(
            target_wallet=target_wallet,
            target_token=target_token,
            target_timestamp=now,
            events=[train_single],
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        serve_single = {
            "wallet_address": target_wallet,
            "token_address": target_token,
            "timestamp": now
        }

        serve_score = compute_cluster_score(
            target_wallet=target_wallet,
            target_token=target_token,
            target_timestamp=now,
            events=[serve_single],
            window_minutes=settings.TRIGGER_WINDOW_MINUTES
        )

        self.assertEqual(train_score, 0.0)
        self.assertEqual(serve_score, 0.0)
        self.assertEqual(train_score, serve_score)


if __name__ == "__main__":
    unittest.main()
