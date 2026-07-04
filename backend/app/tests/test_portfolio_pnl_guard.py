import unittest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.domain.models import FeatureVector, PredictionResult, OpenPosition, ClosedTrade, CooldownState
from app.use_cases.trade_guard import TradeGuard
from app.use_cases.pnl_calculator import PnLCalculator
from app.use_cases.portfolio_service import PortfolioService


class TestPortfolioPnLGuard(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock repositories for TradeGuard
        self.mock_position_repo = MagicMock()
        self.mock_cooldown_repo = MagicMock()
        self.mock_trade_history_repo = MagicMock()
        
        self.trade_guard = TradeGuard(
            position_repo=self.mock_position_repo,
            cooldown_repo=self.mock_cooldown_repo
        )
        
        self.pred = PredictionResult(
            direction="BUY",
            confidence_score=0.85,
            target_price_estimate=0.50,
            token_address="SafeTokenxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            wallet_source="Wha1eA11111111111111111111111111111111111",
            signature="sig_1",
            timestamp=datetime.now(timezone.utc)
        )
        
        self.fv = FeatureVector(
            token_address="SafeTokenxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            wallet_source="Wha1eA11111111111111111111111111111111111",
            signature="sig_1",
            timestamp=datetime.now(timezone.utc),
            position_size_usd=150.0,
            token_age_minutes=100.0,
            liquidity_pool_depth=25000.0,
            slippage_actual=0.01,
            cluster_score=0.0,
            win_rate_30d=0.5,
            avg_holding_time_minutes=20.0,
            typical_trade_size_usd=500.0,
            past_exit_pattern_score=0.0,
            sol_usd_momentum=0.0,
            token_volume_liquidity_ratio=0.2,
            hour_of_day_utc=12
        )

    # ─── Trade Guard Tests ───────────────────────────────────────────────────

    async def test_trade_guard_success_path(self):
        # Setup mocks for success path
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[])
        self.mock_cooldown_repo.get_cooldown = AsyncMock(return_value=None)
        
        allowed, reason = await self.trade_guard.validate_trade(
            prediction=self.pred,
            feature_vector=self.fv,
            sol_balance=2.0,
            position_size_usd=150.0,
            sol_price_usd=150.0
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "Trade validation passed.")

    async def test_trade_guard_slippage_cap_rejected(self):
        # Slippage too high (> 15%)
        self.fv.slippage_actual = 0.16
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[])
        self.mock_cooldown_repo.get_cooldown = AsyncMock(return_value=None)
        
        allowed, reason = await self.trade_guard.validate_trade(
            prediction=self.pred,
            feature_vector=self.fv,
            sol_balance=2.0,
            position_size_usd=150.0,
            sol_price_usd=150.0
        )
        self.assertFalse(allowed)
        self.assertIn("exceeds strict 15% system cap", reason)

    async def test_trade_guard_duplicate_idempotency_rejected(self):
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[])
        
        # Cooldown entry exists within 5 mins
        cooldown_entry = CooldownState(
            wallet_address=self.pred.wallet_source,
            token_address=self.pred.token_address,
            last_trigger_ts=datetime.now(timezone.utc) - timedelta(minutes=2)
        )
        self.mock_cooldown_repo.get_cooldown = AsyncMock(return_value=cooldown_entry)
        
        allowed, reason = await self.trade_guard.validate_trade(
            prediction=self.pred,
            feature_vector=self.fv,
            sol_balance=2.0,
            position_size_usd=150.0,
            sol_price_usd=150.0
        )
        self.assertFalse(allowed)
        self.assertIn("Idempotency Guard", reason)

    async def test_trade_guard_insufficient_sol_balance_rejected(self):
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[])
        self.mock_cooldown_repo.get_cooldown = AsyncMock(return_value=None)
        
        # Low SOL balance (0.01 SOL)
        allowed, reason = await self.trade_guard.validate_trade(
            prediction=self.pred,
            feature_vector=self.fv,
            sol_balance=0.01,
            position_size_usd=150.0,
            sol_price_usd=150.0
        )
        self.assertFalse(allowed)
        self.assertIn("Insufficient SOL balance", reason)

    # ─── PnL Calculator Tests ────────────────────────────────────────────────

    async def test_realized_pnl_calculation(self):
        # Mock closed trades in DB
        trade1 = ClosedTrade(
            trade_id="tr_1",
            wallet_source="wallet_source",
            token_address="token_addr",
            token_symbol="TKN1",
            signal_ts=datetime.now(timezone.utc),
            entry_ts=datetime.now(timezone.utc),
            exit_ts=datetime.now(timezone.utc),
            direction="BUY",
            confidence_score=0.85,
            safety_check_passed=True,
            entry_price=1.0,
            exit_price=1.2,
            position_size_usd=100.0,
            risk_pct=0.01,
            pnl_pct_actual=0.20, # +20%
            r_multiple=2.0,
            label="BUY_BENAR",
            holding_time_minutes=10,
            exit_reason="TP",
            is_paper_trade=True,
            model_version="v0"
        )
        trade2 = ClosedTrade(
            trade_id="tr_2",
            wallet_source="wallet_source",
            token_address="token_addr2",
            token_symbol="TKN2",
            signal_ts=datetime.now(timezone.utc),
            entry_ts=datetime.now(timezone.utc),
            exit_ts=datetime.now(timezone.utc),
            direction="BUY",
            confidence_score=0.80,
            safety_check_passed=True,
            entry_price=2.0,
            exit_price=1.8,
            position_size_usd=200.0,
            risk_pct=0.01,
            pnl_pct_actual=-0.10, # -10%
            r_multiple=-1.0,
            label="SALAH",
            holding_time_minutes=15,
            exit_reason="SL",
            is_paper_trade=True,
            model_version="v0"
        )
        
        self.mock_trade_history_repo.get_closed_trades = AsyncMock(return_value=[trade1, trade2])
        
        # Mock portfolio service
        mock_portfolio_service = MagicMock(spec=PortfolioService)
        
        pnl_calc = PnLCalculator(
            position_repo=self.mock_position_repo,
            trade_history_repo=self.mock_trade_history_repo,
            portfolio_service=mock_portfolio_service
        )
        
        realized = await pnl_calc.calculate_realized_pnl()
        # trade1: 100 * 0.20 = +20 USD
        # trade2: 200 * -0.10 = -20 USD
        # Total: 0 USD
        self.assertEqual(realized, 0.0)

    async def test_portfolio_history_calculation(self):
        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[])
        self.mock_trade_history_repo.get_closed_trades = AsyncMock(return_value=[])
        
        mock_portfolio_service = MagicMock(spec=PortfolioService)
        mock_portfolio_service.get_token_holdings = AsyncMock(return_value=[
            {
                "mint": "So11111111111111111111111111111111111111112",
                "amount": 2.0,
                "decimals": 9,
                "symbol": "SOL",
                "name": "Solana",
                "price_usd": 100.0,
                "value_usd": 200.0
            }
        ])
        
        # Test Case 1: No DB session provided (returns fallback current point)
        pnl_calc_no_db = PnLCalculator(
            position_repo=self.mock_position_repo,
            trade_history_repo=self.mock_trade_history_repo,
            portfolio_service=mock_portfolio_service,
            db_session=None
        )
        summary_no_db = await pnl_calc_no_db.get_portfolio_summary("some_pubkey")
        self.assertIn("history_1d", summary_no_db)
        self.assertIn("history_7d", summary_no_db)
        self.assertEqual(len(summary_no_db["history_1d"]), 1)
        self.assertEqual(summary_no_db["history_1d"][0]["value_usd"], 200.0)
        
        # Test Case 2: DB session provided with mock snapshots
        mock_db = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.timestamp = datetime.now(timezone.utc) - timedelta(hours=2)
        mock_snapshot.portfolio_value_usd = 150.0
        mock_snapshot.total_pnl_usd = 10.0
        mock_snapshot.sol_balance = 1.5
        
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_snapshot]
        
        pnl_calc_db = PnLCalculator(
            position_repo=self.mock_position_repo,
            trade_history_repo=self.mock_trade_history_repo,
            portfolio_service=mock_portfolio_service,
            db_session=mock_db
        )
        summary_db = await pnl_calc_db.get_portfolio_summary("some_pubkey")
        self.assertIn("history_1d", summary_db)
        # It should contain the mock snapshot + the current state (since they are different / time passed)
        self.assertGreaterEqual(len(summary_db["history_1d"]), 1)
        self.assertEqual(summary_db["history_1d"][0]["value_usd"], 150.0)

