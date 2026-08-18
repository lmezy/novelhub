from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.book_cleanup import delete_books
from app.services.search import SearchService


@pytest.mark.asyncio
async def test_delete_books_removes_related_rows_and_search_documents():
    db = AsyncMock()
    db.scalars.return_value = ["chapter-1", "chapter-2"]
    db.commit = AsyncMock()
    db.execute = AsyncMock()

    with patch("app.services.book_cleanup.search_service") as search_mock:
        deleted = await delete_books(db, ["book-1", "book-2"])

    assert deleted == 2
    assert db.commit.await_count == 1
    assert db.execute.await_count == 14
    calls = db.execute.call_args_list
    progress_index = next(
        i for i, c in enumerate(calls)
        if "reading_progress" in str(c.args[0])
    )
    chapter_index = next(
        i for i, c in enumerate(calls)
        if str(c.args[0]).startswith("DELETE FROM chapters")
    )
    assert progress_index < chapter_index
    search_mock.delete_chapters_from_index.assert_called_once_with(
        ["chapter-1", "chapter-2"]
    )
    search_mock.delete_books_from_index.assert_called_once_with(
        ["book-1", "book-2"]
    )


def test_search_bulk_delete_batches_documents():
    service = SearchService.__new__(SearchService)
    service.INDEX_BOOKS = "books"
    service.INDEX_CHAPTERS = "chapters"
    book_index = MagicMock()
    chapter_index = MagicMock()
    service.client = MagicMock(
        index=MagicMock(
            side_effect=[
                book_index,
                chapter_index,
                book_index,
                chapter_index,
            ]
        )
    )
    ids = [str(i) for i in range(1500)]

    service.delete_books_from_index(ids)
    service.delete_chapters_from_index(ids)

    assert book_index.delete_documents.call_count == 2
    assert [len(call.args[0]) for call in book_index.delete_documents.call_args_list] == [
        1000,
        500,
    ]
    assert chapter_index.delete_documents.call_count == 2