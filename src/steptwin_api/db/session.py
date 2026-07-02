from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from steptwin_api.core.config import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_database(settings: Settings) -> None:
    global _engine, _session_factory

    if settings.database_url is None:
        _engine = None
        _session_factory = None
        return

    _engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def close_database() -> None:
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()

    _engine = None
    _session_factory = None


async def get_session() -> AsyncIterator[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database is not configured. Set DATABASE_URL before using DB sessions.")

    async with _session_factory() as session:
        yield session


@asynccontextmanager
async def session_context() -> AsyncIterator[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database is not configured. Set DATABASE_URL before using DB sessions.")

    async with _session_factory() as session:
        yield session


async def ping_database() -> bool | None:
    if _engine is None:
        return None

    async with _engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

    return True
