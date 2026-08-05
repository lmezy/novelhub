import zipfile

import pytest

from app.crawler.plugins.local_markdown import LocalMarkdownPlugin
from app.services.local_file_parser import parse_docx, parse_epub, parse_txt


def test_parse_txt_uses_filename_title_and_author(tmp_path):
    book_file = tmp_path / "斗破苍穹 - 天蚕土豆.txt"
    book_file.write_text(
        "第一章 测试\n正文内容\n\n第二章 继续\n更多内容\n",
        encoding="utf-8",
    )

    meta, chapters = parse_txt(book_file)

    assert meta["title"] == "斗破苍穹"
    assert meta["author"] == "天蚕土豆"
    assert len(chapters) >= 2
    assert chapters[0][0] == "第一章 测试"


def test_parse_docx_extracts_core_metadata(tmp_path):
    book_file = tmp_path / "book.docx"
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>第一章 测试</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>正文内容</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>测试书名</dc:title><dc:creator>测试作者</dc:creator>"
        "</cp:coreProperties>"
    )
    with zipfile.ZipFile(book_file, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("docProps/core.xml", core_xml)

    meta, chapters = parse_docx(book_file)

    assert meta["title"] == "测试书名"
    assert meta["author"] == "测试作者"
    assert chapters[0][0] == "第一章 测试"


def test_parse_epub_metadata_and_chapters(tmp_path):
    from ebooklib import epub

    book_file = tmp_path / "epub-book.epub"
    book = epub.EpubBook()
    book.set_identifier("novelhub-test")
    book.set_title("Epub Book")
    book.set_language("zh")
    book.add_author("Epub Author")
    chapter = epub.EpubHtml(
        title="第一章 测试",
        file_name="chap_01.xhtml",
        lang="zh",
    )
    chapter.content = "<h1>第一章 测试</h1><p>正文内容</p>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    epub.write_epub(str(book_file), book)

    meta, chapters = parse_epub(book_file)

    assert meta["title"] == "Epub Book"
    assert meta["author"] == "Epub Author"
    assert len(chapters) >= 1


@pytest.mark.asyncio
async def test_local_plugin_imports_txt_file(tmp_path):
    book_file = tmp_path / "书名 - 作者.txt"
    book_file.write_text(
        "第一章 测试\n正文内容\n\n第二章 继续\n更多内容\n",
        encoding="utf-8",
    )

    plugin = LocalMarkdownPlugin()
    book = await plugin.fetch_book(f"file://{book_file}")

    assert book.title == "书名"
    assert book.author == "作者"
    assert len(book.chapters) >= 2
    content = await plugin.fetch_chapter_content(book.chapters[0])
    assert "正文内容" in content
