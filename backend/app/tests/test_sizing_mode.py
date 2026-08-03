"""
Unit tests for FITUR 1: Fixed Order Size Trading Mode & Small-Capital Guardrails
"""
import unittest
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from app.core.config import Settings
from app.domain.models import PredictionResult, FeatureVector
from app.use_cases.auto_trade_executor import AutoTradeExecutor


class TestSizingMode(unittest.IsolatedAsyncioTestCase):
    
    def test_startup_fail_fast_on_invalid_fixed_size(self):
        """Test config validation raises ValueError if mode='fixed' but fixed_order_size_usd <= 0."""
        s = Settings()
        invalid_config = {
            "trigger_engine": {"window_minutes": 5, "mode": "AND", "min_token_age_minutes": 2.0, "max_token_age_minutes": 30.0, "min_liquidity_usd": 3000.0, "cooldown_seconds": 3600},
            "decision_gate": {"confidence_threshold": 0.50},
            "sizing": {"mode": "fixed", "fixed_order_size_usd": 0.0},
            "risk": {"risk_pct_per_trade": 0.01, "max_concurrent_positions": 3, "max_total_exposure_usd": 10.0},
            "trailing_tp": {"tiers": [{"r_min": 0, "r_max": 1, "trail_pct": None}]},
            "labeling": {"buy_benar_threshold_r": 3.0, "salah_threshold_r": -1.0},
            "retrain": {"schedule_utc": "02:00", "min_closed_trades_first": 100, "min_closed_trades_alt": 50, "min_buy_benar_in_alt": 15, "rolling_window_days": 30, "rollback_accuracy_drop_pct": 0.05},
            "kill_switch": {"dev_wallet_sell_threshold_pct": 0.05, "slippage_spike_threshold_pct": 0.10},
            "rpc": {"primary_url": "https://api.mainnet-beta.solana.com", "secondary_url": "https://api.mainnet-beta.solana.com", "max_retry": 5}
        }
        with self.assertRaises(ValueError):
            s.validate_config(invalid_config)

    def test_startup_fail_fast_on_invalid_mode(self):
        """Test config validation raises ValueError if mode is invalid."""
        s = Settings()
        invalid_config = {
            "trigger_engine": {"window_minutes": 5, "mode": "AND", "min_token_age_minutes": 2.0, "max_token_age_minutes": 30.0, "min_liquidity_usd": 3000.0, "cooldown_seconds": 3600},
            "decision_gate": {"confidence_threshold": 0.50},
            "sizing": {"mode": "invalid_mode", "fixed_order_size_usd": 1.0},
            "risk": {"risk_pct_per_trade": 0.01, "max_concurrent_positions": 3, "max_total_exposure_usd": 10.0},
            "trailing_tp": {"tiers": [{"r_min": 0, "r_max": 1, "trail_pct": None}]},
            "labeling": {"buy_benar_threshold_r": 3.0, "salah_threshold_r": -1.0},
            "retrain": {"schedule_utc": "02:00", "min_closed_trades_first": 100, "min_closed_trades_alt": 50, "min_buy_benar_in_alt": 15, "rolling_window_days": 30, "rollback_accuracy_drop_pct": 0.05},
            "kill_switch": {"dev_wallet_sell_threshold_pct": 0.05, "slippage_spike_threshold_pct": 0.10},
            "rpc": {"primary_url": "https://api.mainnet-beta.solana.com", "secondary_url": "https://api.mainnet-beta.solana.com", "max_retry": 5}
        }
        with self.assertRaises(ValueError):
            s.validate_config(invalid_config)

    def test_startup_fail_fast_on_exposure_cap_contradiction(self):
        """Test config validation raises ValueError if max_total_exposure_usd < fixed_order_size_usd * max_concurrent_positions."""
        s = Settings()
        invalid_config = {
            "trigger_engine": {"window_minutes": 5, "mode": "AND", "min_token_age_minutes": 2.0, "max_token_age_minutes": 30.0, "min_liquidity_usd": 3000.0, "cooldown_seconds": 3600},
            "decision_gate": {"confidence_threshold": 0.50},
            "sizing": {"mode": "fixed", "fixed_order_size_usd": 5.0},
            "risk": {"risk_pct_per_trade": 0.01, "max_concurrent_positions": 3, "max_total_exposure_usd": 10.0}, # 5 * 3 = 15 > 10
            "trailing_tp": {"tiers": [{"r_min": 0, "r_max": 1, "trail_pct": None}]},
            "labeling": {"buy_benar_threshold_r": 3.0, "salah_threshold_r": -1.0},
            "retrain": {"schedule_utc": "02:00", "min_closed_trades_first": 100, "min_closed_trades_alt": 50, "min_buy_benar_in_alt": 15, "rolling_window_days": 30, "rollback_accuracy_drop_pct": 0.05},
            "kill_switch": {"dev_wallet_sell_threshold_pct": 0.05, "slippage_spike_threshold_pct": 0.10},
            "rpc": {"primary_url": "https://api.mainnet-beta.solana.com", "secondary_url": "https://api.mainnet-beta.solana.com", "max_retry": 5}
        }
        with self.assertRaises(ValueError):
            s.validate_config(invalid_config)

    async def test_fixed_sizing_mode_uses_exact_config_amount(self):
        """Test execute_trade uses fixed USD position size when SIZING_MODE='fixed'."""
        mock_pos_repo = AsyncMock()
        mock_pos_repo.get_open_positions.return_value = []
        mock_cool_repo = AsyncMock()
        mock_cool_repo.get_cooldown.return_value = None
        mock_model_repo = AsyncMock()
        mock_model_repo.get_active_model.return_value = None

        mock_token_info = AsyncMock()
        mock_token_info.get_token_info.return_value = {"price_usd": 1.0}

        executor = AutoTradeExecutor(
            position_repo=mock_pos_repo,
            cooldown_repo=mock_cool_repo,
            model_registry_repo=mock_model_repo,
            token_info_service=mock_token_info
        )

        now = datetime.now(timezone.utc)
        pred = PredictionResult(
            direction="BUY",
            confidence_score=0.92,
            target_price_estimate=2.0,
            token_address="GQAt4nq2S8H6vPwbMsyatsZUmwuFHRuUnRh3BwPzpump",
            wallet_source="29yFzeBZgxf5zqrAkKXwgZtQehRf4pL8WbV2nRJikbw8",
            signature="sig123",
            timestamp=now
        )
        fv = FeatureVector(
            token_address="GQAt4nq2S8H6vPwbMsyatsZUmwuFHRuUnRh3BwPzpump",
            wallet_source="29yFzeBZgxf5zqrAkKXwgZtQehRf4pL8WbV2nRJikbw8",
            signature="sig123",
            timestamp=now,
            position_size_usd=1.0,
            token_age_minutes=30.0,
            liquidity_pool_depth=500000.0,
            win_rate_30d=0.75,
            avg_holding_time_minutes=45.0,
            typical_trade_size_usd=150.0,
            hour_of_day_utc=now.hour
        )

        with patch("app.use_cases.auto_trade_executor.settings") as mock_settings, \
             patch("app.use_cases.trade_guard.TradeGuard") as mock_guard_cls, \
             patch("app.infrastructure.blockchain.trading_service.execute_pumpportal_swap", new=AsyncMock(return_value="tx_mock_123")):
            
            mock_settings.RISK_MAX_CONCURRENT_POSITIONS = 3
            mock_settings.RISK_MAX_TOTAL_EXPOSURE_USD = 10.0
            mock_settings.SIZING_MODE = "fixed"
            mock_settings.SIZING_FIXED_ORDER_SIZE_USD = 2.50
            mock_settings.RISK_MAX_POSITION_SIZE_USD = 5000.0

            mock_guard_inst = AsyncMock()
            mock_guard_inst.validate_trade.return_value = (True, "OK")
            mock_guard_cls.return_value = mock_guard_inst

            open_pos = await executor.execute_trade(prediction=pred, feature_vector=fv)

            self.assertIsNotNone(open_pos)
            self.assertEqual(open_pos.position_size_usd, 2.50)
            self.assertEqual(open_pos.sizing_mode, "fixed")

    async def test_fixed_sizing_mode_rejects_below_min_position(self):
        """Test fixed mode rejects trade when fixed_order_size_usd is below min_position_usd."""
        mock_pos_repo = AsyncMock()
        mock_pos_repo.get_open_positions.return_value = []
        mock_cool_repo = AsyncMock()
        mock_cool_repo.get_cooldown.return_value = None
        mock_model_repo = AsyncMock()

        mock_token_info = AsyncMock()
        # SOL price $200 -> min_position_usd = 0.002 * 200 = $0.40 USD
        mock_token_info.get_token_info.return_value = {"price_usd": 200.0}

        executor = AutoTradeExecutor(
            position_repo=mock_pos_repo,
            cooldown_repo=mock_cool_repo,
            model_registry_repo=mock_model_repo,
            token_info_service=mock_token_info
        )

        now = datetime.now(timezone.utc)
        pred = PredictionResult(
            direction="BUY",
            confidence_score=0.92,
            target_price_estimate=2.0,
            token_address="GQAt4nq2S8H6vPwbMsyatsZUmwuFHRuUnRh3BwPzpump",
            wallet_source="29yFzeBZgxf5zqrAkKXwgZtQehRf4pL8WbV2nRJikbw8",
            signature="sig123",
            timestamp=now
        )
        fv = FeatureVector(
            token_address="GQAt4nq2S8H6vPwbMsyatsZUmwuFHRuUnRh3BwPzpump",
            wallet_source="29yFzeBZgxf5zqrAkKXwgZtQehRf4pL8WbV2nRJikbw8",
            signature="sig123",
            timestamp=now,
            position_size_usd=0.10,
            token_age_minutes=30.0,
            liquidity_pool_depth=500000.0,
            win_rate_30d=0.75,
            avg_holding_time_minutes=45.0,
            typical_trade_size_usd=150.0,
            hour_of_day_utc=now.hour
        )

        with patch("app.use_cases.auto_trade_executor.settings") as mock_settings:
            mock_settings.RISK_MAX_CONCURRENT_POSITIONS = 3
            mock_settings.RISK_MAX_TOTAL_EXPOSURE_USD = 10.0
            mock_settings.SIZING_MODE = "fixed"
            mock_settings.SIZING_FIXED_ORDER_SIZE_USD = 0.10 # $0.10 < $0.40 min_position_usd

            open_pos = await executor.execute_trade(prediction=pred, feature_vector=fv)

            self.assertIsNone(open_pos)
