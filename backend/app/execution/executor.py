import logging
import asyncio
import random
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.core.config import settings
from app.domain.models import OpenPosition, ClosedTrade, OnchainEvent
from app.domain.interfaces import IPositionRepository, ICooldownRepository, IModelRegistryRepository, ITradeHistoryRepository
from app.websocket.manager import manager as ws_manager

logger = logging.getLogger(__name__)


class ParallelExecutionEngine:
    """
    F-09: Three-Layer Position Protection
    Runs concurrent protective layers for an open position to handle exits.
    - Lapis 1: Stop Loss (Fixed -1R baseline)
    - Lapis 2: Staged Trailing Take Profit
    - Lapis 3: On-Chain Independent Kill-Switch
    """
    def __init__(
        self,
        position: OpenPosition,
        position_repo: IPositionRepository,
        cooldown_repo: ICooldownRepository,
        model_registry_repo: IModelRegistryRepository,
        trade_history_repo: ITradeHistoryRepository
    ):
        self.position = position
        self.position_repo = position_repo
        self.cooldown_repo = cooldown_repo
        self.model_registry_repo = model_registry_repo
        self.trade_history_repo = trade_history_repo
        
        self.current_price = position.entry_price or 1.0
        self.peak_price = self.current_price
        self.sl_initial = position.sl_initial
        self.r_val = abs(self.current_price - self.sl_initial) # 1R distance in absolute price
        
        self.exited = False
        self.lock = asyncio.Lock()
        self.tasks = []

    async def start_monitoring(self):
        """
        Spawns the three parallel protective tasks concurrently.
        """
        logger.info(f"[PROTECTION] Initiating 3-layer parallel protection for position {self.position.position_id}")
        self.tasks = [
            asyncio.create_task(self._run_price_monitor_loop())
        ]
        
    async def _run_price_monitor_loop(self):
        """
        Combined monitor loop simulating real-time price feed updates
        and on-chain logs, evaluating all three protection layers concurrently.
        """
        try:
            while not self.exited:
                await asyncio.sleep(1.0) # Check every 1 second
                
                # Simulating a price random walk for testing/demonstration
                price_change = random.uniform(-0.04, 0.05) # Average upward drift
                self.current_price = max(0.01, self.current_price * (1 + price_change))
                
                # 1. Update Peak Price and R-multiples
                r_current = (self.current_price - self.position.entry_price) / (self.position.entry_price - self.sl_initial)
                
                if self.current_price > self.peak_price:
                    self.peak_price = self.current_price
                    self.position.peak_r_multiple = (self.peak_price - self.position.entry_price) / (self.position.entry_price - self.sl_initial)
                
                # 2. Evaluate Layer 1: Price-based Stop Loss
                if self.current_price <= self.sl_initial:
                    logger.info(f"[PROTECTION] [L1] Stop Loss hit at price ${self.current_price:.4f} (SL: ${self.sl_initial:.4f})")
                    await self.execute_exit("SL")
                    break
                    
                # 3. Evaluate Layer 2: Staged Trailing Take Profit
                trailing_sl = None
                peak_r = self.position.peak_r_multiple
                
                if peak_r >= 2.0:
                    self.position.trailing_active = True
                    if peak_r < 5.0:
                        trail_pct = 0.25 # 25% from peak
                    elif peak_r < 10.0:
                        trail_pct = 0.15 # 15% from peak
                    else:
                        trail_pct = 0.10 # 10% from peak
                        
                    trailing_sl = self.peak_price * (1 - trail_pct)
                    self.position.trailing_level = trailing_sl
                    
                    if self.current_price <= trailing_sl:
                        logger.info(f"[PROTECTION] [L2] Trailing TP hit at price ${self.current_price:.4f} (Trailing SL: ${trailing_sl:.4f})")
                        await self.execute_exit("trailing_tp")
                        break

                # 4. Update position state in DB
                await self.position_repo.update_position(self.position)
                
                # 5. Evaluate Layer 3: On-Chain Kill-Switch
                # Simulate an on-chain emergency event (LP removal, dev dumping, etc.) with low probability
                if random.random() < 0.01:
                    reasons = ["kill_switch_lp", "kill_switch_dev_dump", "kill_switch_slippage"]
                    chosen_reason = random.choice(reasons)
                    logger.warning(f"[PROTECTION] [L3] On-chain emergency event detected: {chosen_reason}!")
                    await self.execute_exit(chosen_reason)
                    break
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[PROTECTION] Error in price monitor loop: {e}", exc_info=True)

    async def execute_exit(self, reason: str):
        """
        Executes a market order exit atomically, updates position/cooldown DB state,
        records the closed trade, and broadcasts websocket updates.
        """
        async with self.lock:
            if self.exited:
                return
            self.exited = True
            
            logger.warning(f"[PROTECTION] Exiting position {self.position.position_id} due to {reason}!")
            
            try:
                # 1. Place Market order to mock pump.fun/Jupiter API
                await asyncio.sleep(0.1) # Simulate execution latency
                exit_price = self.current_price
                
                # 2. Calculate PnL and R-multiple
                pnl_pct_actual = (exit_price - self.position.entry_price) / self.position.entry_price
                r_multiple = (exit_price - self.position.entry_price) / (self.position.entry_price - self.sl_initial)
                
                # 3. Labeling: BUY_BENAR (>= +3R), SALAH (<= -1R), HOLD (between)
                if r_multiple >= 3.0:
                    label = "BUY_BENAR"
                elif r_multiple <= -1.0:
                    label = "SALAH"
                else:
                    label = "HOLD"
                    
                # 4. Save to closed_trades DB
                closed_trade = ClosedTrade(
                    trade_id=f"tr_{uuid.uuid4().hex[:8]}",
                    wallet_source=self.position.wallet_source,
                    token_address=self.position.token_address,
                    token_symbol="SIM_TOKEN",
                    signal_ts=self.position.entry_ts or datetime.now(timezone.utc),
                    entry_ts=self.position.entry_ts or datetime.now(timezone.utc),
                    exit_ts=datetime.now(timezone.utc),
                    direction="BUY",
                    confidence_score=self.position.confidence_score,
                    safety_check_passed=True,
                    entry_price=self.position.entry_price or 1.0,
                    exit_price=exit_price,
                    position_size_usd=self.position.position_size_usd,
                    risk_pct=self.position.risk_pct,
                    pnl_pct_actual=pnl_pct_actual,
                    r_multiple=r_multiple,
                    label=label,
                    holding_time_minutes=int(max(1.0, (datetime.now(timezone.utc) - (self.position.entry_ts or datetime.now(timezone.utc))).total_seconds() / 60.0)),
                    exit_reason=reason,
                    is_paper_trade=True,
                    is_bootstrap=False,
                    model_version=self.position.model_version
                )
                
                if self.trade_history_repo:
                    await self.trade_history_repo.add_closed_trade(closed_trade)
                    
                # 5. Remove or close position from position repo
                # Changing state to 'CLOSED' removes it from the get_open_positions query
                self.position.state = "CLOSED"
                self.position.entry_price = exit_price
                await self.position_repo.update_position(self.position)
                
                # 6. Delete cooldown active position mapping (F-14 reset)
                await self.cooldown_repo.delete_cooldown(self.position.wallet_source, self.position.token_address)
                
                # 7. Broadcast trade_closed event to websocket
                trade_closed_event = {
                    "event": "trade_closed",
                    "position_id": self.position.position_id,
                    "token_address": self.position.token_address,
                    "wallet_source": self.position.wallet_source,
                    "entry_price": self.position.entry_price,
                    "exit_price": exit_price,
                    "pnl_pct_actual": pnl_pct_actual,
                    "r_multiple": r_multiple,
                    "exit_reason": reason,
                    "timestamp": closed_trade.exit_ts.isoformat()
                }
                await ws_manager.broadcast(trade_closed_event)
                
                logger.info(f"[PROTECTION] [CLOSED] Position {self.position.position_id} closed at price ${exit_price:.4f} (R-mult: {r_multiple:.2f}R)")
                
            except Exception as e:
                logger.error(f"[PROTECTION] Error executing trade exit: {e}", exc_info=True)
