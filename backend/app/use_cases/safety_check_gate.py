import logging
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.domain.models import FeatureVector, PredictionResult, SafetyCheckResult, FilterAuditLog
from app.domain.interfaces import ITokenSafetyCheckGate, ITokenSafetyService, IFilterLogRepository
from app.websocket.manager import manager as ws_manager

logger = logging.getLogger(__name__)


class SafetyCheckGate(ITokenSafetyCheckGate):
    """
    Layer 3 Use Case: Token Safety Check Gate (F-06)
    Verifies token security criteria and routes prediction alerts.
    Includes an in-memory LRU Cache with a 60-second TTL.
    """
    def __init__(
        self,
        safety_service: ITokenSafetyService,
        filter_log_repo: IFilterLogRepository,
        max_cache_size: int = 100,
        ttl_seconds: float = 60.0
    ):
        self.safety_service = safety_service
        self.filter_log_repo = filter_log_repo
        self.ttl_seconds = ttl_seconds
        self.max_cache_size = max_cache_size
        
        # LRU Cache: token_address -> (insert_time, SafetyCheckResult)
        self.cache = OrderedDict()

    async def evaluate_safety(
        self,
        prediction: PredictionResult,
        feature_vector: FeatureVector
    ) -> SafetyCheckResult:
        """
        Runs safety checks for the token in the PredictionResult.
        """
        token_address = prediction.token_address
        signature = prediction.signature
        
        # 1. Check LRU Cache
        cached_result = self._get_from_cache(token_address)
        if cached_result:
            logger.info(f"[SAFETY GATE] [CACHE HIT] Using cached safety result for {token_address}")
            await self._route_signal_and_log(prediction, cached_result, feature_vector)
            return cached_result
 
        # 2. Fetch Safety Info from Infrastructure Service (DexScreener/RPC)
        try:
            import asyncio
            safety_info = await asyncio.wait_for(
                self.safety_service.get_safety_info(token_address),
                timeout=5.0
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(f"[SAFETY GATE] [FAIL-CLOSED] Timeout fetching safety info for {token_address}. Blocking alert.")
            
            # Log F-19 Central Safety Timeout
            from app.core.error_handler import log_system_error, ErrorType, ErrorSeverity
            asyncio.create_task(log_system_error(
                error_type=ErrorType.SAFETY_API_TIMEOUT,
                severity=ErrorSeverity.ERROR,
                context=f"Timeout limit (5s) exceeded fetching safety parameters for token {token_address}.",
                recovery_action="fail_closed: block trade entry",
                resolution_status="failed"
            ))
 
            result = self._create_failed_result(token_address, "safety_api_failed")
            self._save_to_cache(token_address, result)
            await self._route_signal_and_log(prediction, result, feature_vector)
            return result
        except Exception as e:
            logger.error(f"[SAFETY GATE] [FAIL-CLOSED] Error fetching safety info for {token_address}: {e}. Blocking alert.")
            result = self._create_failed_result(token_address, f"safety_api_error: {str(e)}")
            self._save_to_cache(token_address, result)
            await self._route_signal_and_log(prediction, result, feature_vector)
            return result
 
        # 3. Evaluate criteria (Fail-Fast)
        passed = True
        reason = "Passed all safety criteria"
        
        # LP Locked check
        lp_locked = safety_info.get("liquidity_locked", False)
        if settings.SAFETY_REQUIRE_LP_LOCKED and not lp_locked:
            passed = False
            reason = "lp_not_locked"
            
        # Contract Verified check
        contract_verified = safety_info.get("contract_verified", False)
        if passed and settings.SAFETY_REQUIRE_CONTRACT_VERIFIED and not contract_verified:
            passed = False
            reason = "contract_not_verified"
            
        # Holder Distribution check
        top_10_share = safety_info.get("top_10_holders_share", 1.0)
        if passed and top_10_share >= settings.SAFETY_MAX_TOP_10_HOLDERS_SHARE:
            passed = False
            reason = f"holder_concentration_too_high: top-10 owns {top_10_share:.2%}"
            
        # Mint Authority check
        mint_revoked = safety_info.get("mint_authority_revoked", False)
        if passed and settings.SAFETY_REQUIRE_MINT_AUTHORITY_REVOKED and not mint_revoked:
            passed = False
            reason = "mint_authority_not_revoked"
 
        result = SafetyCheckResult(
            token_address=token_address,
            passed=passed,
            reason=reason,
            liquidity_locked=lp_locked,
            contract_verified=contract_verified,
            top_10_holders_share=top_10_share,
            mint_authority_revoked=mint_revoked,
            timestamp=datetime.now(timezone.utc)
        )
 
        # 4. Save to Cache
        self._save_to_cache(token_address, result)
 
        # 5. Route Signal & Log
        await self._route_signal_and_log(prediction, result, feature_vector)
        
        return result
 
    def _get_from_cache(self, token_address: str) -> Optional[SafetyCheckResult]:
        if token_address not in self.cache:
            return None
        
        import time
        insert_time, result = self.cache[token_address]
        # Check TTL
        if time.time() - insert_time > self.ttl_seconds:
            del self.cache[token_address]
            return None
            
        # Move to end (LRU)
        self.cache.move_to_end(token_address)
        return result
 
    def _save_to_cache(self, token_address: str, result: SafetyCheckResult) -> None:
        if token_address in self.cache:
            del self.cache[token_address]
        elif len(self.cache) >= self.max_cache_size:
            self.cache.popitem(last=False)  # Evict oldest entry
            
        import time
        self.cache[token_address] = (time.time(), result)
 
    def _create_failed_result(self, token_address: str, reason: str) -> SafetyCheckResult:
        return SafetyCheckResult(
            token_address=token_address,
            passed=False,
            reason=reason,
            liquidity_locked=False,
            contract_verified=False,
            top_10_holders_share=1.0,
            mint_authority_revoked=False,
            timestamp=datetime.now(timezone.utc)
        )
 
    async def _route_signal_and_log(
        self,
        prediction: PredictionResult,
        safety_result: SafetyCheckResult,
        feature_vector: Optional[FeatureVector] = None
    ) -> None:
        token_address = prediction.token_address
        signature = prediction.signature
        
        from app.use_cases.dashboard_query import append_signal_event
        import asyncio
        
        # Convert feature vector to dict if available
        features_dict = None
        if feature_vector:
            features_dict = {
                "position_size_usd": feature_vector.position_size_usd,
                "token_age_minutes": feature_vector.token_age_minutes,
                "liquidity_pool_depth": feature_vector.liquidity_pool_depth,
                "slippage_actual": feature_vector.slippage_actual,
                "cluster_score": feature_vector.cluster_score,
                "win_rate_30d": feature_vector.win_rate_30d,
                "avg_holding_time_minutes": feature_vector.avg_holding_time_minutes,
                "typical_trade_size_usd": feature_vector.typical_trade_size_usd,
                "past_exit_pattern_score": feature_vector.past_exit_pattern_score,
                "sol_usd_momentum": feature_vector.sol_usd_momentum,
                "token_volume_liquidity_ratio": feature_vector.token_volume_liquidity_ratio,
                "hour_of_day_utc": feature_vector.hour_of_day_utc
            }
        
        # Check overall routing logic
        if safety_result.passed:
            # Check confidence score vs settings threshold
            if prediction.confidence_score >= settings.CONFIDENCE_THRESHOLD:
                # 1. Emit ALERT Signal
                token_symbol = ""
                token_name = ""
                dex_url = f"https://dexscreener.com/solana/{prediction.token_address}"
                if feature_vector:
                    # Try to get token info from token_info_service if available
                    try:
                        svc = getattr(self, "token_info_service", None)
                        if svc:
                            info = await svc.get_token_info(prediction.token_address)
                            token_symbol = info.get("token_symbol", "") or ""
                            token_name = info.get("name", "") or token_symbol
                            # Build pump.fun or dexscreener URL
                            if token_symbol.lower() not in ("sol", "wsol", "usdc", "usdt"):
                                dex_url = f"https://pump.fun/{prediction.token_address}"
                    except Exception:
                        pass
                
                token_short = f"{prediction.token_address[:6]}...{prediction.token_address[-4:]}"
                wallet_short = f"{prediction.wallet_source[:6]}...{prediction.wallet_source[-4:]}"
                alert_signal = {
                    "event": "ALERT",
                    "signal_id": f"sig_{len(token_address)}",
                    "token_address": token_address,
                    "token_short": token_short,
                    "token_symbol": token_symbol,
                    "token_name": token_name,
                    "dex_url": dex_url,
                    "wallet_source": prediction.wallet_source,
                    "wallet_short": wallet_short,
                    "direction": prediction.direction,
                    "confidence_score": prediction.confidence_score,
                    "target_price_estimate": prediction.target_price_estimate,
                    "safety_passed": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "features": features_dict
                }
                logger.info(f"[SAFETY GATE] [PASSED & FIRED] Emitted alert signal for {token_address}!")
                append_signal_event(alert_signal)
                await ws_manager.broadcast(alert_signal)
                
                # F-08: Trigger Auto Trade Execution (pass feature_vector properly)
                auto_executor = getattr(self, "auto_trade_executor", None)
                if auto_executor:
                    asyncio.create_task(auto_executor.execute_trade(prediction, feature_vector=feature_vector))
            else:
                # Low confidence
                token_short = f"{prediction.token_address[:6]}...{prediction.token_address[-4:]}"
                wallet_short = f"{prediction.wallet_source[:6]}...{prediction.wallet_source[-4:]}"
                log_signal = {
                    "event": "LOG_ONLY",
                    "reason": f"low_confidence: {prediction.confidence_score:.4f} < {settings.CONFIDENCE_THRESHOLD}",
                    "signal_id": f"sig_{len(token_address)}",
                    "token_address": token_address,
                    "token_short": token_short,
                    "wallet_source": prediction.wallet_source,
                    "wallet_short": wallet_short,
                    "direction": prediction.direction,
                    "confidence_score": prediction.confidence_score,
                    "target_price_estimate": prediction.target_price_estimate,
                    "safety_passed": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "features": features_dict
                }
                logger.info(f"[SAFETY GATE] [LOG ONLY] Confidence too low for {token_address}: {prediction.confidence_score:.4f}")
                append_signal_event(log_signal)
                # Log audit info
                await self._log_audit(prediction, f"low_confidence: {prediction.confidence_score:.4f}")
        else:
            # Safety failed
            token_short = f"{prediction.token_address[:6]}...{prediction.token_address[-4:]}"
            wallet_short = f"{prediction.wallet_source[:6]}...{prediction.wallet_source[-4:]}"
            log_signal = {
                "event": "LOG_ONLY",
                "reason": f"safety_failed: {safety_result.reason}",
                "signal_id": f"sig_{len(token_address)}",
                "token_address": token_address,
                "token_short": token_short,
                "wallet_source": prediction.wallet_source,
                "wallet_short": wallet_short,
                "direction": prediction.direction,
                "confidence_score": prediction.confidence_score,
                "target_price_estimate": prediction.target_price_estimate,
                "safety_passed": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "features": features_dict
            }
            logger.info(f"[SAFETY GATE] [BLOCKED] Token safety check failed for {token_address}. Reason: {safety_result.reason}")
            append_signal_event(log_signal)
            # Log audit info
            await self._log_audit(prediction, f"safety_failed: {safety_result.reason}")


    async def _log_audit(self, prediction: PredictionResult, reason: str) -> None:
        try:
            log_entry = FilterAuditLog(
                log_id="sf_" + str(uuid.uuid4())[:8],
                signature=prediction.signature,
                wallet_address=prediction.wallet_source,
                event_type="safety_check",
                token_mint=prediction.token_address,
                amount_usd=0.0,
                is_relevant=False,
                reason=reason,
                timestamp=datetime.now(timezone.utc)
            )
            await self.filter_log_repo.add_log(log_entry)
        except Exception as e:
            logger.error(f"[SAFETY GATE] Error writing audit log: {e}")
