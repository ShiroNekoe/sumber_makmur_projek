import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple

# Tambahkan backend ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("discover_niche_wallets")

# API Keys and URLs
DEXSCREENER_BASE = "https://api.dexscreener.com"
HELIUS_API_KEY   = "00f9de1e-3d75-46e0-9e7e-fee21a442a51"
HELIUS_RPC_URL   = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_REST_BASE = f"https://api.helius.xyz/v0"
QUICKNODE_URL    = "https://convincing-orbital-gas.solana-mainnet.quiknode.pro/21ba38b9733739c54695b200c406dfa2e03ca0de"
PUBLIC_RPC_URL   = "https://api.mainnet-beta.solana.com"

# Thresholds
MIN_WIN_RATE            = 0.62
MIN_TOTAL_SWAPS         = 6
MAX_LAST_ACTIVE_DAYS    = 7
MIN_AVG_PROFIT_PCT      = 0.05
MIN_MULTI_TOKEN_COUNT   = 3
MIN_PAIR_VOLUME_USD     = 50000
MIN_PAIR_TXNS_24H       = 150
MIN_PAIR_LIQUIDITY_USD  = 30000
TOP_PAIRS_TO_SCAN       = 15
SIGS_PER_PAIR           = 30
MAX_CANDIDATE_WALLETS   = 100
MAX_WALLETS_TO_ADD      = 10

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
STABLE_MINTS = {WSOL_MINT, USDC_MINT, USDT_MINT}

# Blacklist criteria
BLACKLIST_KEYWORDS = {"BONK", "PEPE", "DOGE", "FARTCOIN", "FART"}
BLACKLIST_MINTS = {
    "DezXAZ8z7PnrnRJjz3wXBoRgixrfNg7yFLBnRx4S75Jb", # BONK
    "9b3j5dg64BDm18mC69o1zM45p1LsNz29o2FDN26Dpump", # Fartcoin
}

token_cache = {}

def http_get(url: str, timeout: int = 15, retries: int = 2) -> Optional[dict]:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "SumberMakmurBot/2.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(2)
            else:
                return None
        except Exception:
            return None
    return None

def rpc_post(method: str, params: list, timeout: int = 12) -> Optional[dict]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for url in [HELIUS_RPC_URL, QUICKNODE_URL, PUBLIC_RPC_URL]:
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.loads(r.read().decode("utf-8"))
            if "result" in res:
                return res["result"]
        except Exception:
            pass
    return None

def helius_enhanced_txs(wallet: str, tx_type: str = "SWAP", limit: int = 100) -> List[dict]:
    url = f"{HELIUS_REST_BASE}/addresses/{wallet}/transactions?api-key={HELIUS_API_KEY}&type={tx_type}&limit={limit}"
    result = http_get(url, timeout=20, retries=2)
    if isinstance(result, list):
        return result
    return []

def get_token_symbol(mint: str) -> str:
    if mint in token_cache:
        return token_cache[mint]
    
    url = f"{DEXSCREENER_BASE}/latest/dex/tokens/{mint}"
    data = http_get(url)
    if data and data.get("pairs"):
        symbol = str(data["pairs"][0].get("baseToken", {}).get("symbol", "")).upper()
        token_cache[mint] = symbol
        return symbol
    token_cache[mint] = "UNKNOWN"
    return "UNKNOWN"

def fetch_top_niche_pairs() -> List[dict]:
    logger.info("📡 [DexScreener] Mengambil top Solana pairs (Non-Pepe/Bonk/Doge/Fartcoin)...")
    # Query for other popular coins to get diverse smart wallets
    search_queries = ["WIF", "POPCAT", "MEW", "SLERF", "BOME", "GIGA", "FWOG", "PENGU"]
    all_pairs: Dict[str, dict] = {}
    
    for query in search_queries:
        url = f"{DEXSCREENER_BASE}/latest/dex/search?q={query}"
        data = http_get(url)
        if not data:
            continue
        pairs = data.get("pairs") or []
        for p in pairs:
            if p.get("chainId") != "solana":
                continue
            base_symbol = str(p.get("baseToken", {}).get("symbol", "")).upper()
            if any(k in base_symbol for k in BLACKLIST_KEYWORDS):
                continue
            pair_addr = p.get("pairAddress")
            if pair_addr and pair_addr not in all_pairs:
                all_pairs[pair_addr] = p
        time.sleep(0.5)
        
    filtered = []
    for p in all_pairs.values():
        vol_h24 = float((p.get("volume") or {}).get("h24") or 0)
        liq_usd = float((p.get("liquidity") or {}).get("usd") or 0)
        txns_data = p.get("txns") or {}
        txns_h24 = txns_data.get("h24") or {}
        txns_h24_buy = int(txns_h24.get("buys") or 0) if isinstance(txns_h24, dict) else 0
        txns_h24_sell = int(txns_h24.get("sells") or 0) if isinstance(txns_h24, dict) else 0
        txns_total = txns_h24_buy + txns_h24_sell
        
        if vol_h24 >= MIN_PAIR_VOLUME_USD and liq_usd >= MIN_PAIR_LIQUIDITY_USD and txns_total >= MIN_PAIR_TXNS_24H:
            p["_vol_h24"] = vol_h24
            p["_liq_usd"] = liq_usd
            p["_txns_total"] = txns_total
            filtered.append(p)
            
    filtered.sort(key=lambda x: x["_vol_h24"], reverse=True)
    return filtered[:TOP_PAIRS_TO_SCAN]

