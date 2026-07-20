import json
import urllib.request
import sys

sys.stdout.reconfigure(encoding='utf-8')

mints_to_check = [
    "93tRm6L1bNr15FTjnYUazRh4J3Ths2bL84mmyCBNtpump",
    "DiaXHsNJwvGXEjx1HN7phGL7YfHPtBuvzHoCk28Apump",
    "CASHx9KJUStyftLFWGvEVf59SGeG9sh5FfccnZMVPCASH",
    "2cAtqsRafKS7baN3mvJARhyZiMRdW4fZYNUUWUrCpump",
]

print("=== VERIFIKASI TOKEN Solana ASLI DI DEXSCREENER ===")

for mint in mints_to_check:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
            pairs = data.get("pairs") or []
            if pairs:
                p = pairs[0]
                base_token = p.get("baseToken", {})
                name = str(base_token.get('name')).encode('ascii', errors='ignore').decode('ascii')
                symbol = str(base_token.get('symbol')).encode('ascii', errors='ignore').decode('ascii')
                print(f"\n[OK] TOKEN REAL TERTERA DI DEXSCREENER: {mint}")
                print(f"     Nama Token    : {name} ({symbol})")
                print(f"     DEX           : {p.get('dexId')}")
                print(f"     Harga USD     : ${p.get('priceUsd')}")
                print(f"     Likuiditas    : ${p.get('liquidity', {}).get('usd')}")
                print(f"     URL DexScreener: {p.get('url')}")
            else:
                print(f"\n[NOT INDEXED YET] Mint {mint}")
    except Exception as e:
        print(f"Error checking {mint}: {e}")
