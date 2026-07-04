import logging
import json
import urllib.request
import asyncio
from typing import Dict, List, Any
from datetime import datetime, timezone, timedelta
from app.core.config import settings

logger = logging.getLogger(__name__)

# Simple in-memory cache for token metadata (symbol, name, decimals)
metadata_cache: Dict[str, Dict[str, Any]] = {}


class PortfolioService:
    """
    F-07 / B1: Portfolio holdings query service.
    Queries the Solana RPC to fetch active token balances for the wallet.
    Caches token metadata to minimize RPC requests.
    """
    def __init__(self, token_info_service=None):
        self.token_info_service = token_info_service

    async def get_token_holdings(self, pubkey_str: str) -> List[Dict[str, Any]]:
        """
        Queries getTokenAccountsByOwner and getBalance via RPC, and aggregates token holdings.
        """
        rpc_url = getattr(settings, "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        
        # 1. Fetch native SOL balance
        sol_balance = 0.0
        try:
            sol_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "getBalance",
                "params": [pubkey_str]
            }
            def sync_sol_fetch():
                req = urllib.request.Request(
                    rpc_url,
                    data=json.dumps(sol_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=8) as response:
                    return json.loads(response.read().decode("utf-8"))
            sol_res = await asyncio.to_thread(sync_sol_fetch)
            if "result" in sol_res:
                lamports = sol_res["result"].get("value", 0)
                sol_balance = lamports / 1_000_000_000.0
        except Exception as sol_err:
            logger.error(f"[PORTFOLIO SERVICE] Failed to fetch native SOL balance: {sol_err}")

        # Get SOL price
        sol_price = 77.34 # default fallback
        if self.token_info_service:
            try:
                # Use WSOL mint address to fetch real-time SOL price from DexScreener
                info = await self.token_info_service.get_token_info("So11111111111111111111111111111111111111112")
                if info and info.get("price_usd", 0.0) > 0.0:
                    sol_price = info["price_usd"]
            except Exception:
                pass

        holdings = []
        
        # Prepend SOL as primary holding if it exists or even if 0
        holdings.append({
            "mint": "So11111111111111111111111111111111111111112",
            "amount": sol_balance,
            "decimals": 9,
            "symbol": "SOL",
            "name": "Solana",
            "price_usd": sol_price,
            "value_usd": sol_balance * sol_price
        })

        # 2. Fetch SPL token accounts
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                pubkey_str,
                {
                    "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
                },
                {
                    "encoding": "jsonParsed"
                }
            ]
        }

        def sync_fetch():
            try:
                req = urllib.request.Request(
                    rpc_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=8) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as e:
                return {"error": str(e)}

        res = await asyncio.to_thread(sync_fetch)
        
        if "error" in res or "result" not in res:
            logger.warning(f"[PORTFOLIO SERVICE] RPC query failed: {res.get('error', 'unknown error')}")
            return holdings

        accounts = res["result"].get("value", [])
        
        for acc in accounts:
            try:
                parsed_info = acc["account"]["data"]["parsed"]["info"]
                mint = parsed_info["mint"]
                token_amount_data = parsed_info["tokenAmount"]
                ui_amount = token_amount_data.get("uiAmount", 0.0)
                decimals = token_amount_data.get("decimals", 9)
                
                if ui_amount <= 0.0:
                    continue
                
                # Fetch metadata with caching
                if mint == "So11111111111111111111111111111111111111112":
                    meta = {"symbol": "WSOL", "name": "Wrapped SOL"}
                else:
                    meta = await self.get_token_metadata(mint)
                
                # Determine current price if available
                current_price = 0.0
                if self.token_info_service:
                    try:
                        info = await self.token_info_service.get_token_info(mint)
                        current_price = info.get("price_usd", 0.0)
                    except Exception:
                        pass
                
                value_usd = ui_amount * current_price
                
                holdings.append({
                    "mint": mint,
                    "amount": ui_amount,
                    "decimals": decimals,
                    "symbol": meta["symbol"],
                    "name": meta["name"],
                    "price_usd": current_price,
                    "value_usd": value_usd
                })
            except Exception as parse_err:
                logger.error(f"[PORTFOLIO SERVICE] Error parsing token account: {parse_err}")
                continue
                
        return holdings

    async def get_token_metadata(self, mint_address: str) -> Dict[str, Any]:
        """
        Retrieves symbol and name for a mint address with memory caching.
        """
        now = datetime.now(timezone.utc)
        
        # Check cache (expire after 1 hour)
        if mint_address in metadata_cache:
            cache_entry = metadata_cache[mint_address]
            if now - cache_entry["cached_at"] < timedelta(hours=1):
                return cache_entry["data"]

        # Default fallback metadata
        metadata = {
            "symbol": mint_address[:6].upper(),
            "name": f"Token {mint_address[:4]}",
        }

        # Try fetching from token info service (DexScreener)
        if self.token_info_service:
            try:
                info = await self.token_info_service.get_token_info(mint_address)
                if info and "symbol" in info:
                    metadata["symbol"] = info["symbol"]
                    metadata["name"] = info.get("name", info["symbol"])
            except Exception:
                pass

        # Update cache
        metadata_cache[mint_address] = {
            "cached_at": now,
            "data": metadata
        }
        
        return metadata
