import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.core import error_handler
from app.core.error_handler import ErrorType, ErrorSeverity, log_system_error
from app.domain.models import OpenPosition, PredictionResult, FeatureVector
from app.use_cases.auto_trade_executor import AutoTradeExecutor
from app.use_cases.safety_check_gate import SafetyCheckGate
from app.execution.executor import ParallelExecutionEngine
from app.websocket.manager import manager as ws_manager
from app.core.config import settings


class TestErrorRecoveryEngine(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Mock WebSocket broadcast
        self.original_broadcast = ws_manager.broadcast
        ws_manager.broadcast = AsyncMock()

        # Mock repositories for executor/engine tests
        self.mock_position_repo = MagicMock()
        self.mock_cooldown_repo = MagicMock()
        self.mock_model_registry_repo = MagicMock()
        self.mock_trade_history_repo = MagicMock()

        self.mock_position_repo.get_open_positions = AsyncMock(return_value=[])
        self.mock_position_repo.add_position = AsyncMock()
        self.mock_position_repo.update_position = AsyncMock()
        self.mock_cooldown_repo.set_cooldown = AsyncMock()
        self.mock_cooldown_repo.delete_cooldown = AsyncMock()
        self.mock_cooldown_repo.get_cooldown = AsyncMock(return_value=None)
        self.mock_trade_history_repo.add_closed_trade = AsyncMock()

        self.mock_model_registry_repo.get_active_model = AsyncMock(return_value=None)

        self.executor = AutoTradeExecutor(
            position_repo=self.mock_position_repo,
            cooldown_repo=self.mock_cooldown_repo,
            model_registry_repo=self.mock_model_registry_repo,
            trade_history_repo=self.mock_trade_history_repo
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

    async def asyncTearDown(self):
        ws_manager.broadcast = self.original_broadcast

    async def test_log_system_error_broadcasts_websocket_alert(self):
        await log_system_error(
            error_type=ErrorType.RPC_DISCONNECTED,
            severity=ErrorSeverity.WARNING,
            context="Solana WebSocket disconnected.",
            recovery_action="reconnect_retry"
        )
        ws_manager.broadcast.assert_called_once()
        called_args = ws_manager.broadcast.call_args[0][0]
        self.assertEqual(called_args["type"], "system_alert")
        self.assertEqual(called_args["data"]["alert_type"], "rpc_disconnected")
        self.assertEqual(called_args["data"]["level"], "warning")

    async def test_safety_api_timeout_failclosed_triggers_alert(self):
        # Mock safety service to timeout
        mock_safety_service = MagicMock()
        mock_safety_service.get_safety_info = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_filter_log_repo = MagicMock()
        mock_filter_log_repo.add_filter_log = AsyncMock()

        gate = SafetyCheckGate(mock_safety_service, mock_filter_log_repo)

        # Trigger check: safety API times out, should return failed result, block alert
        result = await gate.evaluate_safety(self.pred, self.fv)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "safety_api_failed")

        await asyncio.sleep(0.05)
        from unittest.mock import ANY
        ws_manager.broadcast.assert_any_call({
            "type": "system_alert",
            "data": {
                "event": "system_alert",
                "alert_type": "safety_api_timeout",
                "message": "[ERROR] Timeout limit (5s) exceeded fetching safety parameters for token SafeTokenxxxxxxxxxxxxxxxxxxxxxxxxxxxx.",
                "level": "error",
                "timestamp": ANY
            }
        })

    async def test_entry_order_failure_retries_and_logs_failed(self):
        # FailEntryToken triggers mock entry failure in AutoTradeExecutor
        self.pred.token_address = "FailEntryTokenxxxxxxxxxxxxxxxxxxxxxxx"
        
        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            res = await self.executor.execute_trade(self.pred, self.fv)
            
            # Sizing checked, trade aborted
            self.assertIsNone(res)
            # Sleep called twice for retries (3 attempts total, 2 sleep retries)
            self.assertEqual(mock_sleep.call_count, 2)
            
            # F-07 WS warning emitted
            called_args = ws_manager.broadcast.call_args[0][0]
            self.assertEqual(called_args["type"], "system_alert")
            self.assertEqual(called_args["data"]["alert_type"], "entry_failed")

    async def test_exit_order_normal_failure_retries_with_slippage_increments(self):
        pos = OpenPosition(
            position_id="pos_exit_fail",
            wallet_source="whale_addr",
            token_address="FailExitTokenxxxxxxxxxxxxxxxxxxxxxxxx",
            state="OPEN",
            entry_price=1.0,
            entry_ts=datetime.now(timezone.utc),
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=100.0,
            confidence_score=0.8,
            model_version="v0"
        )

        engine = ParallelExecutionEngine(
            pos,
            self.mock_position_repo,
            self.mock_cooldown_repo,
            self.mock_model_registry_repo,
            self.mock_trade_history_repo
        )

        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            # Trigger exit (normal Stop Loss)
            await engine.execute_exit("SL")
            
            # Assert closed trade was written
            self.mock_trade_history_repo.add_closed_trade.assert_called_once()
            # Assert state updated to CLOSED
            self.assertEqual(pos.state, "CLOSED")
            
            # Failed twice before success -> mock_sleep called 2x for exit retries
            self.assertEqual(mock_sleep.call_count, 3) # 2 failures sleep + 1 simulated latency sleep
            
            # WebSocket alerts exit_pending broadcasted
            self.assertTrue(ws_manager.broadcast.called)
            called_args = ws_manager.broadcast.call_args_list[0][0][0]
            self.assertEqual(called_args["type"], "system_alert")
            self.assertEqual(called_args["data"]["alert_type"], "exit_pending")

    async def test_exit_order_emergency_killswitch_failure_retries_aggressively_no_delay(self):
        pos = OpenPosition(
            position_id="pos_ks_fail",
            wallet_source="whale_addr",
            token_address="FailKillSwitchExitTokenxxxxxxxxxxxxxxxx",
            state="OPEN",
            entry_price=1.0,
            entry_ts=datetime.now(timezone.utc),
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=100.0,
            confidence_score=0.8,
            model_version="v0"
        )

        engine = ParallelExecutionEngine(
            pos,
            self.mock_position_repo,
            self.mock_cooldown_repo,
            self.mock_model_registry_repo,
            self.mock_trade_history_repo
        )

        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            # Trigger emergency kill-switch exit
            await engine.execute_exit("kill_switch_lp")
            
            # Closed trade written
            self.mock_trade_history_repo.add_closed_trade.assert_called_once()
            # CLOSED
            self.assertEqual(pos.state, "CLOSED")
            
            # Aggressive retry: no delay, mock_sleep should only be called once (the successful latency sleep)
            self.assertEqual(mock_sleep.call_count, 1)
            
            # WebSocket alert critical_exit_failed emitted
            self.assertTrue(ws_manager.broadcast.called)
            # Find the critical alert in broadcast calls
            broadcast_calls = [c[0][0] for c in ws_manager.broadcast.call_args_list]
            critical_alert = next(b for b in broadcast_calls if b.get("type") == "system_alert")
            self.assertEqual(critical_alert["data"]["alert_type"], "critical_exit_failed")
