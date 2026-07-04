import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from app.core.config import settings
from app.domain.models import PredictionResult, FeatureVector, OpenPosition
from app.domain.interfaces import IPositionRepository, ICooldownRepository

logger = logging.getLogger(__name__)


class TradeGuard:
    """
    F-08 / A4: Pre-Execution Trade Guard
    Enforces risk constraints, slippage caps, balance checking, and idempotency guards.
    """
    def __init__(
        self,
        position_repo: IPositionRepository,
        cooldown_repo: ICooldownRepository
    ):
        self.position_repo = position_repo
        self.cooldown_repo = cooldown_repo

    async def validate_trade(
        self,
        prediction: PredictionResult,
        feature_vector: FeatureVector,
        sol_balance: float,
        position_size_usd: float,
        sol_price_usd: float
    ) -> tuple[bool, str]:
        """
        Validates if a trade meets all parameters and can proceed.
        Returns: (bool_allowed, reason_string)
        """
        token_address = prediction.token_address
        wallet_source = prediction.wallet_source
        
        # 1. Slippage Cap validation
        slippage_estimate = 0.005
        if feature_vector is not None:
            slippage_estimate = feature_vector.slippage_actual or 0.005
            
        max_slippage = getattr(settings, "SLIPPAGE_TOLERANCE", 0.05)
        # Enforce strict 15% system slippage cap
        if slippage_estimate > 0.15:
            return False, f"Blocked: Slippage {slippage_estimate:.1%} exceeds strict 15% system cap."
        if slippage_estimate > max_slippage:
            return False, f"Blocked: Slippage {slippage_estimate:.1%} exceeds configured tolerance of {max_slippage:.1%}."

        # 2. Check status of system (Kill-Switch / Degraded Mode)
        from app.blockchain.monitor import SolanaWebSocketMonitor
        if SolanaWebSocketMonitor.degraded_mode:
            return False, "Blocked: System in Degraded Mode (new trigger pipeline suspended)."

        # 3. Position limits & Exposure Check
        try:
            open_positions = await self.position_repo.get_open_positions()
        except Exception as e:
            logger.error(f"[TRADE GUARD] Failed to query open positions: {e}")
            return False, f"Blocked: Database query failed (fail-closed)."

        max_positions = getattr(settings, "RISK_MAX_CONCURRENT_POSITIONS", 3)
        if len(open_positions) >= max_positions:
            return False, f"Blocked: Max concurrent positions cap reached ({len(open_positions)}/{max_positions})."

        # Check if we already have an open position for this token
        if any(p.token_address == token_address for p in open_positions):
            return False, f"Blocked: Position already open for token {token_address}."

        # 4. Idempotency Check (Duplicate check for wallet-token in 5 minutes trigger window)
        # NOTE: If TriggerEngine already cleared an expired cooldown, skip this check
        # to avoid the deadlock where: reset → new cooldown set → guard blocks immediately.
        cooldown_already_cleared = getattr(prediction, 'cooldown_already_cleared', False)
        cooldown = await self.cooldown_repo.get_cooldown(wallet_source, token_address)
        if cooldown and not cooldown_already_cleared:
            now = datetime.now(timezone.utc)
            last_ts = cooldown.last_trigger_ts
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            # Check if last trigger is within 5 minute window
            if now - last_ts < timedelta(minutes=5):
                return False, f"Blocked: Idempotency Guard - Duplicate signal for ({wallet_source[:6]}, {token_address[:6]}) in 5m window."
            if cooldown.active_position_id:
                return False, f"Blocked: Wallet-Token pair already has an active position tracking."

        # 5. Balance Validation
        # Cost of trade in SOL
        sol_cost = position_size_usd / sol_price_usd
        priority_fee_sol = 0.003
        required_sol = sol_cost + priority_fee_sol + 0.001 # add buffer for gas fee
        
        if sol_balance < required_sol:
            return False, f"Blocked: Insufficient SOL balance. Required: {required_sol:.4f} SOL, Available: {sol_balance:.4f} SOL."

        return True, "Trade validation passed."
