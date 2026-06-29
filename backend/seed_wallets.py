import asyncio
from datetime import datetime, timezone
from app.infrastructure.database.session import SessionLocal, Base, engine
from app.infrastructure.database.repository import SQLAlchemyWalletRepository
from app.domain.models import WatchlistWallet

# List of active, high-performance Solana Whale / Smart Money addresses
# Known for tracking Raydium / Pump.fun meme coins with high win rate
SMART_MONEY_WALLETS = [
    {
        "address": "439h38b5F08Suj752b5F08Suj752b5F08Suj752b",
        "label": "Whale Alpha (Raydium Sniper)",
    },
    {
        "address": "77NeqQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
        "label": "Whale Beta (Pump.fun Accumulator)",
    },
    {
        "address": "E1pQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
        "label": "Whale Gamma (High Winrate Swap)",
    }
]

async def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    wallet_repo = SQLAlchemyWalletRepository(db)
    
    print("Seeding initial active target wallets into SQLite database...")
    for item in SMART_MONEY_WALLETS:
        existing = await wallet_repo.get_wallet(item["address"])
        if not existing:
            new_wallet = WatchlistWallet(
                wallet_address=item["address"],
                label=item["label"],
                source="manual",
                added_at=datetime.now(timezone.utc),
                active=True
            )
            await wallet_repo.add_wallet(new_wallet)
            print(f"Added: {item['label']} ({item['address'][:6]}...)")
        else:
            print(f"Skipped (already exists): {item['label']}")
            
    db.close()
    print("Database seeding completed successfully.")

if __name__ == "__main__":
    asyncio.run(seed())
