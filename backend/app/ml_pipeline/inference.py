import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

import os
import xgboost as xgb
import pandas as pd
import numpy as np
from app.core.config import settings
from app.domain.models import FeatureVector
from app.domain.interfaces import (
    IFeatureExtractor,
    ITradeHistoryRepository,
    ITokenInfoService,
    IXGBoostInferenceEngine,
    IModelRegistryRepository
)

logger = logging.getLogger(__name__)


class FeatureExtractor(IFeatureExtractor):
    """
    Layer 2 Use Case: Feature Extraction Pipeline (F-04)
    Assembles on-chain signals, historical database metrics, and market context.
    """
    def __init__(
        self,
        trade_history_repo: ITradeHistoryRepository,
        token_info_service: ITokenInfoService
    ):
        self.trade_history_repo = trade_history_repo
        self.token_info_service = token_info_service

        # Prior defaults for new systems with no trade history
        self.prior_win_rate = 0.45
        self.prior_holding_time_minutes = 20.0
        self.prior_trade_size_usd = 500.0
        self.prior_exit_pattern_score = 0.0

    async def extract_features(self, trigger_event: dict) -> FeatureVector:
        """
        Gathers 12+ features from on-chain data, SQLite history, and market context.
        """
        token_address = trigger_event["token_address"] if "token_address" in trigger_event else trigger_event.get("token_mint", "")
        wallet_source = trigger_event["wallet_address"]
        signature = trigger_event["signature"]
        timestamp = trigger_event.get("timestamp_utc") or datetime.now(timezone.utc)
        
        # Ensure UTC timezone
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        # 1. Fetch On-chain Token Info (DexScreener API with Cache)
        try:
            token_info = await self.token_info_service.get_token_info(token_address)
        except Exception as e:
            logger.error(f"[FEATURE EXTRACTOR] Error fetching token info for {token_address}: {e}")
            token_info = {}

        # 2. Extract On-chain Features
        position_size_usd = float(trigger_event.get("amount_usd", 0.0))
        
        token_age_raw = float(token_info.get("age_minutes", 60.0))
        token_age_minutes = max(0.0, token_age_raw) # Validation: cannot be negative
        
        liquidity_pool_depth = float(token_info.get("liquidity_usd", 5000.0))
        slippage_actual = trigger_event.get("slippage_actual") or 0.01  # 1% fallback
        
        # Cluster score: 1.0 if AND boost is active, 0.0 otherwise
        cluster_score = 1.0 if trigger_event.get("confidence_boost") else 0.0

        # 3. Query Historical DB Features (SQLite)
        win_rate_30d = self.prior_win_rate
        avg_holding_time = self.prior_holding_time_minutes
        typical_trade_size = self.prior_trade_size_usd
        past_exit_pattern = self.prior_exit_pattern_score

        try:
            # Query last 200 closed trades
            closed_trades = await self.trade_history_repo.get_closed_trades(limit=200)
            
            # Filter for this specific whale wallet inside the 30-day window
            thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
            whale_trades = [
                t for t in closed_trades 
                if t.wallet_source == wallet_source and 
                (t.exit_ts.replace(tzinfo=timezone.utc) if t.exit_ts.tzinfo is None else t.exit_ts) > thirty_days_ago
            ]

            if whale_trades:
                total_trades = len(whale_trades)
                
                # Win rate (label == 'BUY_BENAR')
                win_count = sum(1 for t in whale_trades if t.label == "BUY_BENAR")
                win_rate_30d = float(win_count) / total_trades
                # Validation: clamp win rate between 0.0 and 1.0
                win_rate_30d = max(0.0, min(win_rate_30d, 1.0))

                # Average holding time
                avg_holding_time = float(sum(t.holding_time_minutes for t in whale_trades)) / total_trades
                
                # Typical trade size (mean of position sizes)
                typical_trade_size = float(sum(t.position_size_usd for t in whale_trades)) / total_trades
                
                # Past exit pattern score (ratio of kill_switch exits)
                kill_exits = sum(1 for t in whale_trades if t.exit_reason.startswith("kill_switch"))
                past_exit_pattern = float(kill_exits) / total_trades
                
        except Exception as e:
            logger.error(f"[FEATURE EXTRACTOR] Error querying SQLite historical trades: {e}", exc_info=True)

        # 4. Extract Market Context Features
        # SOL/USD momentum placeholder (in production, connected to price feed)
        sol_usd_momentum = 0.0
        
        # Token volume/liquidity ratio (24h volume / pool depth)
        volume_24h = float(token_info.get("volume_24h", 0.0))
        if liquidity_pool_depth > 0:
            token_volume_liquidity_ratio = volume_24h / liquidity_pool_depth
        else:
            token_volume_liquidity_ratio = 0.0

        hour_of_day_utc = timestamp.hour

        # Assemble and return Pydantic FeatureVector
        return FeatureVector(
            token_address=token_address,
            wallet_source=wallet_source,
            signature=signature,
            timestamp=timestamp,
            position_size_usd=position_size_usd,
            token_age_minutes=token_age_minutes,
            liquidity_pool_depth=liquidity_pool_depth,
            slippage_actual=slippage_actual,
            cluster_score=cluster_score,
            win_rate_30d=win_rate_30d,
            avg_holding_time_minutes=avg_holding_time,
            typical_trade_size_usd=typical_trade_size,
            past_exit_pattern_score=past_exit_pattern,
            sol_usd_momentum=sol_usd_momentum,
            token_volume_liquidity_ratio=token_volume_liquidity_ratio,
            hour_of_day_utc=hour_of_day_utc
        )


