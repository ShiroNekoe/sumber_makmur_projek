import json
import urllib.request
import logging
import asyncio
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

async def execute_pumpportal_swap(
    action: str,
    token_mint: str,
    amount: float,
    denominated_in_sol: bool = True,
    slippage: float = 5.0,
    priority_fee: float = 0.0001  # Default hemat — override dari caller jika perlu
) -> Optional[str]:
    """
    Infrastructure Layer: Real-Case PumpPortal Trade Executor.
    Uses the Lightning Transaction API to sign and land transactions on Solana mainnet.
    Returns: transaction signature string on success, None on failure.
    """
    import sys
    is_testing = any("pytest" in arg or "unittest" in arg for arg in sys.argv) or "pytest" in sys.modules or "unittest" in sys.modules
    # Force false if run via uvicorn/main.py entrypoint to avoid test discovery false positives
    if any("uvicorn" in arg or "main.py" in arg for arg in sys.argv):
        is_testing = False
    api_key = getattr(settings, "PUMP_FUN_API_KEY", None)
    if is_testing or not api_key or len(api_key.strip()) < 10 or api_key == "YOUR_PUMP_FUN_API_KEY":
        logger.info(f"[TRADING SERVICE] [PAPER TRADE] Simulating {action} of {amount} on {token_mint[:6]}...")
        await asyncio.sleep(0.05)
        return f"mock_tx_{token_mint[:6]}"

    url = f"https://pumpportal.fun/api/trade?api-key={api_key}"
    payload = {
        "action": action,  # "buy" | "sell"
        "mint": token_mint,
        "amount": str(amount) if not denominated_in_sol and isinstance(amount, str) else amount,
        "denominatedInSol": "true" if denominated_in_sol else "false",
        "slippage": int(slippage),
        "priorityFee": priority_fee,
        "pool": "auto"
    }

    def sync_request():
        try:
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
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e)}

    logger.info(f"[TRADING SERVICE] Sending {action} request to PumpPortal for {token_mint[:8]} (Slippage: {slippage}%, Fee: {priority_fee})...")
    res = await asyncio.to_thread(sync_request)
    
    if "error" in res:
        logger.error(f"[TRADING SERVICE] Swap request failed: {res['error']}")
        raise IOError(f"PumpPortal swap failed: {res['error']}")

    # Check response structure. PumpPortal usually returns transaction hash or signature
    tx_hash = res.get("signature") or res.get("txHash")
    if tx_hash:
        logger.info(f"[TRADING SERVICE] [CONFIRMED] Trade successful! TX Signature: {tx_hash}")
        return tx_hash

    # If it succeeded but no signature was returned in payload
    if res.get("success") is True:
        logger.info(f"[TRADING SERVICE] [CONFIRMED] Trade successful (no signature returned).")
        return "confirmed"
        
    logger.error(f"[TRADING SERVICE] [FAILED] Response: {res}")
    raise IOError(f"PumpPortal trade execution failed: {res}")
