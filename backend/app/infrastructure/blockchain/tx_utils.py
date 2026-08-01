import logging
import json
import asyncio
import urllib.request
from typing import Optional, List, Dict, Any

from app.core.config import settings

logger = logging.getLogger(__name__)


async def fetch_transaction_details(signature: str, rpc_url: Optional[str] = None) -> Optional[dict]:
    """
    Fetches full confirmed transaction payload from Solana RPC with fallback chain.
    Uses encoding=jsonParsed to parse SOL and SPL Token balance deltas.
    """
    rpc_chain = [
        rpc_url or settings.RPC_PRIMARY_URL,
        settings.RPC_PRIMARY_URL,
        settings.RPC_SECONDARY_URL,
        "https://api.mainnet-beta.solana.com",
    ]
    # Deduplicate while preserving order
    seen = set()
    rpc_chain = [u for u in rpc_chain if u and not (u in seen or seen.add(u))]

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
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
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as e:
                return {"error": str(e)}

        # Retry up to 2 times per RPC endpoint
        for attempt in range(2):
            res = await asyncio.to_thread(sync_fetch)
            if isinstance(res, dict) and "error" not in res and "result" in res:
                result = res["result"]
                if isinstance(result, dict):
                    return result
            await asyncio.sleep(0.5)

    logger.warning(f"[TX UTILS] Unable to fetch transaction details for {signature[:12]}... from all RPC endpoints.")
    return None


def calculate_onchain_executed_price(
    tx_details: dict,
    wallet_address: str,
    token_mint: str,
    sol_price_usd: float
) -> Optional[float]:
    """
    Calculates actual executed token price in USD from confirmed on-chain transaction balance deltas.

    Formula:
      net_sol_spent = (preBalances[wallet] - postBalances[wallet] - fee_if_fee_payer) / 1e9
      net_token_received = postTokenBalance[wallet, mint] - preTokenBalance[wallet, mint]
      executed_price_usd = (net_sol_spent * sol_price_usd) / net_token_received

    Returns:
      executed_price_usd (float) if valid, or None if transaction payload is invalid/incomplete,
      or balance delta calculation results in non-positive numbers (<= 0).
    """
    if not tx_details or not isinstance(tx_details, dict):
        return None

    meta = tx_details.get("meta")
    if not meta or not isinstance(meta, dict) or meta.get("err") is not None:
        logger.warning(f"[ONCHAIN SLIPPAGE] Transaction {tx_details.get('slot', '')} contained on-chain error or missing meta.")
        return None

    # Extract accountKeys from message
    transaction = tx_details.get("transaction", {})
    message = transaction.get("message", {})
    raw_account_keys = message.get("accountKeys", [])
    account_keys = [
        k.get("pubkey") if isinstance(k, dict) else str(k)
        for k in raw_account_keys
    ]

    if wallet_address not in account_keys:
        logger.warning(f"[ONCHAIN SLIPPAGE] Wallet {wallet_address[:8]}... not in transaction accountKeys.")
        return None

    wallet_idx = account_keys.index(wallet_address)

    # 1. SOL balances (in lamports)
    pre_balances = meta.get("preBalances", [])
    post_balances = meta.get("postBalances", [])

    if wallet_idx >= len(pre_balances) or wallet_idx >= len(post_balances):
        logger.warning(f"[ONCHAIN SLIPPAGE] Wallet index {wallet_idx} out of range for pre/postBalances.")
        return None

    fee_lamports = float(meta.get("fee", 0.0))
    # Fee is deducted from fee payer (account index 0). Subtract fee if wallet is fee payer.
    fee_deduction = fee_lamports if wallet_idx == 0 else 0.0

    gross_sol_delta = float(pre_balances[wallet_idx] - post_balances[wallet_idx])
    sol_delta_lamports = gross_sol_delta - fee_deduction
    net_sol_spent = sol_delta_lamports / 1e9

    if net_sol_spent <= 0:
        logger.warning(
            f"[ONCHAIN SLIPPAGE] Invalid net SOL spent ({net_sol_spent:.6f} SOL, gross: {gross_sol_delta/1e9:.6f}, fee: {fee_lamports/1e9:.6f}) "
            f"for wallet {wallet_address[:8]}..."
        )
        return None

    # 2. Token balances parsing (supports SPL Token and Token-2022)
    def _extract_token_balance(balances_list: list) -> float:
        total_bal = 0.0
        for b in balances_list:
            if not isinstance(b, dict):
                continue
            if b.get("mint") != token_mint:
                continue

            owner = b.get("owner")
            acc_idx = b.get("accountIndex")
            is_match = (owner == wallet_address)
            if not is_match and acc_idx is not None and isinstance(acc_idx, int) and acc_idx < len(account_keys):
                is_match = (account_keys[acc_idx] == wallet_address)

            if is_match:
                amt_info = b.get("uiTokenAmount") or {}
                amt = float(amt_info.get("uiAmount") or 0.0)
                total_bal += amt
        return total_bal

    pre_token_bal = _extract_token_balance(meta.get("preTokenBalances", []))
    post_token_bal = _extract_token_balance(meta.get("postTokenBalances", []))
    net_token_received = post_token_bal - pre_token_bal

    if net_token_received <= 0:
        logger.warning(
            f"[ONCHAIN SLIPPAGE] Invalid net token received ({net_token_received:.6f}, pre: {pre_token_bal}, post: {post_token_bal}) "
            f"for token {token_mint[:8]}... and wallet {wallet_address[:8]}..."
        )
        return None

    usd_spent = net_sol_spent * sol_price_usd
    executed_price_usd = usd_spent / net_token_received

    if executed_price_usd <= 0:
        logger.warning(f"[ONCHAIN SLIPPAGE] Calculated executed_price_usd is non-positive: ${executed_price_usd:.6f}")
        return None

    return executed_price_usd
