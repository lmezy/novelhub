"""Tests for reading progress endpoints."""

import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from fastapi import HTTPException

from app.api.routes.progress import get_progress


def _user(user_id="u1"):
    return SimpleNamespace(id=user_id, role="user")


def _progress(**overrides):
    data = {
        "id": "p1",
        "user_id": "u1",
        "book_id": "book-1",
        "chapter_id": "ch-5",
        "position": 42,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_get_progress_returns_saved_progress_for_current_user():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id="book-1"))
    saved = _progress()
    db.scalar = AsyncMock(return_value=saved)

    with patch("app.api.routes.progress.ensure_book_visible", return_value=True):
        result = await get_progress("book-1", _user(), db)

    assert result is saved


@pytest.mark.asyncio
async def test_get_progress_returns_none_when_no_progress():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id="book-1"))
    db.scalar = AsyncMock(return_value=None)

    with patch("app.api.routes.progress.ensure_book_visible", return_value=True):
        result = await get_progress("book-1", _user(), db)

    assert result is None


@pytest.mark.asyncio
async def test_get_progress_404_when_book_not_visible():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id="book-1"))

    with patch("app.api.routes.progress.ensure_book_visible", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            await get_progress("book-1", _user(), db)

    assert exc_info.value.status_code == 404
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_progress_queries_current_user_and_book():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id="book-1"))
    db.scalar = AsyncMock(return_value=None)

    with patch("app.api.routes.progress.ensure_book_visible", return_value=True):
        await get_progress("book-1", _user("u1"), db)

    stmt = db.scalar.call_args.args[0]
    assert "book_id" in str(stmt)
    assert "user_id" in str(stmt)


@pytest.mark.asyncio
async def test_get_progress_requires_auth(client):
    resp = await client.get("/api/progress/book-1")
    assert resp.status_code == 401
