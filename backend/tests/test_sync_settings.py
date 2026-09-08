from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient
import pytest

from app.core.database import get_db
from app.main import app
from app.services.settings import (
    get_auto_sync_settings,
    set_auto_sync_settings,
)
from app.services.auth import require_admin


@pytest.mark.asyncio
async def test_auto_sync_settings_defaults():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    result = await get_auto_sync_settings(db)

    assert result == {"enabled": False, "time": "03:00"}


@pytest.mark.asyncio
async def test_set_auto_sync_settings_saves():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.commit = AsyncMock()

    result = await set_auto_sync_settings(db, True, "06:30")

    assert result == {"enabled": True, "time": "06:30"}
    assert db.add.call_count == 3
    assert db.commit.await_count == 3


@pytest.mark.asyncio
async def test_set_auto_sync_settings_rejects_bad_time():
    db = AsyncMock()

    with pytest.raises(ValueError):
        await set_auto_sync_settings(db, True, "25:00")


@pytest.mark.asyncio
async def test_disable_auto_sync_cancels_stale_auto_tasks():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    result = await set_auto_sync_settings(db, False, "06:30")

    assert result == {"enabled": False, "time": "06:30"}
    # 3 setting writes + 1 task cancellation update
    assert db.commit.await_count == 4
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_auto_sync_settings_route():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.commit = AsyncMock()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_admin] = lambda: None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            get_resp = await client.get("/api/admin/settings/auto-sync")
            put_resp = await client.put(
                "/api/admin/settings/auto-sync",
                json={"enabled": True, "time": "06:30"},
            )
    finally:
        app.dependency_overrides.clear()

    assert get_resp.status_code == 200
    assert get_resp.json() == {"enabled": False, "time": "03:00"}
    assert put_resp.status_code == 200
    assert put_resp.json() == {"enabled": True, "time": "06:30"}
