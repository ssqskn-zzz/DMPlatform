from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from dgov.core.db import get_session
from dgov.main import app
from dgov.models.base import Base
from dgov.services.auth import seed_default_users
from dgov.services.governance import seed_demo_data


@pytest.fixture
async def db_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An isolated in-memory SQLite database with seeded users and demo data."""

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_default_users(session)
        await seed_demo_data(session)
    yield factory
    await engine.dispose()


@pytest.fixture
async def client(
    db_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """Run the API against an isolated in-memory SQLite database and seeded demo data."""

    async def override_session() -> AsyncIterator:
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        login = await http_client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "abc123456789"}
        )
        assert login.status_code == 200
        http_client.headers.update({"Authorization": f"Bearer {login.json()['token']}"})
        yield http_client
    app.dependency_overrides.clear()
