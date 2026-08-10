import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import MissingGreenlet, SQLAlchemyError

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook
from app.models import Book, Chapter, Cookie, Source
from app.services.sync import SyncPaused, SyncService




def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    db.scalar = AsyncMock(return_value=None)
    return db

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


def test_clean_sync_tags_drops_title_and_author_fragments():
    tags = {
        "官路之谁与争锋(卷帘西风666)",
        "卷帘西风666",
        "官路之谁与争锋最新章节",
        "仙侠武侠",
    }

    result = SyncService._clean_sync_tags(
        tags,
        title="官路之谁与争锋",
        author="卷帘西风666",
    )

    assert result == ["仙侠武侠"]


def test_chapter_concurrency_uses_env_override():
    from app.core.config import settings

    with patch.object(settings, "SYNC_IGNORE_RATE_LIMIT", True):
        assert SyncService._chapter_concurrency({"concurrentRate": "2000"}) == 9


@pytest.mark.asyncio
async def test_save_tags_deduplicates_duplicate_names():
    db = _mock_db()
    db.scalars = AsyncMock(return_value=[])
    service = SyncService(db)

    tag_a = SimpleNamespace(id="t1")
    tag_b = SimpleNamespace(id="t2")
    repo = MagicMock()
    repo.get_or_create = AsyncMock(side_effect=[tag_a, tag_b])

    with patch("app.services.sync.TagRepository", return_value=repo):
        await service._save_tags("book1", ["r18", "R18", "xuanhuan", "r18"])

    assert db.add.call_count == 2
    assert repo.get_or_create.await_count == 2


def test_sync_thread_count_caps_at_legado_max():
    from app.core.config import settings, sync_thread_count

    with patch.object(settings, "SYNC_THREAD_COUNT", 32):
        assert sync_thread_count() == 9


@pytest.mark.asyncio
async def test_find_same_title_books_filters_normalized_title():
    db = _mock_db()
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
    db = _mock_db()
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
async def test_persist_cover_downloads_and_saves_local_file():
    db = _mock_db()
    service = SyncService(db)
    service.storage = MagicMock()
    service.storage.save_cover.return_value = "covers/book-1.jpg"
    book = Book(id="book-1")
    plugin = SimpleNamespace(
        fetch_cover=AsyncMock(return_value=(b"\xff\xd8\xff\xe0", "image/jpeg"))
    )
    remote_book = SimpleNamespace(cover_url="https://example.com/cover.jpg")

    remote_url = await service._persist_cover(book, plugin, remote_book)

    assert remote_url == "https://example.com/cover.jpg"
    assert book.cover == "covers/book-1.jpg"
    plugin.fetch_cover.assert_awaited_once_with("https://example.com/cover.jpg")
    service.storage.save_cover.assert_called_once_with("book-1", b"\xff\xd8\xff\xe0")


@pytest.mark.asyncio
async def test_persist_cover_falls_back_to_remote_url_when_fetch_fails():
    db = _mock_db()
    service = SyncService(db)
    service.storage = MagicMock()
    book = Book(id="book-1")
    plugin = SimpleNamespace(
        fetch_cover=AsyncMock(side_effect=RuntimeError("network down"))
    )
    remote_book = SimpleNamespace(cover_url="https://example.com/cover.jpg")

    remote_url = await service._persist_cover(book, plugin, remote_book)

    assert remote_url == "https://example.com/cover.jpg"
    assert book.cover == "https://example.com/cover.jpg"


