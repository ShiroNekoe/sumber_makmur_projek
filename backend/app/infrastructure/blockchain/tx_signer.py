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
                {"encoding": "base64", "skipPreflight": True, "maxRetries": 5}
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
        confirmed, err_desc = await poll_signature_status(tx_hash, active_url)
        
        if confirmed:
            logger.info(f"[TX SIGNER] Transaction successfully confirmed: {tx_hash}")
            return tx_hash
        elif err_desc and err_desc != "timeout":
            logger.error(f"[TX SIGNER] Transaction failed on-chain: {tx_hash} | Error: {err_desc}")
            raise IOError(f"Transaction failed on-chain: {err_desc}")
        else:
            logger.error(f"[TX SIGNER] Transaction expired or not confirmed: {tx_hash}")
            raise TimeoutError("Transaction confirmation timed out or expired on-chain.")
            
    except Exception as e:
        logger.error(f"[TX SIGNER] Sign & Broadcast error: {e}", exc_info=True)
        raise e
 
 
async def poll_signature_status(tx_hash: str, rpc_url: str) -> tuple[bool, Optional[str]]:
    """
    Polls getSignatureStatuses for a given transaction hash.
    Timeout after 45 seconds.
    Returns: (confirmed_bool, error_description_or_none)
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignatureStatuses",
        "params": [
            [tx_hash],
            {"searchTransactionHistory": True}  # Diubah ke True agar aman mencari tx yang terkonfirmasi lambat
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
            err_msg = json.dumps(status.get("err"))
            logger.error(f"[TX SIGNER] Transaction failed with error: {err_msg}")
            return False, err_msg
            
        confirmation_status = status.get("confirmationStatus")
        if confirmation_status in ("processed", "confirmed", "finalized"):
            logger.info(f"[TX SIGNER] Confirmed on-chain ({confirmation_status}) at slot {status.get('slot')}")
            return True, None
            
    return False, "timeout"


async def close_token_account(
    token_address: str,
    signer_keypair: Keypair,
    token_price_usd: float = 0.0
) -> Optional[str]:
    """
    Constructs, signs, and broadcasts a close token account transaction
    for the given token_address, owned by signer_keypair.
    If the account has a dust balance (worth < $0.20 USD), it appends a burn instruction
    to zero the balance before closing the account.
    If the balance is non-dust (worth >= $0.20 USD), it skips closing to protect user funds.
    """
    try:
        from solders.pubkey import Pubkey
        from solders.message import MessageV0
        from solders.transaction import VersionedTransaction
        from solders.hash import Hash
        from solders.instruction import Instruction, AccountMeta
        import struct
        
        owner_pub = signer_keypair.pubkey()
        mint_pub = Pubkey.from_string(token_address)
        ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
        
        primary_url = getattr(settings, "RPC_PRIMARY_URL", "https://api.mainnet-beta.solana.com")
        
        # 1. Detect token program dynamically by querying mint owner
        token_program_str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
        info_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [str(mint_pub), {"encoding": "jsonParsed"}]
        }
        def sync_fetch_info():
            try:
                req = urllib.request.Request(
                    primary_url,
                    data=json.dumps(info_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=6) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception:
                return {}
                
        info_res = await asyncio.to_thread(sync_fetch_info)
        if "result" in info_res and info_res["result"] and info_res["result"].get("value"):
            owner = info_res["result"]["value"].get("owner")
            if owner:
                token_program_str = owner
                
        TOKEN_PROGRAM_ID = Pubkey.from_string(token_program_str)
        ata = Pubkey.find_program_address(
            [bytes(owner_pub), bytes(TOKEN_PROGRAM_ID), bytes(mint_pub)],
            ASSOCIATED_TOKEN_PROGRAM_ID
        )[0]
        
        # 1.5. Wait 2.0s to allow sell transaction to finalize on RPC node before querying balance
        await asyncio.sleep(2.0)
        
        # 2. Query ATA balance first via RPC
        balance_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountBalance",
            "params": [str(ata)]
        }
        
        def sync_fetch_balance():
            req = urllib.request.Request(
                primary_url,
                data=json.dumps(balance_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
                
        balance_res = await asyncio.to_thread(sync_fetch_balance)
        
        ui_amount = 0.0
        raw_amount = 0
        
        if "result" in balance_res and balance_res["result"].get("value"):
            val = balance_res["result"]["value"]
            ui_amount = float(val.get("uiAmount") or 0.0)
            raw_amount = int(val.get("amount") or 0)
        else:
            # If the account doesn't exist or is already closed, we can skip
            logger.info(f"[TX SIGNER] [CLOSE ATA] Token account {str(ata)[:6]}... does not exist or has no active balance. Skipping close.")
            return None
            
        instructions = []
        
        # 3. Check for non-zero balance (dust check)
        if raw_amount > 0:
            value_usd = ui_amount * token_price_usd
            # If the remaining tokens are worth more than $0.01, skip closing to protect funds
            if value_usd >= 0.01:
                logger.warning(
                    f"[TX SIGNER] [CLOSE ATA] Token account {str(ata)[:6]}... still has non-dust balance: "
                    f"{ui_amount} tokens (~${value_usd:.2f} USD). Skipping close to protect user funds."
                )
                return None
                
            # If it's dust (worth < $0.01), append a burn instruction
            logger.info(
                f"[TX SIGNER] [CLOSE ATA] Token account {str(ata)[:6]}... has dust balance: "
                f"{ui_amount} tokens (~${value_usd:.6f} USD). Appending burn instruction for {raw_amount} raw tokens."
            )
            burn_ix = Instruction(
                program_id=TOKEN_PROGRAM_ID,
                accounts=[
                    AccountMeta(pubkey=ata, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=mint_pub, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=owner_pub, is_signer=True, is_writable=False)
                ],
                data=struct.pack("<BQ", 8, raw_amount)
            )
            instructions.append(burn_ix)
            
        # 4. Build close instruction
        close_ix = Instruction(
            program_id=TOKEN_PROGRAM_ID,
            accounts=[
                AccountMeta(pubkey=ata, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_pub, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner_pub, is_signer=True, is_writable=False)
            ],
            data=bytes([9])
        )
        instructions.append(close_ix)
        # 5. Fetch recent blockhash
        blockhash_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getLatestBlockhash",
            "params": []
        }
        
        def sync_fetch_blockhash():
            req = urllib.request.Request(
                primary_url,
                data=json.dumps(blockhash_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
                
        res = await asyncio.to_thread(sync_fetch_blockhash)
        if "error" in res or "result" not in res:
            raise IOError(f"Failed to fetch blockhash for close account: {res.get('error')}")
            
        blockhash_str = res["result"]["value"]["blockhash"]
        blockhash = Hash.from_string(blockhash_str)
        
        # 6. Compile message and sign VersionedTransaction
        message = MessageV0.try_compile(
            payer=owner_pub,
            instructions=instructions,
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash
        )
        
        tx = VersionedTransaction(message, [signer_keypair])
        raw_tx_bytes = bytes(tx)
        
        logger.info(f"[TX SIGNER] [CLOSE ATA] Reclaiming rent for token account {str(ata)[:6]}...")
        # 7. Broadcast using existing signed tx broadcaster
        tx_sig = await sign_and_broadcast_transaction(raw_tx_bytes, signer_keypair)
        logger.info(f"[TX SIGNER] [CLOSE ATA] Rent successfully reclaimed in TX: {tx_sig}")
        return tx_sig
        
    except Exception as e:
        logger.error(f"[TX SIGNER] [CLOSE ATA] Failed to close token account for {token_address}: {e}", exc_info=True)
        return None
