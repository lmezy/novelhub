"""Exercise metadata correction and chapter preservation through book sync."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.crawler.base import RemoteBook, RemoteChapter
from app.models import Book, Chapter, Source
from app.services.content_quality import foreign_synopsis
from app.services.storage import BookStorage
from app.services.sync import SyncService


BLURB = "灯塔熄灭三年后，送信人终于在海岸找到失踪的航海图。遥远的小岛上，只有一位钟表匠记得船队当年出港的时间。"
PROSE = "这是主角沿着石阶走进城门，第一次看清城中街巷的情景。" * 50
GALLERY = "![第一页](https://example.test/a.jpg)\n![第二页](https://example.test/b.jpg)"


@pytest.mark.asyncio
async def test_resync_metadata_and_foreign_synopsis(session_factory, tmp_path):
    storage = BookStorage(str(tmp_path))
    book_url = "https://example.test/books/8"
    remote = RemoteChapter(book_url + "/1", "第一章", book_url + "/1", 1)
    metadata = RemoteBook(book_url, "都市夜航", "林", None, None, [remote],
                          tags=["《都市夜航》", "作者：林", "都市", "林间"])
    fetch = AsyncMock(return_value=PROSE)
    plugin = SimpleNamespace(fetch_book=AsyncMock(return_value=metadata), fetch_chapter_content=fetch)
    async with session_factory() as db:
        db.add(Source(id="source", name="test", plugin_name="yuedu", enabled=True,
                      url=book_url, owner_id="reader", config={}))
        db.add(Book(id="other", title="岛上来信", description=BLURB))
        await db.commit()
    with (
        patch("app.services.sync.get_plugin", return_value=plugin),
        patch("app.services.sync.load_source_cookie", AsyncMock(return_value=None)),
        patch("app.services.sync.search_service", MagicMock()),
        patch("app.services.sync.refresh_search_kinds", AsyncMock()),
    ):
        async with session_factory() as db:
            result = await SyncService(db, storage).sync_book("source", book_url)
            book_id = result["book_id"]
            book = await db.get(Book, book_id)
            chapter = await db.scalar(select(Chapter).where(Chapter.book_id == book_id))
            chapter_id = chapter.id
            path = Path(chapter.content_path)
            original = path.read_bytes()
            book.kind = "comic"
            await db.commit()
        async with session_factory() as db:
            result = await SyncService(db, storage).resync_book(book_id)
            book = await db.get(Book, book_id)
            assert book.kind == "novel" and result["skipped_chapters"] == 1
            assert {"都市", "林间"} <= set(book.tag_names)
            assert {"《都市夜航》", "作者：林"}.isdisjoint(book.tag_names)
            assert "都市" in book.category_names
            assert fetch.await_count == 1
        async with session_factory() as db:
            fetch.return_value = "内容简介：" + BLURB
            result = await SyncService(db, storage).resync_book(book_id, refetch_content=True)
            assert "其他书籍的简介" in result["failed_chapters"][0]["error"]
            assert path.read_bytes() == original
            with pytest.raises(ValueError, match="其他书籍的简介"):
                await SyncService(db, storage).resync_chapter(chapter_id)
            fetch.return_value = PROSE[:65]
            result = await SyncService(db, storage).resync_book(book_id, refetch_content=True)
            assert "开头" in result["failed_chapters"][0]["error"] and path.read_bytes() == original
        async with session_factory() as db:
            chapter = await db.get(Chapter, chapter_id)
            chapter.content_path, chapter.hash = storage.write_chapter("林", metadata.title, 1, "第一章", BLURB)
            await db.commit()
            fetch.return_value = GALLERY
            result = await SyncService(db, storage).resync_book(book_id)
            assert result["repaired_chapters"] == result["created_chapters"] == 1
            backup = tmp_path / book_id / ".previous" / (chapter_id + ".txt")
            assert BLURB in backup.read_text(encoding="utf-8")
            book = await db.get(Book, book_id)
            book.kind = "novel"
            await db.commit()
        async with session_factory() as db:
            fetch.reset_mock()
            result = await SyncService(db, storage).resync_book(book_id)
            assert result["skipped_chapters"] == 1
            assert (await db.get(Book, book_id)).kind == "comic"
            fetch.assert_not_awaited()
        async with session_factory() as db:
            service = SyncService(db, storage)
            await service._save_tags(book_id, ["都市夜航", "林", "奇幻", "all-ages"])
            book = await db.get(Book, book_id)
            book.kind = book.kind_override = "novel"
            book.categories_manually_set = True
            await db.commit()
        plugin.fetch_book.return_value = replace(metadata, title="夜航续集", author="江", tags=[])
        async with session_factory() as db:
            await SyncService(db, storage).resync_book(book_id)
        async with session_factory() as db:
            corrected = await db.get(Book, book_id)
            assert (corrected.title, corrected.author_name) == ("夜航续集", "江")
            assert set(corrected.tag_names) == {"奇幻", "all-ages"}
            assert corrected.category_names == ["都市"]
            assert corrected.kind == corrected.kind_override == "novel"


def test_book_card_requires_foreign_identity_and_not_a_recommendation_chapter():
    card = f"书名：岛上来信\n作者：林\n作品简介：{BLURB}"
    assert foreign_synopsis(card, "都市夜航", "第三章", {})
    assert not foreign_synopsis(card, "岛上来信", "第三章", {})
    assert not foreign_synopsis(card, "都市夜航", "新书推荐", {})
    assert not foreign_synopsis("他指着书架说：\n" + card, "都市夜航", "第三章", {})


def test_last_revision_survives_noop_and_rollback(tmp_path):
    from app.services.storage import BookRef, ChapterFileBatch

    storage = BookStorage(str(tmp_path))
    ref = BookRef(book_id="book")
    path, digest = storage.write_chapter("author", "book", 1, "one", PROSE, ref=ref)
    archive = storage.previous_chapter_path("book", "id")
    def replace(body, previous_hash, commit):
        batch = ChapterFileBatch()
        _, new_hash = storage.write_chapter("author", "book", 1, "one", body, ref=ref,
                                            chapter_id="id", previous_path=path,
                                            previous_hash=previous_hash, batch=batch)
        batch.finish(committed=commit)
        return new_hash
    second = replace("second version", digest, True)
    original = archive.read_bytes()
    assert PROSE in original.decode("utf-8")
    replace("second version", second, True)
    replace("third version", second, False)
    assert archive.read_bytes() == original
    assert storage.chapter_digest(path) == second


@pytest.mark.asyncio
async def test_previous_download_uses_book_visibility_and_only_returns_the_backup(tmp_path):
    from fastapi import HTTPException
    from app.api.routes.chapters import download_previous_chapter

    storage = BookStorage(str(tmp_path))
    archive = storage.previous_chapter_path("book", "id")
    archive.parent.mkdir(parents=True)
    archive.write_text("old", encoding="utf-8")
    db = AsyncMock()
    chapter = Chapter(id="id", book_id="book")
    book = Book(id="book", title="Book", owner_id="admin", is_r18=False)
    user = SimpleNamespace(id="admin", role="super_admin", non_r18_enabled=True,
                           allow_all_ages=True, r18_enabled=True, show_r18=True)
    db.get.side_effect = [chapter, book, chapter, book]
    with patch("app.api.routes.chapters.BookStorage", return_value=storage):
        response = await download_previous_chapter("id", user, db)
        assert response.path == archive and response.headers["cache-control"] == "private, no-store"
        book.hidden = True
        with pytest.raises(HTTPException) as error:
            await download_previous_chapter("id", user, db)
        assert error.value.status_code == 404
