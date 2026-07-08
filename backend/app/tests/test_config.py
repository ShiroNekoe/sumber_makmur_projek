import os
import yaml
import unittest
import asyncio
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from app.core.config import Settings
from app.websocket.manager import manager as ws_manager


class TestConfigManagement(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env_patches = {
            "RPC_PRIMARY_URL": "https://api.mainnet-beta.solana.com",
            "SOLANA_RPC_URL": "https://api.mainnet-beta.solana.com",
            "RPC_SECONDARY_URL": "https://api.devnet.solana.com",
            "SOLANA_RPC_FALLBACK_URL": "https://api.devnet.solana.com"
        }
        self.env_patcher = patch.dict(os.environ, self.env_patches)
        self.env_patcher.start()
        
        self.valid_config = {
            "trigger_engine": {
                "window_minutes": 5,
                "mode": "AND",
                "min_token_age_minutes": 60,
                "max_token_age_minutes": 120,
                "min_liquidity_usd": 5000.0,
                "cooldown_seconds": 3600
            },
            "decision_gate": {
                "confidence_threshold": 0.75
            },
            "risk": {
                "risk_pct_per_trade": 0.01,
                "max_concurrent_positions": 3
            },
            "trailing_tp": {
                "tiers": [
                    {"r_min": 1, "r_max": 2, "trail_pct": None},
                    {"r_min": 2, "r_max": 5, "trail_pct": 0.25},
                    {"r_min": 5, "r_max": None, "trail_pct": 0.15}
                ]
            },
            "labeling": {
                "buy_benar_threshold_r": 3.0,
                "salah_threshold_r": -1.0
            },
            "retrain": {
                "schedule_utc": "02:00",
                "min_closed_trades_first": 100,
                "min_closed_trades_alt": 50,
                "min_buy_benar_in_alt": 15,
                "rolling_window_days": 30,
                "rollback_accuracy_drop_pct": 0.05
            },
            "kill_switch": {
                "dev_wallet_sell_threshold_pct": 0.05,
                "slippage_spike_threshold_pct": 0.10
            },
            "rpc": {
                "primary_url": "https://api.mainnet-beta.solana.com",
                "secondary_url": "https://api.devnet.solana.com",
                "max_retry": 5
            },
            "relevance_filter": {
                "min_swap_amount_usd": 10.0,
                "min_lp_change_usd": 1000.0,
                "dex_routers": [
                    "6EF8rrect3EDQS425286575m1111111111111111"
                ],
                "custodial_exchanges": []
            }
        }
        self.settings = Settings()
        self.original_broadcast = ws_manager.broadcast
        ws_manager.broadcast = AsyncMock()

    def tearDown(self):
        self.env_patcher.stop()
        ws_manager.broadcast = self.original_broadcast

    def test_valid_config_loading(self):
        self.settings.apply_config(self.valid_config)
        self.assertEqual(self.settings.TRIGGER_WINDOW_MINUTES, 5)
        self.assertEqual(self.settings.CONFIDENCE_THRESHOLD, 0.75)
        self.assertEqual(self.settings.RISK_MAX_CONCURRENT_POSITIONS, 3)

    def test_missing_required_fields_raises_error(self):
        # Remove trigger_engine.window_minutes
        bad_config = yaml.safe_load(yaml.dump(self.valid_config))
        del bad_config["trigger_engine"]["window_minutes"]
        with self.assertRaises(ValueError) as ctx:
            self.settings.apply_config(bad_config)
        self.assertIn("window_minutes", str(ctx.exception))

    def test_invalid_trigger_window_value_bounds(self):
        # window_minutes <= 0
        bad_config = yaml.safe_load(yaml.dump(self.valid_config))
        bad_config["trigger_engine"]["window_minutes"] = 0
        with self.assertRaises(ValueError) as ctx:
            self.settings.apply_config(bad_config)
        self.assertIn("window_minutes", str(ctx.exception))

    def test_invalid_confidence_threshold_value_bounds(self):
        # confidence_threshold > 1.0
        bad_config = yaml.safe_load(yaml.dump(self.valid_config))
        bad_config["decision_gate"]["confidence_threshold"] = 1.05
        with self.assertRaises(ValueError) as ctx:
            self.settings.apply_config(bad_config)
        self.assertIn("confidence_threshold", str(ctx.exception))

    def test_overlapping_trailing_tp_tiers_raises_error(self):
        bad_config = yaml.safe_load(yaml.dump(self.valid_config))
        # Set tier 1: 2 to 5, tier 2: 4 to 10 (overlap from 4 to 5)
        bad_config["trailing_tp"]["tiers"] = [
            {"r_min": 1, "r_max": 2, "trail_pct": None},
            {"r_min": 2, "r_max": 5, "trail_pct": 0.25},
            {"r_min": 4, "r_max": 10, "trail_pct": 0.15}
        ]
        with self.assertRaises(ValueError) as ctx:
            self.settings.apply_config(bad_config)
        self.assertIn("overlaps", str(ctx.exception))

    def test_invalid_rpc_url_format_raises_error(self):
        bad_config = yaml.safe_load(yaml.dump(self.valid_config))
        bad_config["rpc"]["primary_url"] = "ftp://invalid-url.com"
        with self.assertRaises(ValueError) as ctx:
            self.settings.apply_config(bad_config)
        self.assertIn("primary_url", str(ctx.exception))

    def test_hot_reload_applies_non_critical_changes(self):
        self.settings.apply_config(self.valid_config, hot_reload=False)
        
        # Change non-critical setting: window_minutes and confidence_threshold
        updated_config = yaml.safe_load(yaml.dump(self.valid_config))
        updated_config["trigger_engine"]["window_minutes"] = 10
        updated_config["decision_gate"]["confidence_threshold"] = 0.85
        
        self.settings.apply_config(updated_config, hot_reload=True)
        self.assertEqual(self.settings.TRIGGER_WINDOW_MINUTES, 10)
        self.assertEqual(self.settings.CONFIDENCE_THRESHOLD, 0.85)

    def test_hot_reload_rpc_changes_warnings_not_applied(self):
        self.settings.apply_config(self.valid_config, hot_reload=False)
        
        # Change critical RPC URL
        updated_config = yaml.safe_load(yaml.dump(self.valid_config))
        updated_config["rpc"]["primary_url"] = "https://new-rpc-url.solana.com"
        
        with patch("app.core.config.logger.warning") as mock_warn:
            self.settings.apply_config(updated_config, hot_reload=True)
            # RPC URL must remain the old one since it's a critical reload
            self.assertEqual(self.settings.RPC_PRIMARY_URL, "https://api.mainnet-beta.solana.com")
            # Warning logger called
            mock_warn.assert_called_once()

    async def test_watch_config_loop_reload_success(self):
        test_file = "test_config_watch.yaml"
        self.settings.CONFIG_FILE_PATH = os.path.abspath(test_file)
        
        # Write valid initial config
        with open(test_file, "w") as f:
            yaml.dump(self.valid_config, f)
            
        try:
            self.settings.apply_config(self.valid_config, hot_reload=False)
            
            # Start watcher in the background
            task = asyncio.create_task(self.settings.watch_config_loop())
            await asyncio.sleep(0.2)
            
            # Update file to trigger reload
            updated_config = yaml.safe_load(yaml.dump(self.valid_config))
            updated_config["trigger_engine"]["window_minutes"] = 15
            
            with open(test_file, "w") as f:
                yaml.dump(updated_config, f)
            
            # Let the loop detect the modification
            await asyncio.sleep(2.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            # Non-critical setting updated successfully
            self.assertEqual(self.settings.TRIGGER_WINDOW_MINUTES, 15)
            # Success WS message broadcasted
            ws_manager.broadcast.assert_called_once()
            called_args = ws_manager.broadcast.call_args[0][0]
            self.assertEqual(called_args["type"], "system_alert")
            self.assertEqual(called_args["data"]["alert_type"], "config_reload")
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)

    async def test_watch_config_loop_reload_failure_keeps_old_config(self):
        test_file = "test_config_watch_fail.yaml"
        self.settings.CONFIG_FILE_PATH = os.path.abspath(test_file)
        
        # Write valid initial config
        with open(test_file, "w") as f:
            yaml.dump(self.valid_config, f)
            
        try:
            self.settings.apply_config(self.valid_config, hot_reload=False)
            
            # Start watcher in the background
            task = asyncio.create_task(self.settings.watch_config_loop())
            await asyncio.sleep(0.2)
            
            # Corrupt the file with invalid range validation failure (negative window_minutes)
            updated_config = yaml.safe_load(yaml.dump(self.valid_config))
            updated_config["trigger_engine"]["window_minutes"] = -5
            
            with open(test_file, "w") as f:
                yaml.dump(updated_config, f)
            
            # Let the loop detect the modification and fail
            await asyncio.sleep(2.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            # Config remains the original valid value (5)
            self.assertEqual(self.settings.TRIGGER_WINDOW_MINUTES, 5)
            # Failure WS message broadcasted
            ws_manager.broadcast.assert_called_once()
            called_args = ws_manager.broadcast.call_args[0][0]
            self.assertEqual(called_args["type"], "system_alert")
            self.assertEqual(called_args["data"]["alert_type"], "config_reload_error")
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)
