# Trigger auto-reload with active on-chain wallets
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


from datetime import datetime, timezone, timedelta


class StubMLPipeline(IMLPipeline):
    def __init__(self, trade_history_repo, token_info_service, model_registry_repo, safety_check_gate):
        from app.ml_pipeline.inference import FeatureExtractor, XGBoostInferenceEngine
        self.feature_extractor = FeatureExtractor(trade_history_repo, token_info_service)
        self.inference_engine = XGBoostInferenceEngine(model_registry_repo, trade_history_repo)
        self.safety_check_gate = safety_check_gate

    async def analyze_token(
        self,
        token_address: str,
        wallet_source: str,
        confidence_boost: bool,
        signature: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        logger.info(
            f"[ML PIPELINE] [STUB] Triggered XGBoost inference analysis for token: {token_address} "
            f"from wallet: {wallet_source} (confidence boost: {confidence_boost})."
        )
        try:
            # Simulate trigger event using passed signature and timestamp if available
            trigger_event = {
                "token_address": token_address,
                "wallet_address": wallet_source,
                "signature": signature or ("simulated_sig_" + datetime.now(timezone.utc).strftime("%H%M%S")),
                "amount_usd": 1500.0,
                "confidence_boost": confidence_boost,
                "timestamp_utc": timestamp or datetime.now(timezone.utc)
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
                timestamp=trigger_event["timestamp_utc"],
                cooldown_already_cleared=trigger_event.get("cooldown_already_cleared", False)
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

    import os
    import shutil
    from sqlalchemy import text

    db_file = "sumber_makmur.db"
    backup_file = "sumber_makmur_backup.db"
    db_ok = False
    
    db = SessionLocal()
    
    # F-19 Register SessionLocal factory to central error handler
    from app.core import error_handler
    error_handler.register_session_factory(SessionLocal)

    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as db_err:
        logger.critical(f"[DATABASE CORRUPT] Database health check failed: {db_err}")
        if os.path.exists(backup_file):
            logger.warning("[DATABASE CORRUPT] Attempting recovery from daily backup...")
            try:
                db.close()
                shutil.copyfile(backup_file, db_file)
                db = SessionLocal()
                db.execute(text("SELECT 1"))
                db_ok = True
                logger.info("[DATABASE RECOVERY] Database restored from backup successfully.")
            except Exception as backup_err:
                logger.critical(f"[DATABASE RECOVERY] Failed to restore database from backup: {backup_err}. System HALTING.")
                raise SystemExit("Database corrupted and backup restore failed. Halt.")
        else:
            logger.critical("[DATABASE CORRUPT] No backup database found. System HALTING.")
            raise SystemExit("Database corrupted and no backup found. Halt.")

    if db_ok:
        try:
            shutil.copyfile(db_file, backup_file)
            logger.info(f"[DATABASE] Daily backup created successfully: {backup_file}")
        except Exception as backup_err:
            logger.warning(f"[DATABASE] Failed to create daily backup: {backup_err}")

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
            trade_history_repo=trade_history_repo,
            token_info_service=token_info_service,
            token_safety_service=safety_service
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
            ml_pipeline=ml_pipeline,
            position_repo=position_repo
        )
        
        # F-12 Dynamic Wallet Discovery background service
        from app.use_cases.wallet_discovery import WalletDiscoveryService
        wallet_discovery_service = WalletDiscoveryService(wallet_repo, token_info_service)
        await wallet_discovery_service.start()
        app.state.wallet_discovery_service = wallet_discovery_service

        # F-13 Token Age & Liquidity Hard Filter
        from app.infrastructure.database.repository import SQLAlchemyHardFilterLogRepository
        from app.use_cases.hard_filter import TokenAgeLiquidityHardFilter
        hard_filter_log_repo = SQLAlchemyHardFilterLogRepository(db)
        hard_filter = TokenAgeLiquidityHardFilter(
            token_info_service=token_info_service,
            trigger_engine=trigger_engine,
            hard_filter_log_repo=hard_filter_log_repo
        )
        app.state.hard_filter = hard_filter

        relevance_filter = RelevanceFilter(
            filter_log_repo=filter_log_repo,
            trigger_engine=trigger_engine,
            wallet_repo=wallet_repo,
            wallet_discovery_service=wallet_discovery_service,
            hard_filter=hard_filter
        )
        # F-17 Crash Recovery Service
        from app.use_cases.crash_recovery import CrashRecoveryService
        recovery_service = CrashRecoveryService(
            position_repo=position_repo,
            cooldown_repo=cooldown_repo,
            model_registry_repo=model_registry_repo,
            trade_history_repo=trade_history_repo,
            token_info_service=token_info_service,
            retrain_scheduler=retrain_scheduler
        )
        # F-11 Startup historical bootstrap model training (runs in background to prevent startup block)
        asyncio.create_task(ml_pipeline.inference_engine.ensure_model_ready())

        await recovery_service.run_recovery()
        
        if os.getenv("SIMULATION_MODE") == "True":
            from app.blockchain.monitor import SolanaMonitorSimulator
            active_wallets = await wallet_repo.get_active_wallets()
            wallet_addresses = [w.wallet_address for w in active_wallets]
            monitor = SolanaMonitorSimulator(wallets=wallet_addresses)
            logger.warning("[STARTUP] SIMULATION_MODE active. Running Solana Monitor Simulator instead of live WebSocket client.")
        else:
            monitor = SolanaWebSocketMonitor()
            
        monitor_use_case = MonitorWalletsUseCase(wallet_repo, monitor, relevance_filter)
        await monitor_use_case.initialize_and_start()
        app.state.monitor_use_case = monitor_use_case

        # Initialize Portfolio & PnL Services
        from app.use_cases.portfolio_service import PortfolioService
        from app.use_cases.pnl_calculator import PnLCalculator
        from app.infrastructure.blockchain.wallet_manager import load_wallet_from_env

        portfolio_service = PortfolioService(token_info_service=token_info_service)
        app.state.portfolio_service = portfolio_service

        pnl_calculator = PnLCalculator(
            position_repo=position_repo,
            trade_history_repo=trade_history_repo,
            portfolio_service=portfolio_service,
            db_session=db  # Pass db session for equity snapshot recording
        )
        app.state.pnl_calculator = pnl_calculator

        async def portfolio_polling_loop():
            """
            Polls portfolio and records equity snapshots for accurate history tracking.
            
            Strategy:
            1. Seed historical snapshots if table is empty
            2. Detect significant portfolio changes (top-ups, trades, etc.)
            3. Record snapshot immediately when change detected
            4. Record hourly baseline snapshot for continuity
            5. Ensure initial snapshot exists for new wallets
            """
            await asyncio.sleep(5)  # Wait for app to initialize briefly
            
            # Retrieve active wallet address to seed history
            try:
                from app.infrastructure.blockchain.wallet_manager import load_wallet_from_env
                keypair = load_wallet_from_env()
                pubkey_str = str(keypair.pubkey()) if keypair else "2fRGriSp8o32KdV1K8yxic1ZBLnqJXRiXpQK9ovCebf8"
                await pnl_calculator.populate_historical_snapshots_if_empty(pubkey_str)
            except Exception as e:
                logger.error(f"[PORTFOLIO POLLING] Failed to populate historical snapshots: {e}")

            last_portfolio_value = None
            last_snapshot_time = None
            polling_interval = 60  # Poll every 60 seconds for fast change detection
            
            while True:
                try:
                    keypair = load_wallet_from_env()
                    pubkey_str = str(keypair.pubkey()) if keypair else "2fRGriSp8o32KdV1K8yxic1ZBLnqJXRiXpQK9ovCebf8"
                    
                    summary = await pnl_calculator.get_portfolio_summary(pubkey_str)
                    current_portfolio_value = summary["portfolio_value_usd"]
                    
                    try:
                        sol_holding = next((h for h in summary.get("holdings", []) if h["symbol"] == "SOL"), None)
                        sol_balance = sol_holding["amount"] if sol_holding else 0.0
                        sol_price = sol_holding["price_usd"] if sol_holding else 77.34
                        token_value = sum(h["value_usd"] for h in summary.get("holdings", []) if h["symbol"] != "SOL")
                        
                        # Ensure wallet has initial snapshot
                        await pnl_calculator.ensure_initial_snapshot(
                            wallet_address=pubkey_str,
                            portfolio_value_usd=current_portfolio_value,
                            sol_balance=sol_balance,
                            sol_price_usd=sol_price
                        )
                        
                        should_record_snapshot = False
                        trigger_reason = None
                        
                        # Check for significant portfolio changes
                        if last_portfolio_value is not None:
                            value_change = abs(current_portfolio_value - last_portfolio_value)
                            value_change_pct = value_change / last_portfolio_value if last_portfolio_value > 0 else 0
                            
                            # Trigger snapshot if: >1% change OR >$10 change
                            if value_change_pct > 0.01 or value_change > 10.0:
                                should_record_snapshot = True
                                trigger_reason = f"significant_change ({value_change_pct*100:.1f}% or ${value_change:.2f})"
                        
                        # Trigger hourly snapshot for baseline
                        now = datetime.now(timezone.utc)
                        
                        if last_snapshot_time is None or (now - last_snapshot_time).total_seconds() >= 3600:
                            should_record_snapshot = True
                            if trigger_reason is None:
                                trigger_reason = "hourly_baseline"
                            else:
                                trigger_reason += " + hourly_baseline"
                        
                        # Record snapshot if triggered
                        if should_record_snapshot:
                            await pnl_calculator.record_equity_snapshot(
                                wallet_address=pubkey_str,
                                portfolio_value_usd=current_portfolio_value,
                                sol_balance=sol_balance,
                                sol_price_usd=sol_price,
                                token_holdings_value_usd=token_value,
                                realized_pnl_usd=summary["realized_pnl_usd"],
                                unrealized_pnl_usd=summary["unrealized_pnl_usd"],
                                trigger_type="periodic",
                                trigger_reason=trigger_reason
                            )
                            last_snapshot_time = now
                        
                        last_portfolio_value = current_portfolio_value
                        
                    except Exception as snap_err:
                        logger.error(f"[PORTFOLIO POLLING] Error in snapshot recording: {snap_err}", exc_info=True)
                    
                    # Broadcast portfolio update to websocket clients
                    await manager.broadcast_event("portfolio_update", summary)
                    
                except asyncio.CancelledError:
                    break
                except Exception as ex:
                    logger.error(f"[PORTFOLIO POLLING] Error in polling loop: {ex}")
                
                # Poll frequently for change detection, but record hourly
                await asyncio.sleep(polling_interval)


        app.state.portfolio_polling_task = asyncio.create_task(portfolio_polling_loop())

        # Start config watchdog hot-reload loop
        if settings.CONFIG_FILE_PATH:
            app.state.config_watch_task = asyncio.create_task(settings.watch_config_loop())
            logger.info("[STARTUP] Spawned background task for config hot-reload.")
    except Exception as e:
        logger.error(f"Error starting Wallet Monitor on startup: {e}", exc_info=True)
        
    yield
    
    # Shutdown logic
    if hasattr(app.state, "portfolio_polling_task"):
        app.state.portfolio_polling_task.cancel()
        logger.info("[SHUTDOWN] Cancelled portfolio polling task.")

    if hasattr(app.state, "config_watch_task"):
        app.state.config_watch_task.cancel()
        logger.info("[SHUTDOWN] Cancelled config hot-reload task.")

    if hasattr(app.state, "monitor_use_case"):
        try:
            await app.state.monitor_use_case.stop()
        except Exception as e:
            logger.error(f"Error stopping monitor: {e}")

    if hasattr(app.state, "wallet_discovery_service"):
        try:
            await app.state.wallet_discovery_service.stop()
        except Exception as e:
            logger.error(f"Error stopping wallet discovery: {e}")
            
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