@pytest.mark.asyncio
async def test_sync_book_continues_after_failed_chapter():
    db = _mock_db()
    db.get.return_value = _source()
    db.scalar.return_value = None
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    remote_book = RemoteBook(
        source_book_id="https://example.com/book/1",
        title="Book",
        author="Author",
        description=None,
        status=None,
        chapters=[
            RemoteChapter(
                source_chapter_id="1",
                title="Chapter 1",
                url="https://example.com/book/1.html",
                chapter_number=1,
            ),
            RemoteChapter(
                source_chapter_id="2",
                title="Chapter 2",
                url="https://example.com/book/2.html",
                chapter_number=2,
            ),
        ],
        tags=["tag1"],
    )
    plugin = AsyncMock()
    plugin.fetch_book.return_value = remote_book

    async def fake_fetch_chapter(chapter):
        if chapter.chapter_number == 1:
            raise RuntimeError("network down")
        return "content-2"

    plugin.fetch_chapter_content.side_effect = fake_fetch_chapter

    service = SyncService(db)
    service.storage = MagicMock()
    service.storage.write_metadata = MagicMock()
    service.storage.write_chapter.return_value = ("path", "hash")

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.emit"),
        patch("app.services.sync.search_service"),
        patch("app.services.auto_categorize.AutoCategorizationService"),
        patch.object(service, "_save_tags", AsyncMock()),
        patch.object(service, "_find_same_title_books", AsyncMock(return_value=[])),
        patch.object(service, "_book_tag_names", AsyncMock(return_value=[])),
    ):
        result = await service.sync_book("src1", "https://example.com/book/1")

    assert result["created_chapters"] == 1
    assert result["skipped_chapters"] == 0
    assert len(result["failed_chapters"]) == 1
    assert result["failed_chapters"][0]["chapter_number"] == 1
    assert "network down" in result["failed_chapters"][0]["error"]


@pytest.mark.asyncio
async def test_sync_book_loads_existing_tags_without_lazy_load():
    db = _mock_db()
    db.get.return_value = _source()
    db.scalar.return_value = None
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    remote_book = RemoteBook(
        source_book_id="https://example.com/book/1",
        title="Book",
        author="Author",
        description=None,
        status=None,
        chapters=[
            RemoteChapter(
                source_chapter_id="1",
                title="Chapter 1",
                url="https://example.com/book/1.html",
                chapter_number=1,
            ),
        ],
        tags=["remote"],
    )
    plugin = AsyncMock()
    plugin.fetch_book.return_value = remote_book
    plugin.fetch_chapter_content.return_value = "content"

    class LazyTagBook(Book):
        @property
        def tag_names(self):
            raise MissingGreenlet(
                "greenlet_spawn has not been called; can't call await_only() here"
            )

    book = LazyTagBook(
        id="book-1",
        source_id="src1",
        source_book_id="https://example.com/book/1",
        title="Book",
    )

    service = SyncService(db)
    service.storage = MagicMock()
    service.storage.write_metadata = MagicMock()
    service.storage.write_chapter.return_value = ("path", "hash")

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.emit"),
        patch("app.services.sync.search_service"),
        patch("app.services.auto_categorize.AutoCategorizationService"),
        patch.object(service, "_get_or_create_author", AsyncMock(return_value=MagicMock(id="author-1"))),
        patch.object(service, "_get_or_create_book", AsyncMock(return_value=(book, True))),
        patch.object(service, "_find_same_title_books", AsyncMock(return_value=[])),
        patch.object(service, "_book_tag_names", AsyncMock(return_value=["Book", "old"])),
        patch.object(service, "_save_tags", AsyncMock()),
    ):
        result = await service.sync_book("src1", "https://example.com/book/1")

        assert result["created_chapters"] == 1
        service._save_tags.assert_awaited_once_with(
            "book-1",
            ["all-ages", "old", "remote"],
        )


