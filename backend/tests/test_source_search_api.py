from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.services.auth import get_current_user


@pytest.mark.asyncio
async def test_source_search_api_returns_in_library_status():
    source = SimpleNamespace(
        id="yuedu_test",
        name="Test Source",
        enabled=True,
        plugin_name="yuedu",
        config={},
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=source)
    db.execute = AsyncMock(return_value=[])

    fake_plugin = SimpleNamespace(
        search_books=AsyncMock(return_value=[
            {
                "name": "Book One",
                "author": "Author",
                "bookUrl": "https://example.com/novel/1.html",
                "lastChapter": "Chapter 1",
            }
        ])
    )

    async def fake_user():
        return SimpleNamespace(id="user-1", role="super_admin")

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db] = lambda: db

    try:
        with patch(
            "app.api.routes.sources.get_plugin",
            return_value=fake_plugin,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/sources/yuedu_test/search",
                    params={"q": "test", "page": 1},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["results"][0]["name"] == "Book One"
    assert body["results"][0]["in_library"] is False
    assert body["results"][0]["source_id"] == "yuedu_test"
