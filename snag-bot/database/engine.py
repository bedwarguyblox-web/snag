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
    # Replit provides postgres:// — asyncpg needs postgresql+asyncpg://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


engine: AsyncEngine = create_async_engine(
    _build_dsn(),
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_pre_ping=True,          # detect dead connections and replace them
    echo=False,
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
