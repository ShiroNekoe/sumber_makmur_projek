import os
import sys
import unittest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import settings
from app.domain.models import OpenPosition, ClosedTrade, PredictionResult, FeatureVector
from app.use_cases.risk_guard import PortfolioRiskGuard
from app.use_cases.trade_guard import TradeGuard
from app.use_cases.auto_trade_executor import AutoTradeExecutor


class TestPortfolioRiskGuard(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_pos_repo = AsyncMock()
        self.mock_trade_repo = AsyncMock()
        self.mock_cooldown_repo = AsyncMock()
        self.mock_cooldown_repo.get_cooldown = AsyncMock(return_value=None)
        self.mock_model_repo = AsyncMock()

        self.risk_guard = PortfolioRiskGuard(
            position_repo=self.mock_pos_repo,
            trade_history_repo=self.mock_trade_repo
        )

        self.trade_guard = TradeGuard(
            position_repo=self.mock_pos_repo,
            cooldown_repo=self.mock_cooldown_repo,
            risk_guard=self.risk_guard
        )

        now = datetime.now(timezone.utc)
        self.pred = PredictionResult(
            direction="BUY",
            confidence_score=0.85,
            target_price_estimate=0.05,
            token_address="RiskToken111111111111111111111111111111111",
            wallet_source="TestWalletSource",
            signature="test_sig_risk_guard",
            timestamp=now,
            cooldown_already_cleared=False
        )

        self.fv = FeatureVector(
            token_address="RiskToken111111111111111111111111111111111",
            wallet_source="TestWalletSource",
            signature="test_sig_risk_guard",
            timestamp=now,
            position_size_usd=500.0,
            token_age_minutes=15.0,
            liquidity_pool_depth=10000.0,
            slippage_actual=0.01,
            cluster_score=1.0,
            win_rate_30d=0.5,
            avg_holding_time_minutes=15.0,
            typical_trade_size_usd=500.0,
            past_exit_pattern_score=0.0,
            sol_usd_momentum=0.0,
            token_volume_liquidity_ratio=0.5,
            hour_of_day_utc=now.hour
        )

    async def test_trading_allowed_within_limits(self):
        # Setup no loss
        self.mock_trade_repo.get_closed_trades = AsyncMock(return_value=[])
        self.mock_pos_repo.get_open_positions = AsyncMock(return_value=[])

        allowed, reason = await self.risk_guard.is_trading_allowed()
        self.assertTrue(allowed)
        self.assertEqual(reason, "Trading allowed.")

        # TradeGuard validation
        valid, msg = await self.trade_guard.validate_trade(
            prediction=self.pred,
            feature_vector=self.fv,
            sol_balance=5.0,
            position_size_usd=100.0,
            sol_price_usd=150.0
        )
        self.assertTrue(valid)

    async def test_daily_circuit_breaker_triggered(self):
        now = datetime.now(timezone.utc)
        # Mock daily realized loss of -$60 USD on $1000 baseline (6% > 5% max_daily_loss_pct)
        loss_trade = ClosedTrade(
            trade_id="t_loss",
            wallet_source="w1",
            token_address="token1",
            token_symbol="T1",
            signal_ts=now - timedelta(hours=1),
            entry_ts=now - timedelta(hours=1),
            exit_ts=now - timedelta(minutes=10),
            direction="BUY",
            confidence_score=0.8,
            safety_check_passed=True,
            entry_price=10.0,
            exit_price=4.0,
            position_size_usd=100.0,
            risk_pct=0.01,
            pnl_pct_actual=-0.60, # -60 USD
            r_multiple=-1.0,
            label="SALAH",
            holding_time_minutes=50,
            exit_reason="sl_target",
            is_paper_trade=False,
            is_bootstrap=False,
            model_version="v0"
        )

        self.mock_trade_repo.get_closed_trades = AsyncMock(return_value=[loss_trade])
        self.mock_pos_repo.get_open_positions = AsyncMock(return_value=[])

        allowed, reason = await self.risk_guard.is_trading_allowed()
        self.assertFalse(allowed)
        self.assertIn("Circuit Breaker Triggered: Daily loss", reason)

        # Validate TradeGuard blocks trade when circuit breaker is active
        valid, msg = await self.trade_guard.validate_trade(
            prediction=self.pred,
            feature_vector=self.fv,
            sol_balance=5.0,
            position_size_usd=100.0,
            sol_price_usd=150.0
        )
        self.assertFalse(valid)
        self.assertIn("Blocked: Circuit Breaker Triggered", msg)

    async def test_weekly_circuit_breaker_triggered(self):
        fake_now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc) # Wednesday
        tuesday = fake_now - timedelta(days=1)
        
        # Mock weekly loss of -$3.50 USD on $10 baseline (35% > 28% max_weekly_loss_pct)
        # Closed on Tuesday (within week, but before Wednesday's start_of_day)
        loss_trade = ClosedTrade(
            trade_id="t_weekly_loss",
            wallet_source="w1",
            token_address="token1",
            token_symbol="T1",
            signal_ts=tuesday - timedelta(hours=1),
            entry_ts=tuesday - timedelta(hours=1),
            exit_ts=tuesday,
            direction="BUY",
            confidence_score=0.8,
            safety_check_passed=True,
            entry_price=10.0,
            exit_price=6.5,
            position_size_usd=10.0,
            risk_pct=0.01,
            pnl_pct_actual=-0.35, # -3.50 USD
            r_multiple=-1.0,
            label="SALAH",
            holding_time_minutes=60,
            exit_reason="sl_target",
            is_paper_trade=False,
            is_bootstrap=False,
            model_version="v0"
        )

        self.mock_trade_repo.get_closed_trades = AsyncMock(return_value=[loss_trade])
        self.mock_pos_repo.get_open_positions = AsyncMock(return_value=[])

        with patch("app.use_cases.risk_guard.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            allowed, reason = await self.risk_guard.is_trading_allowed()

        self.assertFalse(allowed)
        self.assertIn("Circuit Breaker Triggered: Weekly loss", reason)

    async def test_weekly_circuit_breaker_not_triggered_below_threshold(self):
        fake_now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc) # Wednesday
        tuesday = fake_now - timedelta(days=1)

        # Mock weekly loss of -$2.00 USD on $10 baseline (20% < 28% max_weekly_loss_pct)
        # Closed on Tuesday (within week, but before Wednesday's start_of_day)
        loss_trade = ClosedTrade(
            trade_id="t_weekly_loss_sub",
            wallet_source="w1",
            token_address="token1",
            token_symbol="T1",
            signal_ts=tuesday - timedelta(hours=1),
            entry_ts=tuesday - timedelta(hours=1),
            exit_ts=tuesday,
            direction="BUY",
            confidence_score=0.8,
            safety_check_passed=True,
            entry_price=10.0,
            exit_price=8.0,
            position_size_usd=10.0,
            risk_pct=0.01,
            pnl_pct_actual=-0.20, # -2.00 USD
            r_multiple=-1.0,
            label="SALAH",
            holding_time_minutes=60,
            exit_reason="sl_target",
            is_paper_trade=False,
            is_bootstrap=False,
            model_version="v0"
        )

        self.mock_trade_repo.get_closed_trades = AsyncMock(return_value=[loss_trade])
        self.mock_pos_repo.get_open_positions = AsyncMock(return_value=[])

        with patch("app.use_cases.risk_guard.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            allowed, reason = await self.risk_guard.is_trading_allowed()

        self.assertTrue(allowed)
        self.assertEqual(reason, "Trading allowed.")

    async def test_real_usd_exposure_cap_exceeded(self):
        # Test that AutoTradeExecutor blocks trade entry if total USD exposure exceeds cap ($2500)
        # even if active position count (e.g. 2) is below max positions limit (5)
        now = datetime.now(timezone.utc)
        open_pos1 = OpenPosition(
            position_id="pos1",
            token_address="Token1111111111111111111111111111111111",
            wallet_source="w1",
            state="OPEN",
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=1200.0,
            confidence_score=0.8,
            model_version="v0",
            entry_price=1.0,
            entry_ts=now - timedelta(minutes=10)
        )
        open_pos2 = OpenPosition(
            position_id="pos2",
            token_address="Token2222222222222222222222222222222222",
            wallet_source="w2",
            state="OPEN",
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=1000.0,
            confidence_score=0.8,
            model_version="v0",
            entry_price=1.0,
            entry_ts=now - timedelta(minutes=5)
        )
        # Total existing exposure = $2200. New entry = $500. Total = $2700 > $2500 cap!

        self.mock_pos_repo.get_open_positions = AsyncMock(return_value=[open_pos1, open_pos2])
        self.mock_cooldown_repo.get_cooldown = AsyncMock(return_value=None)

        executor = AutoTradeExecutor(
            position_repo=self.mock_pos_repo,
            cooldown_repo=self.mock_cooldown_repo,
            model_registry_repo=self.mock_model_repo,
            risk_guard=self.risk_guard
        )

        trade = await executor.execute_trade(self.pred, self.fv)
        self.assertIsNone(trade)


if __name__ == "__main__":
    unittest.main()
