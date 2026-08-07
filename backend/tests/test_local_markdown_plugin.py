import json

import pytest

from app.crawler.plugins.local_markdown import LocalMarkdownPlugin


@pytest.mark.asyncio
async def test_local_markdown_accepts_file_url(tmp_path):
    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "metadata.json").write_text(
        json.dumps({
            "title": "测试书",
            "author": "作者",
            "description": "简介",
            "status": "completed",
            "tags": ["都市"],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (book_dir / "000001.md").write_text(
        "# 第一章\n\n正文内容\n",
        encoding="utf-8",
    )

    plugin = LocalMarkdownPlugin()
    url = f"file://{book_dir.as_posix()}"
    book = await plugin.fetch_book(url)

    assert book.title == "测试书"
    assert book.author == "作者"
    assert len(book.chapters) == 1
    assert book.chapters[0].title == "第一章"


@pytest.mark.asyncio
async def test_local_markdown_accepts_non_numbered_chapter_files(tmp_path):
    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "chapter-1.md").write_text(
        "# Chapter One\n\ncontent\n",
        encoding="utf-8",
    )

    plugin = LocalMarkdownPlugin()
    book = await plugin.fetch_book(f"file://{book_dir.as_posix()}")

    assert len(book.chapters) == 1
    assert book.chapters[0].title == "Chapter One"


@pytest.mark.asyncio
async def test_local_markdown_detects_r18_and_tags_from_content(tmp_path):
    book_dir = tmp_path / "book"
    book_dir.mkdir()
    (book_dir / "000001.md").write_text(
        "# 第一章\n\n主角在都市里修炼，正文包含色情内容\n",
        encoding="utf-8",
    )

    plugin = LocalMarkdownPlugin()
    book = await plugin.fetch_book(f"file://{book_dir.as_posix()}")

    assert book.is_r18 is True
    assert "都市" in book.tags
