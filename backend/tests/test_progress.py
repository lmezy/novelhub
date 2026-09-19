"""Tests for reading progress endpoints."""

from datetime import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from fastapi import HTTPException

from app.api.routes.progress import get_progress
from app.schemas.progress import ReadingProgressOut


def _user(user_id="u1"):
    return SimpleNamespace(id=user_id, role="user")


def _admin(user_id="u1"):
    return SimpleNamespace(id=user_id, role="super_admin")


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


def test_progress_out_accepts_a_cleared_chapter_pointer():
    """``chapter_id`` is ``ON DELETE SET NULL``, so it really can be NULL.

    Re-syncing a book deletes its stale chapters and clears the progress pointer
    (migration 0031).  While the response model declared ``chapter_id: str``,
    every ``GET /api/progress`` for a user holding such a row answered 500
    ("Input should be a valid string") -- the home page's 继续阅读 list.
    """
    from app.schemas.progress import ReadingProgressOut

    payload = ReadingProgressOut(
        id="p1", user_id="u1", book_id="book-1", chapter_id=None,
        position=42, updated_at=datetime(2026, 9, 19, 10, 0, 0),
    )

    assert payload.chapter_id is None


@pytest.mark.asyncio
async def test_list_progress_serializes_a_cleared_chapter_pointer():
    """The endpoint must render a NULL pointer, not fail response validation."""
    from datetime import datetime

    from app.api.routes.progress import list_progress
    from app.models import ReadingProgress

    row = ReadingProgress(
        id="p1", user_id="u1", book_id="book-1", chapter_id=None, position=42,
    )
    row.updated_at = datetime(2026, 9, 19, 10, 0, 0)
    db = AsyncMock()
    db.scalars.return_value = [row]

    result = await list_progress("u1", _admin(), db)

    assert ReadingProgressOut.model_validate(result[0]).chapter_id is None
