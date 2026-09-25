"""Search results carry the book's cover.

The search index stores no cover: a re-sync or a user-chosen cover
(``PUT /books/{id}/cover``) writes only the ``books`` row, so an indexed copy
would go stale.  ``/api/search`` therefore attaches the cover to the page it
returns, in the same query that already decides visibility.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.services.auth import get_current_user_or_token


def _db_with_books(rows):
    """A fake ``AsyncSession`` whose one ``execute`` returns ``rows``."""
    result = MagicMock()
    result.all.return_value = rows
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


PAYLOAD = {
    "conditions": [{"field": "title", "mode": "exact", "value": "西游记"}],
    "match": "and",
    "scope": "books",
    "offset": 0,
    "limit": 20,
}


async def _post_search(db, role="super_admin", user_id="user-1"):
    """Run one ``/search/advanced`` call against a fake session and user."""

    async def fake_user():
        return SimpleNamespace(id=user_id, role=role)

    app.dependency_overrides[get_current_user_or_token] = fake_user
    app.dependency_overrides[get_db] = lambda: db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/search/advanced", json=PAYLOAD)
    finally:
        app.dependency_overrides.clear()


def _fake_result(hits):
    return {"hits": hits, "total": len(hits), "offset": 0, "limit": 20}


@pytest.mark.asyncio
async def test_hits_carry_a_locally_stored_cover_via_the_cover_route():
    fake = _fake_result([{"id": "b1", "type": "book", "title": "西游记"}])
    # ``books.cover`` holds the storage path for a downloaded cover.
    db = _db_with_books([("b1", None, True, "covers/b1.jpg", None)])
    with patch(
        "app.api.routes.search.search_service.advanced_search",
        return_value=fake,
    ):
        resp = await _post_search(db)

    assert resp.status_code == 200
    hit = resp.json()["hits"][0]
    assert hit["cover"] == "/api/books/b1/cover"
    assert hit["cover_url"] == "/api/books/b1/cover"


@pytest.mark.asyncio
async def test_a_user_chosen_cover_wins_over_the_source_cover():
    fake = _fake_result([{"id": "b1", "type": "book", "title": "西游记"}])
    db = _db_with_books([("b1", None, True, "covers/b1.jpg", "https://cdn.example/x.jpg")])
    with patch(
        "app.api.routes.search.search_service.advanced_search",
        return_value=fake,
    ):
        resp = await _post_search(db)

    assert resp.json()["hits"][0]["cover"] == "https://cdn.example/x.jpg"


@pytest.mark.asyncio
async def test_chapter_hits_use_the_cover_of_their_book():
    fake = _fake_result([
        {
            "id": "c1",
            "book_id": "b1",
            "type": "chapter",
            "title": "第一回",
            "book_title": "西游记",
        },
    ])
    db = _db_with_books([("b1", None, True, "https://remote.example/cover.jpg", None)])
    with patch(
        "app.api.routes.search.search_service.advanced_search",
        return_value=fake,
    ):
        resp = await _post_search(db)

    hit = resp.json()["hits"][0]
    # A remote cover URL is served as it is; only local files go through the route.
    assert hit["cover"] == "https://remote.example/cover.jpg"


@pytest.mark.asyncio
async def test_a_book_without_a_cover_gets_no_cover_field():
    fake = _fake_result([{"id": "b1", "type": "book", "title": "西游记"}])
    db = _db_with_books([("b1", None, True, None, None)])
    with patch(
        "app.api.routes.search.search_service.advanced_search",
        return_value=fake,
    ):
        resp = await _post_search(db)

    # No cover means no ``<img>`` pointed at a guaranteed 404; the list falls
    # back to its title placeholder.
    assert "cover" not in resp.json()["hits"][0]


@pytest.mark.asyncio
async def test_invisible_books_are_still_filtered_out():
    """The cover lookup is the visibility lookup -- one query, both jobs."""
    fake = {
        "hits": [
            {"id": "b1", "type": "book", "title": "公开的书"},
            {"id": "b2", "type": "book", "title": "别人的私人书"},
        ],
        "total": 500,
        "offset": 0,
        "limit": 20,
    }
    db = _db_with_books([
        ("b1", None, True, "covers/b1.jpg", None),
        ("b2", "someone-else", False, "covers/b2.jpg", None),
    ])
    with patch(
        "app.api.routes.search.search_service.advanced_search",
        return_value=fake,
    ):
        resp = await _post_search(db, role="user")

    body = resp.json()
    hits = body["hits"]
    assert [hit["id"] for hit in hits] == ["b1"]
    assert hits[0]["cover"] == "/api/books/b1/cover"
    # The real count reaches non-admins too: clamping it to ``len(hits)`` made
    # the total never exceed one page, so the client disabled "下一页" forever
    # and showed "1 / 1" for a 500-hit search.
    assert body["total"] == 500


@pytest.mark.asyncio
async def test_a_failing_cover_lookup_still_serves_the_hits():
    """Decoration must not be able to break the search itself."""
    fake = _fake_result([{"id": "b1", "type": "book", "title": "西游记"}])
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("database is down"))
    with patch(
        "app.api.routes.search.search_service.advanced_search",
        return_value=fake,
    ):
        resp = await _post_search(db)

    assert resp.status_code == 200
    assert [hit["id"] for hit in resp.json()["hits"]] == ["b1"]


@pytest.mark.asyncio
async def test_the_legacy_search_endpoint_also_reports_the_real_total():
    """``GET /api/search`` shares the rule, so a non-admin can page there too."""

    async def fake_user():
        return SimpleNamespace(id="user-1", role="user")

    app.dependency_overrides[get_current_user_or_token] = fake_user
    db = _db_with_books([("b1", None, True, "covers/b1.jpg", None)])
    app.dependency_overrides[get_db] = lambda: db
    fake = {
        "hits": [{"id": "b1", "title": "西游记"}],
        "estimatedTotalHits": 321,
        "offset": 0,
        "limit": 20,
    }
    try:
        with patch(
            "app.api.routes.search.search_service.search_books",
            return_value=fake,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/search", params={"q": "西游记", "scope": "books"},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["total"] == 321
