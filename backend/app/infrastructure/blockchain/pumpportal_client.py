import logging
import json
import urllib.request
import urllib.error
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)


async def build_trade_transaction(
    public_key: str,
    action: str,
    token_mint: str,
    amount: float,
    denominated_in_sol: bool = True,
    slippage: float = 5.0,
    priority_fee: float = 0.003,
    pool: str = "pump"
) -> Optional[bytes]:
    """
    Sends POST request to PumpPortal's Local Transaction API.
    Returns the raw unsigned transaction bytes on success.
    Implements exponential backoff retries for transient errors (timeouts/5xx).
    Throws permanent errors (400, client errors) immediately.
    """
    url = "https://pumpportal.fun/api/trade-local"
    
    payload = {
        "publicKey": public_key,
        "action": action,
        "mint": token_mint,
        "amount": str(amount) if not denominated_in_sol and isinstance(amount, str) else amount,
        "denominatedInSol": "true" if denominated_in_sol else "false",
        "slippage": int(slippage),
        "priorityFee": priority_fee,
        "pool": pool
    }
    
    # Retry configuration
    max_attempts = 4
    base_delay = 1.0 # 1s, 2s, 4s, 8s backoff
    
    for attempt in range(max_attempts):
        try:
            def sync_post():
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=8) as response:
                    return response.read()

            raw_tx_bytes = await asyncio.to_thread(sync_post)
            if raw_tx_bytes and len(raw_tx_bytes) > 50: # valid Solana transactions are at least ~100 bytes
                return raw_tx_bytes
            else:
                raise ValueError("Empty or invalid transaction payload returned from PumpPortal API")
                
        except urllib.error.HTTPError as http_err:
            status_code = http_err.code
            # Client errors (e.g. 400 Bad Request, 401, 403, 404) are permanent
            if 400 <= status_code < 500:
                error_body = http_err.read().decode("utf-8", errors="ignore")
                logger.error(f"[PUMPPORTAL CLIENT] Permanent error {status_code}: {error_body}")
                raise ValueError(f"Permanent PumpPortal API failure: {error_body}")
            
            # 5xx Server errors are transient
            logger.warning(f"[PUMPPORTAL CLIENT] Transient HTTP error {status_code} (attempt {attempt+1}/{max_attempts})")
            if attempt == max_attempts - 1:
                raise IOError(f"PumpPortal API failed after {max_attempts} attempts: {http_err}")
                
        except (urllib.error.URLError, asyncio.TimeoutError, Exception) as transient_err:
            logger.warning(f"[PUMPPORTAL CLIENT] Transient connection issue (attempt {attempt+1}/{max_attempts}): {transient_err}")
            if attempt == max_attempts - 1:
                raise IOError(f"PumpPortal connection failed after {max_attempts} attempts: {transient_err}")
                
        # Exponential backoff delay
        delay = base_delay * (2 ** attempt)
        await asyncio.sleep(delay)
        
    return None
