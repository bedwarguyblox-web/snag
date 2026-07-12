"""
Async SQLAlchemy engine + session factory.
One pooled engine for the entire process — never create engines inside command handlers.
"""

import os
import asyncio
import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.exc import OperationalError

from config import DB_POOL_SIZE, DB_MAX_OVERFLOW

logger = logging.getLogger(__name__)

# Build the asyncpg DSN from DATABASE_URL (Replit provides this as a pg:// URL)
def _build_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    # Normalise scheme so asyncpg driver is used regardless of the source host
    # (Replit provides postgres://, Neon/Supabase/Railway provide postgresql://)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    # Strip ?sslmode=require from the DSN string — asyncpg handles SSL via
    # connect_args instead; leaving it in the URL causes a parse error.
    if "?sslmode=" in url:
        url = url.split("?sslmode=")[0]
    return url


def _ssl_args() -> dict:
    """Return connect_args that enable SSL when the original URL requests it."""
    raw = os.environ.get("DATABASE_URL", "")
    if "sslmode=require" in raw or "neon.tech" in raw or "supabase" in raw:
        import ssl
        ctx = ssl.create_default_context()
        return {"ssl": ctx}
    return {}


engine: AsyncEngine = create_async_engine(
    _build_dsn(),
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_pre_ping=True,          # detect dead connections and replace them
    echo=False,
    connect_args=_ssl_args(),    # enables SSL for Neon/Supabase/Railway etc.
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def create_all_tables() -> None:
    """Create all tables defined in models (idempotent via CREATE IF NOT EXISTS)."""
    from database.models import Base  # local import to avoid circular at module load

    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables ready.")
            return
        except OperationalError as exc:
            if attempt == max_attempts:
                raise
            wait = 2 ** attempt
            logger.warning(
                "DB connection attempt %d/%d failed (%s). Retrying in %ds…",
                attempt,
                max_attempts,
                exc,
                wait,
            )
            await asyncio.sleep(wait)
