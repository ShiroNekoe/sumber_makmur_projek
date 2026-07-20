import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.ml_pipeline.new_token_discovery_service import NewTokenDiscoveryService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_agamemnon_filter")

async def test_agamemnon():
    service = NewTokenDiscoveryService()
    agamemnon_mint = "2cAtqsRafKS7baN3mvJARhyZiMRdW4fZYNUUWUrCpump"
    
    logger.info("Fetching snapshot for Agamemnon (%s)...", agamemnon_mint)
    snapshot = await service._fetch_pair_snapshot(agamemnon_mint)
    
    if snapshot:
        logger.info("Snapshot pair_created_at: %s", snapshot.pair_created_at)
        logger.info("Liquidity USD: $%.2f", snapshot.liquidity_usd)
        passed = service._passes_filters(snapshot)
        logger.info("Does Agamemnon (1d 1h old) pass filters? -> %s", passed)
        if not passed:
            logger.info("✅ SUCCESS: Agamemnon was REJECTED because it is older than 30 minutes!")
        else:
            logger.error("❌ FAILED: Agamemnon passed filter!")

if __name__ == "__main__":
    asyncio.run(test_agamemnon())
