"""
emergency_close_all.py
======================
Menjual SEMUA SPL token di wallet dan menutup semua token accounts.
Jalankan sekali untuk membersihkan posisi orphaned dari versi sistem lama.

Usage:
    cd sumber-makmur-hype-V.2
    backend\.venv\Scripts\python backend/scripts/emergency_close_all.py
"""

import asyncio
import json
import logging
import sys
import os
import urllib.request
from datetime import datetime, timezone

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Adjust path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

from app.core.config import settings
from app.infrastructure.blockchain.wallet_manager import load_wallet_from_env
from app.infrastructure.blockchain.pumpportal_client import build_trade_transaction
from app.infrastructure.blockchain.tx_signer import sign_and_broadcast_transaction, close_token_account


RPC_URL = getattr(settings, "RPC_PRIMARY_URL", "https://api.mainnet-beta.solana.com")
PRIORITY_FEE = 0.0005   # Hemat, cukup untuk konfirmasi
SLIPPAGE_PCT = 25       # 25% slippage agar pasti terjual
MIN_SOL_FOR_FEE = 0.003 # Minimal SOL yang harus tersisa untuk biaya


def rpc_call(method: str, params: list) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(
        RPC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_token_price_usd(mint: str) -> float:
    """Fetch token price from DexScreener."""
    try:
        url = f"https://api.dexscreener.com/tokens/v1/solana/{mint}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            pairs = data if isinstance(data, list) else data.get("pairs", [])
            if pairs:
                return float(pairs[0].get("priceUsd", 0.0) or 0.0)
    except Exception as e:
        logger.warning(f"Could not fetch price for {mint}: {e}")
    return 0.0


async def main():
    print("\n" + "=" * 65)
    print("  EMERGENCY CLOSE ALL - Sumber Makmur Hype")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 65)

    # 1. Load keypair
    keypair = load_wallet_from_env()
    if not keypair:
        logger.error("FATAL: Tidak bisa load keypair dari .env. Pastikan WALLET_PRIVATE_KEY sudah diset.")
        sys.exit(1)

    pubkey_str = str(keypair.pubkey())
    logger.info(f"Wallet: {pubkey_str}")

    # 2. Check SOL balance
    sol_res = rpc_call("getBalance", [pubkey_str])
    sol_lamports = sol_res.get("result", {}).get("value", 0)
    sol_balance_start = sol_lamports / 1_000_000_000
    logger.info(f"SOL balance awal: {sol_balance_start:.6f} SOL")

    if sol_balance_start < MIN_SOL_FOR_FEE:
        logger.error(f"SOL terlalu sedikit ({sol_balance_start:.6f}) untuk membayar fee. Minimum: {MIN_SOL_FOR_FEE} SOL")
        sys.exit(1)

    # 3. Fetch all SPL token accounts
    logger.info("Fetching semua SPL token accounts...")
    token_res = rpc_call("getTokenAccountsByOwner", [
        pubkey_str,
        {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
        {"encoding": "jsonParsed"}
    ])

    accounts = token_res.get("result", {}).get("value", [])
    logger.info(f"Ditemukan {len(accounts)} token accounts.")

    tokens_to_process = []
    zero_balance_mints = []
    for acc in accounts:
        try:
            info = acc["account"]["data"]["parsed"]["info"]
            mint = info["mint"]
            ui_amount = float(info["tokenAmount"].get("uiAmount") or 0.0)
            raw_amount = int(info["tokenAmount"].get("amount") or 0)
            decimals = int(info["tokenAmount"].get("decimals", 0))
            if ui_amount > 0:
                tokens_to_process.append({"mint": mint, "amount": ui_amount, "raw_amount": raw_amount, "decimals": decimals})
            else:
                zero_balance_mints.append(mint)
        except Exception as e:
            logger.warning(f"Skip account karena parse error: {e}")

    if not tokens_to_process and not zero_balance_mints:
        logger.info("Tidak ada token accounts. Wallet sudah bersih.")
        return

    if tokens_to_process:
        print(f"\n  Token BERISI yang akan dijual ({len(tokens_to_process)}):")
        for t in tokens_to_process:
            price = get_token_price_usd(t["mint"])
            value = t["amount"] * price
            t["price_usd"] = price
            t["value_usd"] = value
            print(f"  - {t['mint'][:16]}... | {t['amount']:.4f} token | ${value:.4f} USD")

    if zero_balance_mints:
        print(f"\n  Token accounts KOSONG yang akan ditutup ({len(zero_balance_mints)}):")
        for m in zero_balance_mints:
            print(f"  - {m[:16]}...")

    # 4. Sell each token with non-zero balance
    sold_count = 0
    failed_mints = []

    if tokens_to_process:
        print("\n  === MULAI PROSES JUAL ===")
        for token in tokens_to_process:
            mint = token["mint"]
            logger.info(f"\n[SELL] Token: {mint}")
            logger.info(f"       Jumlah: {token['amount']:.4f} | Harga: ${token.get('price_usd', 0):.6f} | Nilai: ${token.get('value_usd', 0):.4f}")

            try:
                unsigned_tx = await build_trade_transaction(
                    public_key=pubkey_str,
                    action="sell",
                    token_mint=mint,
                    amount="100%",
                    denominated_in_sol=False,
                    slippage=SLIPPAGE_PCT,
                    priority_fee=PRIORITY_FEE,
                    pool="auto"
                )

                if not unsigned_tx:
                    raise ValueError("PumpPortal mengembalikan transaksi kosong")

                tx_sig = await sign_and_broadcast_transaction(unsigned_tx, keypair)
                logger.info(f"[SELL] SUKSES - TX: {tx_sig}")
                logger.info(f"       Solscan: https://solscan.io/tx/{tx_sig}")
                sold_count += 1
                await asyncio.sleep(4)  # Tunggu konfirmasi sebelum lanjut

            except Exception as e:
                logger.error(f"[SELL] GAGAL untuk {mint}: {e}")
                failed_mints.append(mint)
                continue

    # 5. Close ALL token accounts (including zero-balance ones)
    print("\n  === MENUTUP SEMUA TOKEN ACCOUNTS ===")
    all_mints_to_close = [t["mint"] for t in tokens_to_process] + zero_balance_mints
    closed_count = 0

    for mint in all_mints_to_close:
        logger.info(f"[CLOSE ATA] Token: {mint}")
        try:
            close_sig = await close_token_account(mint, keypair, token_price_usd=0.0)
            if close_sig:
                logger.info(f"[CLOSE ATA] Rent reclaimed - TX: {close_sig}")
                logger.info(f"             Solscan: https://solscan.io/tx/{close_sig}")
                closed_count += 1
            else:
                logger.info(f"[CLOSE ATA] Account tidak ada atau saldo masih ada, dilewati.")
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"[CLOSE ATA] Tidak bisa tutup account {mint}: {e}")

    # 6. Final SOL balance check
    sol_res2 = rpc_call("getBalance", [pubkey_str])
    sol_lamports2 = sol_res2.get("result", {}).get("value", 0)
    sol_balance_end = sol_lamports2 / 1_000_000_000

    print("\n" + "=" * 65)
    print("  RINGKASAN EKSEKUSI")
    print("=" * 65)
    print(f"  Token berhasil dijual : {sold_count}/{len(tokens_to_process)}")
    print(f"  Akun berhasil ditutup : {closed_count}/{len(all_mints_to_close)}")
    print(f"  SOL awal              : {sol_balance_start:.6f} SOL")
    print(f"  SOL akhir             : {sol_balance_end:.6f} SOL")
    delta = sol_balance_end - sol_balance_start
    print(f"  Delta SOL             : {delta:+.6f} SOL")
    if failed_mints:
        print(f"\n  Token yang GAGAL dijual (masih ada di wallet):")
        for m in failed_mints:
            print(f"    - {m}")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
