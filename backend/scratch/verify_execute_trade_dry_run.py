"""
Scratch script untuk verifikasi end-to-end execute_trade() dalam mode PAPER / DRY RUN.
Membuktikan execute_trade() berjalan sukses dari awal hingga akhir tanpa exception (Termasuk fix NameError).
"""
import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

# Ensure backend path is in sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from app.domain.models import PredictionResult, FeatureVector, WatchlistWallet
from app.use_cases.auto_trade_executor import AutoTradeExecutor
from app.infrastructure.database.repository import (
    SQLAlchemyPositionRepository,
    SQLAlchemyCooldownRepository,
    SQLAlchemyModelRegistryRepository,
    SQLAlchemyWalletRepository
)
from app.infrastructure.database.session import SessionLocal, engine, run_db_migrations
from app.infrastructure.database.models import Base

async def run_dry_run_test():
    os.environ["SIMULATION_MODE"] = "True"
    Base.metadata.create_all(bind=engine)
    run_db_migrations(engine)
    db = SessionLocal()

    try:
        wallet_repo = SQLAlchemyWalletRepository(db)
        pos_repo = SQLAlchemyPositionRepository(db)
        cool_repo = SQLAlchemyCooldownRepository(db)
        model_repo = SQLAlchemyModelRegistryRepository(db)

        test_wallet = "29yFzeBZgxf5zqrAkKXwgZtQehRf4pL8WbV2nRJikbw8"
        test_token = "GQAt4nq2S8H6vPwbMsyatsZUmwuFHRuUnRh3BwPzpump"

        # Ensure wallet is in watchlist database for Foreign Key constraint
        existing = await wallet_repo.get_wallet(test_wallet)
        if not existing:
            await wallet_repo.add_wallet(WatchlistWallet(
                wallet_address=test_wallet,
                label="DryRunWallet",
                source="manual",
                added_at=datetime.now(timezone.utc),
                active=True,
                status="approved"
            ))

        # Mock token_info_service to simulate deep liquidity pool and valid token info
        async def mock_get_token_info(mint):
            if mint == "So11111111111111111111111111111111111111112":
                return {"price_usd": 150.0}
            return {
                "price_usd": 1.0,
                "liquidity_usd": 500000.0,
                "virtual_sol_reserves": 1000000.0,
                "virtual_token_reserves": 1000000.0
            }

        mock_token_service = AsyncMock()
        mock_token_service.get_token_info.side_effect = mock_get_token_info

        executor = AutoTradeExecutor(
            position_repo=pos_repo,
            cooldown_repo=cool_repo,
            model_registry_repo=model_repo,
            token_info_service=mock_token_service
        )

        now = datetime.now(timezone.utc)
        pred = PredictionResult(
            direction="BUY",
            confidence_score=0.92,
            target_price_estimate=2.0,
            token_address=test_token,
            wallet_source=test_wallet,
            signature="dryrun_sig_123456789",
            timestamp=now
        )

        fv = FeatureVector(
            token_address=test_token,
            wallet_source=test_wallet,
            signature="dryrun_sig_123456789",
            timestamp=now,
            position_size_usd=10.0,
            token_age_minutes=30.0,
            liquidity_pool_depth=500000.0,
            win_rate_30d=0.75,
            avg_holding_time_minutes=45.0,
            typical_trade_size_usd=150.0,
            hour_of_day_utc=now.hour
        )

        mock_curve_data = {
            "virtualSolReserves": 100000000000000, # 100,000 SOL
            "virtualTokenReserves": 1000000000000000, # 1,000,000,000 tokens
            "realSolReserves": 30000000000,
            "realTokenReserves": 793100000000000,
            "tokenTotalSupply": 1000000000000000,
            "complete": False
        }

        logger.info("=== STARTING DRY RUN execute_trade() ===")
        # Patch fetch_bonding_curve_account_info in bonding_curve_price module
        with patch("app.infrastructure.blockchain.bonding_curve_price.fetch_bonding_curve_account_info", new=AsyncMock(return_value=mock_curve_data)):
            pos = await executor.execute_trade(prediction=pred, feature_vector=fv)
            
        logger.info(f"=== execute_trade() COMPLETED! Result pos: {pos} ===")
        if pos:
            logger.info(
                f"[SUCCESS] Position ID: {pos.position_id}, Token: {pos.token_address[:8]}..., "
                f"Wallet: {pos.wallet_source[:8]}..., Entry Price: ${pos.entry_price}, "
                f"State: {pos.state}, Slippage: {pos.slippage_actual}"
            )

    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(run_dry_run_test())
