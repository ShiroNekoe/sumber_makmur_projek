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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()