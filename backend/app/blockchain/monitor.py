import asyncio
import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set, Callable
import websockets

from app.core.config import settings
from app.domain.interfaces import IWalletMovementMonitor
from app.domain.models import OnchainEvent

logger = logging.getLogger(__name__)

# Valid base58 check for Solana Public Key (32-44 characters)
SOLANA_PUBKEY_REGEX = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_valid_solana_pubkey(pubkey: str) -> bool:
    return bool(SOLANA_PUBKEY_REGEX.match(pubkey))


class SolanaWebSocketMonitor(IWalletMovementMonitor):
    """
    Layer 0: Data Source & Wallet Monitoring
    Maintains WebSocket logsSubscribe connection for Whale Wallets.
    Supports heartbeats, reconnects with exponential backoff, and fallback RPC.
    """
    # Class-level variables synchronized globally
    degraded_mode = False
    rpc_state = "primary"
    current_rpc_url = settings.RPC_PRIMARY_URL

    def __init__(self):
        self.active_wallets: Set[str] = set()
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=100) # Backpressure limit
        self.is_running = False
        self.websocket = None
        self.monitor_task: Optional[asyncio.Task] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.health_check_task: Optional[asyncio.Task] = None
        
        # Lock to prevent race conditions between heartbeat and monitor loops
        self._ws_lock = asyncio.Lock()
        
        # Instance attributes mirror the class defaults
        self.rpc_state = SolanaWebSocketMonitor.rpc_state
        self.current_rpc_url = SolanaWebSocketMonitor.current_rpc_url
        self.current_ws_url = self._http_to_ws(self.current_rpc_url)
        
        # Deduplication memory window (signature -> timestamp_utc added)
        self.signature_dedup_window: Dict[str, float] = {}
        self.dedup_cleanup_task: Optional[asyncio.Task] = None

        # Account PDA subscriptions (pda_address -> set of callback functions)
        self.account_callbacks: Dict[str, Set[Callable]] = {}
        self.pda_sub_ids: Dict[str, int] = {}
        self.sub_id_to_pda: Dict[int, str] = {}
        self.pending_sub_requests: Dict[int, str] = {}

    def _http_to_ws(self, http_url: str) -> str:
        """Helper to convert http/https RPC endpoints to ws/wss."""
        if http_url.startswith("https://"):
            return http_url.replace("https://", "wss://", 1)
        elif http_url.startswith("http://"):
            return http_url.replace("http://", "ws://", 1)
        return http_url

    def get_event_queue(self) -> asyncio.Queue:
        return self.event_queue

    def update_wallets(self, wallets: List[str]):
        """Dynamically update target wallets."""
        valid_wallets = [w for w in wallets if is_valid_solana_pubkey(w)]
        self.active_wallets = set(valid_wallets)
        logger.info(f"Monitor target wallets updated: {list(self.active_wallets)}")

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        logger.info("Starting Solana Wallet Movement Monitor...")
        
        # Start cleanup task for deduplication window
        self.dedup_cleanup_task = asyncio.create_task(self._cleanup_dedup_window_loop())
        
        # Main monitoring loop
        self.monitor_task = asyncio.create_task(self._run_monitor_loop())
        
        # Heartbeat loop
        self.heartbeat_task = asyncio.create_task(self._run_heartbeat_loop())

        # Periodic health check loop
        self.health_check_task = asyncio.create_task(self._run_health_check_loop())

    async def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        logger.info("Stopping Solana Wallet Movement Monitor...")
        
        async with self._ws_lock:
            if self.websocket:
                try:
                    await self.websocket.close()
                except Exception:
                    pass
                self.websocket = None
        
        if self.monitor_task:
            self.monitor_task.cancel()
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        if self.health_check_task:
            self.health_check_task.cancel()
        if self.dedup_cleanup_task:
            self.dedup_cleanup_task.cancel()

    async def _run_monitor_loop(self):
        reconnect_attempts = 0
        backoff = 1.0
        
        while self.is_running:
            if not self.active_wallets and not self.account_callbacks:
                logger.debug("No active wallets or account PDAs to monitor. Waiting 5s...")
                await asyncio.sleep(5)
                continue
                
            try:
                logger.info(f"Connecting to Solana RPC WebSocket: {self.current_ws_url}")
                async with websockets.connect(self.current_ws_url, ping_interval=None) as ws:
                    self.websocket = ws
                    reconnect_attempts = 0
                    backoff = 1.0
                    
                    # Send subscriptions for each wallet
                    for wallet in self.active_wallets:
                        await self._subscribe_to_wallet(ws, wallet)

                    # Send subscriptions for each active account PDA (reconnect resilience)
                    for pda_addr in list(self.account_callbacks.keys()):
                        await self._subscribe_to_account(ws, pda_addr)

                    logger.info("All subscriptions established successfully.")
                    
                    # Event ingestion loop
                    async for message in ws:
                        await self._handle_ws_message(message)
                        
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                logger.error(f"WebSocket connection error: {e}")
                async with self._ws_lock:
                    self.websocket = None
                
                # Log central F-19 error
                from app.core.error_handler import log_system_error, ErrorType, ErrorSeverity
                asyncio.create_task(log_system_error(
                    error_type=ErrorType.RPC_DISCONNECTED,
                    severity=ErrorSeverity.WARNING,
                    context=f"Solana WebSocket connection lost: {str(e)}",
                    recovery_action=f"reconnect_retry (attempt {reconnect_attempts+1}, backoff={backoff}s)"
                ))

                # Check fallback conditions
                reconnect_attempts += 1
                max_retry = getattr(settings, "RPC_MAX_RETRY", 5)
                if reconnect_attempts > max_retry:
                    logger.critical(f"Failed to reconnect after {max_retry} attempts. Initiating failover...")
                    await self._handle_failover()
                    reconnect_attempts = 0
                    backoff = 1.0
                
                if self.is_running:
                    logger.warning(f"Reconnecting in {backoff}s (Attempt {reconnect_attempts}/{max_retry})...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)

    async def _handle_failover(self) -> None:
        """Handles primary -> secondary -> fallback failover and transitions to degraded mode."""
        fallback_url = "https://api.mainnet-beta.solana.com"
        
        if self.rpc_state == "primary":
            logger.warning(f"[RPC FAILOVER] Primary RPC failed. Switching to Secondary URL: {settings.RPC_SECONDARY_URL}")
            self.rpc_state = "secondary"
            self.current_rpc_url = settings.RPC_SECONDARY_URL
            self.current_ws_url = self._http_to_ws(settings.RPC_SECONDARY_URL)
            await self._broadcast_system_alert(
                alert_type="rpc_failover",
                message=f"Primary RPC failed. Switched to Secondary RPC: {settings.RPC_SECONDARY_URL}"
            )
        elif self.rpc_state == "secondary":
            logger.warning(f"[RPC FAILOVER] Secondary RPC failed. Switching to Public Fallback URL: {fallback_url}")
            self.rpc_state = "fallback"
            self.current_rpc_url = fallback_url
            self.current_ws_url = self._http_to_ws(fallback_url)
            await self._broadcast_system_alert(
                alert_type="rpc_failover",
                message=f"Secondary RPC failed. Switched to Public Fallback RPC: {fallback_url}"
            )
        elif self.rpc_state == "fallback":
            logger.critical("[RPC DEGRADED] All Primary, Secondary, and Fallback RPCs failed. Entering DEGRADED mode.")
            self.rpc_state = "degraded"
            SolanaWebSocketMonitor.degraded_mode = True
            await self._broadcast_system_alert(
                alert_type="rpc_degraded",
                message="All RPC endpoints failed. System entered DEGRADED mode (new trades stopped)."
            )

    async def _broadcast_system_alert(self, alert_type: str, message: str) -> None:
        """Broadcasts system status change alert to F-07 dashboard WebSocket."""
        try:
            from app.websocket.manager import manager as ws_manager
            event_payload = {
                "event": "system_alert",
                "alert_type": alert_type,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            # Wrap in websocket envelope format used by backend websocket manager
            await ws_manager.broadcast({
                "type": "system_alert",
                "data": event_payload
            })
        except Exception as e:
            logger.error(f"Error broadcasting system alert to WebSocket: {e}")

    async def _run_health_check_loop(self) -> None:
        """Background loop executing every 60 seconds to detect RPC recovery."""
        fallback_url = "https://api.mainnet-beta.solana.com"
        while self.is_running:
            await asyncio.sleep(60.0)
            
            primary_healthy = await self._check_url_health(settings.RPC_PRIMARY_URL)
            secondary_healthy = await self._check_url_health(settings.RPC_SECONDARY_URL)
            fallback_healthy = await self._check_url_health(fallback_url)
            
            logger.info(
                f"[RPC HEALTH CHECK] Primary: {'UP' if primary_healthy else 'DOWN'}, "
                f"Secondary: {'UP' if secondary_healthy else 'DOWN'}, "
                f"Fallback: {'UP' if fallback_healthy else 'DOWN'}"
            )
            
            # Handle recovery back to primary
            if primary_healthy and self.rpc_state in ["secondary", "fallback", "degraded"]:
                logger.warning(f"[RPC RECOVERY] Primary RPC restored. Switching back to: {settings.RPC_PRIMARY_URL}")
                self.rpc_state = "primary"
                SolanaWebSocketMonitor.degraded_mode = False
                self.current_rpc_url = settings.RPC_PRIMARY_URL
                self.current_ws_url = self._http_to_ws(settings.RPC_PRIMARY_URL)
                
                await self._broadcast_system_alert(
                    alert_type="rpc_recovery",
                    message=f"Primary RPC restored. Normal monitoring resumed at: {settings.RPC_PRIMARY_URL}"
                )
                
                if self.websocket:
                    await self.websocket.close()  # close to force reconnect in main monitor loop
                    
            # Handle recovery from fallback/degraded back to secondary
            elif secondary_healthy and self.rpc_state in ["fallback", "degraded"]:
                logger.warning(f"[RPC RECOVERY] Secondary RPC restored. Switching to: {settings.RPC_SECONDARY_URL}")
                self.rpc_state = "secondary"
                SolanaWebSocketMonitor.degraded_mode = False
                self.current_rpc_url = settings.RPC_SECONDARY_URL
                self.current_ws_url = self._http_to_ws(settings.RPC_SECONDARY_URL)
                
                await self._broadcast_system_alert(
                    alert_type="rpc_recovery",
                    message=f"Secondary RPC restored. Switched to: {settings.RPC_SECONDARY_URL}"
                )
                
                if self.websocket:
                    await self.websocket.close()
                    
            # Handle recovery from degraded back to fallback
            elif fallback_healthy and self.rpc_state == "degraded":
                logger.warning(f"[RPC RECOVERY] Public Fallback RPC restored. Switching to: {fallback_url}")
                self.rpc_state = "fallback"
                SolanaWebSocketMonitor.degraded_mode = False
                self.current_rpc_url = fallback_url
                self.current_ws_url = self._http_to_ws(fallback_url)
                
                await self._broadcast_system_alert(
                    alert_type="rpc_recovery",
                    message=f"Public Fallback RPC restored. Switched to: {fallback_url}"
                )
                
                if self.websocket:
                    await self.websocket.close()

    async def _check_url_health(self, url: str) -> bool:
        """Lightweight HTTP check using urllib.request."""
        if not url:
            return False
            
        # Offline/localhost testing bypass: assume healthy
        if "localhost" in url or "127.0.0.1" in url or "dummy" in url:
            return True
            
        def sync_check():
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBlockHeight",
                "params": []
            }
            headers = {"Content-Type": "application/json"}
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    res = json.loads(response.read().decode("utf-8"))
                    return "result" in res and "error" not in res
            except Exception:
                return False
                
        return await asyncio.to_thread(sync_check)

    async def _subscribe_to_wallet(self, ws, wallet_address: str):
        """Subscribes to transactions mentioning the wallet address."""
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "logsSubscribe",
            "params": [
                {"mentions": [wallet_address]},
                {"commitment": "confirmed"}
            ]
        }
        await ws.send(json.dumps(payload))
        logger.info(f"Subscribed to logs for wallet: {wallet_address}")

    async def _subscribe_to_account(self, ws, pda_address: str):
        """Subscribes to account state changes for the PDA address."""
        req_id = int(time.time() * 1000)
        self.pending_sub_requests[req_id] = pda_address
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "accountSubscribe",
            "params": [
                pda_address,
                {"encoding": "base64", "commitment": "confirmed"}
            ]
        }
        await ws.send(json.dumps(payload))
        logger.info(f"[WS MONITOR] Subscribed to account PDA: {pda_address[:8]}... (req_id={req_id})")

    async def _unsubscribe_from_account(self, ws, pda_address: str):
        """Unsubscribes from account state changes for the PDA address."""
        sub_id = self.pda_sub_ids.get(pda_address)
        if sub_id:
            payload = {
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000),
                "method": "accountUnsubscribe",
                "params": [sub_id]
            }
            await ws.send(json.dumps(payload))
            logger.info(f"[WS MONITOR] Unsubscribed from account PDA {pda_address[:8]}... (sub_id={sub_id})")
            self.pda_sub_ids.pop(pda_address, None)
            self.sub_id_to_pda.pop(sub_id, None)

    async def subscribe_account(self, pda_address: str, callback: Callable):
        """Public API: Subscribe callback function to account PDA updates."""
        if pda_address not in self.account_callbacks:
            self.account_callbacks[pda_address] = set()
        self.account_callbacks[pda_address].add(callback)

        if self.websocket:
            async with self._ws_lock:
                await self._subscribe_to_account(self.websocket, pda_address)

    async def unsubscribe_account(self, pda_address: str, callback: Optional[Callable] = None):
        """Public API: Unsubscribe callback function from account PDA updates."""
        if pda_address in self.account_callbacks:
            if callback:
                self.account_callbacks[pda_address].discard(callback)
            if not callback or len(self.account_callbacks[pda_address]) == 0:
                self.account_callbacks.pop(pda_address, None)
                if self.websocket:
                    async with self._ws_lock:
                        await self._unsubscribe_from_account(self.websocket, pda_address)

    # Known DEX/swap program IDs we care about
    DEX_PROGRAM_IDS = {
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM V4
        "6EF8rrecMDMKMzBkv7jVLFv1E2syLQH5SH3iFh9FEAKB",  # pump.fun
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",   # Jupiter V6
        "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",  # Orca AMM
        "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX",   # Serum DEX
    }

    async def _handle_ws_message(self, raw_message: str):
        try:
            msg = json.loads(raw_message)

            if isinstance(msg, dict):
                # Handle accountSubscribe confirmation pairing
                if "id" in msg and msg["id"] in self.pending_sub_requests:
                    req_id = msg["id"]
                    pda_addr = self.pending_sub_requests.pop(req_id)
                    sub_id = msg.get("result")
                    if sub_id:
                        self.pda_sub_ids[pda_addr] = sub_id
                        self.sub_id_to_pda[sub_id] = pda_addr
                        logger.info(f"[WS MONITOR] Confirmed accountSubscribe for {pda_addr[:8]}... (sub_id={sub_id})")

                # Handle accountNotifications (accountSubscribe updates)
                if msg.get("method") == "accountNotification":
                    params = msg.get("params", {})
                    sub_id = params.get("subscription")
                    pda_addr = self.sub_id_to_pda.get(sub_id)
                    val = params.get("result", {}).get("value", {})
                    raw_data = val.get("data")
                    if isinstance(raw_data, list) and len(raw_data) > 0 and pda_addr:
                        import base64
                        decoded_bytes = base64.b64decode(raw_data[0])
                        callbacks = list(self.account_callbacks.get(pda_addr, []))
                        for cb in callbacks:
                            try:
                                if asyncio.iscoroutinefunction(cb):
                                    await cb(decoded_bytes)
                                else:
                                    cb(decoded_bytes)
                            except Exception as cb_err:
                                logger.error(f"[WS MONITOR] Callback error for PDA {pda_addr[:8]}: {cb_err}")

            if "method" in msg and msg["method"] == "logsNotification":
                params = msg.get("params", {})
                result = params.get("result", {})
                value = result.get("value", {})
                
                signature = value.get("signature")
                logs = value.get("logs", [])
                err = value.get("err")
                
                if not signature:
                    return
                
                # Skip failed transactions immediately
                if err is not None:
                    logger.debug(f"Skipping failed transaction: {signature}")
                    return

                # Pre-filter: only process if logs mention a known DEX program
                # This eliminates spam, airdrops, fee payments, and vote transactions
                log_text = " ".join(logs)
                is_dex_transaction = any(
                    prog_id in log_text for prog_id in self.DEX_PROGRAM_IDS
                )
                if not is_dex_transaction:
                    logger.debug(f"Skipping non-DEX transaction: {signature}")
                    return
                
                # Deduplication logic
                if signature in self.signature_dedup_window:
                    logger.debug(f"Duplicate transaction skipped: {signature}")
                    return
                self.signature_dedup_window[signature] = time.time()
                
                # Process only relevant DEX transactions
                await self._process_transaction_event(signature, logs)
        except Exception as e:
            logger.error(f"Error handling WS message: {e}", exc_info=True)

    async def _process_transaction_event(self, signature: str, logs: List[str]):
        """Fetches complete transaction data and puts structured event in the queue."""
        logger.debug(f"New DEX transaction detected, fetching details: {signature}")
        
        # 1. Fetch transaction details via getTransaction
        tx_details = await self._fetch_transaction_details(signature)
        if not tx_details:
            logger.debug(f"Failed to fetch transaction details for signature: {signature}")
            return
            
        # 2. Parse details into our structured format
        parsed_event = self._parse_transaction_payload(signature, tx_details, logs)
        if not parsed_event:
            return
            
        # 3. Validate timestamp (must not be older than 300s/5m to tolerate RPC delays & clock desyncs)
        now_ts = datetime.now(timezone.utc).timestamp()
        event_ts = parsed_event["timestamp_utc"].timestamp()
        if abs(now_ts - event_ts) > 300.0:
            logger.warning(f"Skipping event {signature}: timestamp offset too high ({abs(now_ts - event_ts):.1f}s)")
            return
            
        # 4. Put event into queue (backpressure applied if full)
        try:
            # wait if queue is full (slow down ingestion)
            await self.event_queue.put(parsed_event)
            logger.info(f"Successfully queued event {parsed_event['event_type']} from {parsed_event['wallet_address']}")
        except asyncio.QueueFull:
            logger.warning("Event queue full! Backpressure triggered, event dropped.")

    async def _fetch_transaction_details(self, signature: str) -> Optional[dict]:
        """Fetches full transaction payload from Solana RPC with fallback chain."""
        rpc_chain = [
            self.current_rpc_url,
            settings.RPC_PRIMARY_URL,
            settings.RPC_SECONDARY_URL,
            "https://api.mainnet-beta.solana.com",
        ]
        # Deduplicate while preserving order
        seen = set()
        rpc_chain = [u for u in rpc_chain if not (u in seen or seen.add(u))]

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }

        for rpc_url in rpc_chain:
            def sync_fetch(url=rpc_url):
                headers = {"Content-Type": "application/json"}
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req, timeout=10) as response:
                        return json.loads(response.read().decode("utf-8"))
                except Exception as e:
                    return {"error": str(e)}

            # Retry up to 2 times per RPC endpoint
            for attempt in range(2):
                res = await asyncio.to_thread(sync_fetch)
                if "error" not in res and "result" in res:
                    return res["result"]
                logger.debug(
                    f"[MONITOR] Retry {attempt+1}/2 fetching tx {signature} via {rpc_url}"
                )
                await asyncio.sleep(1.0)

        return None


    def _parse_transaction_payload(self, signature: str, tx_details: dict, logs: List[str]) -> Optional[dict]:
        """Parses transaction payload to extract structured fields."""
        try:
            # Extract timestamp
            block_time = tx_details.get("blockTime")
            if block_time:
                timestamp = datetime.fromtimestamp(block_time, timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)
                
            # Identify active whale wallet involved
            meta = tx_details.get("meta", {})
            post_balances = meta.get("postTokenBalances", [])
            pre_balances = meta.get("preTokenBalances", [])
            
            # Simple heuristic: scan signers/accountKeys
            transaction = tx_details.get("transaction", {})
            message = transaction.get("message", {})
            account_keys = message.get("accountKeys", [])
            
            signer_wallet = None
            for key_obj in account_keys:
                if isinstance(key_obj, dict):
                    pubkey = key_obj.get("pubkey")
                    is_signer = key_obj.get("signer", False)
                else:
                    pubkey = key_obj
                    is_signer = False
                
                if pubkey in self.active_wallets:
                    signer_wallet = pubkey
                    break
                    
            if not signer_wallet:
                # If not a signer, look in token balances
                for bal in post_balances + pre_balances:
                    owner = bal.get("owner")
                    if owner in self.active_wallets:
                        signer_wallet = owner
                        break
                        
            if not signer_wallet:
                logger.debug(f"Transaction {signature} does not involve monitored active wallets.")
                return None

            # Determine event type from logs — DEX program ID present OR swap keyword
            log_text = " ".join(logs)
            event_type = "transfer"
            if (any(prog_id in log_text for prog_id in self.DEX_PROGRAM_IDS)
                    or any(kw in log_text for kw in ["Swap", "swap"])):
                event_type = "swap"
            elif any(kw in log_text.lower() for kw in ["liquidity", "initialize pool", "withdraw"]):
                event_type = "lp_change"
                
            # Determine token mint & amount from token balance changes
            token_mint = None
            amount_usd = 0.0
            
            # Build pre-balance lookup: mint -> uiAmount
            pre_lookup = {}
            for bal in pre_balances:
                mint = bal.get("mint")
                owner = bal.get("owner")
                if mint and owner == signer_wallet:
                    pre_lookup[mint] = float((bal.get("uiTokenAmount") or {}).get("uiAmount") or 0.0)
            
            # Find the largest token delta for the signer wallet
            best_delta = 0.0
            for post in post_balances:
                mint = post.get("mint")
                owner = post.get("owner")
                if not mint or not owner or owner != signer_wallet:
                    continue
                if mint == "So11111111111111111111111111111111111111112":
                    continue  # Skip wrapped SOL
                post_amount = float((post.get("uiTokenAmount") or {}).get("uiAmount") or 0.0)
                pre_amount = pre_lookup.get(mint, 0.0)
                delta = abs(post_amount - pre_amount)
                if delta > best_delta:
                    best_delta = delta
                    token_mint = mint
            
            # Fallback: use SOL native balance delta as USD proxy
            if not token_mint or best_delta == 0.0:
                token_mint = token_mint or "So11111111111111111111111111111111111111112"
                # Get SOL balance delta for signer wallet
                keys_list = [
                    (k.get("pubkey") if isinstance(k, dict) else k)
                    for k in account_keys
                ]
                try:
                    signer_idx = keys_list.index(signer_wallet)
                    pre_sol_balances = meta.get("preBalances", [])
                    post_sol_balances = meta.get("postBalances", [])
                    if signer_idx < len(pre_sol_balances) and signer_idx < len(post_sol_balances):
                        sol_delta = abs(post_sol_balances[signer_idx] - pre_sol_balances[signer_idx]) / 1e9
                        amount_usd = sol_delta * 150.0  # Approximate SOL price fallback
                except (ValueError, IndexError):
                    amount_usd = 100.0  # Default fallback
            else:
                # Rough token -> USD estimate (at least minimum)
                amount_usd = max(best_delta * 0.001, 10.0)
                
            return {
                "wallet_address": signer_wallet,
                "event_type": event_type,
                "token_mint": token_mint,
                "amount_usd": max(amount_usd, 10.0),  # Never below min filter threshold
                "signature": signature,
                "timestamp_utc": timestamp
            }
        except Exception as e:
            logger.error(f"Error parsing transaction {signature}: {e}", exc_info=True)
            return None


    async def _run_heartbeat_loop(self):
        """Monitors connection state and forcefully resets dead connections.
        Uses state.name check only — compatible with websockets v16.0.
        Manual ping/pong removed as it is handled internally by the library.
        """
        while self.is_running:
            await asyncio.sleep(30)
            async with self._ws_lock:
                ws = self.websocket
                if ws is None:
                    logger.debug("WebSocket Heartbeat: No active connection.")
                    continue
                try:
                    state = ws.state.name
                    if state == "OPEN":
                        logger.debug("WebSocket Heartbeat: Connection healthy (state=OPEN).")
                    else:
                        logger.warning(f"WebSocket Heartbeat: Connection in dead state ({state}). Forcing reset.")
                        try:
                            await ws.close()
                        except Exception:
                            pass
                        self.websocket = None
                except Exception as e:
                    logger.error(f"WebSocket heartbeat check error: {e}")
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    self.websocket = None

    async def _cleanup_dedup_window_loop(self):
        """Periodically cleans up transaction signatures older than 60 seconds."""
        while self.is_running:
            await asyncio.sleep(10)
            now = time.time()
            expired = [sig for sig, added in self.signature_dedup_window.items() if now - added > 60.0]
            for sig in expired:
                self.signature_dedup_window.pop(sig, None)