@pytest.mark.asyncio
async def test_sync_book_does_not_rewrite_other_source_tags():
    db = _mock_db()
    db.get.return_value = _source()
    db.scalar.return_value = None
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    remote_book = RemoteBook(
        source_book_id="https://example.com/book/1",
        title="Book",
        author="Author",
        description=None,
        status=None,
        chapters=[
            RemoteChapter(
                source_chapter_id="1",
                title="Chapter 1",
                url="https://example.com/book/1.html",
                chapter_number=1,
            ),
        ],
        tags=["remote"],
    )
    plugin = AsyncMock()
    plugin.fetch_book.return_value = remote_book
    plugin.fetch_chapter_content.return_value = "content"

    book = Book(
        id="book-1",
        source_id="src1",
        source_book_id="https://example.com/book/1",
        title="Book",
    )
    other_source_book = Book(
        id="book-2",
        source_id="src2",
        source_book_id="https://example.com/book/2",
        title="Book",
        is_r18=False,
    )

    service = SyncService(db)
    service.storage = MagicMock()
    service.storage.write_metadata = MagicMock()
    service.storage.write_chapter.return_value = ("path", "hash")

    async def fake_book_tag_names(book_id: str) -> list[str]:
        if book_id == "book-1":
            return ["Book", "old"]
        return ["other-old"]

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.emit"),
        patch("app.services.sync.search_service"),
        patch("app.services.auto_categorize.AutoCategorizationService"),
        patch.object(
            service,
            "_get_or_create_author",
            AsyncMock(return_value=MagicMock(id="author-1")),
        ),
        patch.object(
            service,
            "_get_or_create_book",
            AsyncMock(return_value=(book, True)),
        ),
        patch.object(
            service,
            "_find_same_title_books",
            AsyncMock(return_value=[other_source_book]),
        ),
        patch.object(service, "_book_tag_names", side_effect=fake_book_tag_names),
        patch.object(service, "_save_tags", AsyncMock()),
    ):
        result = await service.sync_book("src1", "https://example.com/book/1")

        assert result["created_chapters"] == 1
        service._save_tags.assert_awaited_once_with(
            "book-1",
            ["all-ages", "old", "remote"],
        )


@pytest.mark.asyncio
async def test_sync_book_reports_chapter_progress():
    db = _mock_db()
    db.get.return_value = _source()
    db.scalar.return_value = None
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    remote_book = RemoteBook(
        source_book_id="https://example.com/book/1",
        title="Book",
        author="Author",
        description=None,
        status=None,
        chapters=[
            RemoteChapter(
                source_chapter_id="1",
                title="Chapter 1",
                url="https://example.com/book/1.html",
                chapter_number=1,
            ),
            RemoteChapter(
                source_chapter_id="2",
                title="Chapter 2",
                url="https://example.com/book/2.html",
                chapter_number=2,
            ),
        ],
        tags=["remote"],
    )
    plugin = AsyncMock()
    plugin.fetch_book.return_value = remote_book
    plugin.fetch_chapter_content.return_value = "content"

    service = SyncService(db)
    service.storage = MagicMock()
    service.storage.write_metadata = MagicMock()
    service.storage.write_chapter.return_value = ("path", "hash")
    progress_cb = AsyncMock()

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.emit"),
        patch("app.services.sync.search_service"),
        patch("app.services.auto_categorize.AutoCategorizationService"),
        patch.object(service, "_get_or_create_author", AsyncMock(return_value=MagicMock(id="author-1"))),
        patch.object(service, "_get_or_create_book", AsyncMock(return_value=(MagicMock(id="book-1", title="Book", is_r18=False), True))),
        patch.object(service, "_find_same_title_books", AsyncMock(return_value=[])),
        patch.object(service, "_book_tag_names", AsyncMock(return_value=[])),
        patch.object(service, "_save_tags", AsyncMock()),
    ):
        result = await service.sync_book(
            "src1",
            "https://example.com/book/1",
            progress_cb=progress_cb,
        )

    assert result["created_chapters"] == 2
    assert progress_cb.await_count == 2
    last = progress_cb.await_args.args[0]
    assert last["created_chapters"] == 2
    assert last["total_chapters"] == 2


