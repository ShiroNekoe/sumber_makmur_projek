import logging
import os
import asyncio
import hashlib
import numpy as np
import pandas as pd
import xgboost as xgb
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from app.core.config import settings
from app.domain.models import ModelRegistry, ClosedTrade
from app.domain.interfaces import ITradeHistoryRepository, IModelRegistryRepository, IXGBoostInferenceEngine
from app.websocket.manager import manager as ws_manager
from app.ml_pipeline.training_utils import compute_class_sample_weights

logger = logging.getLogger(__name__)


def _deterministic_hash(text: str) -> int:
    """Returns a deterministic integer hash for a string."""
    return int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)


class RetrainScheduler:
    """
    F-10: 24h Retrain & Rollback Scheduler
    Automatically retrains the XGBoost classifier from SQLite trade logs
    and performs rollback guard evaluation.
    """
    def __init__(
        self,
        trade_history_repo: ITradeHistoryRepository,
        model_registry_repo: IModelRegistryRepository,
        inference_engine: IXGBoostInferenceEngine,
        models_dir: str = "models"
    ):
        self.trade_history_repo = trade_history_repo
        self.model_registry_repo = model_registry_repo
        self.inference_engine = inference_engine
        self.models_dir = models_dir
        
    async def retrain_model_if_needed(self, force: bool = False) -> bool:
        """
        Runs retraining if force is True, or if 24 hours have passed since the last retraining.
        """
        logger.info("[RETRAIN] Checking if model retraining is needed...")
        
        # Check database for last retrained model
        active_model = await self.model_registry_repo.get_active_model()
        if active_model and not force:
            time_since_train = datetime.now(timezone.utc) - (active_model.trained_at.replace(tzinfo=timezone.utc) if active_model.trained_at.tzinfo is None else active_model.trained_at)
            if time_since_train < timedelta(hours=24):
                logger.info(f"[RETRAIN] Active model {active_model.model_version} was trained {time_since_train.total_seconds()/3600:.1f} hours ago. Retraining skipped.")
                return False

        # Fetch training data on the main event loop. All async DB access (and db_lock,
        # which is bound to this loop) must happen here, not inside the worker thread below.
        trades: List[ClosedTrade] = await self.trade_history_repo.get_closed_trades(limit=1000)

        # Run CPU-bound training (pandas/numpy/xgboost only, no DB/event-loop access)
        # in a background executor to prevent blocking the FastAPI event loop
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._sync_retrain, trades, active_model)

        if result is None:
            return False

        try:
            await self.model_registry_repo.add_model_version(result["registry_entry"])

            if result["rollback"]:
                logger.warning(f"[RETRAIN] New model version {result['new_version']} registered as ROLLED_BACK. Keeping older model active.")
                await ws_manager.broadcast({
                    "event": "rollback_alert",
                    "model_version": result["new_version"],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                return False

            if active_model:
                active_model.is_active = False
                await self.model_registry_repo.update_model_version(active_model)
            logger.info(f"[RETRAIN] New model version {result['new_version']} ACTIVATED and saved to disk.")
        except Exception as e:
            logger.error(f"[RETRAIN] Error persisting retrain result: {e}", exc_info=True)
            return False

        # Hot-reload inference engine
        # Clear cached model so it reloads active model on next prediction
        if hasattr(self.inference_engine, "model"):
            self.inference_engine.model = None
            self.inference_engine.current_model_version = None
            logger.info("[RETRAIN] Inference engine model hot-reload triggered successfully.")
                
        # Broadcast retrain complete event
        await ws_manager.broadcast({
            "event": "model_updated",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
            
        return True

    def _sync_retrain(self, trades: List[ClosedTrade], active_model: Optional[ModelRegistry]) -> Optional[dict]:
        """
        Synchronous, CPU-bound retraining steps run inside a worker thread.

        Pure computation only (pandas/numpy/xgboost + local model file I/O). No async
        DB or WebSocket calls happen here: asyncio primitives created on the main loop
        (like db_lock) cannot be used from a different event loop running in this
        worker thread, so all DB persistence happens back in retrain_model_if_needed()
        on the main loop once this function returns.

        Returns:
            None if retraining was skipped (insufficient data) or failed.
            Otherwise a dict with keys: registry_entry, new_version, rollback.
        """
        try:
            # 1. Filter: rolling 30 days and exit_reason NOT LIKE 'kill_switch%'
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.RETRAIN_ROLLING_WINDOW_DAYS)
            
            filtered_trades = []
            for t in trades:
                exit_ts = t.exit_ts.replace(tzinfo=timezone.utc) if t.exit_ts.tzinfo is None else t.exit_ts
                if exit_ts > cutoff and not t.exit_reason.startswith("kill_switch"):
                    filtered_trades.append(t)
                    
            n_samples = len(filtered_trades)
            
            # Count BUY_BENAR trades
            buy_benar_count = sum(1 for t in filtered_trades if t.label == "BUY_BENAR")
            
            logger.info(f"[RETRAIN] Total filtered closed trades in 30d window: {n_samples} (BUY_BENAR: {buy_benar_count})")
            
            # 2. Validate minimum sample size criteria
            min_first = settings.RETRAIN_MIN_CLOSED_TRADES_FIRST
            min_alt = settings.RETRAIN_MIN_CLOSED_TRADES_ALT
            min_buy_alt = settings.RETRAIN_MIN_BUY_BENAR_IN_ALT
            
            meets_criteria = (n_samples >= min_first) or (n_samples >= min_alt and buy_benar_count >= min_buy_alt)
            
            if not meets_criteria:
                logger.warning(
                    f"[RETRAIN] [SKIPPED] Insufficient training data. "
                    f"Required: {min_first} total, or {min_alt} total with {min_buy_alt} BUY_BENAR. "
                    f"Actual: {n_samples} total, {buy_benar_count} BUY_BENAR."
                )
                return None
                
            # 3. Labeling and Feature Matrix Construction
            labels = []
            feature_data = {
                "position_size_usd": [],
                "token_age_minutes": [],
                "liquidity_pool_depth": [],
                "slippage_actual": [],
                "cluster_score": [],
                "win_rate_30d": [],
                "avg_holding_time_minutes": [],
                "typical_trade_size_usd": [],
                "past_exit_pattern_score": [],
                "sol_usd_momentum": [],
                "token_volume_liquidity_ratio": [],
                "hour_of_day_utc": []
            }
            
            from app.domain.cluster_logic import compute_cluster_score
            for t in filtered_trades:
                # Re-calculate/assign labels based on R-multiple
                if t.r_multiple >= settings.LABELING_BUY_BENAR_THRESHOLD_R:
                    labels.append(1) # BUY_BENAR
                elif t.r_multiple <= settings.LABELING_SALAH_THRESHOLD_R:
                    labels.append(2) # SALAH
                else:
                    labels.append(0) # HOLD
                    
                # Reconstruct REAL feature vector from trade attributes and rolling wallet stats
                entry_ts = t.entry_ts.replace(tzinfo=timezone.utc) if t.entry_ts.tzinfo is None else t.entry_ts
                
                # Rolling 30-day prior trades for this wallet
                prior_trades = [
                    pt for pt in filtered_trades
                    if pt.wallet_source == t.wallet_source
                    and pt.trade_id != t.trade_id
                    and 0 < (entry_ts - (pt.entry_ts.replace(tzinfo=timezone.utc) if pt.entry_ts.tzinfo is None else pt.entry_ts)).total_seconds() <= 30 * 86400
                ]
                
                if prior_trades:
                    win_count = sum(1 for pt in prior_trades if pt.label == "BUY_BENAR")
                    win_rate_30d = float(win_count) / len(prior_trades)
                    avg_holding = float(sum(pt.holding_time_minutes for pt in prior_trades)) / len(prior_trades)
                    typical_size = float(sum(pt.position_size_usd for pt in prior_trades)) / len(prior_trades)
                    exit_pattern = float(sum(1 for pt in prior_trades if pt.exit_reason and pt.exit_reason.startswith("kill_switch"))) / len(prior_trades)
                else:
                    win_rate_30d = 0.45
                    avg_holding = 20.0
                    typical_size = float(t.position_size_usd or 500.0)
                    exit_pattern = 0.0

                cluster_score = compute_cluster_score(
                    target_wallet=t.wallet_source,
                    target_token=t.token_address,
                    target_timestamp=entry_ts,
                    events=filtered_trades,
                    window_minutes=settings.TRIGGER_WINDOW_MINUTES
                )

                # Real slippage if recorded from live execution, else 0.01 structural limit
                slippage_val = float(getattr(t, "slippage_actual", None) or 0.01)

                feature_data["position_size_usd"].append(float(t.position_size_usd or 500.0))
                feature_data["token_age_minutes"].append(60.0)
                feature_data["liquidity_pool_depth"].append(5000.0)
                feature_data["slippage_actual"].append(slippage_val)
                feature_data["cluster_score"].append(float(cluster_score))
                feature_data["win_rate_30d"].append(float(max(0.0, min(win_rate_30d, 1.0))))
                feature_data["avg_holding_time_minutes"].append(float(avg_holding))
                feature_data["typical_trade_size_usd"].append(float(typical_size))
                feature_data["past_exit_pattern_score"].append(float(exit_pattern))
                feature_data["sol_usd_momentum"].append(0.0)
                feature_data["token_volume_liquidity_ratio"].append(0.0)
                feature_data["hour_of_day_utc"].append(float(entry_ts.hour))
                
            df_X = pd.DataFrame(feature_data)
            df_y = np.array(labels)
            
            # 4. Train/Val Split (80/20 stratified split)
            from app.ml_pipeline.training_utils import stratified_train_test_split
            X_train, X_val, y_train, y_val = stratified_train_test_split(
                df_X.to_numpy(), df_y, test_size=0.20, random_state=42
            )
            
            # Class imbalance handling (sesuai 03 - Pipeline AI): win rate
            # rendah by design membuat label SALAH jauh lebih banyak dari
            # BUY_BENAR pada data closed trades riil. Sample weight inverse
            # frequency dihitung hanya dari training split, lalu dipasang ke
            # DMatrix training (bukan validation) agar metrik val_accuracy
            # tetap mengukur performa pada distribusi data asli/apa adanya.
            train_sample_weights = compute_class_sample_weights(y_train, num_class=3)
            
            dtrain = xgb.DMatrix(X_train, label=y_train, weight=train_sample_weights)
            dval = xgb.DMatrix(X_val, label=y_val)
            
            params = {
                "max_depth": 6,
                "learning_rate": 0.05,
                "objective": "multi:softprob",
                "num_class": 3,
                "seed": 42
            }
            
            model = xgb.train(params, dtrain, num_boost_round=300)
            
            # 5. Evaluate validation accuracy and Expectancy R
            probs = model.predict(dval)
            preds = np.argmax(probs, axis=1)
            
            # Validation Accuracy
            val_accuracy = float(np.sum(preds == y_val) / len(y_val))
            
            # Expectancy R calculation: Expectancy = (WR * avg_R_win) - ((1 - WR) * 1R)
            # Based on validation set predicted BUYs (class 1)
            predicted_buys_idx = np.where(preds == 1)[0]
            if len(predicted_buys_idx) > 0:
                true_labels_of_buys = y_val[predicted_buys_idx]
                wr = np.sum(true_labels_of_buys == 1) / len(predicted_buys_idx)
                
                # Mock R calculations: win = +3.5R, loss/salah = -1.2R, hold = +0.5R
                avg_win_r = 3.5
                avg_loss_r = 1.2
                expectancy_r = (wr * avg_win_r) - ((1 - wr) * avg_loss_r)
            else:
                expectancy_r = 0.0
                
            logger.info(f"[RETRAIN] Retraining finished. Val Accuracy: {val_accuracy:.2%}, Expectancy: {expectancy_r:.2f}R")
            
            # 6. Rollback Guard Check (against the active-model snapshot fetched on the main loop)
            rollback = False
            if active_model:
                accuracy_drop = active_model.validation_accuracy - val_accuracy
                if accuracy_drop > settings.RETRAIN_ROLLBACK_ACCURACY_DROP_PCT:
                    logger.warning(f"[RETRAIN] [ROLLBACK TRIGGERED] Accuracy dropped by {accuracy_drop:.2%} (> {settings.RETRAIN_ROLLBACK_ACCURACY_DROP_PCT:.2%}).")
                    rollback = True
                elif expectancy_r < 0.0:
                    logger.warning(f"[RETRAIN] [ROLLBACK TRIGGERED] Expectancy {expectancy_r:.2f} is negative.")
                    rollback = True
                    
            # 7. Save model to disk and prepare registry entry (DB write happens on the main loop)
            new_version = f"v{int(datetime.now(timezone.utc).timestamp())}"
            filepath = os.path.join(self.models_dir, f"{new_version}.json")
            os.makedirs(self.models_dir, exist_ok=True)
            
            model.save_model(filepath)
            
            registry_entry = ModelRegistry(
                model_version=new_version,
                trained_at=datetime.now(timezone.utc),
                training_sample_count=n_samples,
                validation_accuracy=val_accuracy,
                expectancy_r=expectancy_r,
                is_active=not rollback,
                rolled_back=rollback
            )

            return {
                "registry_entry": registry_entry,
                "new_version": new_version,
                "rollback": rollback
            }

        except Exception as e:
            logger.error(f"[RETRAIN] Error in retraining scheduler: {e}", exc_info=True)
            return None