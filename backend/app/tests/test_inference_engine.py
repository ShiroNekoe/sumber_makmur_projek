import unittest
import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from app.domain.models import FeatureVector, ModelRegistry
from app.ml_pipeline.inference import XGBoostInferenceEngine


class TestXGBoostInferenceEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Create a temporary directory for storing model files during tests
        self.test_dir = tempfile.mkdtemp()
        
        # Mock repositories
        self.mock_model_registry_repo = MagicMock()
        self.mock_model_registry_repo.get_active_model = AsyncMock(return_value=None)
        self.mock_model_registry_repo.add_model_version = AsyncMock()
        
        # Initialize inference engine with mock repo and temp dir
        self.engine = XGBoostInferenceEngine(
            model_registry_repo=self.mock_model_registry_repo,
            models_dir=self.test_dir
        )
        
        # Setup dummy feature vector
        self.fv = FeatureVector(
            token_address="TokenAddressxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            wallet_source="Wha1eA11111111111111111111111111111111111",
            signature="test_signature_12345",
            timestamp=datetime.now(timezone.utc),
            position_size_usd=1500.0,
            token_age_minutes=120.0,
            liquidity_pool_depth=25000.0,
            slippage_actual=0.01,
            cluster_score=1.0,
            win_rate_30d=0.65,
            avg_holding_time_minutes=35.0,
            typical_trade_size_usd=800.0,
            past_exit_pattern_score=0.1,
            sol_usd_momentum=0.03,
            token_volume_liquidity_ratio=0.15,
            hour_of_day_utc=14
        )

    def tearDown(self):
        # Clean up temp directory
        shutil.rmtree(self.test_dir)

    async def test_cold_start_bootstrap_creates_v0_model(self):
        # Ensure model is not loaded initially
        self.assertIsNone(self.engine.model)
        
        # Run inference (should trigger bootstrap because active model is None)
        result = await self.engine.run_inference(self.fv)
        
        # Assertions
        self.assertIsNotNone(self.engine.model)
        self.assertEqual(self.engine.current_model_version, "v0")
        
        # Check v0.json model file is created in temporary directory
        model_file = os.path.join(self.test_dir, "v0.json")
        self.assertTrue(os.path.exists(model_file))
        
        # Check registry DB registration was called
        self.mock_model_registry_repo.add_model_version.assert_called_once()
        args, _ = self.mock_model_registry_repo.add_model_version.call_args
        registry_entry = args[0]
        self.assertEqual(registry_entry.model_version, "v0")
        self.assertTrue(registry_entry.is_active)
        self.assertEqual(registry_entry.training_sample_count, 120)
        self.assertEqual(registry_entry.expectancy_r, 0.15)
        
        # Check prediction results
        self.assertIn("direction", result)
        self.assertIn("confidence_score", result)
        self.assertIn("target_price_estimate", result)
        self.assertIn(result["direction"], ["BUY", "SELL", "HOLD"])
        self.assertTrue(0.0 <= result["confidence_score"] <= 1.0)
        
        # Check target price estimate offsets
        if result["direction"] == "BUY":
            self.assertEqual(result["target_price_estimate"], 0.50)
        elif result["direction"] == "SELL":
            self.assertEqual(result["target_price_estimate"], -0.20)
        else:
            self.assertEqual(result["target_price_estimate"], 0.0)

    async def test_loads_active_model_from_disk_if_exists(self):
        # Mock active model entry in registry DB
        active_model_meta = ModelRegistry(
            model_version="v2",
            trained_at=datetime.now(timezone.utc),
            training_sample_count=200,
            validation_accuracy=0.72,
            expectancy_r=0.25,
            is_active=True,
            rolled_back=False
        )
        self.mock_model_registry_repo.get_active_model.return_value = active_model_meta
        
        # Pre-bootstrap to get a valid model file, save it as v2.json
        await self.engine._bootstrap_model_v0()
        shutil.copy(
            os.path.join(self.test_dir, "v0.json"),
            os.path.join(self.test_dir, "v2.json")
        )
        
        # Reset engine model state so it tries to load v2
        self.engine.model = None
        self.engine.current_model_version = None
        
        # Run inference
        result = await self.engine.run_inference(self.fv)
        
        # Assert it loaded model v2 instead of bootstrapping v0
        self.assertEqual(self.engine.current_model_version, "v2")
        self.assertIsNotNone(self.engine.model)
        self.mock_model_registry_repo.get_active_model.assert_called_once()
        
        # Check prediction output contains keys
        self.assertIn("direction", result)
        self.assertIn("confidence_score", result)
        self.assertIn("target_price_estimate", result)
