import urllib.request
import json

def get_tx(sig):
    payload = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'getTransaction',
        'params': [sig, {'encoding': 'json', 'maxSupportedTransactionVersion': 0}]
    }
    req = urllib.request.Request(
        'https://mainnet.helius-rpc.com/?api-key=00f9de1e-3d75-46e0-9e7e-fee21a442a51',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"Error fetching tx {sig}: {e}")
        return {}

def print_breakdown(sig, label):
    print(f"\n=== {label} ({sig[:8]}...) SOL BREAKDOWN ===")
    res = get_tx(sig)
    result = res.get('result', {})
    if not result:
        print("No result found.")
        return
    meta = result.get('meta', {})
    transaction = result.get('transaction', {})
    account_keys = transaction.get('message', {}).get('accountKeys', [])
    pre = meta.get('preBalances', [])
    post = meta.get('postBalances', [])
    
    for i, k in enumerate(account_keys):
        pubkey = k if isinstance(k, str) else k.get('pubkey')
        delta = (post[i] - pre[i]) / 1e9
        if delta != 0:
            # Check if this address is the user's wallet
            role = ""
            if pubkey == '2fRGriSp8o32KdV1K8yxic1ZBLnqJXRiXpQK9ovCebf8':
                role = " (USER WALLET)"
            elif pubkey == '6xCtR2Eq1VumsoRdNutcfSQfLMk7xUa2BrMx18tqpump':
                role = " (TOKEN MINT)"
            print(f"{pubkey}{role}: {delta:+.9f} SOL")

print_breakdown('53QUNm1GbYTXrFu9uqgyDNqgASbYtj7mFaJe5P8aBFJwYLvt9XL9mNn3gidqtSiRYhopEmrYCYFgqtajDCxTQZdA', 'P1 BUY')
print_breakdown('5ABDuj35v4tKGPVF7JWqEXqHe2RivKTE9ejqTfr9TXJvgELqDZp694g3rbieJozBAbpYkENyQ3eZZ3PoZpM4APPW', 'P1 SELL')
print_breakdown('3gkNJyAoBo8HktsDsYJ46wtqHjXKLH2Eau7NsFjXArwhL5R2N2ch46EBdjCpK6t7rU1NUoNZww41MNRnAvAA3iP3', 'P2 BUY')
print_breakdown('4x7Q1cNysqG2w9F4y89YpvCmPtu4bjVtK68AkahaJJLmQWwiT9jJVUc522WdtygCpbJLAPxLQ2KijC8qP4TiYCvS', 'P2 SELL')
