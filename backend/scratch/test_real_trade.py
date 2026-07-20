import sys
import os
import json
import urllib.request
import asyncio
from datetime import datetime

# Add root folder to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.infrastructure.blockchain.wallet_manager import load_wallet_from_env, get_sol_balance
from app.infrastructure.blockchain.trading_service import execute_pumpportal_swap

# Force is_testing = False for this manual execution script so it executes real on-chain trade
os.environ["SIMULATION_MODE"] = "False"

async def get_token_balance(wallet_address: str, token_mint: str) -> float:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            wallet_address,
            {"mint": token_mint},
            {"encoding": "jsonParsed"}
        ]
    }
    req = urllib.request.Request(
        settings.SOLANA_RPC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        def query():
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        res = await asyncio.to_thread(query)
        accounts = res.get("result", {}).get("value", [])
        if not accounts:
            return 0.0
        amount = accounts[0].get("account", {}).get("data", {}).get("parsed", {}).get("info", {}).get("tokenAmount", {}).get("uiAmount", 0.0)
        return float(amount)
    except Exception as e:
        print(f"Error querying token balance: {e}")
        return 0.0

async def test_trade():
    print("--- REAL SOLANA MEME COIN TRADE TEST (PART 2) ---")
    
    # 1. Load wallet
    keypair = load_wallet_from_env()
    if not keypair:
        print("Error: SOLANA_WALLET_PRIVATE_KEY is missing or invalid in .env")
        return
        
    pubkey = str(keypair.pubkey())
    print(f"Loaded Wallet: {pubkey}")
    
    # 2. Check initial balances
    sol_before = await get_sol_balance(keypair.pubkey())
    print(f"Initial SOL Balance: {sol_before:.6f} SOL")
    if sol_before < 0.002:
        print("Error: SOL balance is too low to run the test (need at least 0.002 SOL).")
        return
        
    token_mint = "6xCtR2Eq1VumsoRdNutcfSQfLMk7xUa2BrMx18tqpump"
    print(f"Target Meme Token: {token_mint}")
    
    token_bal_before = await get_token_balance(pubkey, token_mint)
    print(f"Initial Token Balance: {token_bal_before:.6f} tokens (Leftovers from Part 1)")
    
    buy_amount_sol = 0.001
    print(f"\n[1/2] Initiating BUY order for {buy_amount_sol} SOL...")
    
    try:
        buy_sig = await execute_pumpportal_swap(
            action="buy",
            token_mint=token_mint,
            amount=buy_amount_sol,
            denominated_in_sol=True,
            slippage=10.0,
            priority_fee=0.0001
        )
        print(f"BUY Success! Transaction Signature: {buy_sig}")
        
        # We wait 15 seconds to let the blockchain finalize and index the transaction
        print("Waiting 15 seconds for transaction settlement and RPC indexing...")
        await asyncio.sleep(15)
        
        sol_after_buy = await get_sol_balance(keypair.pubkey())
        token_bal_after_buy = await get_token_balance(pubkey, token_mint)
        print(f"SOL after BUY: {sol_after_buy:.6f} SOL (Deducted: {sol_before - sol_after_buy:.6f} SOL)")
        print(f"Token balance after BUY: {token_bal_after_buy:.6f} tokens")
        
        if token_bal_after_buy <= 0.0:
            print("Warning: Token balance is 0. Buy transaction may have failed or not settled. Aborting Sell.")
            return
            
        print(f"\n[2/2] Initiating SELL order for 100% of tokens ({token_bal_after_buy:.6f} tokens)...")
        sell_sig = await execute_pumpportal_swap(
            action="sell",
            token_mint=token_mint,
            amount="100%",
            denominated_in_sol=False,
            slippage=15.0,
            priority_fee=0.0001
        )
        print(f"SELL Success! Transaction Signature: {sell_sig}")
        print("Waiting 12 seconds for transaction to settle...")
        await asyncio.sleep(12)
        
        sol_final = await get_sol_balance(keypair.pubkey())
        token_bal_final = await get_token_balance(pubkey, token_mint)
        print(f"Final SOL Balance: {sol_final:.6f} SOL")
        print(f"Final Token Balance: {token_bal_final:.6f} tokens")
        
        net_sol_change = sol_final - sol_before
        print(f"Net SOL Change for this test run: {net_sol_change:.6f} SOL")
        
    except Exception as e:
        print(f"Trade execution failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_trade())
