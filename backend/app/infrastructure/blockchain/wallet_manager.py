import logging
import json
import urllib.request
from typing import Optional
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from app.core.config import settings

logger = logging.getLogger(__name__)


def load_wallet_from_env() -> Optional[Keypair]:
    """
    Loads Keypair from SOLANA_WALLET_PRIVATE_KEY environment variable.
    Redacts keys in all logs for security.
    """
    try:
        pk_str = getattr(settings, "SOLANA_WALLET_PRIVATE_KEY", None)
        if not pk_str or len(pk_str.strip()) < 10 or pk_str == "YOUR_SOLANA_WALLET_PRIVATE_KEY":
            logger.warning("[WALLET MANAGER] No valid SOLANA_WALLET_PRIVATE_KEY found in settings. Running in PAPER/DRY RUN mode.")
            return None
        
        # Load keypair from base58 private key string
        keypair = Keypair.from_base58_string(pk_str.strip())
        logger.info(f"[WALLET MANAGER] Wallet keypair successfully loaded. Public Key: {keypair.pubkey()}")
        return keypair
    except Exception as e:
        logger.error(f"[WALLET MANAGER] Failed to load wallet keypair: [REDACTED_ERROR]")
        raise ValueError("Invalid SOLANA_WALLET_PRIVATE_KEY format")


async def get_sol_balance(pubkey: Pubkey) -> float:
    """
    Queries current Solana wallet balance via RPC JSON-RPC HTTP query.
    Returns balance in SOL (float).
    """
    rpc_url = getattr(settings, "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [str(pubkey)]
    }
    
    def sync_fetch():
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

    import asyncio
    res = await asyncio.to_thread(sync_fetch)
    
    if "error" in res or "result" not in res:
        logger.error(f"[WALLET MANAGER] Failed to query SOL balance from RPC: {res.get('error', 'unknown error')}")
        return 0.0
        
    lamports = res["result"].get("value", 0)
    sol_balance = float(lamports) / 1_000_000_000.0
    logger.info(f"[WALLET MANAGER] Wallet {str(pubkey)[:6]}... balance: {sol_balance:.4f} SOL")
    return sol_balance
