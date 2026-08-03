import asyncio
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# For SQLite, we set check_same_thread=False to allow multi-threaded FastAPI handlers to share sessions
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False}
)

# Serializes write access across all repositories/background tasks so concurrent
# asyncio tasks never issue overlapping writes on the single shared SQLite connection.
# Used by the @db_locked decorator in repository.py (`async with db_lock:`).
db_lock = asyncio.Lock()


# SQLite optimization for production-readiness on limited hardware (avoid locking errors)
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")       # Write-Ahead Logging for high concurrency
    cursor.execute("PRAGMA synchronous=NORMAL")     # Faster writes, safe in WAL mode
    cursor.execute("PRAGMA foreign_keys=ON")        # Enable FK constraint enforcement
    cursor.execute("PRAGMA busy_timeout=30000")     # Wait up to 30s for locks instead of failing immediately (defense-in-depth against external lockers e.g. OneDrive sync)
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def run_db_migrations(engine_obj):
    """Idempotent database migration for SQLite schema columns."""
    try:
        from sqlalchemy import text
        with engine_obj.connect() as conn:
            res = conn.execute(text("PRAGMA table_info(open_positions)"))
            cols = [row[1] for row in res.fetchall()]
            if cols:
                if "slippage_actual" not in cols:
                    conn.execute(text("ALTER TABLE open_positions ADD COLUMN slippage_actual FLOAT"))
                    conn.commit()
                if "dev_wallet_address" not in cols:
                    conn.execute(text("ALTER TABLE open_positions ADD COLUMN dev_wallet_address VARCHAR"))
                    conn.commit()
                if "sizing_mode" not in cols:
                    conn.execute(text("ALTER TABLE open_positions ADD COLUMN sizing_mode VARCHAR DEFAULT 'risk_pct'"))
                    conn.commit()

            res_ct = conn.execute(text("PRAGMA table_info(closed_trades)"))
            cols_ct = [row[1] for row in res_ct.fetchall()]
            if cols_ct:
                if "sizing_mode" not in cols_ct:
                    conn.execute(text("ALTER TABLE closed_trades ADD COLUMN sizing_mode VARCHAR DEFAULT 'risk_pct'"))
                    conn.commit()
    except Exception:
        pass


run_db_migrations(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()