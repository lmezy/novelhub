from unittest.mock import AsyncMock, patch

import pytest

from app.services.book_cleanup import delete_books


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
    assert db.execute.await_count == 9
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
    search_mock.delete_chapter_from_index.assert_any_call("chapter-1")
    search_mock.delete_chapter_from_index.assert_any_call("chapter-2")
    search_mock.delete_book_from_index.assert_any_call("book-1")
    search_mock.delete_book_from_index.assert_any_call("book-2")
