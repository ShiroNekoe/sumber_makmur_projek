import logging
import json
import base64
import asyncio
import urllib.request
from typing import Optional, List
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from app.core.config import settings

logger = logging.getLogger(__name__)


async def sign_and_broadcast_transaction(
    raw_tx_bytes: bytes,
    signer_keypair: Keypair
) -> Optional[str]:
    """
    Deserializes, signs locally, and broadcasts a transaction.
    Uses the configured primary RPC URL with failover to secondary RPC URL.
    Polls getSignatureStatuses until confirmed or timeout.
    """
    try:
        # 1. Deserialize transaction message
        tx = VersionedTransaction.from_bytes(raw_tx_bytes)
        
        # 2. Sign transaction locally using keypair
        signed_tx = VersionedTransaction(tx.message, [signer_keypair])
        tx_hash = str(signed_tx.signatures[0])
        
        # 3. Base64 encode serialized bytes for RPC payload
        serialized_bytes = bytes(signed_tx)
        b64_tx_payload = base64.b64encode(serialized_bytes).decode("utf-8")
        
        # Determine RPC URLs
        primary_url = getattr(settings, "RPC_PRIMARY_URL", "https://api.mainnet-beta.solana.com")
        secondary_url = getattr(settings, "RPC_SECONDARY_URL", "https://api.devnet.solana.com")
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                b64_tx_payload,
                {"encoding": "base64", "skipPreflight": False}
            ]
        }
        
        # Broadcast with fallback
        broadcast_success = False
        active_url = primary_url
        
        for attempt in range(2):
            url_to_use = primary_url if attempt == 0 else secondary_url
            def sync_send():
                try:
                    req = urllib.request.Request(
                        url_to_use,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=10) as response:
                        return json.loads(response.read().decode("utf-8"))
                except Exception as e:
                    return {"error": str(e)}
            
            logger.info(f"[TX SIGNER] Broadcasting transaction {tx_hash[:8]}... to RPC: {url_to_use}")
            res = await asyncio.to_thread(sync_send)
            
            if "error" not in res and "result" in res:
                broadcast_success = True
                active_url = url_to_use
                break
            else:
                err_msg = res.get("error", "unknown RPC error")
                logger.warning(f"[TX SIGNER] Broadcast attempt {attempt+1} failed on {url_to_use}: {err_msg}")
        
        if not broadcast_success:
            raise IOError("Transaction broadcast failed on all RPC endpoints.")
            
        # 4. Poll getSignatureStatuses until confirmed
        logger.info(f"[TX SIGNER] Broadcast complete. Polling status for signature: {tx_hash}")
        confirmed = await poll_signature_status(tx_hash, active_url)
        
        if confirmed:
            logger.info(f"[TX SIGNER] Transaction successfully confirmed: {tx_hash}")
            return tx_hash
        else:
            logger.error(f"[TX SIGNER] Transaction expired or not confirmed: {tx_hash}")
            raise TimeoutError("Transaction confirmation timed out or expired on-chain.")
            
    except Exception as e:
        logger.error(f"[TX SIGNER] Sign & Broadcast error: {e}", exc_info=True)
        raise e


async def poll_signature_status(tx_hash: str, rpc_url: str) -> bool:
    """
    Polls getSignatureStatuses for a given transaction hash.
    Timeout after 45 seconds.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignatureStatuses",
        "params": [
            [tx_hash],
            {"searchTransactionHistory": False}
        ]
    }
    
    max_seconds = 45
    for sec in range(max_seconds):
        await asyncio.sleep(1.0)
        
        def sync_check():
            try:
                req = urllib.request.Request(
                    rpc_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as e:
                return {"error": str(e)}
                
        res = await asyncio.to_thread(sync_check)
        if "error" in res or "result" not in res:
            continue
            
        value = res["result"].get("value", [])
        if not value or value[0] is None:
            continue
            
        status = value[0]
        # Check transaction error
        if status.get("err"):
            logger.error(f"[TX SIGNER] Transaction failed with error: {status['err']}")
            return False
            
        confirmation_status = status.get("confirmationStatus")
        if confirmation_status in ("processed", "confirmed", "finalized"):
            logger.info(f"[TX SIGNER] Confirmed on-chain ({confirmation_status}) at slot {status.get('slot')}")
            return True
            
    return False
