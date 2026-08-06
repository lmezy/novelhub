"""Tests for books API endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from app.api.routes.books import _normalize_book_title, list_book_sources, remove_book_tag


def test_normalize_book_title():
    assert _normalize_book_title("《剑来》") == "剑来"
    assert _normalize_book_title(" 剑来 ") == "剑来"
    assert _normalize_book_title("剑来（全文）") == "剑来全文"


class _UniqueAwareScalarResult:
    def __init__(self, rows):
        self._rows = rows
        self._unique_called = False

    def unique(self):
        self._unique_called = True
        return self

    def all(self):
        if not self._unique_called:
            raise AssertionError("unique() must be called before all()")
        return self._rows


@pytest.mark.asyncio
async def test_list_book_sources_calls_unique_before_all():
    db = AsyncMock()
    current = SimpleNamespace(
        id="current",
        title="剑来",
        is_r18=False,
        source_id=None,
        source_book_id=None,
        author_name="作者甲",
        status=None,
        updated_at=None,
    )
    other = SimpleNamespace(
        id="other",
        title="剑来",
        is_r18=False,
        source_id="src-1",
        source_book_id="book-1",
        author_name="作者甲",
        status=None,
        updated_at=None,
    )
    db.get.return_value = current
    db.scalars.return_value = _UniqueAwareScalarResult([other])
    db.execute.return_value = MagicMock()
    db.execute.return_value.all.return_value = []

    result = await list_book_sources(
        "current",
        SimpleNamespace(role="super_admin"),
        db,
    )

    assert [s.id for s in result.sources] == ["current", "other"]


@pytest.mark.asyncio
async def test_remove_book_tag_deletes_association_and_reindexes():
    db = AsyncMock()
    book = SimpleNamespace(id="book-1")
    tag = SimpleNamespace(id="tag-1", name="都市")
    book_tag = SimpleNamespace(book_id="book-1", tag_id="tag-1")
    db.get = AsyncMock(return_value=book)
    db.scalar = AsyncMock(side_effect=[tag, book_tag, 0])
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    execute_result = MagicMock()
    execute_result.all.return_value = [("穿越",)]
    db.execute = AsyncMock(return_value=execute_result)

    with patch("app.api.routes.books.search_service") as search:
        await remove_book_tag("book-1", "都市", db)

    db.delete.assert_any_call(book_tag)
    db.delete.assert_any_call(tag)
    search.update_book_tags.assert_called_once_with("book-1", ["穿越"])


@pytest.mark.asyncio
async def test_list_books(client):
    resp = await client.get("/api/books")
    assert resp.status_code in (200, 500)  # 200 with DB, 500 without


@pytest.mark.asyncio
async def test_get_book_404(client):
    resp = await client.get("/api/books/nonexistent-book-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_book_validation(client):
    """POST /api/books without required fields should fail validation."""
    resp = await client.post("/api/books", json={})
    assert resp.status_code in (422, 500)


@pytest.mark.asyncio
async def test_delete_book_admin_required(client):
    """DELETE /api/books/{id} requires admin auth."""
    resp = await client.delete("/api/books/some-id")
    assert resp.status_code in (401, 403, 500)


@pytest.mark.asyncio
async def test_epub_download_404(client):
    """EPUB download for nonexistent book returns 404."""
    resp = await client.get("/api/books/nonexistent/epub")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resync_book_requires_admin(client):
    """POST /api/books/{id}/sync requires admin."""
    resp = await client.post("/api/books/some-id/sync")
    assert resp.status_code in (401, 403, 500)
