import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.domain.interfaces import IRelevanceFilter, IFilterLogRepository, ITriggerEngine, IWalletRepository
from app.domain.models import FilterAuditLog

logger = logging.getLogger(__name__)

# Global scan cycle/event counter for terminal visualization
_scan_cycle_counter = 0



class RelevanceFilter(IRelevanceFilter):
    """
    Layer 0 Filter: Rule-Based Classifier (Relevance Filter F-02)
    Filters incoming raw transaction events to separate trade-relevant actions
    from normal transfers, gas top-ups, or custodial exchange deposits.
    """
    # Fallback minimal DEX list to use if configured list is empty
    FALLBACK_DEX_ROUTERS = [
        "6EF8rrect3EDQS425286575m1111111111111111",  # pump.fun program
        "675k1aCcZ1V9et197Y21o5t3A8tFmgm5Rz2845m2u3"   # Raydium AMM V4
    ]

    # Exclude base liquidity and stable tokens from entering the ML pipeline
    BASE_TOKENS_BLACKLIST = {
        "So11111111111111111111111111111111111111112",  # Wrapped SOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
        "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB",  # USDH
    }


    def __init__(
        self,
        filter_log_repo: IFilterLogRepository,
        trigger_engine: ITriggerEngine,
        wallet_repo: IWalletRepository,
        wallet_discovery_service = None,
        hard_filter = None
    ):
        self.filter_log_repo = filter_log_repo
        self.trigger_engine = trigger_engine
        self.wallet_repo = wallet_repo
        self.wallet_discovery_service = wallet_discovery_service
        self.hard_filter = hard_filter

    async def process_event(self, event_data: dict) -> None:
        """
        Dequeues an event, executes 5 sequential deterministic rule checks (fail-fast),
        logs the audit log to SQLite, and forwards passed events to the Trigger Engine.
        """
        try:
            # 0. Basic Validation (Malformed check)
            required_fields = ["wallet_address", "event_type", "amount_usd", "signature"]
            if not all(field in event_data for field in required_fields):
                logger.error(f"[RELEVANCE FILTER] Malformed event received: {event_data}")
                return

            wallet_address = event_data["wallet_address"]
            event_type = event_data["event_type"]
            amount_usd = float(event_data.get("amount_usd", 0.0))
            token_mint = event_data.get("token_mint")
            signature = event_data["signature"]

            global _scan_cycle_counter
            _scan_cycle_counter += 1
            wallet_short = f"{wallet_address[:6]}...{wallet_address[-4:]}" if len(wallet_address) > 10 else wallet_address
            sig_short = f"{signature[:12]}..." if len(signature) > 12 else signature
            logger.info(
                f"─── MONITOR EVENT #{_scan_cycle_counter}: {wallet_short} ({event_type.upper()}) — SIGNATURE: {sig_short} ───"
            )
            timestamp = event_data.get("timestamp_utc") or datetime.now(timezone.utc)
            
            # Destination/receiver could be in payload for transfers
            receiver_address = event_data.get("receiver_address")

            is_relevant = True
            reason = "Passed all relevance checks"

            # --- Rule 1: DEX Router Check ---
            # If swap or lp_change, must involve a known DEX program id
            if event_type in ["swap", "lp_change"]:
                dex_routers = settings.DEX_ROUTERS
                if not dex_routers:
                    logger.warning("[RELEVANCE FILTER] Configured DEX router list is empty! Using fallback list.")
                    dex_routers = self.FALLBACK_DEX_ROUTERS
                
                # Check if token_mint is a DEX router OR if the payload contains specific program id
                # For logs-based subscription, we parse program logs
                # In parsed payload, we verify the transaction involves the DEX router
                program_id = event_data.get("program_id")
                if program_id and program_id not in dex_routers:
                    is_relevant = False
                    reason = "irrelevant_non_dex"

            # --- Rule 2: USD Value Check ---
            if is_relevant:
                min_swap = settings.MIN_SWAP_AMOUNT_USD
                if amount_usd < min_swap:
                    is_relevant = False
                    reason = f"irrelevant_low_value (Amount ${amount_usd:.2f} < ${min_swap:.2f})"

            # --- Rule 3: Self-Transfer Check ---
            if is_relevant and receiver_address:
                # If receiver is also in our monitored watchlist
                existing_receiver = await self.wallet_repo.get_wallet(receiver_address)
                if existing_receiver and existing_receiver.active:
                    is_relevant = False
                    reason = "self_transfer"

            # --- Rule 4: Custodial Exchange Check ---
            if is_relevant and receiver_address:
                exchanges = settings.CUSTODIAL_EXCHANGES
                if receiver_address in exchanges:
                    is_relevant = False
                    reason = "custodial_deposit"

            # --- Rule 5: LP Change Significance ---
            if is_relevant and event_type == "lp_change":
                min_lp = settings.MIN_LP_CHANGE_USD
                if amount_usd < min_lp:
                    is_relevant = False
                    reason = f"insignificant_lp_change (LP Change ${amount_usd:.2f} < ${min_lp:.2f})"

            # --- Rule 6: Base/Stable Token Blacklist Check ---
            if is_relevant and token_mint:
                if token_mint in self.BASE_TOKENS_BLACKLIST:
                    is_relevant = False
                    reason = f"base_stable_token_blacklisted ({token_mint})"


            # --- Logging and Persisting ---
            log_id = uuid.uuid4().hex
            audit_log = FilterAuditLog(
                log_id=log_id,
                signature=signature,
                wallet_address=wallet_address,
                event_type=event_type,
                token_mint=token_mint,
                amount_usd=amount_usd,
                is_relevant=is_relevant,
                reason=reason,
                timestamp=timestamp
            )
            
            # Save audit log to SQLite
            await self.filter_log_repo.add_log(audit_log)

            if is_relevant:
                logger.info(
                    f"[RELEVANCE FILTER] [PASSED] Signature: {signature}. "
                    f"Wallet {wallet_address} {event_type} event passed. Reason: {reason}"
                )
                # Trigger Dynamic Wallet Discovery (F-12)
                if self.wallet_discovery_service is not None:
                    asyncio.create_task(self.wallet_discovery_service.discover_wallets(event_data))

                # Forward to Hard Filter (F-13) if present, otherwise directly to Trigger Engine (F-03)
                if self.hard_filter is not None:
                    await self.hard_filter.process_event(event_data)
                else:
                    await self.trigger_engine.trigger_event(event_data)
            else:
                logger.info(
                    f"[RELEVANCE FILTER] [FILTERED] Signature: {signature}. "
                    f"Wallet {wallet_address} {event_type} event filtered out. Reason: {reason}"
                )

        except Exception as e:
            logger.error(f"[RELEVANCE FILTER] Error processing event: {e}", exc_info=True)
            # Skip malformed or error-inducing event to keep the monitor loop running
            return
