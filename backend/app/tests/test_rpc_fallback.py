import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.blockchain.monitor import SolanaWebSocketMonitor
from app.core.config import settings
from app.use_cases.trigger_engine import TriggerEngine
from app.execution.executor import ParallelExecutionEngine
from app.domain.models import OpenPosition


class TestRpcFallbackAndDegradedMode(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reset SolanaWebSocketMonitor class variables
        SolanaWebSocketMonitor.degraded_mode = False
        SolanaWebSocketMonitor.rpc_state = "primary"
        SolanaWebSocketMonitor.current_rpc_url = settings.RPC_PRIMARY_URL

    async def test_initial_state(self):
        monitor = SolanaWebSocketMonitor()
        self.assertEqual(monitor.rpc_state, "primary")
        self.assertEqual(monitor.current_rpc_url, settings.RPC_PRIMARY_URL)
        self.assertFalse(SolanaWebSocketMonitor.degraded_mode)

    async def test_failover_to_secondary(self):
        monitor = SolanaWebSocketMonitor()
        
        # Trigger failover once
        await monitor._handle_failover()
        self.assertEqual(monitor.rpc_state, "secondary")
        self.assertEqual(monitor.current_rpc_url, settings.RPC_SECONDARY_URL)
        self.assertFalse(SolanaWebSocketMonitor.degraded_mode)

    async def test_failover_to_degraded(self):
        monitor = SolanaWebSocketMonitor()
        monitor.rpc_state = "secondary"

        # Failover from secondary triggers degraded mode
        await monitor._handle_failover()
        self.assertEqual(monitor.rpc_state, "degraded")
        self.assertTrue(SolanaWebSocketMonitor.degraded_mode)

    async def test_trigger_engine_blocked_in_degraded_mode(self):
        SolanaWebSocketMonitor.degraded_mode = True

        cooldown_repo = MagicMock()
        token_info_service = MagicMock()
        ml_pipeline = MagicMock()
        ml_pipeline.analyze_token = AsyncMock()

        engine = TriggerEngine(
            cooldown_repo=cooldown_repo,
            token_info_service=token_info_service,
            ml_pipeline=ml_pipeline
        )

        event = {
            "wallet_address": "whale_addr",
            "token_mint": "token_mint",
            "signature": "sig_degraded",
            "timestamp_utc": None
        }

        await engine.trigger_event(event)

        # Assert trigger engine returned early without processing cooldown/pipeline
        cooldown_repo.get_cooldown.assert_not_called()
        ml_pipeline.analyze_token.assert_not_called()

    async def test_recovery_to_primary(self):
        monitor = SolanaWebSocketMonitor()
        monitor.rpc_state = "degraded"
        SolanaWebSocketMonitor.degraded_mode = True
        monitor.is_running = True

        # Mock primary health check as healthy, secondary as unhealthy
        with patch.object(monitor, "_check_url_health", AsyncMock(side_effect=lambda url: url == settings.RPC_PRIMARY_URL)):
            async def mock_sleep(seconds):
                monitor.is_running = False
            
            with patch("asyncio.sleep", mock_sleep):
                await monitor._run_health_check_loop()
            
            self.assertEqual(monitor.rpc_state, "primary")
            self.assertEqual(monitor.current_rpc_url, settings.RPC_PRIMARY_URL)
            self.assertFalse(SolanaWebSocketMonitor.degraded_mode)

    async def test_recovery_degraded_to_secondary(self):
        monitor = SolanaWebSocketMonitor()
        monitor.rpc_state = "degraded"
        SolanaWebSocketMonitor.degraded_mode = True
        monitor.is_running = True

        # Mock secondary health check as healthy, primary as unhealthy
        with patch.object(monitor, "_check_url_health", AsyncMock(side_effect=lambda url: url == settings.RPC_SECONDARY_URL)):
            async def mock_sleep(seconds):
                monitor.is_running = False
                
            with patch("asyncio.sleep", mock_sleep):
                await monitor._run_health_check_loop()
            
            self.assertEqual(monitor.rpc_state, "secondary")
            self.assertEqual(monitor.current_rpc_url, settings.RPC_SECONDARY_URL)
            self.assertFalse(SolanaWebSocketMonitor.degraded_mode)

    async def test_executor_poll_interval_in_degraded_mode(self):
        position = OpenPosition(
            position_id="pos_1",
            wallet_source="whale_addr",
            token_address="token_mint",
            state="OPEN",
            sl_initial=0.9,
            risk_pct=0.01,
            position_size_usd=1000.0,
            confidence_score=0.8,
            model_version="v0"
        )
        
        engine = ParallelExecutionEngine(
            position=position,
            position_repo=MagicMock(),
            cooldown_repo=MagicMock(),
            model_registry_repo=MagicMock(),
            trade_history_repo=MagicMock()
        )

        # Check normal polling interval
        SolanaWebSocketMonitor.degraded_mode = False
        
        # Test dynamically changing poll interval in L3 protection loop sleep call
        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            async def run_limited_loop():
                # Runs one loop cycle and exits
                engine.exited = False
                
                async def check_signals_stub():
                    engine.exited = True
                    return None
                    
                with patch.object(engine, "_check_onchain_kill_signals", AsyncMock(side_effect=check_signals_stub)):
                    await engine._run_kill_switch_loop()

            await run_limited_loop()
            mock_sleep.assert_called_with(2.0)

        # Check degraded mode polling interval (30.0s)
        SolanaWebSocketMonitor.degraded_mode = True
        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            async def run_limited_loop():
                engine.exited = False
                
                async def check_signals_stub():
                    engine.exited = True
                    return None
                    
                with patch.object(engine, "_check_onchain_kill_signals", AsyncMock(side_effect=check_signals_stub)):
                    await engine._run_kill_switch_loop()

            await run_limited_loop()
            mock_sleep.assert_called_with(30.0)
