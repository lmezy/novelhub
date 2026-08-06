from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.auth import get_current_user


@pytest.mark.asyncio
async def test_advanced_search_endpoint_returns_hits():
    async def fake_user():
        return SimpleNamespace(id="user-1", role="super_admin")

    app.dependency_overrides[get_current_user] = fake_user
    payload = {
        "conditions": [{"field": "title", "mode": "exact", "value": "西游记"}],
        "match": "and",
        "scope": "books",
        "offset": 0,
        "limit": 20,
    }
    fake_result = {
        "hits": [{"id": "b1", "type": "book", "title": "西游记", "score": 100}],
        "total": 1,
        "offset": 0,
        "limit": 20,
    }
    try:
        with patch(
            "app.api.routes.search.search_service.advanced_search",
            return_value=fake_result,
        ) as mock_search:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/search/advanced", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["hits"][0]["title"] == "西游记"
    mock_search.assert_called_once()


@pytest.mark.asyncio
async def test_legacy_search_endpoint_still_returns_hits():
    async def fake_user():
        return SimpleNamespace(id="user-1", role="super_admin")

    app.dependency_overrides[get_current_user] = fake_user
    fake_result = {
        "hits": [{"id": "b1", "title": "西游记"}],
        "estimatedTotalHits": 1,
        "offset": 0,
        "limit": 20,
    }
    try:
        with patch(
            "app.api.routes.search.search_service.search_books",
            return_value=fake_result,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/search",
                    params={"q": "西游记", "scope": "books"},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["total"] == 1
