import urllib.request
import json
import sys
import os

def get_tx(sig):
    payload = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'getTransaction',
        'params': [sig, {'encoding': 'json', 'maxSupportedTransactionVersion': 0}]
    }
    url = os.getenv("SOLANA_RPC_PRIMARY_URL") or "https://api.mainnet-beta.solana.com"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"Error fetching tx {sig}: {e}")
        return {}

def check_sol_change(res, owner):
    result = res.get('result', {})
    if not result:
        return 0.0
    meta = result.get('meta', {})
    transaction = result.get('transaction', {})
    account_keys = transaction.get('message', {}).get('accountKeys', [])
    owner_idx = -1
    for i, k in enumerate(account_keys):
        pubkey = k if isinstance(k, str) else k.get('pubkey')
        if pubkey == owner:
            owner_idx = i
            break
    if owner_idx == -1:
        return 0.0
    pre = meta.get('preBalances', [])[owner_idx]
    post = meta.get('postBalances', [])[owner_idx]
    return (post - pre) / 1e9

owner = '2fRGriSp8o32KdV1K8yxic1ZBLnqJXRiXpQK9ovCebf8'

print("=== PART 1 ANALYSIS ===")
p1_buy = get_tx('53QUNm1GbYTXrFu9uqgyDNqgASbYtj7mFaJe5P8aBFJwYLvt9XL9mNn3gidqtSiRYhopEmrYCYFgqtajDCxTQZdA')
p1_sell = get_tx('5ABDuj35v4tKGPVF7JWqEXqHe2RivKTE9ejqTfr9TXJvgELqDZp694g3rbieJozBAbpYkENyQ3eZZ3PoZpM4APPW')

print("P1 BUY TX FEE:", p1_buy.get('result', {}).get('meta', {}).get('fee', 0) / 1e9, "SOL")
print("P1 BUY SOL CHANGE:", check_sol_change(p1_buy, owner), "SOL")
print("P1 SELL TX FEE:", p1_sell.get('result', {}).get('meta', {}).get('fee', 0) / 1e9, "SOL")
print("P1 SELL SOL CHANGE:", check_sol_change(p1_sell, owner), "SOL")

print("\n=== PART 2 ANALYSIS ===")
p2_buy = get_tx('3gkNJyAoBo8HktsDsYJ46wtqHjXKLH2Eau7NsFjXArwhL5R2N2ch46EBdjCpK6t7rU1NUoNZww41MNRnAvAA3iP3')
p2_sell = get_tx('4x7Q1cNysqG2w9F4y89YpvCmPtu4bjVtK68AkahaJJLmQWwiT9jJVUc522WdtygCpbJLAPxLQ2KijC8qP4TiYCvS')

print("P2 BUY TX FEE:", p2_buy.get('result', {}).get('meta', {}).get('fee', 0) / 1e9, "SOL")
print("P2 BUY SOL CHANGE:", check_sol_change(p2_buy, owner), "SOL")
print("P2 SELL TX FEE:", p2_sell.get('result', {}).get('meta', {}).get('fee', 0) / 1e9, "SOL")
print("P2 SELL SOL CHANGE:", check_sol_change(p2_sell, owner), "SOL")
