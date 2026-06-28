from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.core.config import settings
from app.websocket.manager import manager
from app.infrastructure.database.session import engine, Base
import app.infrastructure.database.models  # noqa: F401


import logging
from app.domain.interfaces import IMLPipeline

logger = logging.getLogger("app.main")


from datetime import datetime, timezone


class StubMLPipeline(IMLPipeline):
    def __init__(self, trade_history_repo, token_info_service, model_registry_repo, safety_check_gate):
        from app.ml_pipeline.inference import FeatureExtractor, XGBoostInferenceEngine
        self.feature_extractor = FeatureExtractor(trade_history_repo, token_info_service)
        self.inference_engine = XGBoostInferenceEngine(model_registry_repo, trade_history_repo)
        self.safety_check_gate = safety_check_gate

    async def analyze_token(self, token_address: str, wallet_source: str, confidence_boost: bool) -> None:
        logger.info(
            f"[ML PIPELINE] [STUB] Triggered XGBoost inference analysis for token: {token_address} "
            f"from wallet: {wallet_source} (confidence boost: {confidence_boost})."
        )
        try:
            # Simulate trigger event
            trigger_event = {
                "token_address": token_address,
                "wallet_address": wallet_source,
                "signature": "simulated_sig_" + datetime.now(timezone.utc).strftime("%H%M%S"),
                "amount_usd": 1500.0,
                "confidence_boost": confidence_boost,
                "timestamp_utc": datetime.now(timezone.utc)
            }
            # Extract features!
            fv = await self.feature_extractor.extract_features(trigger_event)
            logger.info(
                f"[FEATURE EXTRACTOR] Extracted Feature Vector (12+ features) successfully:\n"
                f" - Metadata: token={fv.token_address}, wallet={fv.wallet_source}, signature={fv.signature}\n"
                f" - On-Chain: size=${fv.position_size_usd:.2f}, age={fv.token_age_minutes:.1f}m, "
                f"depth=${fv.liquidity_pool_depth:.2f}, slippage={fv.slippage_actual}, cluster={fv.cluster_score}\n"
                f" - History: win_rate={fv.win_rate_30d:.2f}, avg_hold={fv.avg_holding_time_minutes:.1f}m, "
                f"size=${fv.typical_trade_size_usd:.2f}, exit_pattern={fv.past_exit_pattern_score:.2f}\n"
                f" - Market: momentum={fv.sol_usd_momentum}, ratio={fv.token_volume_liquidity_ratio:.4f}, hour={fv.hour_of_day_utc} UTC"
            )
            # Run inference!
            pred = await self.inference_engine.run_inference(fv)
            logger.info(
                f"[XGBOOST INFERENCE] Inference Result for {token_address}:\n"
                f" - Direction: {pred['direction']}\n"
                f" - Confidence: {pred['confidence_score']:.4f}\n"
                f" - Target Price Estimate Offset: {pred['target_price_estimate']:+.2%}"
            )
            
            # Pack prediction into PredictionResult object
            from app.domain.models import PredictionResult
            pred_result = PredictionResult(
                direction=pred["direction"],
                confidence_score=pred["confidence_score"],
                target_price_estimate=pred["target_price_estimate"],
                token_address=token_address,
                wallet_source=wallet_source,
                signature=trigger_event["signature"],
                timestamp=trigger_event["timestamp_utc"]
            )
            
            # Evaluate safety checks
            await self.safety_check_gate.evaluate_safety(pred_result, fv)
            
        except Exception as e:
            logger.error(f"[ML PIPELINE] Error in stub feature extraction & inference: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create SQLite database tables on startup
    Base.metadata.create_all(bind=engine)
    
    # Initialize SQLite session and dependencies
    from app.infrastructure.database.session import SessionLocal
    from app.infrastructure.database.repository import (
        SQLAlchemyWalletRepository,
        SQLAlchemyFilterLogRepository,
        SQLAlchemyCooldownRepository,
        SQLAlchemyTradeHistoryRepository,
        SQLAlchemyModelRegistryRepository,
        SQLAlchemyPositionRepository
    )
    from app.blockchain.monitor import SolanaWebSocketMonitor
    from app.infrastructure.blockchain.token_service import SolanaTokenInfoService, SolanaTokenSafetyService
    from app.use_cases.safety_check_gate import SafetyCheckGate
    from app.use_cases.trigger_engine import TriggerEngine
    from app.use_cases.relevance_filter import RelevanceFilter
    from app.use_cases.monitor_wallets import MonitorWalletsUseCase
    from app.use_cases.auto_trade_executor import AutoTradeExecutor
    from app.use_cases.dashboard_query import DashboardQueryService
    from app.use_cases.retrain_scheduler import RetrainScheduler
    import asyncio

    db = SessionLocal()
    app.state.db = db
    try:
        wallet_repo = SQLAlchemyWalletRepository(db)
        filter_log_repo = SQLAlchemyFilterLogRepository(db)
        cooldown_repo = SQLAlchemyCooldownRepository(db)
        trade_history_repo = SQLAlchemyTradeHistoryRepository(db)
        model_registry_repo = SQLAlchemyModelRegistryRepository(db)
        position_repo = SQLAlchemyPositionRepository(db)
        
        token_info_service = SolanaTokenInfoService()
        safety_service = SolanaTokenSafetyService()
        safety_check_gate = SafetyCheckGate(safety_service, filter_log_repo)
        
        ml_pipeline = StubMLPipeline(
            trade_history_repo=trade_history_repo,
            token_info_service=token_info_service,
            model_registry_repo=model_registry_repo,
            safety_check_gate=safety_check_gate
        )
        
        auto_trade_executor = AutoTradeExecutor(
            position_repo=position_repo,
            cooldown_repo=cooldown_repo,
            model_registry_repo=model_registry_repo,
            trade_history_repo=trade_history_repo
        )
        safety_check_gate.auto_trade_executor = auto_trade_executor
        
        dashboard_query_service = DashboardQueryService(
            trade_history_repo=trade_history_repo,
            wallet_repo=wallet_repo,
            position_repo=position_repo,
            model_registry_repo=model_registry_repo
        )
        app.state.dashboard_query_service = dashboard_query_service
        
        # F-10 Retraining scheduler
        retrain_scheduler = RetrainScheduler(
            trade_history_repo=trade_history_repo,
            model_registry_repo=model_registry_repo,
            inference_engine=ml_pipeline.inference_engine
        )
        app.state.retrain_scheduler = retrain_scheduler
        
        async def retrain_loop():
            try:
                await retrain_scheduler.retrain_model_if_needed()
            except Exception as e:
                logger.error(f"Error in startup retrain: {e}")
            while True:
                try:
                    await asyncio.sleep(3600) # Check every hour
                    await retrain_scheduler.retrain_model_if_needed()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in retrain loop: {e}")
                    
        app.state.retrain_task = asyncio.create_task(retrain_loop())
        
        trigger_engine = TriggerEngine(
            cooldown_repo=cooldown_repo,
            token_info_service=token_info_service,
            ml_pipeline=ml_pipeline
        )
        
        relevance_filter = RelevanceFilter(
            filter_log_repo=filter_log_repo,
            trigger_engine=trigger_engine,
            wallet_repo=wallet_repo
        )
        
        monitor = SolanaWebSocketMonitor()
        monitor_use_case = MonitorWalletsUseCase(wallet_repo, monitor, relevance_filter)
        await monitor_use_case.initialize_and_start()
        app.state.monitor_use_case = monitor_use_case
    except Exception as e:
        logger.error(f"Error starting Wallet Monitor on startup: {e}", exc_info=True)
        
    yield
    
    # Shutdown logic
    if hasattr(app.state, "monitor_use_case"):
        try:
            await app.state.monitor_use_case.stop()
        except Exception as e:
            logger.error(f"Error stopping monitor: {e}")
            
    if hasattr(app.state, "retrain_task"):
        app.state.retrain_task.cancel()
        
    db.close()


app = FastAPI(
    title=f"{settings.PROJECT_NAME} Backend",
    description="AI Smart Money Trading System Backend (5-Layer Architecture)",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for frontend origin (Vite default dev port)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
def read_root():
    return {"status": "online", "system": "Sumber Makmur Trading Engine"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        query_service = getattr(app.state, "dashboard_query_service", None)
        await manager.send_initial_state(websocket, query_service)
        while True:
            # Maintain connection, handle client events/pings if sent
            data = await websocket.receive_text()
            # In skeleton mode, simply bounce back a ping acknowledgement
            await websocket.send_json({"type": "ping_ack", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
