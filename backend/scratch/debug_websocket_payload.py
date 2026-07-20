import asyncio
import json
import logging
import websockets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_ws")

async def debug_ws():
    url = "wss://mainnet.helius-rpc.com/?api-key=00f9de1e-3d75-46e0-9e7e-fee21a442a51"
    async with websockets.connect(url) as ws:
        sub = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": ["6EF8rrecMDMKMzBkv7jVLFv1E2syLQH5SH3iFh9FEAKB"]}, # pump.fun
                {"commitment": "confirmed"}
            ]
        }
        await ws.send(json.dumps(sub))
        logger.info("Subscribed. Waiting for 3 messages...")

        msg_count = 0
        async for msg in ws:
            data = json.loads(msg)
            if data.get("method") == "logsNotification":
                msg_count += 1
                print("\n--- RAW LOGS NOTIFICATION PAYLOAD #", msg_count, "---")
                print(json.dumps(data, indent=2))
                if msg_count >= 3:
                    break

if __name__ == "__main__":
    asyncio.run(debug_ws())
