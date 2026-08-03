from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.crawler.base import RemoteBook, RemoteShelfBook
from app.models import Cookie, Source
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


@pytest.mark.asyncio
async def test_sync_book_uses_fallback_title_when_remote_title_missing():
    db = AsyncMock()
    db.get.return_value = _source()
    db.scalar.return_value = None
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

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

    storage = MagicMock()

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.search_service") as search_mock,
    ):
        service = SyncService(db, storage=storage)
        result = await service.sync_book("src1", "https://example.com/novel/33927.html")

    assert result["book_id"]
    added_books = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args and call.args[0].__class__.__name__ == "Book"
    ]
    assert added_books
    assert added_books[0].title == "33927.html"
    assert storage.write_metadata.call_args.args[0] == "Unknown"
    assert storage.write_metadata.call_args.args[1] == "33927.html"
    search_mock.index_book.assert_called_once()


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
