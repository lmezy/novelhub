"""Chapter content chunking: the endpoint a long chapter is read through.

One chapter can hold hundreds of thousands of characters (several books in the
library are a whole novel in a single chapter), so the reader pulls it in
bounded slices.  These tests pin the contract those slices rely on.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes.chapters import get_chapter_content_chunk, get_chapter_content_meta


def _chapter(content_path="/storage/books/a/b/000001.md"):
    return SimpleNamespace(
        id="chapter-1",
        book_id="book-1",
        title="第一章",
        chapter_number=1,
        source_chapter_id="https://example.com/1.html",
        content_path=content_path,
        hash="deadbeef",
    )


def _db(chapter, book=None):
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[chapter, book or SimpleNamespace(id="book-1")])
    return db


USER = SimpleNamespace(id="u1", role="super_admin")


@pytest.mark.asyncio
async def test_chunk_walks_the_whole_chapter_and_then_stops():
    """Offsets must chain to the very end and then report no next chunk."""
    content = "".join(f"{i:06d}行\n" for i in range(1, 401))  # 3200 chars
    with (
        patch("app.api.routes.chapters.ensure_book_visible", return_value=True),
        patch("app.api.routes.chapters.BookStorage.read_chapter", return_value=content),
    ):
        seen = []
        offset = 0
        for _ in range(10):
            chunk = await get_chapter_content_chunk(
                "chapter-1", offset=offset, limit=1000, user=USER, db=_db(_chapter()),
            )
            seen.append(chunk["content"])
            assert chunk["total_length"] == len(content)
            if chunk["next_offset"] is None:
                break
            offset = chunk["next_offset"]

    assert "".join(seen) == content
    assert len(seen) > 1


@pytest.mark.asyncio
async def test_chunk_reports_the_content_hash():
    """The reader keys its chunk cache on this value."""
    with (
        patch("app.api.routes.chapters.ensure_book_visible", return_value=True),
        patch("app.api.routes.chapters.BookStorage.read_chapter", return_value="abcdef"),
    ):
        chunk = await get_chapter_content_chunk(
            "chapter-1", offset=0, limit=1000, user=USER, db=_db(_chapter()),
        )

    assert chunk["hash"] == "deadbeef"
    assert chunk["next_offset"] is None


@pytest.mark.asyncio
async def test_content_meta_reports_length_without_the_body():
    content = "x" * 317_503
    with (
        patch("app.api.routes.chapters.ensure_book_visible", return_value=True),
        patch("app.api.routes.chapters.BookStorage.read_chapter", return_value=content),
    ):
        meta = await get_chapter_content_meta("chapter-1", user=USER, db=_db(_chapter()))

    assert meta == {"id": "chapter-1", "hash": "deadbeef", "total_length": 317_503}
