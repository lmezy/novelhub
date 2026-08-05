import json

import pytest

from app.main import app
from app.services.auth import require_admin
from app.services.local_library import parse_local_book, scan_local_library


def _book_dir(root, author, title, metadata=None):
    book = root / author / title
    book.mkdir(parents=True, exist_ok=True)
    (book / "000001.md").write_text("# Chapter 1\nhello", encoding="utf-8")
    if metadata:
        (book / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False),
            encoding="utf-8",
        )
    return book


def test_scan_local_library_finds_book_directories(tmp_path):
    _book_dir(
        tmp_path,
        "Alice",
        "Book One",
        {
            "title": "Book One",
            "author": "Alice",
            "status": "completed",
            "tags": ["fantasy"],
        },
    )
    _book_dir(tmp_path, "Bob", "Book Two")
    (tmp_path / "readme.md").write_text("# readme\n", encoding="utf-8")
    (tmp_path / "单独小说 - 作者.txt").write_text(
        "第一章 测试\n正文\n",
        encoding="utf-8",
    )

    books = scan_local_library(str(tmp_path), max_depth=3)

    assert len(books) == 3
    by_title = {b["title"]: b for b in books}
    assert by_title["Book One"]["author"] == "Alice"
    assert by_title["Book One"]["chapter_count"] == 1
    assert by_title["Book Two"]["has_metadata"] is False
    assert by_title["单独小说"]["format"] == "txt"


def test_scan_local_library_respects_max_depth(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "deep-book"
    deep.mkdir(parents=True)
    (deep / "000001.md").write_text("# Deep\nx", encoding="utf-8")

    assert scan_local_library(str(tmp_path), max_depth=2) == []
    assert len(scan_local_library(str(tmp_path), max_depth=4)) == 1


def test_parse_local_book_returns_chapter_refs(tmp_path):
    book = _book_dir(
        tmp_path,
        "Alice",
        "Book One",
        {"title": "Book One", "author": "Alice"},
    )
    (book / "000002.md").write_text("# Chapter 2\nworld", encoding="utf-8")

    info = parse_local_book(book)

    assert info["chapter_count"] == 2
    assert [c["chapter_number"] for c in info["chapters"]] == [1, 2]
    assert info["chapters"][0]["title"] == "Chapter 1"


@pytest.mark.asyncio
async def test_scan_endpoint_returns_books(tmp_path, client):
    _book_dir(tmp_path, "Alice", "Book One")

    app.dependency_overrides[require_admin] = lambda: None
    try:
        resp = await client.post(
            "/api/sync/local/scan",
            json={"path": str(tmp_path), "max_depth": 3},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["books"][0]["title"] == "Book One"
    assert body["books"][0]["chapter_count"] == 1


@pytest.mark.asyncio
async def test_content_endpoint_reads_chapter(tmp_path, client):
    book = _book_dir(tmp_path, "Alice", "Book One")
    chapter = book / "000001.md"

    app.dependency_overrides[require_admin] = lambda: None
    try:
        resp = await client.post(
            "/api/sync/local/content",
            json={"path": str(chapter)},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Chapter 1"
    assert "hello" in body["content"]
