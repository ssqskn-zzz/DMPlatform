from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dgov.core.config import settings
from dgov.models.base import Base

_engine_kwargs: dict[str, object] = {
    "echo": settings.db_echo,
    "pool_pre_ping": True,
    "pool_recycle": 3600,
}
# SQLite (in-memory demo / tests) uses StaticPool and rejects pool_size.
if not settings.mysql_dsn.startswith("sqlite"):
    _engine_kwargs.update({"pool_size": 10, "max_overflow": 20})

engine = create_async_engine(settings.mysql_dsn, **_engine_kwargs)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def init_db() -> None:
    """Create tables at startup (dev convenience; use alembic for real deployments)."""
    if not settings.auto_create_tables:
        return
    import dgov.models  # noqa: F401  # register every ORM model on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    await engine.dispose()
