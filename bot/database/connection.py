"""
bot/database/connection.py
──────────────────────────
Async SQLAlchemy 2.0 engine, session factory, and context manager helpers.
Supports both SQLite (via aiosqlite) and PostgreSQL (via asyncpg).
"""
from __future__ import annotations

import contextlib
import logging
from typing import AsyncGenerator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from bot.config import DATABASE_URL

logger = logging.getLogger(__name__)

# ── Engine ────────────────────────────────────────────────────────────────────

def _build_engine() -> AsyncEngine:
    """Create an async engine with settings appropriate for the backend."""
    is_sqlite = DATABASE_URL.startswith("sqlite")

    kwargs: dict = {
        "echo": False,  # Set to True for SQL debug logging
        "future": True,
    }

    if is_sqlite:
        # SQLite does not support concurrent connections well; use NullPool
        # and enable WAL mode for better concurrency.
        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        # PostgreSQL / asyncpg pool settings
        kwargs["poolclass"] = AsyncAdaptedQueuePool
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_timeout"] = 30
        kwargs["pool_recycle"] = 1800
        kwargs["pool_pre_ping"] = True

    engine = create_async_engine(DATABASE_URL, **kwargs)
    logger.info("Database engine created: %s", DATABASE_URL.split("@")[-1])
    return engine


engine: AsyncEngine = _build_engine()

# ── Session factory ───────────────────────────────────────────────────────────

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── Context managers ──────────────────────────────────────────────────────────

@contextlib.asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide a transactional scope for a series of operations.

    Usage::

        async with get_session() as session:
            result = await session.execute(select(User))
    """
    session: AsyncSession = AsyncSessionFactory()
    try:
        yield session
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("Database error, transaction rolled back: %s", exc)
        raise
    except Exception as exc:
        await session.rollback()
        logger.exception("Unexpected error, transaction rolled back: %s", exc)
        raise
    finally:
        await session.close()


@contextlib.asynccontextmanager
async def get_read_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Read-only session (no commit/rollback overhead).
    Use for SELECT-only operations.
    """
    session: AsyncSession = AsyncSessionFactory()
    try:
        yield session
    except SQLAlchemyError as exc:
        logger.exception("Database read error: %s", exc)
        raise
    finally:
        await session.close()


# ── Database lifecycle ────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables defined in the models (if they don't exist)."""
    from bot.database.models import Base  # local import to avoid circular imports

    async with engine.begin() as conn:
        # Enable WAL mode on SQLite for better write concurrency
        if DATABASE_URL.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON;")
            await conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
            await conn.exec_driver_sql("PRAGMA cache_size=-64000;")

        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables initialised.")


async def close_db() -> None:
    """Dispose the connection pool gracefully on shutdown."""
    await engine.dispose()
    logger.info("Database engine disposed.")


async def check_db_connection() -> bool:
    """Return True if the database is reachable, False otherwise."""
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        return True
    except Exception as exc:
        logger.error("Database connection check failed: %s", exc)
        return False
