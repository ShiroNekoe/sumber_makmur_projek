#!/usr/bin/env python3
"""
Smart Money Wallet Discovery Script
=====================================
Menggunakan DexScreener API + Helius Enhanced Transaction API untuk:
1. Menemukan 30 token pair Solana terpanas berdasarkan volume 24h
2. Mengekstrak alamat dompet trader aktif dari setiap pair
3. Menganalisis riwayat trading setiap wallet (win rate, profitabilitas)
4. Menambahkan top 20 wallet ke watchlist database

Cara pakai:
    python backend/scripts/discover_smart_wallets.py
    python backend/scripts/discover_smart_wallets.py --dry-run
    python backend/scripts/discover_smart_wallets.py --limit 10
"""

import asyncio
import argparse
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("discover_wallets")

# ─── Konstanta API ────────────────────────────────────────────────────────────
DEXSCREENER_BASE = "https://api.dexscreener.com"
HELIUS_API_KEY   = "00f9de1e-3d75-46e0-9e7e-fee21a442a51"
HELIUS_RPC_URL   = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_REST_BASE = f"https://api.helius.xyz/v0"
QUICKNODE_URL    = "https://convincing-orbital-gas.solana-mainnet.quiknode.pro/21ba38b9733739c54695b200c406dfa2e03ca0de"
PUBLIC_RPC_URL   = "https://api.mainnet-beta.solana.com"

# ─── Threshold Filter Ketat ────────────────────────────────────────────────────
MIN_WIN_RATE            = 0.62   # Minimum 62% win rate
MIN_TOTAL_SWAPS         = 8      # Minimum 8 swap trades teranalisis
MAX_LAST_ACTIVE_DAYS    = 7      # Harus aktif dalam 7 hari terakhir
MIN_AVG_PROFIT_PCT      = 0.05   # Minimum 5% rata-rata profit per trade
MIN_MULTI_TOKEN_COUNT   = 3      # Trading minimal 3 token berbeda (diversifikasi)
MIN_PAIR_VOLUME_USD     = 75000  # Filter DexScreener: volume 24h > $75k
MIN_PAIR_TXNS_24H       = 200    # Filter DexScreener: transaksi 24h > 200
MIN_PAIR_LIQUIDITY_USD  = 50000  # Filter DexScreener: likuiditas > $50k
TOP_PAIRS_TO_SCAN       = 30     # Scan 30 pair teratas
SIGS_PER_PAIR           = 50     # Ambil 50 signature per pair
MAX_CANDIDATE_WALLETS   = 300    # Batas analisis wallet
MAX_WALLETS_TO_ADD      = 20     # Batas penambahan ke DB
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
STABLE_MINTS = {WSOL_MINT, USDC_MINT, USDT_MINT}


# ─── HTTP Helpers ─────────────────────────────────────────────────────────────
def http_get(url: str, timeout: int = 15, retries: int = 2) -> Optional[dict]:
    """Sync HTTP GET dengan retry."""
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
                time.sleep(3)
            else:
                logger.debug(f"GET HTTP {e.code}: {url[:70]}")
                return None
        except Exception as e:
            logger.debug(f"GET error: {url[:70]} — {e}")
            return None
    return None


def rpc_post(method: str, params: list, timeout: int = 12) -> Optional[dict]:
    """JSON-RPC call dengan 3-stage fallback: Helius → QuickNode → Public."""
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
        except Exception as e:
            logger.debug(f"RPC {method} failed on {url[:40]}: {e}")
    return None


def helius_enhanced_txs(wallet: str, tx_type: str = "SWAP", limit: int = 100) -> List[dict]:
    """
    Helius Enhanced Transactions REST API.
    Mengembalikan transaksi yang sudah diparsing dengan type classification.
    """
    url = (
        f"{HELIUS_REST_BASE}/addresses/{wallet}/transactions"
        f"?api-key={HELIUS_API_KEY}&type={tx_type}&limit={limit}"
    )
    result = http_get(url, timeout=20, retries=2)
    if isinstance(result, list):
        return result
    return []