@pytest.mark.asyncio
async def test_sync_book_continues_after_database_error():
    db = _mock_db()
    db.get.return_value = _source()
    db.scalar.return_value = None
    db.rollback = AsyncMock()
    commit_calls = 0

    async def fake_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 3:
            raise SQLAlchemyError("fk")

    db.commit = AsyncMock(side_effect=fake_commit)
    db.flush = AsyncMock()
    db.add = MagicMock()

    remote_book = RemoteBook(
        source_book_id="https://example.com/book/1",
        title="Book",
        author="Author",
        description=None,
        status=None,
        chapters=[
            RemoteChapter(
                source_chapter_id="1",
                title="Chapter 1",
                url="https://example.com/book/1.html",
                chapter_number=1,
            ),
            RemoteChapter(
                source_chapter_id="2",
                title="Chapter 2",
                url="https://example.com/book/2.html",
                chapter_number=2,
            ),
            RemoteChapter(
                source_chapter_id="3",
                title="Chapter 3",
                url="https://example.com/book/3.html",
                chapter_number=3,
            ),
        ],
        tags=["remote"],
    )
    plugin = AsyncMock()
    plugin.fetch_book.return_value = remote_book
    plugin.fetch_chapter_content.return_value = "content"

    service = SyncService(db)
    service.storage = MagicMock()
    service.storage.write_metadata = MagicMock()
    service.storage.write_chapter.return_value = ("path", "hash")

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.emit"),
        patch("app.services.sync.search_service"),
        patch("app.services.auto_categorize.AutoCategorizationService"),
        patch.object(service, "_get_or_create_author", AsyncMock(return_value=MagicMock(id="author-1"))),
        patch.object(service, "_get_or_create_book", AsyncMock(return_value=(MagicMock(id="book-1", title="Book", is_r18=False), True))),
        patch.object(service, "_find_same_title_books", AsyncMock(return_value=[])),
        patch.object(service, "_book_tag_names", AsyncMock(return_value=[])),
        patch.object(service, "_save_tags", AsyncMock()),
    ):
        result = await service.sync_book("src1", "https://example.com/book/1")

    assert result["created_chapters"] == 2
    assert len(result["failed_chapters"]) == 1
    assert "fk" in result["failed_chapters"][0]["error"]
    assert plugin.fetch_chapter_content.await_count == 3


@pytest.mark.asyncio
async def test_reconcile_chapter_ids_upgrades_legacy_numeric_ids():
    legacy1 = Chapter(
        id="c1",
        book_id="book-1",
        chapter_number=1,
        source_chapter_id="1",
    )
    legacy2 = Chapter(
        id="c2",
        book_id="book-1",
        chapter_number=2,
        source_chapter_id="2",
    )
    db = AsyncMock()
    db.scalars = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [legacy1, legacy2])
    )
    db.flush = AsyncMock()
    service = SyncService(db)

    result = await service._reconcile_chapter_ids(
        "book-1",
        [
            RemoteChapter(
                source_chapter_id="https://example.com/book/1.html",
                title="Chapter 1",
                url="https://example.com/book/1.html",
                chapter_number=1,
            ),
            RemoteChapter(
                source_chapter_id="https://example.com/book/2.html",
                title="Chapter 2",
                url="https://example.com/book/2.html",
                chapter_number=2,
            ),
        ],
    )

    assert result == {
        "https://example.com/book/1.html",
        "https://example.com/book/2.html",
    }
    assert legacy1.source_chapter_id == "https://example.com/book/1.html"
    assert legacy2.source_chapter_id == "https://example.com/book/2.html"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_chapter_ids_keeps_existing_urls():
    existing = Chapter(
        id="c1",
        book_id="book-1",
        chapter_number=1,
        source_chapter_id="https://example.com/book/1.html",
    )
    db = AsyncMock()
    db.scalars = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [existing])
    )
    db.flush = AsyncMock()
    service = SyncService(db)

    result = await service._reconcile_chapter_ids(
        "book-1",
        [
            RemoteChapter(
                source_chapter_id="https://example.com/book/1.html",
                title="Chapter 1",
                url="https://example.com/book/1.html",
                chapter_number=1,
            ),
        ],
    )

    assert result == {"https://example.com/book/1.html"}
    assert existing.source_chapter_id == "https://example.com/book/1.html"


