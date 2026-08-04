from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.crawler.base import RemoteBook, RemoteShelfBook
from app.models import Book, Cookie, Source
from app.services.sync import SyncService


def _source(source_id: str = "src1") -> Source:
    return Source(
        id=source_id,
        name="test source",
        url="https://example.com",
        plugin_name="yuedu",
        enabled=True,
        config={},
    )


def test_normalize_title_for_match():
    assert SyncService._normalize_title_for_match("《剑来》") == "剑来"
    assert SyncService._normalize_title_for_match("剑来（全文）") == "剑来全文"
    assert SyncService._normalize_title_for_match(" 剑来 ") == "剑来"


@pytest.mark.asyncio
async def test_find_same_title_books_filters_normalized_title():
    db = AsyncMock()
    db.scalars.return_value = MagicMock()
    db.scalars.return_value.all.return_value = [
        Book(id="a", source_id="s1", title="《剑来》"),
        Book(id="b", source_id="s2", title=" 剑来 "),
        Book(id="c", source_id="s3", title="凡人修仙传"),
    ]
    service = SyncService(db)

    result = await service._find_same_title_books(
        Book(id="x", source_id="s0", title="剑来")
    )

    assert [b.id for b in result] == ["a", "b"]


@pytest.mark.asyncio
async def test_sync_book_rejects_empty_remote_book():
    db = AsyncMock()
    db.get.return_value = _source()

    remote_book = RemoteBook(
        source_book_id="33927.html",
        title=None,
        author=None,
        description=None,
        status=None,
        chapters=[],
    )
    plugin = AsyncMock()
    plugin.fetch_book.return_value = remote_book

    with patch("app.services.sync.get_plugin", return_value=plugin):
        service = SyncService(db)
        with pytest.raises(ValueError, match="no usable metadata/chapters"):
            await service.sync_book("src1", "https://example.com/novel/33927.html")


@pytest.mark.asyncio
async def test_sync_bookshelf_rolls_back_and_continues_after_failure():
    db = AsyncMock()
    db.get.return_value = _source()
    db.scalar.return_value = Cookie(
        id="cookie-1",
        source="src1",
        cookie_data="plain-cookie",
    )
    db.rollback = AsyncMock()

    plugin = AsyncMock()
    plugin.fetch_bookshelf.return_value = [
        RemoteShelfBook(
            source_book_id="book-1.html",
            title="Book 1",
            author="Author",
            url="https://example.com/novel/book-1.html",
        ),
        RemoteShelfBook(
            source_book_id="book-2.html",
            title="Book 2",
            author="Author",
            url="https://example.com/novel/book-2.html",
        ),
    ]
    plugin.set_cookie = MagicMock()

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.safe_decrypt_cookie", return_value="cookie"),
    ):
        service = SyncService(db)
        with patch.object(
            service,
            "sync_book",
            side_effect=[
                RuntimeError("boom"),
                {"book_id": "book-2", "created_chapters": 1, "skipped_chapters": 0},
            ],
        ) as sync_book_mock:
            result = await service.sync_bookshelf("src1")

    assert sync_book_mock.await_count == 2
    assert db.rollback.await_count == 1
    assert result["results"][0]["status"] == "failed"
    assert result["results"][1] == {
        "book_id": "book-2",
        "status": "ok",
        "created_chapters": 1,
        "skipped_chapters": 0,
    }


@pytest.mark.asyncio
async def test_sync_bookshelf_skips_non_http_urls():
    db = AsyncMock()
    db.get.return_value = _source()
    db.scalar.return_value = Cookie(
        id="cookie-1",
        source="src1",
        cookie_data="plain-cookie",
    )

    plugin = AsyncMock()
    plugin.fetch_bookshelf.return_value = [
        RemoteShelfBook(
            source_book_id="javascript:;",
            title="排行",
            author="Unknown",
            url="javascript:;",
        ),
        RemoteShelfBook(
            source_book_id="book-2.html",
            title="Book 2",
            author="Author",
            url="https://example.com/novel/book-2.html",
        ),
    ]
    plugin.set_cookie = MagicMock()

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.safe_decrypt_cookie", return_value="cookie"),
    ):
        service = SyncService(db)
        with patch.object(
            service,
            "sync_book",
            return_value={"book_id": "book-2", "created_chapters": 1, "skipped_chapters": 0},
        ) as sync_book_mock:
            result = await service.sync_bookshelf("src1")

    assert sync_book_mock.await_count == 1
    assert result["results"][0]["status"] == "skipped"
    assert result["results"][0]["error"] == "Unsupported URL"
    assert result["results"][1]["status"] == "ok"


@pytest.mark.asyncio
async def test_discover_and_sync_all_dedupes_and_stops_on_empty():
    db = AsyncMock()
    db.get.return_value = _source()
    db.rollback = AsyncMock()

    plugin = AsyncMock()
    plugin.set_cookie = MagicMock()
    plugin.discover_books.side_effect = [
        [
            RemoteShelfBook(
                source_book_id="a.html",
                title="A",
                author="Author",
                url="https://example.com/a.html",
            ),
            RemoteShelfBook(
                source_book_id="b.html",
                title="B",
                author="Author",
                url="https://example.com/b.html",
            ),
        ],
        [
            RemoteShelfBook(
                source_book_id="b.html",
                title="B",
                author="Author",
                url="https://example.com/b.html",
            ),
            RemoteShelfBook(
                source_book_id="c.html",
                title="C",
                author="Author",
                url="https://example.com/c.html",
            ),
        ],
        [],
    ]

    with patch("app.services.sync.get_plugin", return_value=plugin):
        service = SyncService(db)
        with patch.object(
            service,
            "sync_book",
            return_value={"book_id": "x", "created_chapters": 2, "skipped_chapters": 1},
        ) as sync_book_mock:
            result = await service.discover_and_sync_all("src1", max_pages=10)

    assert sync_book_mock.await_count == 3
    assert result["pages_checked"] == 2
    assert result["books_found"] == 3
    assert result["books_synced"] == 3
    assert result["chapters_created"] == 6
    assert result["chapters_skipped"] == 3
