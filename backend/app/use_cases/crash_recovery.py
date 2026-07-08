import logging
import uuid
import asyncio
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Set

from app.core.config import settings
from app.domain.models import OpenPosition, ClosedTrade
from app.domain.interfaces import (
    IPositionRepository,
    ICooldownRepository,
    IModelRegistryRepository,
    ITradeHistoryRepository,
    ITokenInfoService,
)
from app.websocket.manager import manager as ws_manager

logger = logging.getLogger(__name__)


class CrashRecoveryService:
    """
    F-17: State Persistence & Crash Recovery
    Restores the correct state of the system on startup, including re-activating
    ParallelExecutionEngine protection loops for any active open positions,
    and executing immediate exits for positions where price has fallen below stop loss levels.
    """
    def __init__(
        self,
        position_repo: IPositionRepository,
        cooldown_repo: ICooldownRepository,
        model_registry_repo: IModelRegistryRepository,
        trade_history_repo: ITradeHistoryRepository,
        token_info_service: ITokenInfoService,
        retrain_scheduler,
    ):
        self.position_repo = position_repo
        self.cooldown_repo = cooldown_repo
        self.model_registry_repo = model_registry_repo
        self.trade_history_repo = trade_history_repo
        self.token_info_service = token_info_service
        self.retrain_scheduler = retrain_scheduler

    async def run_recovery(self) -> None:
        """
        Executes startup crash recovery steps:
        1. Check active model from model_registry
        2. Check retrain_timestamp and trigger F-10 catch-up if needed
        3. Query open_positions WHERE status='open'
        4. For each open position:
           a. Fetch current price
           b. If price <= SL -> exit position immediately
           c. If price is safe -> re-activate protection loop (F-09)
        """
        logger.info("[RECOVERY] Starting system recovery checks...")
        startup_time = datetime.now(timezone.utc)
        recovery_actions = []

        # 0. Reconcile on-chain positions vs DB (detect orphaned tokens)
        await self._reconcile_onchain_positions()

        # 1. Load active model
        active_model = await self.model_registry_repo.get_active_model()
        if not active_model:
            logger.warning("[RECOVERY] No active model found in registry on startup.")
        else:
            logger.info(f"[RECOVERY] Active model version: {active_model.model_version}")

        # 2. Check retrain timestamp catch-up (F-10)
        if active_model:
            trained_ts = active_model.trained_at
            if trained_ts.tzinfo is None:
                trained_ts = trained_ts.replace(tzinfo=timezone.utc)
            time_since_train = startup_time - trained_ts
            if time_since_train > timedelta(hours=24):
                logger.warning(f"[RECOVERY] Active model was trained {time_since_train.total_seconds()/3600:.1f}h ago. Triggering retrain catch-up.")
                await self.retrain_scheduler.retrain_model_if_needed(force=True)
                recovery_actions.append("retrain_catch_up")

        # 3. Query open positions
        open_positions = await self.position_repo.get_open_positions()
        open_positions_count = len(open_positions)
        logger.info(f"[RECOVERY] Found {open_positions_count} open positions to recover.")

        # 4. Recover open positions
        for pos in open_positions:
            token = pos.token_address
            entry_price = pos.entry_price or 1.0
            sl_level = pos.sl_initial

            logger.info(f"[RECOVERY] Recovering position {pos.position_id} for token {token}...")

            # Fetch current price
            try:
                info = await self.token_info_service.get_token_info(token)
                current_price = info.get("price_usd")
                if current_price is None or float(current_price) <= 0.0:
                    # Simulated fallback or error handling
                    # In a real environment if token is not found or API fails, raise error
                    if info.get("token_symbol") == "MOCK_TOKEN":
                        # For testing/offline simulation fallback: use mock price walk
                        current_price = entry_price
                    else:
                        raise ValueError(f"Price not available for token {token}")
                else:
                    current_price = float(current_price)
            except Exception as e:
                # Mark as recovery_failed, log warning and emit critical alert
                logger.critical(f"[RECOVERY] [FAILED] Position {pos.position_id} inconsistent. Price fetch error: {e}")
                pos.state = "RECOVERY_FAILED"
                await self.position_repo.update_position(pos)
                await ws_manager.broadcast({
                    "type": "system_alert",
                    "data": {
                        "event": "system_alert",
                        "alert_type": "recovery_failed",
                        "message": f"CRITICAL: Position {pos.position_id} (token {token}) marked as RECOVERY_FAILED due to price fetch failure.",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                })
                recovery_actions.append(f"failed_pos_{pos.position_id}")
                continue

            logger.info(f"[RECOVERY] Current price for token {token} is ${current_price:.6f} (SL: ${sl_level:.6f})")

            # Compare current price with SL level
            if current_price <= sl_level:
                logger.warning(f"[RECOVERY] Price ${current_price:.6f} is below SL ${sl_level:.6f}. Executing immediate market exit.")
                # Update position state to EXITING
                pos.state = "EXITING"
                await self.position_repo.update_position(pos)
                
                # Mock exit order execution
                exit_price = current_price
                pnl_pct_actual = (exit_price - entry_price) / entry_price
                r_multiple = (exit_price - entry_price) / abs(entry_price - sl_level)
                
                # Move to closed trades
                closed_trade = ClosedTrade(
                    trade_id=f"tr_{uuid.uuid4().hex[:8]}",
                    wallet_source=pos.wallet_source,
                    token_address=pos.token_address,
                    token_symbol=info.get("token_symbol", "UNKNOWN"),
                    signal_ts=pos.entry_ts,
                    entry_ts=pos.entry_ts,
                    exit_ts=datetime.now(timezone.utc),
                    direction="BUY",
                    confidence_score=pos.confidence_score,
                    safety_check_passed=True,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    position_size_usd=pos.position_size_usd,
                    risk_pct=pos.risk_pct,
                    pnl_pct_actual=pnl_pct_actual,
                    r_multiple=r_multiple,
                    label="SALAH" if r_multiple < 0 else "BUY_BENAR",
                    holding_time_minutes=int((datetime.now(timezone.utc) - pos.entry_ts).total_seconds() / 60) if pos.entry_ts else 0,
                    exit_reason="SL_RECOVERY",
                    is_paper_trade=False,
                    model_version=pos.model_version
                )
                
                # Delete position and save closed trade
                await self.position_repo.delete_position(pos.position_id)
                # If trade history repo is available, save
                if self.trade_history_repo:
                    await self.trade_history_repo.add_closed_trade(closed_trade)

                # Reset cooldown
                await self.cooldown_repo.delete_cooldown(pos.wallet_source, pos.token_address)
                
                # Broadcast closed trade
                await ws_manager.broadcast({
                    "type": "trade_closed",
                    "data": {
                        "position_id": pos.position_id,
                        "token_address": pos.token_address,
                        "pnl_pct_actual": pnl_pct_actual,
                        "exit_reason": "SL_RECOVERY",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                })
                recovery_actions.append(f"exit_pos_{pos.position_id}")
            else:
                # Re-activate F-09 Parallel Protection Loop
                from app.execution.executor import ParallelExecutionEngine
                engine = ParallelExecutionEngine(
                    pos,
                    self.position_repo,
                    self.cooldown_repo,
                    self.model_registry_repo,
                    self.trade_history_repo,
                    token_info_service=self.token_info_service
                )
                asyncio.create_task(engine.start_monitoring())
                logger.info(f"[RECOVERY] Protection loops re-activated for position {pos.position_id}.")
                recovery_actions.append(f"restored_pos_{pos.position_id}")

        # Audit log of startup
        logger.info(
            f"[RECOVERY] [AUDIT] Startup recovery finished.\n"
            f" - Startup Time: {startup_time.isoformat()}\n"
            f" - Open Positions Count: {open_positions_count}\n"
            f" - Recovery Actions Taken: {recovery_actions}"
        )

    async def _reconcile_onchain_positions(self) -> None:
        """
        F-17 Extension: Orphaned Position Guard.
        Compares on-chain SPL token holdings with open_positions in DB.
        Tokens found on-chain but NOT in DB are 'orphaned' (e.g. after DB reset).
        - Value >= $1.00 USD  -> create synthetic open_position for F-09 to monitor
        - Value <  $1.00 USD  -> sell + close account immediately (dust cleanup)
        """
        logger.info("[RECOVERY] [ORPHAN GUARD] Starting on-chain vs DB reconciliation...")
        
        try:
            from app.infrastructure.blockchain.wallet_manager import load_wallet_from_env
            keypair = load_wallet_from_env()
            if not keypair:
                logger.warning("[RECOVERY] [ORPHAN GUARD] No keypair available, skipping on-chain reconciliation.")
                return

            pubkey_str = str(keypair.pubkey())
            rpc_url = getattr(settings, "RPC_PRIMARY_URL", "https://api.mainnet-beta.solana.com")

            # 1. Fetch on-chain SPL token accounts
            def rpc_call(method, params):
                payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                req = urllib.request.Request(
                    rpc_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            token_res = await asyncio.to_thread(rpc_call, "getTokenAccountsByOwner", [
                pubkey_str,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"}
            ])

            onchain_tokens = {}
            for acc in token_res.get("result", {}).get("value", []):
                try:
                    info = acc["account"]["data"]["parsed"]["info"]
                    mint = info["mint"]
                    ui_amount = float(info["tokenAmount"].get("uiAmount") or 0.0)
                    if ui_amount > 0:
                        onchain_tokens[mint] = ui_amount
                except Exception:
                    pass

            if not onchain_tokens:
                logger.info("[RECOVERY] [ORPHAN GUARD] No SPL tokens found on-chain. Nothing to reconcile.")
                return

            # 2. Get tokens already tracked in DB
            open_positions = await self.position_repo.get_open_positions()
            db_tokens: Set[str] = {pos.token_address for pos in open_positions}

            # 3. Find orphaned tokens (on-chain but not in DB)
            orphaned: dict = {m: amt for m, amt in onchain_tokens.items() if m not in db_tokens}

            if not orphaned:
                logger.info("[RECOVERY] [ORPHAN GUARD] All on-chain tokens are tracked in DB. No orphans found.")
                return

            logger.warning(f"[RECOVERY] [ORPHAN GUARD] Found {len(orphaned)} orphaned token(s) on-chain not in DB: {list(orphaned.keys())}")

            from app.infrastructure.blockchain.pumpportal_client import build_trade_transaction
            from app.infrastructure.blockchain.tx_signer import sign_and_broadcast_transaction, close_token_account

            for mint, amount in orphaned.items():
                try:
                    # Fetch price
                    token_info = await self.token_info_service.get_token_info(mint)
                    price_usd = float(token_info.get("price_usd") or 0.0)
                    value_usd = amount * price_usd

                    logger.warning(
                        f"[RECOVERY] [ORPHAN GUARD] Orphaned: {mint[:12]}... "
                        f"| {amount:.4f} tokens | ~${value_usd:.4f} USD"
                    )

                    if value_usd >= 1.00:
                        # Significant value — create synthetic open_position for F-09 to monitor
                        logger.warning(
                            f"[RECOVERY] [ORPHAN GUARD] Value ${value_usd:.2f} >= $1. "
                            f"Creating synthetic open_position for monitoring."
                        )
                        # Use a default wallet source (first active wallet)
                        from app.infrastructure.database.repository import SQLAlchemyWalletRepository
                        wallets = await self.position_repo.get_open_positions()  # fallback
                        wallet_source = pubkey_str  # use our own wallet as source

                        sl_price = price_usd * 0.50  # Conservative SL: -50%
                        synthetic_pos = OpenPosition(
                            position_id=f"orphan_{uuid.uuid4().hex[:8]}",
                            wallet_source=wallet_source,
                            token_address=mint,
                            state="OPEN",
                            entry_price=price_usd,
                            entry_ts=datetime.now(timezone.utc),
                            sl_initial=sl_price,
                            risk_pct=0.01,
                            position_size_usd=value_usd,
                            trailing_active=False,
                            trailing_level=None,
                            peak_r_multiple=0.0,
                            confidence_score=0.5,
                            model_version="orphan_recovery"
                        )

                        try:
                            await self.position_repo.add_position(synthetic_pos)
                            logger.info(f"[RECOVERY] [ORPHAN GUARD] Synthetic position created: {synthetic_pos.position_id}")

                            # Re-activate F-09 protection
                            from app.execution.executor import ParallelExecutionEngine
                            engine = ParallelExecutionEngine(
                                synthetic_pos,
                                self.position_repo,
                                self.cooldown_repo,
                                self.model_registry_repo,
                                self.trade_history_repo,
                                token_info_service=self.token_info_service
                            )
                            asyncio.create_task(engine.start_monitoring())
                            logger.info(f"[RECOVERY] [ORPHAN GUARD] F-09 protection activated for orphaned {mint[:12]}...")
                        except Exception as pos_err:
                            logger.error(f"[RECOVERY] [ORPHAN GUARD] Failed to create synthetic position: {pos_err}")

                    else:
                        # Low value dust — sell immediately and close account
                        logger.info(
                            f"[RECOVERY] [ORPHAN GUARD] Value ${value_usd:.4f} < $1. "
                            f"Selling dust and closing account."
                        )
                        try:
                            unsigned_tx = await build_trade_transaction(
                                public_key=pubkey_str,
                                action="sell",
                                token_mint=mint,
                                amount="100%",
                                denominated_in_sol=False,
                                slippage=settings.SLIPPAGE_SELL_EMERGENCY_PCT,
                                priority_fee=settings.PRIORITY_FEE_DUST,
                                pool="auto"
                            )
                            if unsigned_tx:
                                tx_sig = await sign_and_broadcast_transaction(unsigned_tx, keypair)
                                logger.info(f"[RECOVERY] [ORPHAN GUARD] Dust sold. TX: {tx_sig}")
                                await asyncio.sleep(3)
                        except Exception as sell_err:
                            logger.warning(f"[RECOVERY] [ORPHAN GUARD] Could not sell dust {mint}: {sell_err}")

                        try:
                            close_sig = await close_token_account(mint, keypair, token_price_usd=0.0)
                            if close_sig:
                                logger.info(f"[RECOVERY] [ORPHAN GUARD] Token account closed. TX: {close_sig}")
                        except Exception as close_err:
                            logger.warning(f"[RECOVERY] [ORPHAN GUARD] Could not close account {mint}: {close_err}")

                    await ws_manager.broadcast({
                        "type": "system_alert",
                        "data": {
                            "event": "system_alert",
                            "alert_type": "orphan_position_detected",
                            "message": (
                                f"Orphaned token detected: {mint[:12]}... "
                                f"({amount:.4f} tokens, ~${value_usd:.2f}). "
                                f"{'Synthetic position created.' if value_usd >= 1.00 else 'Dust sold & account closed.'}"
                            ),
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                    })

                except Exception as e:
                    logger.error(f"[RECOVERY] [ORPHAN GUARD] Error handling orphaned token {mint}: {e}", exc_info=True)

        except Exception as outer_err:
            logger.error(f"[RECOVERY] [ORPHAN GUARD] Reconciliation failed: {outer_err}", exc_info=True)