def extract_traders_from_pair(pair_address: str, limit: int = SIGS_PER_PAIR) -> Set[str]:
    wallets: Set[str] = set()
    sigs = rpc_post("getSignaturesForAddress", [pair_address, {"limit": limit}])
    if not isinstance(sigs, list):
        return wallets
    
    for sig_info in sigs[:limit]:
        sig = sig_info.get("signature")
        if not sig or sig_info.get("err"):
            continue
        
        tx = rpc_post("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not isinstance(tx, dict):
            continue
            
        try:
            msg = tx.get("transaction", {}).get("message", {})
            account_keys = msg.get("accountKeys", [])
            for key_obj in account_keys:
                if isinstance(key_obj, dict) and key_obj.get("signer") and not key_obj.get("writable") is False:
                    pubkey = key_obj.get("pubkey")
                    if pubkey and len(pubkey) >= 32:
                        wallets.add(pubkey)
                    break
                elif isinstance(key_obj, str) and len(key_obj) >= 32:
                    wallets.add(key_obj)
                    break
        except Exception:
            pass
        time.sleep(0.1)
    return wallets

def analyze_wallet(wallet: str) -> Optional[dict]:
    swaps = helius_enhanced_txs(wallet, tx_type="SWAP", limit=50)
    if len(swaps) < 3:
        return None
        
    now_ts = datetime.now(timezone.utc).timestamp()
    last_active_ts = 0
    
    buy_events: Dict[str, List[dict]] = defaultdict(list)
    sell_events: Dict[str, List[dict]] = defaultdict(list)
    tokens_traded: Set[str] = set()
    
    for tx in swaps:
        ts = tx.get("timestamp", 0)
        if ts > last_active_ts:
            last_active_ts = ts
            
        transfers = tx.get("tokenTransfers", [])
        for t in transfers:
            mint = t.get("mint")
            if not mint or mint in STABLE_MINTS:
                continue
            
            # Check Blacklist by mint address directly
            if mint in BLACKLIST_MINTS:
                logger.info(f"  ❌ Wallet {wallet[:10]}... excluded: Traded blacklisted mint directly ({mint[:8]}...)")
                return None
                
            from_acc = t.get("fromUserAccount") or ""
            to_acc = t.get("toUserAccount") or ""
            amount = float(t.get("tokenAmount") or 0)
            if amount <= 0:
                continue
                
            if to_acc == wallet:
                buy_events[mint].append({"ts": ts, "amount": amount})
                tokens_traded.add(mint)
            elif from_acc == wallet:
                sell_events[mint].append({"ts": ts, "amount": amount})
                tokens_traded.add(mint)
                
    # Double check all traded tokens symbols to catch any PEPE/BONK/DOGE/FARTCOIN variants
    for mint in tokens_traded:
        sym = get_token_symbol(mint)
        if any(k in sym for k in BLACKLIST_KEYWORDS):
            logger.info(f"  ❌ Wallet {wallet[:10]}... excluded: Traded blacklisted token symbol {sym}")
            return None
            
    days_since_active = (now_ts - last_active_ts) / 86400 if last_active_ts > 0 else 999
    if days_since_active > MAX_LAST_ACTIVE_DAYS:
        return None
        
    total_trades = 0
    winning_trades = 0
    profit_pcts = []
    
    for mint in set(buy_events.keys()) & set(sell_events.keys()):
        buys = sorted(buy_events[mint], key=lambda x: x["ts"])
        sells = sorted(sell_events[mint], key=lambda x: x["ts"])
        for buy in buys:
            for sell in sells:
                if sell["ts"] > buy["ts"]:
                    total_trades += 1
                    buy_amount = buy["amount"]
                    sell_amount = sell["amount"]
                    if buy_amount > 0:
                        ratio = sell_amount / buy_amount
                        if ratio >= 1.0:
                            winning_trades += 1
                            profit_pcts.append(min(ratio - 1.0, 5.0))
                        else:
                            profit_pcts.append(ratio - 1.0)
                    break
                    
    if total_trades < MIN_TOTAL_SWAPS:
        return None
        
    win_rate = winning_trades / total_trades if total_trades > 0 else 0
    avg_profit = sum(profit_pcts) / len(profit_pcts) if profit_pcts else 0
    
    score = (
        win_rate * 40
        + min(len(tokens_traded) / 10, 1.0) * 20
        + min(total_trades / 50, 1.0) * 20
        + min(avg_profit / 0.5, 1.0) * 20
    ) * 100
    
    return {
        "wallet_address": wallet,
        "win_rate": round(win_rate, 4),
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "avg_profit_pct": round(avg_profit * 100, 2),
        "unique_tokens": len(tokens_traded),
        "days_since_active": round(days_since_active, 1),
        "composite_score": round(score, 2),
    }

async def insert_wallets_to_db(wallets: List[dict]) -> int:
    from app.infrastructure.database.session import SessionLocal
    from app.infrastructure.database.models import WatchlistWalletORM
    
    db = SessionLocal()
    added = 0
    try:
        for idx, w in enumerate(wallets):
            addr = w["wallet_address"]
            existing = db.query(WatchlistWalletORM).filter(
                WatchlistWalletORM.wallet_address == addr
            ).first()
            
            if existing:
                continue
                
            label = f"Niche Money #{idx+1} (WR:{w['win_rate']*100:.0f}% S:{w['composite_score']:.0f})"
            new_wallet = WatchlistWalletORM(
                wallet_address=addr,
                label=label,
                source="niche_discovered",
                active=True,
                status="pending",
                added_at=datetime.now(timezone.utc),
            )
            db.add(new_wallet)
            db.commit()
            added += 1
            logger.info(f"  ✅ DB Added: {addr} (WR: {w['win_rate']*100:.1f}%, Score: {w['composite_score']:.1f})")
    finally:
        db.close()
    return added

async def main():
    logger.info("=" * 65)
    logger.info("🔍 STARTING NICHE SMART MONEY WALLETS DISCOVERY")
    logger.info("   (Targeting: Non-Pepe, Non-Bonk, Non-Doge, Non-Fartcoin)")
    logger.info("=" * 65)
    
    pairs = fetch_top_niche_pairs()
    if not pairs:
        logger.error("❌ No pairs found.")
        return
        
    logger.info(f"\n🏆 Top Pairs to scan:")
    for p in pairs[:5]:
        base = (p.get("baseToken") or {}).get("symbol", "?")
        logger.info(f"  {base} — Vol24h: ${p['_vol_h24']:,.0f} — Txns: {p['_txns_total']}")
        
    candidate_wallets: Set[str] = set()
    for i, pair in enumerate(pairs):
        pair_addr = pair.get("pairAddress")
        base = (pair.get("baseToken") or {}).get("symbol", "?")
        if not pair_addr:
            continue
        logger.info(f"  [{i+1}/{len(pairs)}] Scanning {base} ({pair_addr[:8]}...)")
        traders = extract_traders_from_pair(pair_addr, limit=SIGS_PER_PAIR)
        candidate_wallets.update(traders)
        if len(candidate_wallets) >= MAX_CANDIDATE_WALLETS:
            break
        time.sleep(0.3)
        
    logger.info(f"\nTotal candidate wallets: {len(candidate_wallets)}")
    
    # Remove existing
    try:
        from app.infrastructure.database.session import SessionLocal
        from app.infrastructure.database.models import WatchlistWalletORM
        db = SessionLocal()
        existing = {w.wallet_address for w in db.query(WatchlistWalletORM).all()}
        db.close()
        candidate_wallets -= existing
        logger.info(f"After removing existing DB wallets: {len(candidate_wallets)} candidates")
    except Exception:
        pass
        
    analyzed = []
    candidates = list(candidate_wallets)[:MAX_CANDIDATE_WALLETS]
    for i, wallet in enumerate(candidates):
        if (i+1) % 10 == 0:
            logger.info(f"  Progress: {i+1}/{len(candidates)}...")
        stats = analyze_wallet(wallet)
        if stats:
            # Check strict criteria
            if stats["win_rate"] >= MIN_WIN_RATE and stats["total_trades"] >= MIN_TOTAL_SWAPS and stats["avg_profit_pct"] >= (MIN_AVG_PROFIT_PCT * 100):
                analyzed.append(stats)
        time.sleep(0.15)
        
    analyzed.sort(key=lambda x: x["composite_score"], reverse=True)
    top_niche = analyzed[:MAX_WALLETS_TO_ADD]
    
    if not top_niche:
        logger.warning("No wallets matched criteria.")
        return
        
    logger.info(f"\n🏆 FOUND TOP {len(top_niche)} NICHE SMART MONEY WALLETS:")
    logger.info("-" * 80)
    for i, w in enumerate(top_niche):
        logger.info(
            f"{i+1:>2}. {w['wallet_address']} | Score: {w['composite_score']:.1f} | WR: {w['win_rate']*100:.1f}% | Trades: {w['total_trades']} | AvgProfit: {w['avg_profit_pct']:.1f}%"
        )
    logger.info("-" * 80)
    
    logger.info("Saving to DB...")
    added = await insert_wallets_to_db(top_niche)
    logger.info(f"Finished. Added {added} wallets to DB.")

if __name__ == "__main__":
    asyncio.run(main())
