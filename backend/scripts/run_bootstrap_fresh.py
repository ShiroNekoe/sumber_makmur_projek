#!/usr/bin/env python3
"""
Bootstrap Model v0 Fresh Trigger Script
=======================================
Mengosongkan model registry dan bootstrap trades lama, lalu memicu
HistoricalModelBootstrapService secara langsung menggunakan update parsing logic baru.
Menghasilkan Model v0 berkualitas tinggi dari data riil on-chain.

Cara pakai:
    python backend/scripts/run_bootstrap_fresh.py
"""

import asyncio
import logging
import os
import sys
import shutil
from datetime import datetime, timezone

# Tambahkan backend ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("fresh_bootstrap")

async def main():
    logger.info("=" * 60)
    logger.info("⚡ TRACE-ENRICHED BOOTSTRAP MODEL v0 TRIGGER")
    logger.info("=" * 60)
    
    from app.infrastructure.database.session import SessionLocal, engine
    from app.infrastructure.database.models import ModelRegistryORM, ClosedTradeORM
    from app.infrastructure.database.repository import SQLAlchemyModelRegistryRepository, SQLAlchemyTradeHistoryRepository
    from app.ml_pipeline.bootstrap import HistoricalModelBootstrapService
    from app.core.config import settings

    # 1. Clear old model registry v0 and bootstrap trades
    db = SessionLocal()
    try:
        logger.info("🧹 Membersihkan model registry dan bootstrap trades lama dari SQLite...")
        
        # Hapus closed trades berkode bootstrap first (due to foreign key constraint!)
        deleted_trades = db.query(ClosedTradeORM).filter(
            ClosedTradeORM.trade_id.like("bt_%")
        ).delete(synchronize_session=False)

        # Hapus model v0
        deleted_models = db.query(ModelRegistryORM).filter(
            ModelRegistryORM.model_version == "v0"
        ).delete(synchronize_session=False)
        
        db.commit()
        logger.info(f"  → Trade bootstrap dihapus: {deleted_trades}")
        logger.info(f"  → Model dihapus: {deleted_models}")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Gagal membersihkan DB: {e}")
        return
    finally:
        db.close()

    # 2. Setup folders
    models_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models"))
    os.makedirs(models_dir, exist_ok=True)
    
    # Hapus file model lama v0.json jika ada
    model_file = os.path.join(models_dir, "v0.json")
    if os.path.exists(model_file):
        os.remove(model_file)
        logger.info(f"🗑️  File model v0.json lama dihapus dari {models_dir}")

    # 3. Picu bootstrap baru dengan logic trace-enriched
    db = SessionLocal()
    registry_repo = SQLAlchemyModelRegistryRepository(db)
    trade_repo = SQLAlchemyTradeHistoryRepository(db)
    
    from app.ml_pipeline.bootstrap import SolanaRpcHistoricalTransactionSource
    tx_src = SolanaRpcHistoricalTransactionSource(
        max_signatures_per_wallet=60
    )
    bootstrap_service = HistoricalModelBootstrapService(
        transaction_source=tx_src,
        history_days=7
    )
    
    logger.info("\n🚀 Memulai rekonstruksi data dan training Model v0...")
    logger.info(f"   Target Wallets Count: {len(settings.TARGET_WALLETS)}")
    logger.info(f"   History Days: 7 hari (max 60 signatures per wallet)")
    
    start_time = time.time()
    success = await bootstrap_service.bootstrap_model_v0(
        models_dir=models_dir,
        model_registry_repo=registry_repo,
        trade_history_repo=trade_repo
    )
    duration = time.time() - start_time
    
    if success:
        logger.info("\n" + "=" * 60)
        logger.info(f"🎉 BOOTSTRAP BERHASIL DALAM {duration:.1f} DETIK!")
        logger.info("=" * 60)
        
        # Query results
        model_entry = await registry_repo.get_active_model()
        if model_entry:
            logger.info(f"📊 Model Version: {model_entry.model_version}")
            logger.info(f"📈 Sampel Training: {model_entry.training_sample_count} trades")
            logger.info(f"🎯 Validation Accuracy: {model_entry.validation_accuracy * 100:.1f}%")
            logger.info(f"💵 Expectancy R: {model_entry.expectancy_r:+.2f}R")
            logger.info(f"📁 Path: {os.path.join(models_dir, 'v0.json')}")
        
        db.close()
        
        # Verify db trades
        db = SessionLocal()
        total_trades = db.query(ClosedTradeORM).count()
        logger.info(f"📈 Total closed trades di DB sekarang: {total_trades}")
        db.close()
    else:
        logger.error("\n❌ Bootstrap Model v0 gagal. Periksa logs di atas.")
        db.close()


if __name__ == "__main__":
    import time
    asyncio.run(main())