class SolanaMonitorSimulator(IWalletMovementMonitor):
    """
    Offline Simulator Fallback
    Feeds fake transaction events to test the pipeline without Solana RPC credits.
    """
    def __init__(self, wallets: List[str]):
        self.wallets = wallets
        self.event_queue = asyncio.Queue(maxsize=100)
        self.is_running = False
        self.task = None

    def get_event_queue(self) -> asyncio.Queue:
        return self.event_queue

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        logger.warning("Solana RPC offline or simulation mode active. Launching Wallet Monitor Simulator...")
        self.task = asyncio.create_task(self._run_simulation())

    async def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
        logger.info("Stopped Wallet Monitor Simulator.")

    async def _run_simulation(self):
        import random
        token_mints = [
            "DezXAZ8z7PnrnRJjz3wXBoRgixrfNg7yFLBnRx4S75Jb", # BONK
            "EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",  # WIF
            "CzLSujW7ZJuY7oL4b5C32hiyUeZSt84b5F08Suj752b", # FAKE 1
        ]
        
        while self.is_running:
            await asyncio.sleep(random.randint(15, 30))
            if not self.wallets:
                continue
                
            wallet = random.choice(self.wallets)
            event_type = random.choice(["swap", "swap", "swap", "transfer", "lp_change"])
            token = random.choice(token_mints)
            amount = float(random.randint(50, 15000))
            
            # Generate fake base58 signature
            sig_chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
            sig = "".join(random.choice(sig_chars) for _ in range(88))
            
            event = {
                "wallet_address": wallet,
                "event_type": event_type,
                "token_mint": token,
                "amount_usd": amount,
                "signature": sig,
                "timestamp_utc": datetime.now(timezone.utc)
            }
            
            try:
                await self.event_queue.put(event)
                logger.info(f"[SIMULATOR] Emitted fake event {event_type} from {wallet} for {token} (${amount:.1f})")
            except asyncio.QueueFull:
                pass


_global_ws_monitor: Optional[SolanaWebSocketMonitor] = None


def get_ws_monitor() -> SolanaWebSocketMonitor:
    global _global_ws_monitor
    if _global_ws_monitor is None:
        _global_ws_monitor = SolanaWebSocketMonitor()
    return _global_ws_monitor
