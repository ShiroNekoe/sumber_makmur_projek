import logging
import asyncio
import random
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.core.config import settings
from app.domain.models import OpenPosition, ClosedTrade, OnchainEvent
from app.domain.interfaces import (
    IPositionRepository,
    ICooldownRepository,
    IModelRegistryRepository,
    ITradeHistoryRepository,
    ITokenInfoService,
    ITokenSafetyService,
)
from app.websocket.manager import manager as ws_manager

logger = logging.getLogger(__name__)


class ParallelExecutionEngine:
    """
    F-09: Three-Layer Position Protection

    Tiga lapis proteksi posisi terbuka, sesuai 06 - Eksekusi Otomatis
    (dokumen sumber) dan 02 - Flow Aplikasi:
    - Lapis 1: Stop Loss berbasis harga (fixed -1R, selalu aktif)
    - Lapis 2: Staged Trailing Take Profit (makin ketat saat profit naik)
    - Lapis 3: On-chain Kill-Switch, independen dari harga, prioritas tertinggi

    Implementasi sebelumnya menjalankan ketiga lapis di SATU loop sekuensial
    yang sama (cek harga -> cek SL -> cek trailing -> roll dice acak untuk
    kill-switch). Ini menyalahi instruksi dokumen sumber yang menegaskan
    "tiga lapis proteksi berjalan paralel, bukan berurutan" dan bahwa
    kill-switch harus "berjalan di proses/thread terpisah ... begitu trigger
    ia mengirim order tanpa menunggu evaluasi SL/TP selesai."

    Implementasi ini memisahkan menjadi dua task asyncio independen:
    1. `_run_price_protection_loop` -- Lapis 1 & 2. Keduanya secara inheren
       bergantung pada satu sumber harga yang sama pada waktu yang sama
       (price-based), sehingga tetap dievaluasi dalam satu task harga,
       tapi task ini SAMA SEKALI tidak menunggu atau memblokir kill-switch.
    2. `_run_kill_switch_loop` -- Lapis 3, polling cepat (independen dari
       interval harga) terhadap sinyal on-chain riil (liquidity pool depth
       & holder distribution) lewat ITokenInfoService/ITokenSafetyService,
       bukan `random.random()` seperti sebelumnya.

    Kedua task berbagi `asyncio.Lock` yang sama di `execute_exit()` sehingga
    siapapun yang trigger lebih dulu menang dan mengeksekusi exit; task
    lainnya akan menemukan `self.exited == True` pada pengecekan berikutnya
    dan berhenti sendiri -- atomic exit guarantee tetap dipertahankan persis
    seperti versi sebelumnya.
    """
    def __init__(
        self,
        position: OpenPosition,
        position_repo: IPositionRepository,
        cooldown_repo: ICooldownRepository,
        model_registry_repo: IModelRegistryRepository,
        trade_history_repo: ITradeHistoryRepository,
        token_info_service: Optional[ITokenInfoService] = None,
        token_safety_service: Optional[ITokenSafetyService] = None,
    ):
        self.position = position
        self.position_repo = position_repo
        self.cooldown_repo = cooldown_repo
        self.model_registry_repo = model_registry_repo
        self.trade_history_repo = trade_history_repo

        # Opsional dan backward-compatible: kode lama (mis. test yang sudah
        # ada) yang membuat ParallelExecutionEngine tanpa kedua argumen baru
        # ini tetap berjalan -- kill-switch akan otomatis jatuh ke fallback
        # simulasi (lihat _run_kill_switch_loop) jika service ini None.
        self.token_info_service = token_info_service
        self.token_safety_service = token_safety_service

        self.current_price = position.entry_price or 1.0
        self.peak_price = self.current_price
        self.sl_initial = position.sl_initial
        self.r_val = abs(self.current_price - self.sl_initial) # 1R distance in absolute price
        
        self.exited = False
        self.lock = asyncio.Lock()
        self.tasks = []

        # Baseline on-chain untuk Lapis 3, diisi saat task kill-switch
        # pertama kali jalan (lihat _run_kill_switch_loop).
        self._baseline_liquidity_usd: Optional[float] = None
        self._baseline_top_10_holders_share: Optional[float] = None

    async def start_monitoring(self):
        """
        Spawns the three (now genuinely concurrent) protective tasks.
        Lapis 1 & 2 berbagi satu task harga; Lapis 3 berjalan di dua task:
        1. Polling fallback (_run_kill_switch_loop)
        2. Real-time push-based accountSubscribe (_run_pda_subscription_loop)
        """
        logger.info(f"[PROTECTION] Initiating 3-layer parallel protection for position {self.position.position_id}")
        
        # Populate real dev_wallet_address via read-only RPC if not already present
        if not getattr(self.position, "dev_wallet_address", None):
            try:
                from app.infrastructure.blockchain.bonding_curve_price import fetch_dev_wallet_address
                dev_addr = await fetch_dev_wallet_address(self.position.token_address)
                if dev_addr:
                    self.position.dev_wallet_address = dev_addr
            except Exception as err:
                logger.debug(f"[PROTECTION] Could not fetch dev wallet address for {self.position.token_address[:8]}: {err}")

        self.tasks = [
            asyncio.create_task(self._run_price_protection_loop()),
            asyncio.create_task(self._run_kill_switch_loop()),
            asyncio.create_task(self._run_pda_subscription_loop()),
        ]

    async def _run_pda_subscription_loop(self):
        """
        Push-based wallet-agnostic kill-switch task.
        Subscribes to AccountInfo updates via WebSocket accountSubscribe for the bonding curve PDA account.
        Cleanly unsubscribes on exit or loop termination.
        """
        try:
            from app.infrastructure.blockchain.bonding_curve_price import get_bonding_curve_pda, parse_bonding_curve_account_data
            pda = get_bonding_curve_pda(self.position.token_address)
            if not pda:
                return

            sub_id = None
            ws_url = settings.RPC_PRIMARY_URL.replace("https://", "wss://").replace("http://", "ws://")

            try:
                import websockets
                import base64
                import json
                async with websockets.connect(ws_url, open_timeout=10.0) as ws:
                    sub_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "accountSubscribe",
                        "params": [
                            str(pda),
                            {"encoding": "base64", "commitment": "confirmed"}
                        ]
                    }
                    await ws.send(json.dumps(sub_payload))
                    init_resp = await ws.recv()
                    parsed_init = json.loads(init_resp)
                    sub_id = parsed_init.get("result")
                    logger.info(f"[PROTECTION] accountSubscribe active for PDA {str(pda)[:8]}... (sub_id={sub_id})")

                    while not self.exited:
                        msg_str = await ws.recv()
                        msg_data = json.loads(msg_str)
                        if isinstance(msg_data, dict) and "params" in msg_data:
                            val = msg_data["params"].get("result", {}).get("value", {})
                            raw_data = val.get("data")
                            if isinstance(raw_data, list) and len(raw_data) > 0:
                                b64_str = raw_data[0]
                                decoded_bytes = base64.b64decode(b64_str)
                                parsed_curve = parse_bonding_curve_account_data(decoded_bytes)
                                if parsed_curve:
                                    reason = self.evaluate_reserve_change(
                                        parsed_curve["virtualSolReserves"],
                                        parsed_curve["virtualTokenReserves"]
                                    )
                                    if reason:
                                        logger.warning(f"[PROTECTION] [L3 Push] Wallet-agnostic kill signal: {reason}!")
                                        await self.execute_exit(reason)
                                        break
            finally:
                if sub_id:
                    try:
                        unsub_payload = {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "accountUnsubscribe",
                            "params": [sub_id]
                        }
                        await ws.send(json.dumps(unsub_payload))
                        unsub_resp = await ws.recv()
                        logger.info(f"[PROTECTION] accountUnsubscribe confirmed: {unsub_resp}")
                    except Exception as unsub_err:
                        logger.debug(f"[PROTECTION] Clean accountUnsubscribe attempt: {unsub_err}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[PROTECTION] Error in PDA subscription loop: {e}", exc_info=True)

    async def _run_price_protection_loop(self):
        """
        Lapis 1 (Stop Loss) & Lapis 2 (Staged Trailing Take Profit).
        Evaluates real-time price using on-chain bonding curve reserves (Primary),
        DexScreener API (Secondary for migrated AMM tokens), and Last-Valid-Price Freeze
        with Emergency Exit after 5 consecutive failures (Tersier).

        STRICTLY NO random.uniform() random walk simulation in live production.
        """
        consecutive_price_failures = 0
        MAX_PRICE_FEED_FAILURES = 5

        try:
            while not self.exited:
                await asyncio.sleep(1.0) # Check every 1 second

                fetched_price = None

                # 1. Primary Source: Direct On-Chain Bonding Curve State via RPC
                try:
                    from app.infrastructure.blockchain.bonding_curve_price import get_bonding_curve_price
                    sol_price_usd = getattr(settings, "SOL_USD_FALLBACK", 145.0)
                    fetched_price = await get_bonding_curve_price(
                        self.position.token_address,
                        sol_price_usd=sol_price_usd
                    )
                except Exception as err:
                    logger.debug(f"[PROTECTION] Primary bonding curve price fetch failed for {self.position.token_address[:8]}...: {err}")

                # 2. Secondary Source: DexScreener API (for graduated AMM tokens)
                if fetched_price is None and self.token_info_service:
                    try:
                        token_info = await self.token_info_service.get_token_info(self.position.token_address)
                        if token_info and "price_usd" in token_info and token_info["price_usd"] > 0:
                            fetched_price = float(token_info["price_usd"])
                    except Exception as err:
                        logger.warning(f"[PROTECTION] Secondary DexScreener price fetch failed for {self.position.token_address[:8]}...: {err}")

                # 3. Price Evaluation & Failure Handling
                if fetched_price is not None and fetched_price > 0:
                    self.current_price = fetched_price
                    consecutive_price_failures = 0
                else:
                    # Tersier: Freeze last valid price, do NOT random walk
                    consecutive_price_failures += 1
                    logger.warning(
                        f"[PROTECTION] Price feed unavailable for {self.position.token_address[:8]}... "
                        f"Freezing last valid price ${self.current_price:.6f} (Failure {consecutive_price_failures}/{MAX_PRICE_FEED_FAILURES})."
                    )

                    # Emergency Exit: Protection against blind holding on dead feeds
                    if consecutive_price_failures >= MAX_PRICE_FEED_FAILURES:
                        logger.critical(
                            f"[PROTECTION] [EMERGENCY] Price feed failed {consecutive_price_failures} consecutive times for "
                            f"{self.position.token_address[:8]}... Triggering protective exit."
                        )
                        await self.execute_exit("price_feed_failure")
                        break

                # 1. Update Peak Price and R-multiples
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

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[PROTECTION] Error in price protection loop: {e}", exc_info=True)

    async def _run_kill_switch_loop(self):
        """
        Lapis 3 (On-Chain Kill-Switch) -- independen dari harga, prioritas tertinggi.
        Evaluates real-time on-chain signals:
        - Liquidity pool depth drop / LP removal
        - Dev / Creator wallet sell & holder concentration shift
        - On-chain bonding curve price impact / slippage spike
        """
        try:
            while not self.exited:
                from app.blockchain.monitor import SolanaWebSocketMonitor
                poll_interval = 30.0 if SolanaWebSocketMonitor.degraded_mode else 2.0
                await asyncio.sleep(poll_interval)

                try:
                    reason = await self._check_onchain_kill_signals()
                except Exception as e:
                    logger.error(f"[PROTECTION] [L3] Error checking on-chain kill signals: {e}", exc_info=True)
                    continue

                if reason:
                    logger.warning(f"[PROTECTION] [L3] On-chain emergency event detected: {reason}!")
                    await self.execute_exit(reason)
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[PROTECTION] Error in kill-switch loop: {e}", exc_info=True)

    async def _check_onchain_kill_signals(self, signer_address: Optional[str] = None) -> Optional[str]:
        """
        Evaluates emergency on-chain signals for this open position:
        1. On-Chain Slippage / Price Impact Spike & Wallet-Agnostic Large Sell
        2. Dev Wallet Sell Fast-Path
        3. LP Removal / Liquidity Drop
        """
        token_address = self.position.token_address

        # 1. On-Chain Slippage / Price Impact Spike Detection (Wallet-Agnostic)
        try:
            from app.infrastructure.blockchain.bonding_curve_price import estimate_bonding_curve_price_impact
            sol_price_usd = getattr(settings, "SOL_USD_FALLBACK", 145.0)
            trade_size_sol = self.position.position_size_usd / sol_price_usd if sol_price_usd > 0 else 0.5
            current_impact = await estimate_bonding_curve_price_impact(token_address, trade_size_sol)

            if current_impact is not None:
                if not hasattr(self, "_baseline_price_impact") or self._baseline_price_impact is None:
                    self._baseline_price_impact = current_impact
                else:
                    impact_spike = current_impact - self._baseline_price_impact
                    threshold = getattr(settings, "KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT", 0.15)
                    if impact_spike >= threshold:
                        logger.warning(
                            f"[PROTECTION] [L3] On-chain price impact spiked +{impact_spike:.1%} "
                            f"({self._baseline_price_impact:.1%} -> {current_impact:.1%}) for {token_address[:8]}..."
                        )
                        dev_address = getattr(self.position, "dev_wallet_address", None)
                        if signer_address and dev_address and signer_address.lower() == dev_address.lower():
                            return "kill_switch_dev_dump"
                        return "kill_switch_large_sell"
        except Exception as err:
            logger.debug(f"[PROTECTION] [L3] Slippage spike check skipped: {err}")

        # 2. LP removal detection via liquidity pool depth
        if self.token_info_service is not None:
            token_info = await self.token_info_service.get_token_info(token_address)
            liquidity_usd = float(token_info.get("liquidity_usd", 0.0))

            if self._baseline_liquidity_usd is None:
                self._baseline_liquidity_usd = liquidity_usd
            elif self._baseline_liquidity_usd > 0:
                liquidity_drop_pct = 1.0 - (liquidity_usd / self._baseline_liquidity_usd)
                if liquidity_drop_pct >= settings.KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT:
                    logger.warning(
                        f"[PROTECTION] [L3] Liquidity dropped {liquidity_drop_pct:.1%} "
                        f"(${self._baseline_liquidity_usd:.0f} -> ${liquidity_usd:.0f}) for {token_address}"
                    )
                    return "kill_switch_lp"
                if liquidity_usd > self._baseline_liquidity_usd:
                    self._baseline_liquidity_usd = liquidity_usd

        # 3. Holder concentration shift & Dev wallet sell detection
        if self.token_safety_service is not None:
            safety_info = await self.token_safety_service.get_safety_info(token_address)
            top_10_share = float(safety_info.get("top_10_holders_share", 0.0))

            if self._baseline_top_10_holders_share is None:
                self._baseline_top_10_holders_share = top_10_share
            else:
                holder_shift = top_10_share - self._baseline_top_10_holders_share
                if holder_shift >= settings.KILL_SWITCH_DEV_WALLET_SELL_THRESHOLD_PCT:
                    logger.warning(
                        f"[PROTECTION] [L3] Dev wallet sell / Holder concentration shifted +{holder_shift:.1%} "
                        f"({self._baseline_top_10_holders_share:.1%} -> {top_10_share:.1%}) for {token_address}"
                    )
                    return "kill_switch_dev_dump"

        return None

    def evaluate_reserve_change(
        self,
        new_v_sol: int,
        new_v_token: int,
        signer_address: Optional[str] = None
    ) -> Optional[str]:
        """
        Wallet-agnostic evaluation of reserve state push notification.
        Returns:
          - 'kill_switch_dev_dump' if signer matches dev wallet and sell exceeds threshold
          - 'kill_switch_large_sell' if wallet-agnostic sell exceeds threshold
          - None if change is below threshold
        """
        if not hasattr(self, "_baseline_v_sol") or self._baseline_v_sol is None:
            self._baseline_v_sol = new_v_sol
            self._baseline_v_token = new_v_token
            return None

        threshold = getattr(settings, "KILL_SWITCH_SLIPPAGE_SPIKE_THRESHOLD_PCT", 0.15)
        # Drop in SOL reserves indicates tokens sold back to bonding curve
        if self._baseline_v_sol > 0:
            sol_reserve_drop_pct = (self._baseline_v_sol - new_v_sol) / self._baseline_v_sol
            if sol_reserve_drop_pct >= threshold:
                dev_address = getattr(self.position, "dev_wallet_address", None)
                if signer_address and dev_address and signer_address.lower() == dev_address.lower():
                    return "kill_switch_dev_dump"
                return "kill_switch_large_sell"

        return None

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
                # 1. Place Market order to mock pump.fun/Jupiter API with F-19 retry/recovery
                exit_price = self.current_price
                slippage_tolerance = 0.01 # initial 1%
                exit_success = False
                attempts = 0
                
                is_kill_switch = reason.startswith("kill_switch")
                
                # Testing hooks for exit failures
                fail_exit = (self.position.token_address == "FailExitTokenxxxxxxxxxxxxxxxxxxxxxxxx")
                fail_ks_exit = (self.position.token_address == "FailKillSwitchExitTokenxxxxxxxxxxxxxxxx")
                
                MAX_EXIT_ATTEMPTS = 10  # Batas keras: cegah infinite loop & rate-limit ban PumpPortal
                
                while not exit_success and attempts < MAX_EXIT_ATTEMPTS:
                    attempts += 1
                    try:
                        logger.info(f"[PROTECTION] Placing market exit order for {self.position.token_address} (Attempt {attempts}/{MAX_EXIT_ATTEMPTS}, Slippage: {slippage_tolerance:.1%}) due to {reason}...")
                        
                        if is_kill_switch:
                            if fail_ks_exit and attempts < 3: # fail first 2 attempts for testing
                                raise IOError("pump.fun swap failed (price impact/slippage limit)")
                        else:
                            if fail_exit and attempts < 3: # fail first 2 attempts for testing
                                raise IOError("pump.fun swap failed (network timeout)")
                                
                        from app.infrastructure.blockchain.wallet_manager import load_wallet_from_env
                        keypair = load_wallet_from_env()
                        
                        import sys
                        is_testing = any("pytest" in arg or "unittest" in arg for arg in sys.argv) or "pytest" in sys.modules or "unittest" in sys.modules
                        # Force false if run via uvicorn/main.py entrypoint to avoid test discovery false positives
                        if any("uvicorn" in arg or "main.py" in arg for arg in sys.argv):
                            is_testing = False
                        
                        if keypair and not is_testing:
                            # Local sign/broadcast for exit
                            from app.infrastructure.blockchain.pumpportal_client import build_trade_transaction
                            from app.infrastructure.blockchain.tx_signer import sign_and_broadcast_transaction
                            from app.core.config import settings
                            unsigned_tx = await build_trade_transaction(
                                public_key=str(keypair.pubkey()),
                                action="sell",
                                token_mint=self.position.token_address,
                                amount="100%",
                                denominated_in_sol=False,
                                slippage=slippage_tolerance * 100,  # dinamis, naik tiap retry
                                priority_fee=settings.PRIORITY_FEE_SELL  # dari config, default 0.00005 SOL
                            )
                            tx_sig = await sign_and_broadcast_transaction(unsigned_tx, keypair)
                            # Close token account to reclaim SOL rent after successful on-chain sell
                            try:
                                logger.info(f"[PROTECTION] Reclaiming SOL rent by closing token account for {self.position.token_address}...")
                                from app.infrastructure.blockchain.tx_signer import close_token_account
                                close_sig = await close_token_account(
                                    self.position.token_address, keypair,
                                    token_price_usd=self.current_price
                                )
                                if close_sig:
                                    logger.info(f"[PROTECTION] Token account closed successfully, tx: {close_sig}")
                                else:
                                    logger.warning(f"[PROTECTION] Token account close failed or was skipped.")
                            except Exception as close_err:
                                logger.error(f"[PROTECTION] Error during token account close: {close_err}", exc_info=True)
                        else:
                            # Paper trade fallback
                            from app.infrastructure.blockchain.trading_service import execute_pumpportal_swap
                            tx_sig = await execute_pumpportal_swap(
                                action="sell",
                                token_mint=self.position.token_address,
                                amount="100%",
                                denominated_in_sol=False,
                                slippage=slippage_tolerance * 100
                            )
                        logger.info(f"[PROTECTION] Exit order placed. TX: {tx_sig}")
                        exit_success = True
                    except Exception as e:
                        logger.warning(f"[PROTECTION] Exit order attempt {attempts} failed: {e}")
                        
                        if is_kill_switch:
                            # Retry agresif: immediate, no delay, slippage tolerance naik bertahap sampai max
                            slippage_tolerance = min(slippage_tolerance + 0.05, 0.50) # +5% up to 50% max
                            from app.core.error_handler import log_system_error, ErrorType, ErrorSeverity
                            await log_system_error(
                                error_type=ErrorType.CRITICAL_EXIT_FAILED,
                                severity=ErrorSeverity.CRITICAL,
                                context=f"CRITICAL: Kill-Switch exit failed (Attempt {attempts}/{MAX_EXIT_ATTEMPTS}) for token {self.position.token_address}. Retrying with slippage tolerance raised to {slippage_tolerance:.1%}.",
                                recovery_action="immediate_aggressive_retry",
                                resolution_status="pending"
                            )
                        else:
                            # Retry langsung dengan slippage tolerance +0.5% setiap retry
                            slippage_tolerance = min(slippage_tolerance + 0.005, 0.50)  # +0.5% slippage, max 50%
                            from app.core.error_handler import log_system_error, ErrorType, ErrorSeverity
                            await log_system_error(
                                error_type=ErrorType.EXIT_PENDING,
                                severity=ErrorSeverity.WARNING,
                                context=f"Exit pending: Exit failed (Attempt {attempts}/{MAX_EXIT_ATTEMPTS}) for token {self.position.token_address}. Retrying with slippage tolerance raised to {slippage_tolerance:.1%}.",
                                recovery_action="exit_retry_slippage_bump",
                                resolution_status="pending"
                            )
                            await asyncio.sleep(1.0)  # 1 detik delay antar retry hindari rate-limit

                # Jika semua percobaan habis dan masih gagal: ubah state ke EXIT_FAILED_MANUAL_REVIEW (bukan hapus dari DB!)
                if not exit_success:
                    logger.critical(
                        f"[PROTECTION] [MANUAL REVIEW REQUIRED] Exit GAGAL setelah {MAX_EXIT_ATTEMPTS} percobaan untuk token "
                        f"{self.position.token_address}. Menandai status posisi sebagai 'EXIT_FAILED_MANUAL_REVIEW'."
                    )
                    from app.core.error_handler import log_system_error, ErrorType, ErrorSeverity
                    await log_system_error(
                        error_type=ErrorType.CRITICAL_EXIT_FAILED,
                        severity=ErrorSeverity.CRITICAL,
                        context=f"Exit GAGAL setelah {MAX_EXIT_ATTEMPTS} percobaan untuk {self.position.token_address}. Membutuhkan intervensi manual.",
                        recovery_action="flag_exit_failed_manual_review",
                        resolution_status="manual_review_required"
                    )

                    # Mark state as EXIT_FAILED_MANUAL_REVIEW and persist to DB (do NOT delete!)
                    self.position.state = "EXIT_FAILED_MANUAL_REVIEW"
                    await self.position_repo.update_position(self.position)

                    # Broadcast high-priority WebSocket alert to F-07 dashboard
                    try:
                        await ws_manager.broadcast({
                            "type": "manual_review_required",
                            "data": {
                                "event": "manual_review_required",
                                "position_id": self.position.position_id,
                                "token_address": self.position.token_address,
                                "wallet_source": self.position.wallet_source,
                                "position_size_usd": self.position.position_size_usd,
                                "attempts": attempts,
                                "reason": reason,
                                "message": f"CRITICAL: Market exit failed after {attempts} attempts. Position retained for manual review.",
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            }
                        })
                    except Exception as ws_err:
                        logger.error(f"[PROTECTION] Failed to broadcast manual review WebSocket alert: {ws_err}")

                    return  # Return without creating a closed trade record

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
                resolved_symbol = "UNKNOWN"
                if self.token_info_service:
                    try:
                        token_info = await self.token_info_service.get_token_info(self.position.token_address)
                        if token_info and "token_symbol" in token_info:
                            resolved_symbol = token_info["token_symbol"]
                    except Exception as sym_err:
                        logger.warning(f"[PROTECTION] Could not fetch real token symbol: {sym_err}")
                
                closed_trade = ClosedTrade(
                    trade_id=f"tr_{uuid.uuid4().hex[:8]}",
                    wallet_source=self.position.wallet_source,
                    token_address=self.position.token_address,
                    token_symbol=resolved_symbol,
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
                    is_paper_trade=not (keypair is not None),
                    is_bootstrap=False,
                    model_version=self.position.model_version,
                    slippage_actual=self.position.slippage_actual
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

        # Cleanup di luar `async with self.lock`: begitu salah satu lapis
        # berhasil exit (self.exited sudah True di atas), task lapis lainnya
        # tidak punya alasan untuk terus polling. Cancel dilakukan setelah
        # lock dilepas supaya tidak ada risiko deadlock terhadap task yang
        # sedang menunggu lock yang sama.
        current_task = asyncio.current_task()
        for task in self.tasks:
            if task is not current_task and not task.done():
                task.cancel()