@pytest.mark.asyncio
async def test_ensure_book_row_restores_missing_book():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.commit = AsyncMock()
    service = SyncService(db)

    ok = await service._ensure_book_row(
        "book-1",
        {
            "source_id": "src1",
            "author_id": "author-1",
            "source_book_id": "https://example.com/book/1",
            "title": "Book",
            "description": None,
            "status": None,
            "is_r18": False,
        },
    )

    assert ok is True
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_book_row_keeps_existing_book():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value="book-1")
    db.add = MagicMock()
    db.commit = AsyncMock()
    service = SyncService(db)

    ok = await service._ensure_book_row("book-1", {"title": "Book"})

    assert ok is True
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_resync_chapter_updates_existing_row():
    chapter = Chapter(
        id="c1",
        book_id="b1",
        chapter_number=1,
        source_chapter_id="https://example.com/book/1.html",
        title="Chapter 1",
        content_path="/old/000001.md",
        hash="old-hash",
    )
    book = Book(
        id="b1",
        source_id="src1",
        source_book_id="https://example.com/book/1",
        title="Book",
        author_id="a1",
    )
    book.author = SimpleNamespace(name="Author")
    source = _source()
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[chapter, book, source])
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()

    remote_book = RemoteBook(
        source_book_id="https://example.com/book/1",
        title="Book",
        author="Author",
        description=None,
        status=None,
        chapters=[
            RemoteChapter(
                source_chapter_id="https://example.com/book/1.html",
                title="Chapter 1",
                url="https://example.com/book/1.html",
                chapter_number=1,
            ),
        ],
    )
    plugin = AsyncMock()
    plugin.fetch_book.return_value = remote_book
    plugin.fetch_chapter_content.return_value = "new content"

    service = SyncService(db)
    service.storage = MagicMock()
    service.storage.write_chapter.return_value = ("/new/000001.md", "new-hash")

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.emit"),
        patch("app.services.sync.search_service") as search_mock,
    ):
        result = await service.resync_chapter("c1")

    assert result["updated"] is True
    assert chapter.content_path == "/new/000001.md"
    assert chapter.hash == "new-hash"
    assert chapter.source_chapter_id == "https://example.com/book/1.html"
    search_mock.index_chapter.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_resync_chapter_matches_legacy_id_by_number():
    chapter = Chapter(
        id="c1",
        book_id="b1",
        chapter_number=7,
        source_chapter_id="7",
        title="Chapter 7",
        content_path="/old/000007.md",
        hash="old-hash",
    )
    book = Book(
        id="b1",
        source_id="src1",
        source_book_id="https://example.com/book/1",
        title="Book",
        author_id="a1",
    )
    book.author = SimpleNamespace(name="Author")
    source = _source()
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[chapter, book, source])
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()

    remote_book = RemoteBook(
        source_book_id="https://example.com/book/1",
        title="Book",
        author="Author",
        description=None,
        status=None,
        chapters=[
            RemoteChapter(
                source_chapter_id="https://example.com/book/7.html",
                title="Chapter 7",
                url="https://example.com/book/7.html",
                chapter_number=7,
            ),
        ],
    )
    plugin = AsyncMock()
    plugin.fetch_book.return_value = remote_book
    plugin.fetch_chapter_content.return_value = "fixed content"

    service = SyncService(db)
    service.storage = MagicMock()
    service.storage.write_chapter.return_value = ("/new/000007.md", "new-hash")

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.emit"),
        patch("app.services.sync.search_service"),
    ):
        result = await service.resync_chapter("c1")

    assert result["updated"] is True
    assert chapter.source_chapter_id == "https://example.com/book/7.html"
    assert chapter.content_path == "/new/000007.md"
    assert chapter.hash == "new-hash"


@pytest.mark.asyncio
async def test_sync_bookshelf_rolls_back_and_continues_after_failure():
    db = _mock_db()
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
        "failed_chapters": [],
    }


@pytest.mark.asyncio
async def test_sync_bookshelf_skips_non_http_urls():
    db = _mock_db()
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
    db = _mock_db()
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

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=_mock_db())
    session.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.SessionLocal", return_value=session),
        patch.object(
            SyncService,
            "sync_book",
            AsyncMock(return_value={"book_id": "x", "created_chapters": 2, "skipped_chapters": 1}),
        ) as sync_book_mock,
    ):
        service = SyncService(db)
        result = await service.discover_and_sync_all("src1", max_pages=10)

    assert sync_book_mock.await_count == 3
    assert result["pages_checked"] == 2
    assert result["books_found"] == 3
    assert result["books_synced"] == 3
    assert result["chapters_created"] == 6
    assert result["chapters_skipped"] == 3


