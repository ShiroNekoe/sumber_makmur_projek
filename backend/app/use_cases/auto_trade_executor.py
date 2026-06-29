import logging
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.domain.models import PredictionResult, FeatureVector, OpenPosition
from app.domain.interfaces import (
    IPositionRepository,
    ICooldownRepository,
    IModelRegistryRepository,
    ITokenInfoService,
    ITokenSafetyService,
)
from app.websocket.manager import manager as ws_manager

logger = logging.getLogger(__name__)


class AutoTradeExecutor:
    """
    F-08: Auto Trade Execution Use Case
    Executes trades automatically via mock pump.fun/Jupiter API based on AlertSignal.
    """
    def __init__(
        self,
        position_repo: IPositionRepository,
        cooldown_repo: ICooldownRepository,
        model_registry_repo: IModelRegistryRepository,
        trade_history_repo = None, # optional inject to query active positions or history
        token_info_service: Optional[ITokenInfoService] = None,
        token_safety_service: Optional[ITokenSafetyService] = None,
    ):
        self.position_repo = position_repo
        self.cooldown_repo = cooldown_repo
        self.model_registry_repo = model_registry_repo
        self.trade_history_repo = trade_history_repo

        # Diteruskan ke ParallelExecutionEngine (F-09) agar Lapis 3
        # (kill-switch) bisa polling data on-chain riil. Opsional dan
        # default None -- jika tidak disuntikkan, kill-switch tetap aktif
        # namun fail-open (lihat ParallelExecutionEngine._check_onchain_kill_signals).
        self.token_info_service = token_info_service
        self.token_safety_service = token_safety_service

        self.lock = asyncio.Lock()

    async def execute_trade(
        self,
        prediction: PredictionResult,
        feature_vector: FeatureVector
    ) -> Optional[OpenPosition]:
        async with self.lock:
            token_address = prediction.token_address
            wallet_source = prediction.wallet_source
            
            logger.info(f"[AUTO TRADE] Starting trade execution check for token: {token_address}")

            # 1. Check Correlation Cap (F-16)
            try:
                open_positions = await self.position_repo.get_open_positions()
            except Exception as db_err:
                logger.error(f"[AUTO TRADE] [BLOCKED] Database query for open positions failed: {db_err}. Blocking trade as fail-safe.", exc_info=True)
                return None

            max_positions = getattr(settings, "RISK_MAX_CONCURRENT_POSITIONS", 3)
            if len(open_positions) >= max_positions:
                logger.warning(
                    f"[AUTO TRADE] [BLOCKED] Correlation cap reached. "
                    f"Token: {token_address} blocked. "
                    f"Active positions: {len(open_positions)}/{max_positions}. "
                    f"Timestamp: {datetime.now(timezone.utc).isoformat()}"
                )
                # Emit position cap reached event to F-07 dashboard (info level)
                await ws_manager.broadcast({
                    "type": "position_cap_reached",
                    "data": {
                        "event": "POSITION_CAP_REACHED",
                        "open_count": len(open_positions),
                        "max_count": max_positions,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                })
                return None
                
            # Check if this wallet-token is already in cooldown or active (F-14 check)
            cooldown = await self.cooldown_repo.get_cooldown(wallet_source, token_address)
            if cooldown and cooldown.active_position_id:
                logger.warning(f"[AUTO TRADE] [BLOCKED] Active position already exists for ({wallet_source}, {token_address})")
                return None
            
            # 2. Sizing: 1% risk per trade
            # Position Size USD = (Equity * Risk Pct) / Distance to SL (in Pct)
            # Default equity = 10000.0 USD
            equity = 10000.0 
            risk_pct = settings.RISK_PCT_PER_TRADE
            sl_distance_pct = 0.10 # default 10% distance to SL
            
            position_size_usd = (equity * risk_pct) / sl_distance_pct
            
            # Guard against exceeding max position size limit
            max_pos_size = getattr(settings, "RISK_MAX_POSITION_SIZE_USD", 5000.0)
            if position_size_usd > max_pos_size:
                position_size_usd = max_pos_size
                
            logger.info(f"[AUTO TRADE] Sizing check: equity=${equity}, risk={risk_pct:.1%}, sl_dist={sl_distance_pct:.1%}. Target size: ${position_size_usd:.2f}")
            
            # 3. Place Order (Mock pump.fun API call with F-19 retries)
            try:
                entry_price = None
                order_success = False
                max_attempts = 3
                
                for attempt in range(max_attempts):
                    try:
                        # Simulation hook to test entry order failure
                        if token_address == "FailEntryTokenxxxxxxxxxxxxxxxxxxxxxxx":
                            raise IOError("pump.fun swap failed: Insufficient liquidity pool")
                            
                        if self.token_info_service:
                            token_info = await self.token_info_service.get_token_info(token_address)
                            entry_price = token_info.get("price_usd", 1.0)
                        else:
                            entry_price = 1.0
                        slippage_estimate = 0.005 # 0.5% slippage
                        
                        # Verify slippage tolerance
                        max_slippage = getattr(settings, "SLIPPAGE_TOLERANCE", 0.02)
                        if slippage_estimate > max_slippage:
                            raise ValueError(f"Slippage too high: {slippage_estimate} > {max_slippage}")
                        
                        # Mock Sign and Place Order
                        logger.info(f"[AUTO TRADE] Signing order using local encrypted wallet keypair (attempt {attempt+1})...")
                        await asyncio.sleep(0.05) # Simulate network/signing latency
                        
                        # Confirm transaction (poll status)
                        logger.info(f"[AUTO TRADE] Order placed on pump.fun. TX: mock_tx_{uuid.uuid4().hex[:12]}. Confirming...")
                        await asyncio.sleep(0.05)
                        
                        order_success = True
                        break
                    except Exception as e:
                        logger.warning(f"[AUTO TRADE] Order placement attempt {attempt+1} failed: {e}")
                        if attempt < max_attempts - 1:
                            await asyncio.sleep(0.1) # small delay before retry
                            
                if not order_success:
                    # If still fails: log as 'missed_entry' (TIDAK masuk closed_trades) and emit alert 'entry_failed'
                    logger.error(f"[AUTO TRADE] [FAILED] Entry order placement failed after {max_attempts} attempts.")
                    
                    from app.core.error_handler import log_system_error, ErrorType, ErrorSeverity
                    await log_system_error(
                        error_type=ErrorType.ENTRY_FAILED,
                        severity=ErrorSeverity.ERROR,
                        context=f"Failed to place entry order for token {token_address} after {max_attempts} attempts.",
                        recovery_action="missed_entry: log to dataset only, do not proceed",
                        resolution_status="failed"
                    )
                    return None
                    
                # 4. Save state to SQLite (state = OPEN)
                position_id = f"pos_{uuid.uuid4().hex[:8]}"
                active_model = await self.model_registry_repo.get_active_model()
                model_ver = active_model.model_version if active_model else "v0"
                
                open_pos = OpenPosition(
                    position_id=position_id,
                    wallet_source=wallet_source,
                    token_address=token_address,
                    state="OPEN",
                    entry_price=entry_price,
                    entry_ts=datetime.now(timezone.utc),
                    sl_initial=entry_price * (1 - sl_distance_pct),
                    risk_pct=risk_pct,
                    position_size_usd=position_size_usd,
                    trailing_active=False,
                    trailing_level=None,
                    peak_r_multiple=0.0,
                    confidence_score=prediction.confidence_score,
                    model_version=model_ver
                )
                
                await self.position_repo.add_position(open_pos)
                
                # Set cooldown state for F-14 Cooldown State
                from app.domain.models import CooldownState
                await self.cooldown_repo.set_cooldown(CooldownState(
                    wallet_address=wallet_source,
                    token_address=token_address,
                    last_trigger_ts=datetime.now(timezone.utc),
                    active_position_id=position_id
                ))
                
                logger.info(f"[AUTO TRADE] [CONFIRMED] Position {position_id} opened successfully for {token_address} at price ${entry_price}!")
                
                # 5. Broadcast trade_opened event
                trade_opened_event = {
                    "event": "trade_opened",
                    "position_id": position_id,
                    "token_address": token_address,
                    "wallet_source": wallet_source,
                    "entry_price": entry_price,
                    "position_size_usd": position_size_usd,
                    "sl_level": open_pos.sl_initial,
                    "timestamp": open_pos.entry_ts.isoformat()
                }
                await ws_manager.broadcast(trade_opened_event)
                
                # 6. Trigger F-09 Parallel Protection
                from app.execution.executor import ParallelExecutionEngine
                engine = ParallelExecutionEngine(
                    open_pos,
                    self.position_repo,
                    self.cooldown_repo,
                    self.model_registry_repo,
                    self.trade_history_repo,
                    token_info_service=self.token_info_service,
                    token_safety_service=self.token_safety_service,
                )
                asyncio.create_task(engine.start_monitoring())
                
                return open_pos
                
            except Exception as e:
                logger.error(f"[AUTO TRADE] Error executing trade: {e}", exc_info=True)
                return None