import asyncio
import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set
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
    def __init__(self):
        self.active_wallets: Set[str] = set()
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=100) # Backpressure limit
        self.is_running = False
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.monitor_task: Optional[asyncio.Task] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.current_rpc_url = settings.SOLANA_RPC_URL
        self.current_ws_url = self._http_to_ws(settings.SOLANA_RPC_URL)
        
        # Deduplication memory window (signature -> timestamp_utc added)
        self.signature_dedup_window: Dict[str, float] = {}
        self.dedup_cleanup_task: Optional[asyncio.Task] = None

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

    async def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        logger.info("Stopping Solana Wallet Movement Monitor...")
        
        if self.websocket:
            await self.websocket.close()
        
        if self.monitor_task:
            self.monitor_task.cancel()
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        if self.dedup_cleanup_task:
            self.dedup_cleanup_task.cancel()

    async def _run_monitor_loop(self):
        reconnect_attempts = 0
        backoff = 1.0
        
        while self.is_running:
            if not self.active_wallets:
                logger.debug("No active wallets to monitor. Waiting 5s...")
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
                    
                    logger.info("All subscriptions established successfully.")
                    
                    # Event ingestion loop
                    async for message in ws:
                        await self._handle_ws_message(message)
                        
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                logger.error(f"WebSocket connection error: {e}")
                self.websocket = None
                
                # Check fallback conditions
                reconnect_attempts += 1
                if reconnect_attempts > 5:
                    logger.critical("Failed to reconnect after 5 attempts. Initiating RPC Fallback...")
                    self._switch_to_fallback_rpc()
                    reconnect_attempts = 0
                    backoff = 1.0
                
                if self.is_running:
                    logger.warning(f"Reconnecting in {backoff}s (Attempt {reconnect_attempts}/5)...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)

    def _switch_to_fallback_rpc(self):
        """Switches primary RPC URL to secondary fallback URL."""
        if self.current_rpc_url == settings.SOLANA_RPC_URL:
            self.current_rpc_url = settings.SOLANA_RPC_FALLBACK_URL
            logger.warning(f"RPC switched to fallback: {self.current_rpc_url}")
        else:
            self.current_rpc_url = settings.SOLANA_RPC_URL
            logger.warning(f"RPC switched back to primary: {self.current_rpc_url}")
            
        self.current_ws_url = self._http_to_ws(self.current_rpc_url)

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

    async def _handle_ws_message(self, raw_message: str):
        try:
            msg = json.loads(raw_message)
            if "method" in msg and msg["method"] == "logsNotification":
                params = msg.get("params", {})
                result = params.get("result", {})
                value = result.get("value", {})
                
                signature = value.get("signature")
                logs = value.get("logs", [])
                
                if not signature:
                    return
                
                # Deduplication logic
                if signature in self.signature_dedup_window:
                    logger.debug(f"Duplicate transaction skipped: {signature}")
                    return
                self.signature_dedup_window[signature] = time.time()
                
                # Verify that transaction is relevant to one of our active wallets
                # Logs notification mentions context, let's parse logs to identify wallet & action
                await self._process_transaction_event(signature, logs)
        except Exception as e:
            logger.error(f"Error handling WS message: {e}", exc_info=True)

    async def _process_transaction_event(self, signature: str, logs: List[str]):
        """Fetches complete transaction data and puts structured event in the queue."""
        logger.info(f"New transaction detected, fetching details: {signature}")
        
        # 1. Fetch transaction details via getTransaction
        tx_details = await self._fetch_transaction_details(signature)
        if not tx_details:
            logger.warning(f"Failed to fetch transaction details for signature: {signature}")
            return
            
        # 2. Parse details into our structured format
        parsed_event = self._parse_transaction_payload(signature, tx_details, logs)
        if not parsed_event:
            return
            
        # 3. Validate timestamp (must not be older than 60s)
        now_ts = datetime.now(timezone.utc).timestamp()
        event_ts = parsed_event["timestamp_utc"].timestamp()
        if abs(now_ts - event_ts) > 60.0:
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
        """Fetches full transaction payload from Solana RPC."""
        def sync_fetch():
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                ]
            }
            headers = {"Content-Type": "application/json"}
            req = urllib.request.Request(
                self.current_rpc_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as e:
                return {"error": str(e)}

        # Retry up to 3 times for RPC robustness
        for attempt in range(3):
            res = await asyncio.to_thread(sync_fetch)
            if "error" not in res and "result" in res:
                return res["result"]
            logger.warning(f"Retry {attempt+1}/3 fetching transaction details for {signature}")
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
                    is_signer = False # Simple parsed layout
                
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
                logger.warning(f"Transaction {signature} does not involve monitored active wallets.")
                return None

            # Determine event type
            event_type = "transfer"
            logs_str = " ".join(logs).lower()
            if any(kw in logs_str for kw in ["swap", "buy", "sell"]):
                event_type = "swap"
            elif any(kw in logs_str for kw in ["liquidity", "initialize", "withdraw"]):
                event_type = "lp_change"
                
            # Determine token mint & amount
            token_mint = "So11111111111111111111111111111111111111112"  # Default WSOL
            amount_usd = 0.0
            
            # Find foreign mints moved
            for post in post_balances:
                mint = post.get("mint")
                if mint and mint != "So11111111111111111111111111111111111111112":
                    token_mint = mint
                    # Estimate value
                    ui_amount_info = post.get("uiTokenAmount", {})
                    amount_usd = float(ui_amount_info.get("uiAmount") or 0.0) * 10.0 # Placeholder calculation
                    break
                    
            return {
                "wallet_address": signer_wallet,
                "event_type": event_type,
                "token_mint": token_mint,
                "amount_usd": amount_usd or 100.0, # default if unparsed
                "signature": signature,
                "timestamp_utc": timestamp
            }
        except Exception as e:
            logger.error(f"Error parsing transaction {signature}: {e}", exc_info=True)
            return None

    async def _run_heartbeat_loop(self):
        """Sends lightweight websocket ping to verify connection health."""
        while self.is_running:
            await asyncio.sleep(30)
            if self.websocket and self.websocket.open:
                try:
                    pong_waiter = await self.websocket.ping()
                    await asyncio.wait_for(pong_waiter, timeout=5.0)
                    logger.debug("WebSocket Heartbeat: Connection healthy.")
                except Exception as e:
                    logger.error(f"WebSocket heartbeat failure: {e}")
                    # Force close connection to trigger reconnect backoff
                    await self.websocket.close()
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
