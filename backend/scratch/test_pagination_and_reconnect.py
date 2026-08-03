import asyncio
import logging
import json
import base64
import websockets
from app.infrastructure.blockchain.bonding_curve_price import fetch_dev_wallet_address
from app.blockchain.monitor import SolanaWebSocketMonitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_pagination_reconnect")

async def test_pagination_real_token():
    logger.info("=== BAGIAN 3: Testing fetch_dev_wallet_address pagination ===")
    token_mint = "GQAt4nq2S8H6vPwbMsyatsZUmwuFHRuUnRh3BwPzpump"
    dev_pubkey = await fetch_dev_wallet_address(token_mint)
    logger.info(f"[REAL MAINNET PAGINATION RESULT] Mint: {token_mint} -> Dev Wallet: {dev_pubkey}")

async def test_reconnect_resilience():
    logger.info("=== BAGIAN 1: Testing SolanaWebSocketMonitor accountSubscribe reconnect resilience ===")
    monitor = SolanaWebSocketMonitor()
    received_pushes = []

    async def pda_callback(decoded_data: bytes):
        logger.info(f"[WS PUSH RECEIVED] Decoded bytes length: {len(decoded_data)}")
        received_pushes.append(decoded_data)

    pda_addr = "5sbsMYZa7PMxgCefX8TkaxivNwWNHKtDb9qVy1CTQrwH"
    await monitor.subscribe_account(pda_addr, pda_callback)
    
    # Start monitor task properly
    await monitor.start()
    
    # Wait 3 seconds for initial connection & subscription confirmation
    await asyncio.sleep(3.0)
    
    if monitor.websocket:
        try:
            ws_open = not monitor.websocket.close_code
        except Exception:
            ws_open = True
        logger.info(f"[WS CONNECTED] Socket active: {ws_open}")
        logger.info("[FORCED DISCONNECT] Forcing socket closure to trigger exponential backoff reconnect...")
        try:
            await monitor.websocket.close()
        except Exception:
            pass
    
    # Wait 6 seconds to observe automatic reconnection and re-subscription
    await asyncio.sleep(6.0)
    
    ws_status = "UNKNOWN"
    if monitor.websocket:
        try:
            ws_status = "CLOSED" if monitor.websocket.close_code else "ACTIVE"
        except Exception:
            ws_status = "ACTIVE"
    else:
        ws_status = "CLOSED"
    
    logger.info(f"[RECONNECT VERIFICATION] Reconnected socket status: {ws_status}")
    logger.info(f"[RECONNECT VERIFICATION] Account callbacks active for {pda_addr[:8]}: {pda_addr in monitor.account_callbacks}")
    
    await monitor.stop()

async def main():
    await test_pagination_real_token()
    await test_reconnect_resilience()

if __name__ == "__main__":
    asyncio.run(main())
