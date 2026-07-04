import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.core.config import settings
from app.domain.interfaces import ITriggerEngine, ICooldownRepository, ITokenInfoService, IMLPipeline, IPositionRepository
from app.domain.models import CooldownState

logger = logging.getLogger(__name__)


class TriggerEngine(ITriggerEngine):
    """
    Layer 1 Use Case: Wallet Movement Trigger Engine (F-03)
    Evaluates sliding time windows, cooldowns, and hard filters before firing the ML pipeline.
    """
    def __init__(
        self,
        cooldown_repo: ICooldownRepository,
        token_info_service: ITokenInfoService,
        ml_pipeline: IMLPipeline,
        position_repo: Optional[IPositionRepository] = None
    ):
        self.cooldown_repo = cooldown_repo
        self.token_info_service = token_info_service
        self.ml_pipeline = ml_pipeline
        self.position_repo = position_repo
        
        # Sliding Window Storage: token_mint -> List of event dicts
        self.window_events: Dict[str, List[dict]] = {}
        self.lock = asyncio.Lock()

    async def trigger_event(self, event_data: dict) -> None:
        """
        Processes an event that passed F-02 Relevance Filter:
        1. Evaluates Cooldown rules.
        """
        # F-15 Degraded Mode Check
        from app.blockchain.monitor import SolanaWebSocketMonitor
        if SolanaWebSocketMonitor.degraded_mode:
            logger.warning("[TRIGGER ENGINE] [BLOCKED] Degraded mode active. Discarding new trigger event.")
            return

        wallet_address = event_data["wallet_address"]
        token_mint = event_data["token_mint"]
        signature = event_data["signature"]
        timestamp = event_data.get("timestamp_utc") or datetime.now(timezone.utc)

        # Ensure UTC timezone
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        # 1. Cooldown Check — returns (is_in_cooldown, was_just_cleared)
        is_in_cooldown, cooldown_was_cleared = await self._check_cooldown(wallet_address, token_mint)
        if is_in_cooldown:
            logger.info(
                f"[TRIGGER ENGINE] [IGNORED] Signature: {signature}. "
                f"Pair ({wallet_address}, {token_mint}) is currently in cooldown."
            )
            return

        # Propagate the cleared flag so downstream TradeGuard skips idempotency re-check
        if cooldown_was_cleared:
            event_data["cooldown_already_cleared"] = True

        # 2. Hard Filters Check (Token Age & Liquidity Floor) - Skip if F-13 already processed this event
        if "token_age_minutes" not in event_data or "liquidity_pool_depth" not in event_data:
            token_info = await self.token_info_service.get_token_info(token_mint)
            if not self._passes_hard_filters(token_mint, token_info):
                return

        # 3. Sliding Window Evaluation (Thread-safe lock for in-memory states)
        async with self.lock:
            # Initialize list for this token
            if token_mint not in self.window_events:
                self.window_events[token_mint] = []

            # Cleanup expired events from the window
            cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=settings.TRIGGER_WINDOW_MINUTES)
            self.window_events[token_mint] = [
                ev for ev in self.window_events[token_mint]
                if (ev["timestamp"].replace(tzinfo=timezone.utc) if ev["timestamp"].tzinfo is None else ev["timestamp"]) > cutoff_time
            ]

            # Add current event to window
            self.window_events[token_mint].append({
                "wallet_address": wallet_address,
                "timestamp": timestamp
            })

            # Check trigger condition
            trigger_fired = False
            confidence_boost = False
            
            if settings.TRIGGER_MODE == "OR":
                # OR Mode: any single event triggers the pipeline immediately
                trigger_fired = True
                confidence_boost = False
            elif settings.TRIGGER_MODE == "AND":
                # AND Mode: unique wallets trading the same token within 5m window >= 2
                unique_wallets = {ev["wallet_address"] for ev in self.window_events[token_mint]}
                if len(unique_wallets) >= 2:
                    trigger_fired = True
                    confidence_boost = True

            if trigger_fired:
                logger.info(
                    f"[TRIGGER ENGINE] [FIRED] Condition matched in {settings.TRIGGER_MODE} mode "
                    f"for token {token_mint}. Triggering ML Pipeline Layer 2..."
                )
                
                # Clear window for this token to prevent duplicate immediate triggers
                self.window_events[token_mint] = []
                
                # 4. Trigger ML Pipeline
                await self.ml_pipeline.analyze_token(
                    token_address=token_mint,
                    wallet_source=wallet_address,
                    confidence_boost=confidence_boost,
                    signature=signature,
                    timestamp=timestamp
                )
                
                # 5. Set Cooldown for this wallet/token pair using the event timestamp
                await self._set_cooldown(wallet_address, token_mint, timestamp=timestamp)

    async def _check_cooldown(self, wallet_address: str, token_mint: str) -> tuple[bool, bool]:
        """
        Returns (is_in_cooldown: bool, cooldown_was_just_cleared: bool).
        The second value indicates the cooldown was expired and just deleted —
        downstream TradeGuard must skip idempotency re-check for this event.
        """
        cooldown = await self.cooldown_repo.get_cooldown(wallet_address, token_mint)
        if not cooldown:
            return False, False

        # If active_position_id is linked, check if the position is still active
        if cooldown.active_position_id:
            if self.position_repo:
                pos = await self.position_repo.get_position(cooldown.active_position_id)
                if pos and pos.state in ["OPEN", "PENDING_ENTRY", "EXITING"]:
                    return True, False
                else:
                    # Position closed or failed, reset cooldown
                    logger.info(f"[TRIGGER ENGINE] Cooldown reset: linked position {cooldown.active_position_id} is closed.")
                    await self.cooldown_repo.delete_cooldown(wallet_address, token_mint)
                    return False, True  # cleared=True → TradeGuard must not re-block
            # Fallback if position_repo not injected
            return True, False
        else:
            # Active position is None (pending execution phase). Use a 5-minute timeout.
            last_ts = cooldown.last_trigger_ts
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if elapsed < 300.0:  # 5 minutes pending window
                return True, False
            else:
                logger.warning(f"[TRIGGER ENGINE] Cooldown pending timeout for ({wallet_address}, {token_mint}). Resetting.")
                await self.cooldown_repo.delete_cooldown(wallet_address, token_mint)
                return False, True  # cleared=True → TradeGuard must not re-block

    def _passes_hard_filters(self, token_mint: str, token_info: dict) -> bool:
        age_minutes = token_info.get("age_minutes", 0.0)
        liquidity_usd = token_info.get("liquidity_usd", 0.0)
        symbol = token_info.get("token_symbol", "UNKNOWN")

        # Age check
        if age_minutes < settings.MIN_TOKEN_AGE_MINUTES:
            logger.info(
                f"[TRIGGER ENGINE] [REJECTED] Token {symbol} ({token_mint}) is too new: "
                f"{age_minutes:.1f}m < {settings.MIN_TOKEN_AGE_MINUTES}m threshold."
            )
            return False

        # Liquidity check
        if liquidity_usd < settings.MIN_LIQUIDITY_USD:
            logger.info(
                f"[TRIGGER ENGINE] [REJECTED] Token {symbol} ({token_mint}) has low liquidity: "
                f"${liquidity_usd:.2f} < ${settings.MIN_LIQUIDITY_USD} threshold."
            )
            return False

        return True

    async def _set_cooldown(self, wallet_address: str, token_mint: str, timestamp: Optional[datetime] = None) -> None:
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        cooldown = CooldownState(
            wallet_address=wallet_address,
            token_address=token_mint,
            last_trigger_ts=timestamp,
            active_position_id=None
        )
        await self.cooldown_repo.set_cooldown(cooldown)
        logger.info(f"[TRIGGER ENGINE] Cooldown initialized for ({wallet_address}, {token_mint}).")

