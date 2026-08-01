import logging
import struct
import json
import base64
import asyncio
import urllib.request
from typing import Optional, Tuple, Dict, Any

from solders.pubkey import Pubkey
from app.core.config import settings

logger = logging.getLogger(__name__)

PUMP_FUN_PROGRAM_ID_STR = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_FUN_PROGRAM_ID = Pubkey.from_string(PUMP_FUN_PROGRAM_ID_STR)


def get_bonding_curve_pda(token_mint_str: str) -> Optional[Pubkey]:
    """
    Derives Program Derived Address (PDA) for a pump.fun token bonding curve account.
    Seeds: ["bonding-curve", bytes(token_mint_pubkey)]
    """
    try:
        mint_pubkey = Pubkey.from_string(token_mint_str)
        pda, _ = Pubkey.find_program_address(
            [b"bonding-curve", bytes(mint_pubkey)],
            PUMP_FUN_PROGRAM_ID
        )
        return pda
    except Exception as err:
        logger.warning(f"[BONDING CURVE] Failed to derive PDA for mint {token_mint_str}: {err}")
        return None


def parse_bonding_curve_account_data(raw_data_bytes: bytes) -> Optional[Dict[str, Any]]:
    """
    Parses binary struct of a pump.fun bonding curve account.
    Layout:
      - 8 bytes: Account discriminator
      - uint64: virtualSolReserves
      - uint64: virtualTokenReserves
      - uint64: realSolReserves
      - uint64: realTokenReserves
      - uint64: tokenTotalSupply
      - uint8: complete (bool)
    """
    if len(raw_data_bytes) < 49:
        logger.warning(f"[BONDING CURVE] Account data too short ({len(raw_data_bytes)} bytes < 49 bytes).")
        return None

    try:
        # Unpack 5 uint64s and 1 uint8 following 8-byte discriminator
        v_sol, v_token, r_sol, r_token, supply, complete_byte = struct.unpack(
            "<QQQQQB", raw_data_bytes[8:49]
        )

        return {
            "virtualSolReserves": v_sol,
            "virtualTokenReserves": v_token,
            "realSolReserves": r_sol,
            "realTokenReserves": r_token,
            "tokenTotalSupply": supply,
            "complete": bool(complete_byte)
        }
    except Exception as err:
        logger.warning(f"[BONDING CURVE] Struct unpacking failed: {err}")
        return None


async def fetch_bonding_curve_account_info(
    token_mint_str: str,
    rpc_url: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetches raw account info for pump.fun bonding curve account via RPC.
    Returns parsed dictionary or None if account is missing/invalid.
    """
    pda = get_bonding_curve_pda(token_mint_str)
    if not pda:
        return None

    rpc_chain = [
        rpc_url or settings.RPC_PRIMARY_URL,
        settings.RPC_PRIMARY_URL,
        settings.RPC_SECONDARY_URL,
        "https://api.mainnet-beta.solana.com",
    ]
    seen = set()
    rpc_chain = [u for u in rpc_chain if u and not (u in seen or seen.add(u))]

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [
            str(pda),
            {"encoding": "base64"}
        ]
    }

    for url in rpc_chain:
        def sync_fetch(target_url=url):
            headers = {"Content-Type": "application/json"}
            req = urllib.request.Request(
                target_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=8) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as e:
                return {"error": str(e)}

        for attempt in range(2):
            res = await asyncio.to_thread(sync_fetch)
            if isinstance(res, dict) and "result" in res and res["result"]:
                val = res["result"].get("value")
                if isinstance(val, dict) and "data" in val:
                    raw_data = val["data"]
                    if isinstance(raw_data, list) and len(raw_data) > 0:
                        b64_str = raw_data[0]
                        decoded_bytes = base64.b64decode(b64_str)
                        parsed = parse_bonding_curve_account_data(decoded_bytes)
                        if parsed:
                            return parsed
            await asyncio.sleep(0.3)

    return None


async def get_bonding_curve_price(
    token_mint_str: str,
    rpc_url: Optional[str] = None,
    sol_price_usd: Optional[float] = None
) -> Optional[float]:
    """
    Calculates real-time price in USD directly from on-chain pump.fun bonding curve reserve state.

    Returns:
      - price_usd (float) if token is active on bonding curve.
      - None if token has graduated to AMM (complete == True) or account fetch failed.
    """
    curve_data = await fetch_bonding_curve_account_info(token_mint_str, rpc_url)
    if not curve_data:
        return None

    if curve_data["complete"]:
        logger.info(f"[BONDING CURVE] Token {token_mint_str[:8]}... has completed bonding curve (graduated to AMM).")
        return None

    v_sol = curve_data["virtualSolReserves"]
    v_token = curve_data["virtualTokenReserves"]

    if v_sol <= 0 or v_token <= 0:
        return None

    # Pump.fun tokens use 6 decimal places, SOL uses 9 decimal places
    sol_reserves = v_sol / 1e9
    token_reserves = v_token / 1e6
    price_sol = sol_reserves / token_reserves

    if sol_price_usd is None or sol_price_usd <= 0:
        sol_price_usd = getattr(settings, "SOL_USD_FALLBACK", 145.0)

    price_usd = price_sol * sol_price_usd
    return price_usd


async def estimate_bonding_curve_price_impact(
    token_mint_str: str,
    sol_amount_in: float,
    rpc_url: Optional[str] = None
) -> Optional[float]:
    """
    Estimates deterministic price impact for a swap of sol_amount_in (in SOL)
    on pump.fun bonding curve using constant product formula (k = x * y).

    Returns:
      - price_impact (float, e.g. 0.025 for 2.5%)
      - None if token has graduated or reserves cannot be fetched.
    """
    curve_data = await fetch_bonding_curve_account_info(token_mint_str, rpc_url)
    if not curve_data or curve_data["complete"]:
        return None

    v_sol = curve_data["virtualSolReserves"]
    v_token = curve_data["virtualTokenReserves"]

    if v_sol <= 0 or v_token <= 0 or sol_amount_in <= 0:
        return None

    sol_in_lamports = sol_amount_in * 1e9

    # Spot price in SOL per token (raw lamports / raw token units)
    spot_price_raw = v_sol / v_token

    # Constant product: k = v_sol * v_token
    k = float(v_sol) * float(v_token)
    new_v_sol = float(v_sol) + sol_in_lamports
    new_v_token = k / new_v_sol
    tokens_out = float(v_token) - new_v_token

    if tokens_out <= 0:
        return None

    effective_price_raw = sol_in_lamports / tokens_out
    price_impact = (effective_price_raw - spot_price_raw) / spot_price_raw

    return max(0.0, price_impact)
