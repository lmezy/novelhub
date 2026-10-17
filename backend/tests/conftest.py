import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
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