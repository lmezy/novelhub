from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.main import app
from app.services.manual_import import ManualImportService
from app.services.auth import require_admin


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


@pytest.mark.asyncio
async def test_manual_import_detects_r18_from_chapter_content():
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
        ) as _categorize_mock,
    ):
        service = ManualImportService(db, storage=storage)
        result = await service.import_book(
            title="测试书",
            author="作者",
            description="",
            status="ongoing",
            tags=[],
            chapters=[
                {"title": "第一章", "content": "正文包含色情内容"},
            ],
        )

    assert result["is_r18"] is True
    assert search_mock.index_book.call_args.args[0]["is_r18"] is True


@pytest.mark.asyncio
async def test_manual_import_all_ages_override_ignores_r18_content():
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
        ),
    ):
        service = ManualImportService(db, storage=storage)
        result = await service.import_book(
            title="测试书",
            author="作者",
            description="",
            status="ongoing",
            tags=[],
            chapters=[
                {"title": "第一章", "content": "正文包含色情内容"},
            ],
            is_r18=False,
        )

    assert result["is_r18"] is False
    assert "all-ages" in result["tags"]
    assert search_mock.index_book.call_args.args[0]["is_r18"] is False


@pytest.mark.asyncio
async def test_manual_import_r18_override_marks_clean_text():
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
        ),
    ):
        service = ManualImportService(db, storage=storage)
        result = await service.import_book(
            title="测试书",
            author="作者",
            description="",
            status="ongoing",
            tags=[],
            chapters=[
                {"title": "第一章", "content": "干净的正文内容"},
            ],
            is_r18=True,
        )

    assert result["is_r18"] is True
    assert "r18" in result["tags"]
    assert search_mock.index_book.call_args.args[0]["is_r18"] is True


@pytest.mark.asyncio
async def test_manual_analyze_endpoint_returns_r18_and_tags(client):
    app.dependency_overrides[require_admin] = lambda: None
    try:
        resp = await client.post(
            "/api/books/manual/analyze",
            json={
                "text": "书名：测试书\n作者：作者A\n第一章 开始\n都市修仙色情内容\n",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "测试书"
    assert body["is_r18"] is True
    assert "都市" in body["tags"]