# ─── Step 1: DexScreener — Ambil Top Pairs ────────────────────────────────────
def fetch_top_solana_pairs(top_n: int = TOP_PAIRS_TO_SCAN) -> List[dict]:
    """
    Ambil pair Solana terpanas dari DexScreener berdasarkan volume 24h.
    Filter ketat: volume, likuiditas, dan jumlah transaksi.
    """
    logger.info("📡 [DexScreener] Mengambil top Solana pairs...")
    
    # Ambil dari berbagai keyword untuk coverage lebih luas
    search_queries = ["solana", "SOL", "BONK", "WIF", "MEME"]
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
            pair_addr = p.get("pairAddress")
            if pair_addr and pair_addr not in all_pairs:
                all_pairs[pair_addr] = p
        time.sleep(0.5)  # Rate limit DexScreener
    
    # Filter dan sort
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
    
    # Sort by volume DESC
    filtered.sort(key=lambda x: x["_vol_h24"], reverse=True)
    result = filtered[:top_n]
    
    logger.info(f"  WA  {len(result)} pairs lolos filter dari {len(all_pairs)} total pairs")
    return result


# ─── Step 2: Ekstrak Wallet Addresses dari Pair Transactions ──────────────────
def extract_traders_from_pair(pair_address: str, limit: int = SIGS_PER_PAIR) -> Set[str]:
    """
    Ambil signature dari pair address, parse setiap transaksi untuk 
    mendapatkan wallet yang melakukan swap.
    """
    wallets: Set[str] = set()
    
    # Ambil signatures untuk pair address
    sigs = rpc_post("getSignaturesForAddress", [pair_address, {"limit": limit}])
    if not isinstance(sigs, list):
        return wallets
    
    for sig_info in sigs[:limit]:
        sig = sig_info.get("signature")
        if not sig or sig_info.get("err"):
            continue
        
        # Fetch transaksi
        tx = rpc_post("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not isinstance(tx, dict):
            continue
        
        # Ekstrak first signer (initiator swap)
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
        
        time.sleep(0.1)  # Rate limiting
    
    return wallets


# ─── Step 3: Analisis Wallet via Helius Enhanced TX API ───────────────────────
def analyze_wallet(wallet: str) -> Optional[dict]:
    """
    Analisis riwayat trading wallet menggunakan Helius Enhanced TX API.
    Menghitung: win_rate, total_swaps, avg_profit, multi_token_count, dll.
    Returns None jika wallet tidak memenuhi syarat dasar.
    """
    swaps = helius_enhanced_txs(wallet, tx_type="SWAP", limit=100)
    
    if len(swaps) < 3:  # Minimum data untuk analisis
        return None
    
    now_ts = datetime.now(timezone.utc).timestamp()
    last_active_ts = 0
    
    # Tracking per-token positions
    buy_events: Dict[str, List[dict]] = defaultdict(list)   # mint → list of buy events
    sell_events: Dict[str, List[dict]] = defaultdict(list)  # mint → list of sell events
    tokens_traded: Set[str] = set()
    
    for tx in swaps:
        ts = tx.get("timestamp", 0)
        if ts > last_active_ts:
            last_active_ts = ts
        
        # Helius Enhanced TX format: tokenTransfers
        transfers = tx.get("tokenTransfers", [])
        
        for t in transfers:
            mint = t.get("mint")
            if not mint or mint in STABLE_MINTS:
                continue
            from_acc = t.get("fromUserAccount") or ""
            to_acc = t.get("toUserAccount") or ""
            amount = float(t.get("tokenAmount") or 0)
            if amount <= 0:
                continue
            
            if to_acc == wallet:
                # BUY: token masuk ke wallet
                buy_events[mint].append({"ts": ts, "amount": amount})
                tokens_traded.add(mint)
            elif from_acc == wallet:
                # SELL: token keluar dari wallet
                sell_events[mint].append({"ts": ts, "amount": amount})
                tokens_traded.add(mint)
    
    # Cek apakah aktif dalam 7 hari
    days_since_active = (now_ts - last_active_ts) / 86400 if last_active_ts > 0 else 999
    if days_since_active > MAX_LAST_ACTIVE_DAYS:
        return None
    
    # Hitung matched positions (BUY → SELL pasangan)
    total_trades = 0
    winning_trades = 0
    profit_pcts = []
    
    for mint in set(buy_events.keys()) & set(sell_events.keys()):
        buys = sorted(buy_events[mint], key=lambda x: x["ts"])
        sells = sorted(sell_events[mint], key=lambda x: x["ts"])
        
        for buy in buys:
            # Cari sell yang terjadi setelah buy
            for sell in sells:
                if sell["ts"] > buy["ts"]:
                    total_trades += 1
                    buy_amount = buy["amount"]
                    sell_amount = sell["amount"]
                    if buy_amount > 0:
                        ratio = sell_amount / buy_amount
                        if ratio >= 1.0:
                            winning_trades += 1
                            profit_pcts.append(min(ratio - 1.0, 5.0))  # Cap 500%
                        else:
                            profit_pcts.append(ratio - 1.0)
                    break
    
    if total_trades < MIN_TOTAL_SWAPS:
        return None
    
    win_rate = winning_trades / total_trades if total_trades > 0 else 0
    avg_profit = sum(profit_pcts) / len(profit_pcts) if profit_pcts else 0
    
    # Score komposit (0-100)
    score = (
        win_rate * 40              # Win rate 40%
        + min(len(tokens_traded) / 10, 1.0) * 20    # Diversifikasi 20%
        + min(total_trades / 50, 1.0) * 20          # Aktivitas 20%
        + min(avg_profit / 0.5, 1.0) * 20           # Profit magnitude 20%
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


# ─── Step 4: Strict Filter ────────────────────────────────────────────────────
def apply_strict_filter(stats: dict) -> bool:
    """Filter ketat untuk memastikan hanya smart money yang masuk."""
    if stats["win_rate"] < MIN_WIN_RATE:
        return False
    if stats["total_trades"] < MIN_TOTAL_SWAPS:
        return False
    if stats["avg_profit_pct"] < (MIN_AVG_PROFIT_PCT * 100):
        return False
    if stats["unique_tokens"] < MIN_MULTI_TOKEN_COUNT:
        return False
    if stats["days_since_active"] > MAX_LAST_ACTIVE_DAYS:
        return False
    return True


# ─── Step 5: Insert ke Database ───────────────────────────────────────────────
async def insert_wallets_to_db(wallets: List[dict], dry_run: bool = False) -> int:
    """Insert wallet terpilih ke SQLite watchlist_wallets table."""
    from app.infrastructure.database.session import SessionLocal
    from app.infrastructure.database.models import WatchlistWalletORM
    
    db = SessionLocal()
    added = 0
    
    try:
        for idx, w in enumerate(wallets):
            addr = w["wallet_address"]
            
            # Cek apakah sudah ada
            existing = db.query(WatchlistWalletORM).filter(
                WatchlistWalletORM.wallet_address == addr
            ).first()
            
            if existing:
                logger.info(f"  ⏭️  Skip (sudah ada): {addr[:20]}...")
                continue
            
            label = f"Smart Money #{idx+1} (WR:{w['win_rate']*100:.0f}% S:{w['composite_score']:.0f})"
            
            if dry_run:
                logger.info(f"  [DRY-RUN] Akan tambah: {addr[:20]}... | {label}")
            else:
                new_wallet = WatchlistWalletORM(
                    wallet_address=addr,
                    label=label,
                    source="auto_discovered",
                    active=True,
                    status="pending",
                    added_at=datetime.now(timezone.utc),
                )
                db.add(new_wallet)
                db.commit()
                added += 1
                logger.info(f"  ✅ Ditambahkan: {addr[:20]}... | Score={w['composite_score']:.1f} WR={w['win_rate']*100:.1f}%")
    finally:
        db.close()
    
    return added


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main(dry_run: bool = False, limit: int = MAX_WALLETS_TO_ADD):
    logger.info("=" * 60)
    logger.info("🔍 SMART MONEY WALLET DISCOVERY")
    logger.info("=" * 60)
    
    # Step 1: DexScreener — Ambil top pairs
    pairs = fetch_top_solana_pairs(TOP_PAIRS_TO_SCAN)
    if not pairs:
        logger.error("❌ Tidak ada pair yang ditemukan. Cek koneksi internet.")
        return
    
    logger.info(f"\n🏆 Top 5 Pairs (dari {len(pairs)} pair):")
    for p in pairs[:5]:
        base = (p.get("baseToken") or {}).get("symbol", "?")
        quote = (p.get("quoteToken") or {}).get("symbol", "?")
        logger.info(f"  {base}/{quote} — Vol24h: ${p['_vol_h24']:,.0f} — Txns: {p['_txns_total']}")
    
    # Step 2: Ekstrak trader wallets dari setiap pair
    logger.info(f"\n📥 Mengekstrak trader wallets dari {len(pairs)} pairs...")
    candidate_wallets: Set[str] = set()
    
    for i, pair in enumerate(pairs):
        pair_addr = pair.get("pairAddress")
        base = (pair.get("baseToken") or {}).get("symbol", "?")
        if not pair_addr:
            continue
        
        logger.info(f"  [{i+1}/{len(pairs)}] Scanning pair {base} ({pair_addr[:12]}...)")
        traders = extract_traders_from_pair(pair_addr, limit=SIGS_PER_PAIR)
        candidate_wallets.update(traders)
        logger.info(f"    → {len(traders)} trader ditemukan. Total kandidat: {len(candidate_wallets)}")
        
        # Batasi kandidat
        if len(candidate_wallets) >= MAX_CANDIDATE_WALLETS:
            break
        
        time.sleep(0.5)
    
    logger.info(f"\n✅ Total {len(candidate_wallets)} wallet kandidat ditemukan")
    
    # Hapus wallet yang sudah ada di watchlist
    try:
        from app.infrastructure.database.session import SessionLocal
        from app.infrastructure.database.models import WatchlistWalletORM
        db = SessionLocal()
        existing_wallets = {w.wallet_address for w in db.query(WatchlistWalletORM).all()}
        db.close()
        candidate_wallets -= existing_wallets
        logger.info(f"  → Setelah hapus wallet existing: {len(candidate_wallets)} kandidat")
    except Exception as e:
        logger.warning(f"  ⚠️  Tidak bisa cek DB existing: {e}")
    
    # Step 3: Analisis setiap wallet via Helius Enhanced TX API
    logger.info(f"\n🔬 Menganalisis {min(len(candidate_wallets), MAX_CANDIDATE_WALLETS)} wallets...")
    analyzed: List[dict] = []
    candidates_list = list(candidate_wallets)[:MAX_CANDIDATE_WALLETS]
    
    for i, wallet in enumerate(candidates_list):
        if (i + 1) % 10 == 0:
            logger.info(f"  Progress: {i+1}/{len(candidates_list)}...")
        
        stats = analyze_wallet(wallet)
        if stats:
            analyzed.append(stats)
        
        time.sleep(0.2)  # Rate limiting Helius
    
    logger.info(f"\n📊 {len(analyzed)} wallet berhasil dianalisis")
    
    # Step 4: Filter ketat
    filtered = [w for w in analyzed if apply_strict_filter(w)]
    logger.info(f"🎯 {len(filtered)} wallet lolos filter ketat:")
    logger.info(f"   Kriteria: WinRate≥{MIN_WIN_RATE*100:.0f}% | Trades≥{MIN_TOTAL_SWAPS} | AvgProfit≥{MIN_AVG_PROFIT_PCT*100:.0f}% | UniqueTokens≥{MIN_MULTI_TOKEN_COUNT} | ActiveWithin{MAX_LAST_ACTIVE_DAYS}d")
    
    if not filtered:
        logger.warning("⚠️  Tidak ada wallet yang lolos filter! Pertimbangkan relaksasi threshold.")
        return
    
    # Step 5: Sort by composite score
    filtered.sort(key=lambda x: x["composite_score"], reverse=True)
    top_wallets = filtered[:limit]
    
    # Tampilkan hasil
    logger.info(f"\n🏆 TOP {len(top_wallets)} SMART MONEY WALLETS:")
    logger.info("-" * 80)
    logger.info(f"{'No':>3} {'Wallet':>20} {'Score':>6} {'WinRate':>8} {'Trades':>7} {'AvgProfit':>10} {'Tokens':>7} {'LastActive':>11}")
    logger.info("-" * 80)
    for i, w in enumerate(top_wallets):
        logger.info(
            f"{i+1:>3} {w['wallet_address'][:18]:>20} "
            f"{w['composite_score']:>6.1f} "
            f"{w['win_rate']*100:>7.1f}% "
            f"{w['total_trades']:>7} "
            f"{w['avg_profit_pct']:>9.1f}% "
            f"{w['unique_tokens']:>7} "
            f"{w['days_since_active']:>10.1f}d"
        )
    
    # Step 6: Insert ke database
    logger.info(f"\n💾 Menambahkan {len(top_wallets)} wallet ke database...")
    added = await insert_wallets_to_db(top_wallets, dry_run=dry_run)
    
    if dry_run:
        logger.info(f"\n[DRY-RUN] Selesai. {len(top_wallets)} wallet siap ditambahkan (tidak ada yang disimpan).")
    else:
        logger.info(f"\n✅ SELESAI! {added} wallet baru ditambahkan ke watchlist.")
        logger.info("   Restart backend untuk mulai memantau wallet-wallet ini.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover smart money wallets from DexScreener + Helius")
    parser.add_argument("--dry-run", action="store_true", help="Preview saja, tidak menyimpan ke DB")
    parser.add_argument("--limit", type=int, default=MAX_WALLETS_TO_ADD, help=f"Jumlah wallet yang ditambahkan (default: {MAX_WALLETS_TO_ADD})")
    args = parser.parse_args()
    
    asyncio.run(main(dry_run=args.dry_run, limit=args.limit))