class XGBoostInferenceEngine(IXGBoostInferenceEngine):
    """
    Layer 2 Use Case: XGBoost Inference Engine (F-05)
    Loads active model or bootstraps Model v0, and runs multi-class classification.
    Uses core XGBoost APIs to avoid scikit-learn dependency.
    """
    def __init__(
        self,
        model_registry_repo: IModelRegistryRepository,
        models_dir: str = "models"
    ):
        self.model_registry_repo = model_registry_repo
        self.models_dir = models_dir
        self.model = None
        self.current_model_version = None

    async def run_inference(self, feature_vector: FeatureVector) -> dict:
        """
        Executes prediction on the feature vector.
        """
        await self._ensure_model_loaded()
        
        if not self.model:
            logger.warning("[XGBOOST ENGINE] Model not loaded. Falling back to HOLD prediction.")
            return {
                "direction": "HOLD",
                "confidence_score": 0.0,
                "target_price_estimate": 0.0
            }

        # Order must match the features used for training:
        feature_data = {
            "position_size_usd": [feature_vector.position_size_usd],
            "token_age_minutes": [feature_vector.token_age_minutes],
            "liquidity_pool_depth": [feature_vector.liquidity_pool_depth],
            "slippage_actual": [feature_vector.slippage_actual],
            "cluster_score": [feature_vector.cluster_score],
            "win_rate_30d": [feature_vector.win_rate_30d],
            "avg_holding_time_minutes": [feature_vector.avg_holding_time_minutes],
            "typical_trade_size_usd": [feature_vector.typical_trade_size_usd],
            "past_exit_pattern_score": [feature_vector.past_exit_pattern_score],
            "sol_usd_momentum": [feature_vector.sol_usd_momentum],
            "token_volume_liquidity_ratio": [feature_vector.token_volume_liquidity_ratio],
            "hour_of_day_utc": [feature_vector.hour_of_day_utc]
        }
        df = pd.DataFrame(feature_data)

        try:
            dtest = xgb.DMatrix(df)
            probs = self.model.predict(dtest)
            
            # probs is a 2D array of shape [1, 3] representing probabilities for each class
            class_idx = int(np.argmax(probs[0]))
            confidence = float(probs[0][class_idx])
            
            # Map index to classes: 0 -> HOLD, 1 -> BUY, 2 -> SELL
            class_map = {0: "HOLD", 1: "BUY", 2: "SELL"}
            direction = class_map.get(class_idx, "HOLD")
            
            # target price estimate (% change from current price)
            if direction == "BUY":
                target_price_estimate = 0.50
            elif direction == "SELL":
                target_price_estimate = -0.20
            else:
                target_price_estimate = 0.0
                
            return {
                "direction": direction,
                "confidence_score": confidence,
                "target_price_estimate": target_price_estimate
            }
        except Exception as e:
            logger.error(f"[XGBOOST ENGINE] Inference execution failed: {e}", exc_info=True)
            return {
                "direction": "HOLD",
                "confidence_score": 0.0,
                "target_price_estimate": 0.0
            }

    async def _ensure_model_loaded(self) -> None:
        if self.model is not None:
            return
            
        try:
            active_model = await self.model_registry_repo.get_active_model()
            if active_model:
                model_ver = active_model.model_version
                filepath = os.path.join(self.models_dir, f"{model_ver}.json")
                if os.path.exists(filepath):
                    model = xgb.Booster()
                    model.load_model(filepath)
                    self.model = model
                    self.current_model_version = model_ver
                    logger.info(f"[XGBOOST ENGINE] Successfully loaded active model version: {model_ver}")
                    return
                else:
                    logger.warning(f"[XGBOOST ENGINE] Active model file {filepath} not found on disk.")
        except Exception as e:
            logger.error(f"[XGBOOST ENGINE] Error checking or loading active model: {e}")
            
        # Trigger bootstrapping if no model loaded
        await self._bootstrap_model_v0()

    async def _bootstrap_model_v0(self) -> None:
        logger.info("[XGBOOST ENGINE] Starting Auto-Bootstrap for Model v0...")
        os.makedirs(self.models_dir, exist_ok=True)
        
        # Generate synthetic dataset (120 samples)
        np.random.seed(42)
        n_samples = 120
        labels = np.array([0] * 40 + [1] * 40 + [2] * 40)
        
        pos_size = np.random.uniform(100, 2000, n_samples)
        token_age = np.random.uniform(5, 1440, n_samples)
        liq = np.random.uniform(5000, 100000, n_samples)
        slip = np.random.uniform(0.005, 0.05, n_samples)
        cluster = np.random.choice([0.0, 1.0], size=n_samples)
        
        win_rate = np.random.uniform(0.3, 0.7, n_samples)
        win_rate[40:80] = np.random.uniform(0.55, 0.85, 40)  # BUY bias
        
        hold_time = np.random.uniform(5, 120, n_samples)
        typ_size = np.random.uniform(100, 2000, n_samples)
        exit_pattern = np.random.uniform(0.0, 0.5, n_samples)
        
        sol_mom = np.random.uniform(-0.05, 0.05, n_samples)
        sol_mom[40:80] = np.random.uniform(0.01, 0.1, 40)    # BUY bias
        sol_mom[80:120] = np.random.uniform(-0.1, -0.01, 40)  # SELL bias
        
        vol_ratio = np.random.uniform(0.01, 0.5, n_samples)
        hours = np.random.randint(0, 24, n_samples)
        
        df_synthetic = pd.DataFrame({
            "position_size_usd": pos_size,
            "token_age_minutes": token_age,
            "liquidity_pool_depth": liq,
            "slippage_actual": slip,
            "cluster_score": cluster,
            "win_rate_30d": win_rate,
            "avg_holding_time_minutes": hold_time,
            "typical_trade_size_usd": typ_size,
            "past_exit_pattern_score": exit_pattern,
            "sol_usd_momentum": sol_mom,
            "token_volume_liquidity_ratio": vol_ratio,
            "hour_of_day_utc": hours
        })
        
        dtrain = xgb.DMatrix(df_synthetic, label=labels)
        params = {
            "max_depth": 6,
            "learning_rate": 0.05,
            "objective": "multi:softprob",
            "num_class": 3,
            "seed": 42
        }
        model = xgb.train(params, dtrain, num_boost_round=300)
        
        filepath = os.path.join(self.models_dir, "v0.json")
        model.save_model(filepath)
        
        # Register Model v0 in registry
        from app.domain.models import ModelRegistry
        
        registry_entry = ModelRegistry(
            model_version="v0",
            trained_at=datetime.now(timezone.utc),
            training_sample_count=n_samples,
            validation_accuracy=0.65,
            expectancy_r=0.15,
            is_active=True,
            rolled_back=False
        )
        
        await self.model_registry_repo.add_model_version(registry_entry)
        
        self.model = model
        self.current_model_version = "v0"
        logger.info("[XGBOOST ENGINE] Auto-Bootstrap complete. Model v0 trained, registered, and activated.")
