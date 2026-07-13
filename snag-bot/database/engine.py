"""
Async SQLAlchemy engine + session factory — SQLite backend.

The database file (snag.db) is created automatically in the same directory as
this file if it does not already exist.  SQLAlchemy's create_all() is fully
idempotent — it checks for each table before issuing CREATE TABLE, so running
it on an existing database is always safe.
"""

import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)

# Place snag.db next to this file (i.e. inside snag-bot/database/)
_DB_PATH = Path(__file__).parent / "snag.db"
_DB_URL = f"sqlite+aiosqlite:///{_DB_PATH}"

# Public export so other modules can log/check the file path without re-deriving it.
DB_PATH: Path = _DB_PATH

# StaticPool keeps a single connection open and reuses it — correct for SQLite
# in an async single-process bot.  check_same_thread=False is required for
# SQLite when the same connection is accessed from multiple coroutines.
engine: AsyncEngine = create_async_engine(
    _DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def create_all_tables() -> None:
    """
    Create every table defined in models.py if it does not already exist.
    Safe to call on every startup — existing tables and data are never touched.
    """
    from database.models import Base  # local import avoids circular at module load

    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.begin() as conn:
                # Enable WAL mode for better concurrent read/write performance
                await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                # Enforce foreign key constraints (off by default in SQLite)
                await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                await conn.run_sync(Base.metadata.create_all)
            # ── Startup migrations (idempotent — safe on every boot) ──────────
            # SQLite has no "ADD COLUMN IF NOT EXISTS"; catch OperationalError instead.
            _migrations = [
                (
                    "expiry_warning_sent",
                    "ALTER TABLE listings ADD COLUMN expiry_warning_sent BOOLEAN NOT NULL DEFAULT 0",
                ),
            ]
            for col_name, ddl in _migrations:
                try:
                    async with engine.begin() as mig_conn:
                        await mig_conn.exec_driver_sql(ddl)
                    logger.info("Migration: added column '%s' to listings.", col_name)
                except OperationalError as mig_exc:
                    if "duplicate column" in str(mig_exc).lower():
                        pass  # Already present — idempotent
                    else:
                        logger.warning("Migration warning (%s): %s", col_name, mig_exc)
            if _DB_PATH.exists():
                logger.info("Database ready at %s", _DB_PATH)
            return
        except OperationalError as exc:
            if attempt == max_attempts:
                raise
            wait = 2 ** attempt
            logger.warning(
                "DB attempt %d/%d failed (%s). Retrying in %ds…",
                attempt, max_attempts, exc, wait,
            )
            await asyncio.sleep(wait)
