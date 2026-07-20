import sys
import os
import json
import urllib.request
import time
from datetime import datetime, timezone

# Add root folder to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings

def query_rpc(method, params):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params
    }
    # Using SOLANA_RPC_URL from settings
    req = urllib.request.Request(
        settings.SOLANA_RPC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}

async def check_activity():
    # Load wallets from settings
    wallets = settings.TARGET_WALLETS
    if not wallets:
        print("Error: No wallets found in TARGET_WALLETS configuration.")
        return
        
    print(f"--- CHECKING ACTIVITY FOR {len(wallets)} WALLETS ON-CHAIN ---")
    print("Rate-limit safety: checking with 0.15s interval...")
    
    active_24h = 0
    active_7d = 0
    active_30d = 0
    inactive = 0
    errors = 0
    
    now = datetime.now(timezone.utc)
    
    for i, wallet in enumerate(wallets):
        time.sleep(0.15)  # comply with Helius 10 req/sec limit
        
        # Get latest 1 signature to inspect time
        res = query_rpc("getSignaturesForAddress", [wallet, {"limit": 1}])
        
        if "error" in res:
            print(f"[{i+1}/{len(wallets)}] {wallet[:10]}...: Error querying: {res['error']}")
            errors += 1
            continue
            
        sigs = res.get("result", [])
        if not sigs:
            print(f"[{i+1}/{len(wallets)}] {wallet}: INACTIVE (No transaction history found)")
            inactive += 1
            continue
            
        latest_tx = sigs[0]
        block_time = latest_tx.get("blockTime")
        
        if not block_time:
            print(f"[{i+1}/{len(wallets)}] {wallet}: INACTIVE (No blockTime on latest signature)")
            inactive += 1
            continue
            
        tx_dt = datetime.fromtimestamp(block_time, tz=timezone.utc)
        delta = now - tx_dt
        hours_ago = delta.total_seconds() / 3600.0
        days_ago = hours_ago / 24.0
        
        # Determine status label
        if hours_ago <= 24.0:
            status = f"ACTIVE (Last trade: {hours_ago:.1f} hours ago)"
            active_24h += 1
        elif days_ago <= 7.0:
            status = f"ACTIVE (Last trade: {days_ago:.1f} days ago)"
            active_7d += 1
        elif days_ago <= 30.0:
            status = f"SEMI-ACTIVE (Last trade: {days_ago:.1f} days ago)"
            active_30d += 1
        else:
            status = f"INACTIVE (Last trade: {days_ago:.1f} days ago)"
            inactive += 1
            
        print(f"[{i+1}/{len(wallets)}] {wallet}: {status}")

    print("\n--- ACTIVITY REPORT SUMMARY ---")
    print(f"Total Wallets Scanned: {len(wallets)}")
    print(f"Active in last 24 hours (Daily Traders): {active_24h}")
    print(f"Active in last 7 days: {active_7d}")
    print(f"Active in last 30 days: {active_30d}")
    print(f"Inactive (> 30 days or empty): {inactive}")
    print(f"Query Errors: {errors}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(check_activity())