@pytest.mark.asyncio
async def test_discover_and_sync_all_unlimited_continues_until_empty():
    db = _mock_db()
    db.get.return_value = _source()
    db.rollback = AsyncMock()

    plugin = AsyncMock()
    plugin.set_cookie = MagicMock()
    plugin.discover_books.side_effect = [
        [
            RemoteShelfBook(
                source_book_id="1.html",
                title="1",
                author="Author",
                url="https://example.com/1.html",
            )
        ],
        [
            RemoteShelfBook(
                source_book_id="2.html",
                title="2",
                author="Author",
                url="https://example.com/2.html",
            )
        ],
        [
            RemoteShelfBook(
                source_book_id="3.html",
                title="3",
                author="Author",
                url="https://example.com/3.html",
            )
        ],
        [
            RemoteShelfBook(
                source_book_id="4.html",
                title="4",
                author="Author",
                url="https://example.com/4.html",
            )
        ],
        [],
    ]

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=_mock_db())
    session.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.SessionLocal", return_value=session),
        patch.object(
            SyncService,
            "sync_book",
            AsyncMock(return_value={"book_id": "x", "created_chapters": 1, "skipped_chapters": 0}),
        ) as sync_book_mock,
    ):
        service = SyncService(db)
        result = await service.discover_and_sync_all("src1", max_pages=0)

    assert sync_book_mock.await_count == 4
    assert result["pages_checked"] == 4
    assert result["books_found"] == 4
    assert result["next_page"] == 5


@pytest.mark.asyncio
async def test_discover_and_sync_all_page_batch_requeues():
    db = _mock_db()
    db.get.return_value = _source()
    db.rollback = AsyncMock()

    plugin = AsyncMock()
    plugin.set_cookie = MagicMock()
    plugin.discover_books.side_effect = [
        [
            RemoteShelfBook(
                source_book_id="1.html",
                title="1",
                author="Author",
                url="https://example.com/1.html",
            ),
            RemoteShelfBook(
                source_book_id="2.html",
                title="2",
                author="Author",
                url="https://example.com/2.html",
            ),
        ],
        [
            RemoteShelfBook(
                source_book_id="3.html",
                title="3",
                author="Author",
                url="https://example.com/3.html",
            ),
        ],
    ]

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=_mock_db())
    session.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.SessionLocal", return_value=session),
        patch.object(
            SyncService,
            "sync_book",
            AsyncMock(return_value={"book_id": "x", "created_chapters": 1, "skipped_chapters": 0}),
        ),
    ):
        service = SyncService(db)
        result = await service.discover_and_sync_all(
            "src1",
            max_pages=10,
            page_batch_size=1,
        )

    assert result["pages_checked"] == 1
    assert result["books_found"] == 2
    assert result["done"] is False
    assert result["next_page"] == 2


@pytest.mark.asyncio
async def test_discover_and_sync_all_starts_next_book_when_one_slot_frees():
    db = _mock_db()
    db.get.return_value = _source()
    db.rollback = AsyncMock()

    plugin = AsyncMock()
    plugin.set_cookie = MagicMock()
    plugin.discover_books.return_value = [
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
        RemoteShelfBook(
            source_book_id="c.html",
            title="C",
            author="Author",
            url="https://example.com/c.html",
        ),
        RemoteShelfBook(
            source_book_id="d.html",
            title="D",
            author="Author",
            url="https://example.com/d.html",
        ),
    ]

    release_slow = asyncio.Event()
    fourth_started = asyncio.Event()
    starts: list[str] = []

    async def fake_sync_book(source_id: str, url: str, **kwargs):
        starts.append(url)
        if url.endswith("a.html"):
            await release_slow.wait()
        if url.endswith("d.html"):
            fourth_started.set()
        return {"book_id": url, "created_chapters": 1, "skipped_chapters": 0}

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=_mock_db())
    session.__aexit__ = AsyncMock(return_value=False)
    from app.core.config import settings
    with (
        patch.object(settings, "SYNC_BOOK_CONTINUOUS", True),
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.SessionLocal", return_value=session),
        patch.object(
            SyncService,
            "sync_book",
            AsyncMock(side_effect=fake_sync_book),
        ),
    ):
        service = SyncService(db)
        discover_task = asyncio.create_task(
            service.discover_and_sync_all("src1", max_pages=1)
        )
        await asyncio.wait_for(fourth_started.wait(), timeout=2)
        assert set(starts[:3]) == {
            "https://example.com/a.html",
            "https://example.com/b.html",
            "https://example.com/c.html",
        }
        assert starts[-1] == "https://example.com/d.html"
        release_slow.set()
        result = await asyncio.wait_for(discover_task, timeout=2)

    assert result["books_synced"] == 4


