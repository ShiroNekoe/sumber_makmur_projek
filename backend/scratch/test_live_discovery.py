import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.ml_pipeline.new_token_discovery_service import NewTokenDiscoveryService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_live_discovery")

async def test_live_discovery():
    logger.info("=== STARTING LIVE DISCOVERY & MINT EXTRACTION TEST ===")
    service = NewTokenDiscoveryService()

    count = 0
    max_test_count = 5

    try:
        async with asyncio.timeout(25):
            async for mint in service._fetch_candidate_pairs_via_logs_subscribe():
                count += 1
                logger.info("--> Candidate Mint #%d: %s", count, mint)
                snapshot = await service._fetch_pair_snapshot(mint)
                if snapshot:
                    logger.info("    Snapshot: price=$%.6f, liq=$%.2f, age_mins=%.1f", 
                                snapshot.price_usd, snapshot.liquidity_usd, 
                                (snapshot.pair_created_at and ((service.now() if hasattr(service, 'now') else asyncio.get_event_loop().time()) - snapshot.pair_created_at.timestamp())/60.0 or 0))
                    passed = service._passes_filters(snapshot)
                    logger.info("    Passes Filters? -> %s", passed)
                else:
                    logger.info("    Snapshot not yet indexed on DexScreener for mint: %s", mint)

                if count >= max_test_count:
                    break
    except asyncio.TimeoutError:
        logger.info("Test timeout reached.")
    except Exception as e:
        logger.error("Error: %s", e, exc_info=True)

    logger.info("=== TEST COMPLETE (Extracted %d mints) ===", count)

if __name__ == "__main__":
    asyncio.run(test_live_discovery())
