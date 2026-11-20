import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models.base import Base
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.rate_limit import login_limiter, register_limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    # The limiters are module-level singletons; without a reset, tests that
    # exercise register/login repeatedly from the same fake IP would hit 429.
    login_limiter.reset()
    register_limiter.reset()
    yield


@pytest_asyncio.fixture
async def client():
    """ASGI client without DB -- good for endpoints that don't strictly need DB."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
def mock_db():
    """Returns an AsyncMock that behaves like an AsyncSession."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.scalars = MagicMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest_asyncio.fixture
async def session_factory():
    """SQLite session factory over the real metadata.

    SQLite cannot compile ``JSONB``, and several tables (``users``,
    ``sources``, ``chapter_embeddings`` ...) carry it.  The columns are swapped
    to the generic ``JSON`` type for the duration of the test and restored
    afterwards, so nothing leaks into the rest of the suite.
    """
    swapped = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, JSONB):
                swapped.append((column, column.type))
                column.type = JSON()
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        for column, original in swapped:
            column.type = original