@pytest.mark.asyncio
async def test_discover_and_sync_all_batch_waits_for_full_batch_by_default():
    db = _mock_db()
    db.get.return_value = _source()
    db.rollback = AsyncMock()

    plugin = AsyncMock()
    plugin.set_cookie = MagicMock()
    plugin.discover_books.return_value = [
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
        RemoteShelfBook(
            source_book_id="c.html",
            title="C",
            author="Author",
            url="https://example.com/c.html",
        ),
        RemoteShelfBook(
            source_book_id="d.html",
            title="D",
            author="Author",
            url="https://example.com/d.html",
        ),
    ]

    release_slow = asyncio.Event()
    b_done = asyncio.Event()
    c_done = asyncio.Event()
    fourth_started = asyncio.Event()
    starts: list[str] = []

    async def fake_sync_book(source_id: str, url: str, **kwargs):
        starts.append(url)
        if url.endswith("a.html"):
            await release_slow.wait()
        elif url.endswith("b.html"):
            b_done.set()
        elif url.endswith("c.html"):
            c_done.set()
        elif url.endswith("d.html"):
            fourth_started.set()
        return {"book_id": url, "created_chapters": 1, "skipped_chapters": 0}

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=_mock_db())
    session.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.SessionLocal", return_value=session),
        patch.object(
            SyncService,
            "sync_book",
            AsyncMock(side_effect=fake_sync_book),
        ),
    ):
        service = SyncService(db)
        discover_task = asyncio.create_task(
            service.discover_and_sync_all("src1", max_pages=1)
        )
        await asyncio.wait_for(
            asyncio.gather(b_done.wait(), c_done.wait()),
            timeout=2,
        )
        await asyncio.sleep(0.05)
        assert "https://example.com/d.html" not in starts
        release_slow.set()
        await asyncio.wait_for(fourth_started.wait(), timeout=2)
        result = await asyncio.wait_for(discover_task, timeout=2)

    assert result["books_synced"] == 4
    assert len(starts) == 4


@pytest.mark.asyncio
async def test_sync_book_checkpoint_stops_before_next_chapter():
    db = _mock_db()
    db.get.return_value = _source()
    db.scalar.return_value = None
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    remote_book = RemoteBook(
        source_book_id="https://example.com/book/1",
        title="Book",
        author="Author",
        description=None,
        status=None,
        chapters=[
            RemoteChapter(
                source_chapter_id="1",
                title="Chapter 1",
                url="https://example.com/book/1.html",
                chapter_number=1,
            ),
            RemoteChapter(
                source_chapter_id="2",
                title="Chapter 2",
                url="https://example.com/book/2.html",
                chapter_number=2,
            ),
        ],
        tags=[],
    )
    plugin = AsyncMock()
    plugin.fetch_book.return_value = remote_book
    plugin.fetch_chapter_content.side_effect = ["content-1", "content-2"]

    service = SyncService(db)
    service.storage = MagicMock()
    service.storage.write_metadata = MagicMock()
    service.storage.write_chapter.return_value = ("path", "hash")

    checkpoint_calls = 0

    async def checkpoint() -> None:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls >= 3:
            raise SyncPaused("paused")

    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.emit"),
        patch("app.services.sync.search_service"),
        patch("app.services.auto_categorize.AutoCategorizationService"),
        patch.object(service, "_save_tags", AsyncMock()),
        patch.object(service, "_find_same_title_books", AsyncMock(return_value=[])),
        patch.object(service, "_book_tag_names", AsyncMock(return_value=[])),
    ):
        with pytest.raises(SyncPaused):
            await service.sync_book(
                "src1",
                "https://example.com/book/1",
                checkpoint_cb=checkpoint,
            )

    assert checkpoint_calls >= 3
