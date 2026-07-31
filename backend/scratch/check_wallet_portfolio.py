import sys
import os
import asyncio

sys.path.insert(0, os.path.abspath("backend"))

from app.core.config import settings
from app.infrastructure.blockchain.wallet_manager import load_wallet_from_env, get_sol_balance
from app.infrastructure.blockchain.token_service import SolanaTokenInfoService


async def check_portfolio():
    keypair = load_wallet_from_env()
    if keypair:
        bal = await get_sol_balance(keypair.pubkey())
        info_svc = SolanaTokenInfoService()
        sol_info = await info_svc.get_token_info("So11111111111111111111111111111111111111112")
        sol_price = float(sol_info.get("price_usd", 77.34)) if sol_info else 77.34
        equity_usd = bal * sol_price
        print(f"Loaded Wallet Pubkey : {keypair.pubkey()}")
        print(f"SOL Balance         : {bal:.6f} SOL")
        print(f"SOL Price           : ${sol_price:.2f} USD")
        print(f"Calculated Equity   : ${equity_usd:.2f} USD")
    else:
        print("No live keypair loaded in .env (Running in PAPER / DRY RUN mode)")


if __name__ == "__main__":
    asyncio.run(check_portfolio())
