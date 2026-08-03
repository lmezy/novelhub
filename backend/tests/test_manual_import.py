from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.manual_import import ManualImportService


@pytest.mark.asyncio
async def test_manual_import_creates_book_and_chapters():
    db = AsyncMock()
    db.scalar.return_value = None
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    storage = MagicMock()
    storage.write_metadata.return_value = MagicMock()
    storage.write_chapter.return_value = ("/tmp/book/000001.md", "hash-1")

    with (
        patch("app.services.manual_import.search_service") as search_mock,
        patch(
            "app.services.auto_categorize.AutoCategorizationService.categorize_book",
            new=AsyncMock(),
        ) as categorize_mock,
    ):
        service = ManualImportService(db, storage=storage)
        result = await service.import_book(
            title="测试书",
            author="作者",
            description="简介",
            status="ongoing",
            tags=["都市"],
            chapters=[
                {"title": "第一章", "content": "正文内容"},
            ],
        )

    assert result["created_chapters"] == 1
    assert result["book_id"]
    storage.write_metadata.assert_called_once()
    storage.write_chapter.assert_called_once()
    search_mock.index_book.assert_called_once()
    search_mock.index_chapter.assert_called_once()
    categorize_mock.assert_awaited_once()
