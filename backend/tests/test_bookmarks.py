"""Tests for bookmark endpoints."""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException

from app.api.routes.bookmarks import (
    create_bookmark,
    delete_bookmark,
    list_bookmarks,
    update_bookmark,
)


def _user(user_id="u1"):
    return SimpleNamespace(id=user_id, role="user")


def _bookmark(**overrides):
    data = {
        "id": "bm1",
        "user_id": "u1",
        "book_id": "book-1",
        "chapter_id": "ch-1",
        "position": 42,
        "note": "good part",
        "created_at": datetime(2026, 8, 18, 12, 0, 0),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_create_bookmark_requires_visible_book():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id="book-1"))

    with patch("app.api.routes.bookmarks.ensure_book_visible", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            await create_bookmark(
                SimpleNamespace(book_id="book-1", chapter_id="ch-1", position=42, note=None),
                _user(),
                db,
            )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_bookmark_rejects_chapter_of_another_book():
    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, pk: SimpleNamespace(id="book-1", book_id="other-book") if model.__name__ == "Chapter" else SimpleNamespace(id="book-1"))

    with patch("app.api.routes.bookmarks.ensure_book_visible", return_value=True):
        with pytest.raises(HTTPException) as exc_info:
            await create_bookmark(
                SimpleNamespace(book_id="book-1", chapter_id="ch-1", position=42, note=None),
                _user(),
                db,
            )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_bookmark_uses_authenticated_user():
    db = AsyncMock()
    db.get = AsyncMock(
        side_effect=lambda model, pk: SimpleNamespace(id="book-1", title="Book 1")
        if model.__name__ == "Book"
        else SimpleNamespace(id="ch-1", book_id="book-1", title="Ch1", chapter_number=1)
    )
    db.add = MagicMock()
    db.commit = AsyncMock()

    def _fake_refresh(obj):
        obj.created_at = datetime(2026, 8, 18, 12, 0, 0)

    db.refresh = AsyncMock(side_effect=_fake_refresh)

    with patch("app.api.routes.bookmarks.ensure_book_visible", return_value=True):
        await create_bookmark(
            SimpleNamespace(book_id="book-1", chapter_id="ch-1", position=42, note="x"),
            _user("u1"),
            db,
        )

    added = db.add.call_args.args[0]
    assert added.user_id == "u1"
    assert added.book_id == "book-1"
    assert added.chapter_id == "ch-1"
    assert added.position == 42
    assert added.note == "x"


@pytest.mark.asyncio
async def test_update_bookmark_scoped_to_owner():
    db = AsyncMock()
    db.get = AsyncMock(return_value=_bookmark(user_id="u2"))

    with pytest.raises(HTTPException) as exc_info:
        await update_bookmark("bm1", SimpleNamespace(note="hack", position=None), _user("u1"), db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_bookmark_scoped_to_owner():
    db = AsyncMock()
    db.get = AsyncMock(return_value=_bookmark(user_id="u2"))

    with pytest.raises(HTTPException) as exc_info:
        await delete_bookmark("bm1", _user("u1"), db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_bookmarks_queries_current_user():
    db = AsyncMock()
    db.get = AsyncMock(
        side_effect=lambda model, pk: SimpleNamespace(id="book-1", title="Book 1")
        if model.__name__ == "Book"
        else SimpleNamespace(id="ch-1", book_id="book-1", title="Ch1", chapter_number=1)
    )
    db.scalars = AsyncMock(return_value=[_bookmark()])

    with patch("app.api.routes.bookmarks.ensure_book_visible", return_value=True):
        await list_bookmarks("book-1", _user("u1"), db)

    stmt = db.scalars.call_args.args[0]
    assert "user_id" in str(stmt)
    assert "book_id" in str(stmt)


@pytest.mark.asyncio
async def test_bookmarks_require_auth(client):
    resp = await client.post("/api/bookmarks", json={"book_id": "b", "chapter_id": "c"})
    assert resp.status_code == 401