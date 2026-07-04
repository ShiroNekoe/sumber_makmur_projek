import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from app.core.config import settings
from app.domain.interfaces import IHardFilterLogRepository, ITokenInfoService, ITriggerEngine
from app.domain.models import HardFilterAuditLog

logger = logging.getLogger(__name__)


class TokenAgeLiquidityHardFilter:
    """
    F-13 Token Age & Liquidity Hard Filter Use Case (Enhancement B2)
    Intercepts the flow between Relevance Filter (F-02) and Trigger Engine (F-03)
    to enforce minimum age and liquidity limits, logging decisions for audits.
    """
    def __init__(
        self,
        token_info_service: ITokenInfoService,
        trigger_engine: ITriggerEngine,
        hard_filter_log_repo: IHardFilterLogRepository
    ):
        self.token_info_service = token_info_service
        self.trigger_engine = trigger_engine
        self.hard_filter_log_repo = hard_filter_log_repo
        
        # 5-minute cache: token_address -> (timestamp, data_dict)
        self.token_cache: Dict[str, Tuple[float, dict]] = {}
        self.cache_ttl = 300.0 # 5 minutes

    async def process_event(self, event_data: dict) -> None:
        token_mint = event_data.get("token_mint")
        signature = event_data.get("signature", "unknown")
        
        if not token_mint:
            logger.warning(f"[HARD FILTER] Event {signature} is missing token_mint. Skipping.")
            return

        logger.info(f"[HARD FILTER] Evaluating token age and liquidity for: {token_mint}")

        # Fetch token metadata (with 5-minute cache check)
        token_info = await self._get_token_info_cached(token_mint)
        
        passed = True
        reason = None
        
        age_minutes = 0.0
        liquidity_usd = 0.0
        
        if not token_info:
            # Conservative Approach: DexScreener down or token not found -> DISCARD
            passed = False
            reason = "token_not_found"
        else:
            age_minutes = token_info.get("age_minutes", 0.0)
            liquidity_usd = token_info.get("liquidity_usd", 0.0)
            symbol = token_info.get("token_symbol", "")
            
            # Check for fail-closed DexScreener offline fallback on mainnet
            is_live_network = "api.mainnet-beta.solana.com" in settings.SOLANA_RPC_URL
            is_fallback_token = symbol == "MOCK_TOKEN" and token_mint != "MOCK_TOKEN_ADDR"
            
            import os
            if is_live_network and is_fallback_token and os.getenv("SIMULATION_MODE") != "True":
                passed = False
                reason = "dexscreener_failed"
            elif age_minutes < settings.MIN_TOKEN_AGE_MINUTES:
                passed = False
                reason = f"age_too_low ({age_minutes:.1f}m < {settings.MIN_TOKEN_AGE_MINUTES}m)"
            elif liquidity_usd < settings.MIN_LIQUIDITY_USD:
                passed = False
                reason = f"liquidity_too_low (${liquidity_usd:.2f} < ${settings.MIN_LIQUIDITY_USD:.2f})"

        # Write to Hard Filter Audit Log
        log_id = uuid.uuid4().hex
        audit_log = HardFilterAuditLog(
            log_id=log_id,
            token_address=token_mint,
            age_minutes=age_minutes,
            liquidity_usd=liquidity_usd,
            passed=passed,
            reason=reason,
            timestamp=datetime.now(timezone.utc)
        )
        
        await self.hard_filter_log_repo.add_hard_filter_log(audit_log)

        if passed:
            logger.info(
                f"[HARD FILTER] [PASSED] Token {token_mint} passed hard filters "
                f"(Age: {age_minutes:.1f}m, Liquidity: ${liquidity_usd:.2f})"
            )
            # Enriched metadata
            event_data["token_age_minutes"] = age_minutes
            event_data["liquidity_pool_depth"] = liquidity_usd
            
            # Forward event to Trigger Engine (F-03)
            await self.trigger_engine.trigger_event(event_data)
        else:
            logger.warning(
                f"[HARD FILTER] [DISCARDED] Token {token_mint} failed hard filters. "
                f"Reason: {reason} (Age: {age_minutes:.1f}m, Liquidity: ${liquidity_usd:.2f})"
            )

    async def _get_token_info_cached(self, token_mint: str) -> Optional[dict]:
        # Check local 5-minute cache
        now = time.time()
        if token_mint in self.token_cache:
            cache_time, cached_data = self.token_cache[token_mint]
            if now - cache_time < self.cache_ttl:
                logger.debug(f"[HARD FILTER] [CACHE HIT] Using 5-min cache for {token_mint}")
                return cached_data

        # Fetch from SolanaTokenInfoService
        try:
            info = await self.token_info_service.get_token_info(token_mint)
            if info:
                self.token_cache[token_mint] = (now, info)
                return info
        except Exception as e:
            logger.error(f"[HARD FILTER] Error fetching token info from service: {e}")
            
        return None
