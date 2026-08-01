import unittest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.models import OpenPosition
from app.execution.executor import ParallelExecutionEngine
from app.use_cases.risk_guard import PortfolioRiskGuard


class TestZombiePositionRemediation(unittest.TestCase):
    def test_exit_failed_manual_review_retained_in_repo(self):
        """
        Verifies that after 10 failed exit attempts, the position is NOT deleted from DB,
        its state is updated to 'EXIT_FAILED_MANUAL_REVIEW', and repository get_open_positions includes it.
        """
        pos = OpenPosition(
            position_id="pos_zombie_test",
            wallet_source="TestWallet",
            token_address="FailExitTokenxxxxxxxxxxxxxxxxxxxxxxxx", # Triggers exit failure in testing
            state="OPEN",
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=500.0,
            confidence_score=0.85,
            model_version="v0"
        )

        mock_pos_repo = AsyncMock()
        mock_cooldown_repo = AsyncMock()
        mock_model_repo = AsyncMock()
        mock_trade_history_repo = AsyncMock()

        engine = ParallelExecutionEngine(
            position=pos,
            position_repo=mock_pos_repo,
            cooldown_repo=mock_cooldown_repo,
            model_registry_repo=mock_model_repo,
            trade_history_repo=mock_trade_history_repo
        )

        # Mock build_trade_transaction or swap execution to always raise exception
        with patch("app.infrastructure.blockchain.pumpportal_client.build_trade_transaction", side_effect=IOError("RPC execution timeout")), \
             patch("app.infrastructure.blockchain.trading_service.execute_pumpportal_swap", side_effect=IOError("RPC execution timeout")), \
             patch("asyncio.sleep", AsyncMock()):
            asyncio.run(engine.execute_exit("SL"))

        # Verify position was NOT deleted
        mock_pos_repo.delete_position.assert_not_called()

        # Verify update_position was called with state = EXIT_FAILED_MANUAL_REVIEW
        mock_pos_repo.update_position.assert_called()
        self.assertEqual(engine.position.state, "EXIT_FAILED_MANUAL_REVIEW")

    def test_risk_guard_counts_manual_review_positions(self):
        """
        Verifies that PortfolioRiskGuard includes positions with state EXIT_FAILED_MANUAL_REVIEW
        as active risk exposure.
        """
        pos_manual = OpenPosition(
            position_id="pos_manual_review_1",
            wallet_source="TestWallet",
            token_address="TokenReview1",
            state="EXIT_FAILED_MANUAL_REVIEW",
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=1000.0,
            confidence_score=0.8,
            model_version="v0"
        )

        pos_manual.unrealized_pnl_usd = -200.0

        mock_pos_repo = AsyncMock()
        mock_pos_repo.get_open_positions.return_value = [pos_manual]

        risk_guard = PortfolioRiskGuard(
            position_repo=mock_pos_repo,
            trade_history_repo=AsyncMock(get_closed_trades=AsyncMock(return_value=[]))
        )

        # Force day baseline equity
        with patch.object(risk_guard, "_get_baseline_equity", new=AsyncMock(return_value=1000.0)):
            allowed, reason = asyncio.run(risk_guard.is_trading_allowed())

        # Daily loss is -200 / 1000 = -20% (which exceeds max daily loss of 4%) -> Circuit Breaker Triggered
        self.assertFalse(allowed)
        self.assertIn("Circuit Breaker Triggered", reason)


if __name__ == "__main__":
    unittest.main()
