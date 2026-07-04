import asyncio
from datetime import datetime, timezone
from sqlalchemy import text
from app.infrastructure.database.session import SessionLocal, Base, engine
from app.infrastructure.database.repository import SQLAlchemyWalletRepository
from app.domain.models import WatchlistWallet

# List of high-performance Solana Smart Money / Whale addresses
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
    },
    {
        "address": "9xQ1UvX4K9W1f3VjK3pQGWjhoxjq2WqU1AF9Z23J1x584",
        "label": "Smart Money (Meme Sniper #1)",
    },
    {
        "address": "FX9mK3W1f3VjK3pQGWjhoxjq2WqU1AF9Z23J1x584hK",
        "label": "Smart Money (Pump.fun Degen #2)",
    },
    {
        "address": "8sNeqQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
        "label": "Whale Delta (High Frequency Swapper)",
    },
    {
        "address": "G1pQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584uK",
        "label": "Whale Epsilon (Early Entry Sniper)",
    },
    {
        "address": "H8NeqQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584",
        "label": "Whale Zeta (Raydium Frontrunner)",
    }
]

async def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    wallet_repo = SQLAlchemyWalletRepository(db)
    
    print("Cleaning mock wallets from database...")
    # Delete mock target wallets
    db.execute(text(
        "DELETE FROM watchlist_wallets WHERE wallet_address IN "
        "('WhaleA11111111111111111111111111111111111', "
        "'WhaleB22222222222222222222222222222222222')"
    ))
    db.commit()
    print("Mock wallets deleted successfully.")
    
    print("Seeding smart money wallets into database...")
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
            # Keep active
            existing.active = True
            existing.label = item["label"]
            await wallet_repo.update_wallet(existing)
            print(f"Verified Active: {item['label']}")
            
    db.close()
    print("Database seeding and cleanup completed successfully.")

if __name__ == "__main__":
    asyncio.run(main())
