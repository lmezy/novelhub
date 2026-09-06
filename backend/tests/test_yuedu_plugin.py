import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import json
import time
from bs4 import BeautifulSoup

from app.crawler.plugins.yuedu import YueduPlugin
from app.crawler.plugins.yuedu.rule_engine import YueduRuleEngine
from app.services.proxy_config import ProxyConfig


def test_parse_bookshelf_skips_javascript_links():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    html = """
    <html><body>
      <ul>
        <li class="book-item"><a href="javascript:;" class="book-title">排行</a></li>
        <li class="book-item"><a href="/rank.html" class="book-title">排行榜</a></li>
        <li class="book-item"><a href="/novel/123.html" class="book-title">Book 1</a></li>
      </ul>
    </body></html>
    """

    books = plugin._parse_bookshelf_html(html)

    assert len(books) == 1
    assert books[0].url == "https://example.com/novel/123.html"


def test_rate_limit_disabled_reflects_env_override():
    from app.core.config import settings

    with patch.object(settings, "SYNC_IGNORE_RATE_LIMIT", True):
        assert YueduPlugin._rate_limit_disabled() is True
    with patch.object(settings, "SYNC_IGNORE_RATE_LIMIT", False):
        assert YueduPlugin._rate_limit_disabled() is False


def test_content_text_preserving_images_keeps_markdown_refs():
    html = (
        '<div><p>开头</p>'
        '<img src="https://example.com/a.jpg" alt="图A">'
        '<p>结尾</p></div>'
    )

    text = YueduPlugin._content_text_preserving_images(html)

    assert "![图A](https://example.com/a.jpg)" in text
    assert "<img" not in text
    assert "开头" in text
    assert "结尾" in text


def test_content_text_preserving_images_supports_lazy_src():
    html = '<img data-src="https://example.com/lazy.jpg" alt="懒加载">'

    text = YueduPlugin._content_text_preserving_images(html)

    assert "![懒加载](https://example.com/lazy.jpg)" in text


def test_parse_bookshelf_direct_anchor_fallback():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    html = """
    <html><body>
      <div class="rank">
        <a href="/novel/123.html">Book One</a>
        <a href="/novel/456">Book Two</a>
        <a href="/rank/1.html">排行榜</a>
      </div>
    </body></html>
    """

    books = plugin._parse_bookshelf_html(html)

    assert len(books) == 2
    assert books[0].title == "Book One"
    assert books[1].url == "https://example.com/novel/456"


def test_substitute_page_expressions():
    engine = YueduRuleEngine({"bookSourceUrl": "https://example.com"})

    assert engine._substitute(
        "https://example.com/sort/{{page}}.html", page="2"
    ) == "https://example.com/sort/2.html"
    assert engine._substitute(
        "https://example.com/sort/{{page-1}}.html", page="2"
    ) == "https://example.com/sort/1.html"
    assert engine._substitute(
        "https://example.com/sort/{{page+1}}.html", page="2"
    ) == "https://example.com/sort/3.html"


def test_substitute_url_encoded_legado_placeholders():
    engine = YueduRuleEngine({"bookSourceUrl": "https://example.com"})

    assert engine._substitute(
        "https://example.com/sort/%7B%7Bpage%7D%7D/", page="2"
    ) == "https://example.com/sort/2/"
    assert engine._substitute(
        "https://example.com/search?q=%7B%7BsearchKey%7D%7D&p=%7B%7BsearchPage%7D%7D",
        key="三体",
        page="3",
    ) == "https://example.com/search?q=三体&p=3"


def test_substitute_inner_rules_keeps_rules_without_templates():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    assert plugin.engine._substitute_inner_rules("a@text", "<html></html>") == "a@text"


def test_multiline_js_url_rule_preserves_the_full_expression():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})

    value = plugin.engine._eval_rule_first(
        {"slug": "book-1"},
        '@js:\n"https://example.com/novel/{{$.slug}}"',
    )

    assert value == "https://example.com/novel/book-1"


def test_forum_thread_url_is_treated_as_a_book_detail():
    plugin = YueduPlugin({"bookSourceUrl": "https://forum.example"})

    assert plugin._is_book_url(
        "https://forum.example/index.php?app=forum&act=threadview&tid=123",
        require_pattern=True,
    )


def test_page_scoped_base_url_rule_returns_the_current_book_url():
    plugin = YueduPlugin({"bookSourceUrl": "https://forum.example"})
    current_url = "https://forum.example/index.php?act=threadview&tid=123"
    plugin.engine.set_page_url(current_url)

    assert plugin.engine._eval_rule_first("<html></html>", "@js:baseUrl") == current_url


def test_android_inline_cover_rule_keeps_the_extracted_image_url():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    rule = (
        "img@data-src@js:(function(){"
        "var cipher = Packages.javax.crypto.Cipher.getInstance('AES/CBC/PKCS5Padding');"
        "return result;})();"
    )

    assert plugin.engine._eval_rule_first(
        '<img data-src="/cover.encrypted">', rule
    ) == "/cover.encrypted"


def test_css_rule_supports_single_pipe_or():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    html = '<html><body><div id="content"><p>Main text.</p></div></body></html>'
    assert plugin.engine._eval_rule_str(html, "#content@text|article@text") == "Main text."


def test_parse_book_generic_fills_metadata_and_chapters():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    html = """
    <html><body>
      <h1>书名</h1>
      <p class="author">作者</p>
      <p class="intro">简介内容</p>
      <div class="listmain">
        <a href="/novel/123/1.html">第一章</a>
        <a href="/novel/123/2.html">第二章</a>
      </div>
    </body></html>
    """

    parsed = plugin._parse_book_generic(
        html,
        "https://example.com/novel/123.html",
    )

    assert parsed["title"] == "书名"
    assert parsed["author"] == "作者"
    assert parsed["description"] == "简介内容"
    assert len(parsed["chapters"]) == 2
    assert parsed["chapters"][0].url == "https://example.com/novel/123/1.html"


def test_text_label_rule_extracts_element_text():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    html = '<html><body><p>作 者：lisianthus</p></body></html>'

    assert plugin.engine._eval_rule_str(html, "作 者：@text") == "作 者：lisianthus"


def test_legacy_regex_rule_extracts_author():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    html = '<html><body><p>作者：lisianthus 完结</p></body></html>'

    assert plugin.engine._eval_rule_str(html, r"作者：(.*?)\s") == "lisianthus"


@pytest.mark.asyncio
async def test_fetch_book_cleans_alice_metadata_and_extracts_cover():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.alicesw.com",
        "bookUrlPattern": r"https?://www\.alicesw\.com/novel/\d+\.html",
        "ruleBookInfo": {
            "name": "h1@text",
            "author": "作 者：@text|作者：(.*?)\\s",
            "coverUrl": "img.book-cover@src",
        },
        "ruleToc": {},
        "concurrentRate": "0",
    })
    html = """
    <html><head>
      <title>紫影玉茗-重口-爱丽丝书屋 (ALICESW.COM)</title>
      <meta name="keywords" content="紫影玉茗,lisianthus,重口,痴女,反差,紫影玉茗最新章节">
    </head><body>
      <h1>紫影玉茗-重口-爱丽丝书屋 (ALICESW.COM)</h1>
      <p>作 者：lisianthus</p>
      <img class="book-cover" src="/cover/52311.jpg">
    </body></html>
    """

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://www.alicesw.com/novel/52311.html")

    assert book.title == "紫影玉茗"
    assert book.author == "lisianthus"
    assert book.cover_url == "https://www.alicesw.com/cover/52311.jpg"
    assert "紫影玉茗" not in book.tags
    assert "lisianthus" not in book.tags
    assert "紫影玉茗最新章节" not in book.tags
    assert "重口" in book.tags


def test_build_book_url_uses_configured_detail_prefix():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "bookUrlPattern": "https://example.com/novel/.*",
    })

    assert plugin.build_book_url("37466.html") == "https://example.com/novel/37466.html"


def test_build_book_url_accepts_full_source_book_id():
    plugin = YueduPlugin({
        "bookSourceUrl": "http://m.5859ycdh.com",
        "bookUrlPattern": r"http://m\.5859ycdh\.com/wuba/\d+/?$",
    })
    assert plugin.build_book_url(
        "http://m.5859ycdh.com/wuba/29416"
    ) == "http://m.5859ycdh.com/wuba/29416"


def test_build_headers_uses_http_user_agent():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "httpUserAgent": "CustomAgent/1.0",
    })
    assert plugin._build_headers()["User-Agent"] == "CustomAgent/1.0"


def test_403_fallback_uses_desktop_ua_and_referer():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    fallback = plugin._with_403_fallback({
        "User-Agent": "mobile",
        "Accept": "text/html",
    })
    assert "Windows NT" in fallback["User-Agent"]
    assert fallback["Referer"] == "https://example.com/"
    assert fallback["Accept"] == "text/html"


def test_book_id_from_url_handles_trailing_slash():
    assert YueduPlugin._book_id_from_url(
        "http://m.5859ycdh.com/wuba/29416/"
    ) == "http://m.5859ycdh.com/wuba/29416"
    assert YueduPlugin._book_id_from_url(
        "http://m.5859ycdh.com/wuba/29416"
    ) == "http://m.5859ycdh.com/wuba/29416"
    assert YueduPlugin._book_id_from_url(
        "https://example.com/novel/123.html"
    ) == "https://example.com/novel/123.html"


@pytest.mark.asyncio
async def test_fetch_book_source_book_id_uses_full_normalized_url():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleBookInfo": {},
        "ruleToc": {},
    })
    html = """
    <html><body>
      <h1>Book One</h1>
      <div class="listmain">
        <a href="/novel/123/1.html">Chapter 1</a>
        <a href="/novel/123/2.html">Chapter 2</a>
      </div>
    </body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://example.com/wuba/29416/")

    assert book.source_book_id == "https://example.com/wuba/29416"


@pytest.mark.asyncio
async def test_fetch_book_uses_generic_fallback():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleBookInfo": {},
        "ruleToc": {},
    })
    html = """
    <html><body>
      <h1>书名</h1>
      <div class="listmain">
        <a href="/novel/123/1.html">第一章</a>
        <a href="/novel/123/2.html">第二章</a>
      </div>
    </body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://example.com/novel/123.html")

    assert book.title == "书名"
    assert len(book.chapters) == 2


@pytest.mark.asyncio
async def test_fetch_book_keeps_forum_self_url_as_single_chapter():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://forum.example",
        "ruleBookInfo": {"name": ".main-title@text", "tocUrl": "@js:baseUrl"},
        "ruleToc": {
            "chapterList": ".title-section",
            "chapterName": ".main-title@text",
            "chapterUrl": "@js:baseUrl",
        },
    })
    url = "https://forum.example/index.php?app=forum&act=threadview&tid=123"
    html = """
    <html><body><h1 class="main-title">Forum Book</h1>
    <div class="title-section"></div></body></html>
    """

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book(url)

    assert [(chapter.title, chapter.url) for chapter in book.chapters] == [
        ("Forum Book", url),
    ]


@pytest.mark.asyncio
async def test_fetch_book_uses_chapter_url_as_source_id():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleBookInfo": {},
        "ruleToc": {},
    })
    html = """
    <html><body>
      <h1>书名</h1>
      <div class="listmain">
        <a href="/novel/123/1.html">第一章</a>
        <a href="/novel/123/2.html">第二章</a>
      </div>
    </body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://example.com/novel/123.html")

    assert [(c.source_chapter_id, c.url) for c in book.chapters] == [
        ("https://example.com/novel/123/1.html", "https://example.com/novel/123/1.html"),
        ("https://example.com/novel/123/2.html", "https://example.com/novel/123/2.html"),
    ]


@pytest.mark.asyncio
async def test_fetch_explore_uses_generic_fallback_when_rules_empty():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleSearch": {},
        "ruleExplore": {},
    })
    html = """
    <html><body>
      <ul>
        <li class="book-item"><a href="/novel/123.html" class="book-title">书一</a></li>
        <li class="book-item"><a href="/novel/456.html" class="book-title">书二</a></li>
      </ul>
    </body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        items = await plugin._fetch_explore_url("https://example.com/lists/1.html")

    assert len(items) == 2
    assert items[0]["name"] == "书一"
    assert items[0]["bookUrl"] == "https://example.com/novel/123.html"


@pytest.mark.asyncio
async def test_fetch_explore_falls_back_when_rule_matches_container():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleSearch": {},
        "ruleExplore": {"bookList": "div.book-list"},
        "concurrentRate": "0",
    })
    html = """
    <html><body>
      <div class="book-list">
        <a href="/novel/123.html" class="book-title">Book One</a>
        <a href="/novel/456.html" class="book-title">Book Two</a>
      </div>
    </body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        items = await plugin._fetch_explore_url("https://example.com/lists/65.html")

    assert len(items) == 2
    assert items[0]["bookUrl"] == "https://example.com/novel/123.html"
    assert items[1]["bookUrl"] == "https://example.com/novel/456.html"


@pytest.mark.asyncio
async def test_fetch_explore_resolves_relative_book_urls_against_page():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleSearch": {},
        "ruleExplore": {},
        "concurrentRate": "0",
    })
    html = """
    <html><body>
      <a href="../novel/123.html">Book One</a>
      <a href="../novel/456.html">Book Two</a>
    </body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        items = await plugin._fetch_explore_url("https://example.com/lists/65.html")

    assert [item["bookUrl"] for item in items] == [
        "https://example.com/novel/123.html",
        "https://example.com/novel/456.html",
    ]


ALICE_SOURCE = {
    "bookSourceUrl": "https://www.alicesw.com",
    "bookUrlPattern": r"https?://www\.alicesw\.com/novel/\d+\.html",
    "ruleExplore": {
        "bookList": "table tr",
        "name": "a@text",
        "bookUrl": "a@href",
    },
    "ruleToc": {
        "chapterList": ".list a",
        "chapterName": "a@text",
        "chapterUrl": "a@href",
    },
    "ruleContent": {
        "content": "#content@text",
        "replaceRegex": ["banner.*"],
    },
    "concurrentRate": "0",
}


@pytest.mark.asyncio
async def test_fetch_explore_filters_category_links_when_pattern_configured():
    plugin = YueduPlugin(ALICE_SOURCE)
    html = """
    <html><body><table>
      <tr><td><a href="/lists/65.html">Category</a></td></tr>
      <tr><td><a href="/novel/123.html">Book One</a></td></tr>
      <tr><td><a href="/novel/456.html">Book Two</a></td></tr>
    </table></body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        items = await plugin._fetch_explore_url("https://www.alicesw.com/lists/65.html")

    assert [item["bookUrl"] for item in items] == [
        "https://www.alicesw.com/novel/123.html",
        "https://www.alicesw.com/novel/456.html",
    ]


@pytest.mark.asyncio
async def test_fetch_book_does_not_treat_book_page_as_chapter():
    plugin = YueduPlugin(ALICE_SOURCE)
    html = """
    <html><body>
      <h1>Book One</h1>
      <div class="list">
        <a href="/novel/123.html">Book One</a>
        <a href="/novel/123/1.html">Chapter 1</a>
        <a href="/novel/123/2.html">Chapter 2</a>
        <a href="/lists/65.html">Category</a>
      </div>
    </body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://www.alicesw.com/novel/123.html")

    assert book.title == "Book One"
    assert [(c.title, c.url) for c in book.chapters] == [
        ("Chapter 1", "https://www.alicesw.com/novel/123/1.html"),
        ("Chapter 2", "https://www.alicesw.com/novel/123/2.html"),
    ]


@pytest.mark.asyncio
async def test_fetch_book_accepts_relative_chapter_urls():
    plugin = YueduPlugin(ALICE_SOURCE)
    html = """
    <html><body>
      <h1>Book One</h1>
      <div class="list">
        <a href="123/1.html">Chapter 1</a>
        <a href="123/2.html">Chapter 2</a>
      </div>
    </body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://www.alicesw.com/novel/123.html")

    assert [(c.title, c.url) for c in book.chapters] == [
        ("Chapter 1", "https://www.alicesw.com/novel/123/1.html"),
        ("Chapter 2", "https://www.alicesw.com/novel/123/2.html"),
    ]


@pytest.mark.asyncio
async def test_fetch_book_uses_toc_url_and_resolves_relative_chapters():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleBookInfo": {
            "name": "h1@text",
            "tocUrl": "a.toc@href",
        },
        "ruleToc": {
            "chapterList": "ul.chapters li",
            "chapterName": "a@text",
            "chapterUrl": "a@href",
        },
        "concurrentRate": "0",
    })
    book_html = (
        '<html><body><h1>Book One</h1>'
        '<a class="toc" href="list.html">目录</a></body></html>'
    )
    toc_html = (
        '<html><body><ul class="chapters">'
        '<li><a href="1.html">Chapter 1</a></li>'
        '<li><a href="2.html">Chapter 2</a></li>'
        '</ul></body></html>'
    )
    requested: list[str] = []

    async def fake_get(url):
        requested.append(url)
        if url == "https://example.com/books/123.html":
            return book_html
        if url == "https://example.com/books/list.html":
            return toc_html
        raise AssertionError(f"unexpected url: {url}")

    with patch.object(plugin, "_get", fake_get):
        book = await plugin.fetch_book("https://example.com/books/123.html")

    assert book.title == "Book One"
    assert [(c.title, c.url) for c in book.chapters] == [
        ("Chapter 1", "https://example.com/books/1.html"),
        ("Chapter 2", "https://example.com/books/2.html"),
    ]
    assert requested == [
        "https://example.com/books/123.html",
        "https://example.com/books/list.html",
    ]


@pytest.mark.asyncio
async def test_fetch_book_follows_multiple_toc_pages():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleBookInfo": {
            "name": "h1@text",
            "tocUrl": "a.toc@href",
        },
        "ruleToc": {
            "chapterList": "ul.chapters li",
            "chapterName": "a@text",
            "chapterUrl": "a@href",
            "nextTocUrl": "a.page@href",
        },
        "concurrentRate": "0",
    })
    book_html = (
        '<html><body><h1>Book One</h1>'
        '<a class="toc" href="list.html">目录</a></body></html>'
    )
    page1 = (
        '<html><body><ul class="chapters">'
        '<li><a href="1.html">Chapter 1</a></li>'
        '</ul>'
        '<a class="page" href="list_2.html">2</a>'
        '<a class="page" href="list_3.html">3</a>'
        '</body></html>'
    )
    page2 = (
        '<html><body><ul class="chapters">'
        '<li><a href="2.html">Chapter 2</a></li>'
        '</ul>'
        '<a class="page" href="list_3.html">3</a>'
        '</body></html>'
    )
    page3 = (
        '<html><body><ul class="chapters">'
        '<li><a href="3.html">Chapter 3</a></li>'
        '</ul></body></html>'
    )
    requested: list[str] = []

    async def fake_get(url):
        requested.append(url)
        if url == "https://example.com/books/123.html":
            return book_html
        if url == "https://example.com/books/list.html":
            return page1
        if url == "https://example.com/books/list_2.html":
            return page2
        if url == "https://example.com/books/list_3.html":
            return page3
        raise AssertionError(f"unexpected url: {url}")

    with patch.object(plugin, "_get", fake_get):
        book = await plugin.fetch_book("https://example.com/books/123.html")

    assert [c.title for c in book.chapters] == [
        "Chapter 1",
        "Chapter 2",
        "Chapter 3",
    ]
    assert requested == [
        "https://example.com/books/123.html",
        "https://example.com/books/list.html",
        "https://example.com/books/list_2.html",
        "https://example.com/books/list_3.html",
    ]


@pytest.mark.asyncio
async def test_fetch_chapter_content_handles_replace_regex_list():
    plugin = YueduPlugin(ALICE_SOURCE)
    html = """
    <html><body>
      <div id="content">
        Main text.
        banner advertisement
      </div>
    </body></html>
    """
    chapter = SimpleNamespace(
        url="https://www.alicesw.com/novel/123/1.html",
    )
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        content = await plugin.fetch_chapter_content(chapter)

    assert "Main text" in content
    assert "banner" not in content


@pytest.mark.asyncio
async def test_fetch_chapter_content_generic_fallback():
    plugin = YueduPlugin({
        **ALICE_SOURCE,
        "ruleContent": {"content": "#missing@text"},
    })
    html = """
    <html><body>
      <div id="content"><p>Main text.</p></div>
    </body></html>
    """
    chapter = SimpleNamespace(
        url="https://www.alicesw.com/novel/123/1.html",
    )
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        content = await plugin.fetch_chapter_content(chapter)

    assert "Main text" in content
    assert "<" not in content


def test_next_content_url_resolves_relative_against_current_page():
    engine = YueduRuleEngine({
        "bookSourceUrl": "http://m.5859ycdh.com",
        "ruleContent": {"nextContentUrl": "a.next@href"},
    })
    html = (
        '<html><body>'
        '<a class="next" href="16555538-2.html">下一页</a>'
        '</body></html>'
    )
    assert engine.get_next_content_url(
        html,
        "http://m.5859ycdh.com/wubashu/29416/16555538.html",
    ) == "http://m.5859ycdh.com/wubashu/29416/16555538-2.html"


def test_next_toc_url_resolves_relative_against_current_page():
    engine = YueduRuleEngine({
        "bookSourceUrl": "http://m.5859ycdh.com",
        "ruleToc": {"nextTocUrl": "a.next@href"},
    })
    html = (
        '<html><body>'
        '<a class="next" href="list_2.html">下一页</a>'
        '</body></html>'
    )
    assert engine.get_next_toc_url(
        html,
        "http://m.5859ycdh.com/wuba/29416/list.html",
    ) == "http://m.5859ycdh.com/wuba/29416/list_2.html"


def test_next_content_urls_returns_multiple_absolute_urls():
    engine = YueduRuleEngine({
        "bookSourceUrl": "http://m.5859ycdh.com",
        "ruleContent": {"nextContentUrl": "a.page@href"},
    })
    html = (
        '<html><body>'
        '<a class="page" href="16555538-2.html">2</a>'
        '<a class="page" href="16555538-3.html">3</a>'
        '</body></html>'
    )
    assert engine.get_next_content_urls(
        html,
        "http://m.5859ycdh.com/wubashu/29416/16555538.html",
    ) == [
        "http://m.5859ycdh.com/wubashu/29416/16555538-2.html",
        "http://m.5859ycdh.com/wubashu/29416/16555538-3.html",
    ]


@pytest.mark.asyncio
async def test_fetch_chapter_content_resolves_relative_next_pages():
    plugin = YueduPlugin({
        "bookSourceUrl": "http://m.5859ycdh.com",
        "ruleContent": {
            "content": "#content@text",
            "nextContentUrl": "a.next@href",
        },
        "concurrentRate": "0",
    })
    page1 = (
        '<html><body><div id="content">Page one.</div>'
        '<a class="next" href="16555538-2.html">下一页</a></body></html>'
    )
    page2 = (
        '<html><body><div id="content">Page two.</div>'
        '<a class="next" href="16555538-3.html">下一页</a></body></html>'
    )
    page3 = (
        '<html><body><div id="content">Page three.</div></body></html>'
    )
    requested: list[str] = []

    async def fake_get(url):
        requested.append(url)
        if url.endswith("16555538.html"):
            return page1
        if url.endswith("16555538-2.html"):
            return page2
        if url.endswith("16555538-3.html"):
            return page3
        raise AssertionError(f"unexpected url: {url}")

    chapter = SimpleNamespace(
        url="http://m.5859ycdh.com/wubashu/29416/16555538.html",
    )
    with patch.object(plugin, "_get", fake_get):
        content = await plugin.fetch_chapter_content(chapter)

    assert "Page one." in content
    assert "Page two." in content
    assert "Page three." in content
    assert requested == [
        "http://m.5859ycdh.com/wubashu/29416/16555538.html",
        "http://m.5859ycdh.com/wubashu/29416/16555538-2.html",
        "http://m.5859ycdh.com/wubashu/29416/16555538-3.html",
    ]


@pytest.mark.asyncio
async def test_fetch_chapter_content_stops_at_next_chapter_url():
    plugin = YueduPlugin({
        "bookSourceUrl": "http://m.5859ycdh.com",
        "ruleContent": {
            "content": "#content@text",
            "nextContentUrl": "a.next@href",
        },
        "concurrentRate": "0",
    })
    page1 = (
        '<html><body><div id="content">Page one.</div>'
        '<a class="next" href="16555539.html">下一章</a></body></html>'
    )
    requested: list[str] = []

    async def fake_get(url):
        requested.append(url)
        if url.endswith("16555538.html"):
            return page1
        raise AssertionError(f"unexpected url: {url}")

    chapter = SimpleNamespace(
        url="http://m.5859ycdh.com/wubashu/29416/16555538.html",
        next_url="http://m.5859ycdh.com/wubashu/29416/16555539.html",
    )
    with patch.object(plugin, "_get", fake_get):
        content = await plugin.fetch_chapter_content(chapter)

    assert content == "Page one."
    assert requested == ["http://m.5859ycdh.com/wubashu/29416/16555538.html"]


@pytest.mark.asyncio
async def test_get_falls_back_to_direct_when_proxy_unreachable():
    YueduPlugin._clients.clear()
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "concurrentRate": "0",
    })

    proxy_calls: list[str | None] = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            proxy = self.kwargs.get("proxy")
            proxy_calls.append(proxy)
            self.response = SimpleNamespace(
                status_code=200,
                headers={},
                text="<html>ok</html>",
                raise_for_status=lambda: None,
            )

        async def get(self, url, headers=None):
            if self.kwargs.get("proxy"):
                raise httpx.ConnectError("proxy down", request=None)
            return self.response

    try:
        with (
            patch("httpx.AsyncClient", FakeClient),
            patch("asyncio.sleep", AsyncMock()),
            patch(
                "app.services.proxy_config.get_proxy_config",
                return_value=ProxyConfig(
                    enabled=True,
                    https_proxy="http://127.0.0.1:1",
                    http_proxy="http://127.0.0.1:1",
                ),
            ),
        ):
            html = await plugin._get("https://example.com/page")
    finally:
        YueduPlugin._clients.clear()

    assert html == "<html>ok</html>"
    assert proxy_calls[0] == "http://127.0.0.1:1"
    assert proxy_calls[-1] is None


SEARCH_SOURCE = {
    "bookSourceUrl": "https://example.com",
    "bookUrlPattern": r"https?://example\.com/novel/\d+\.html",
    "searchUrl": "https://example.com/search?q={{key}}",
    "ruleSearch": {
        "bookList": "div.result",
        "name": "a@text",
        "bookUrl": "a@href",
        "author": ".author@text",
        "lastChapter": ".latest@text",
    },
    "concurrentRate": "0",
}


@pytest.mark.asyncio
async def test_search_books_filters_category_links_and_normalizes():
    plugin = YueduPlugin(SEARCH_SOURCE)
    html = """
    <html><body>
      <div class="result"><a href="/novel/123.html">Book One</a><span class="author">Author A</span><span class="latest">Chapter 1</span></div>
      <div class="result"><a href="/lists/65.html">Category</a></div>
      <div class="result"><a href="/novel/456.html">Book Two</a><span class="author">Author B</span></div>
    </body></html>
    """
    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        items = await plugin.search_books("test", page=1)

    assert [item["bookUrl"] for item in items] == [
        "https://example.com/novel/123.html",
        "https://example.com/novel/456.html",
    ]
    assert items[0]["author"] == "Author A"
    assert items[0]["lastChapter"] == "Chapter 1"


API_SEARCH_SOURCE = {
    "bookSourceUrl": "https://api.example.com",
    "bookUrlPattern": r"https?://api\.example\.com/books/\d+",
    "searchUrl": 'https://api.example.com/search,{"method":"POST","body":{"q":"searchKey","page":{{searchPage}}}}',
    "ruleSearch": {
        "bookList": "$.data",
        "name": "$.title",
        "author": "$.author",
        "bookUrl": "$.url",
    },
    "concurrentRate": "0",
}


@pytest.mark.asyncio
async def test_search_books_posts_json_body_with_legacy_placeholders():
    plugin = YueduPlugin(API_SEARCH_SOURCE)
    response_json = json.dumps({
        "data": [
            {"title": "Book A", "author": "Author", "url": "/books/1"},
            {"title": "Book B", "author": "Author", "url": "/books/2"},
        ]
    })
    captured: dict[str, object] = {}

    async def fake_post(url, body=None, headers=None):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return response_json

    with patch.object(plugin, "_post", fake_post):
        items = await plugin.search_books("hello", page=2)

    assert captured["url"] == "https://api.example.com/search"
    assert "hello" in str(captured["body"])
    assert '"page": 2' in str(captured["body"])
    assert captured["headers"].get("Content-Type") == "application/json"
    assert [item["bookUrl"] for item in items] == [
        "https://api.example.com/books/1",
        "https://api.example.com/books/2",
    ]


def test_search_books_requires_search_url():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleSearch": {"bookList": "div.result"},
    })
    with pytest.raises(ValueError, match="searchUrl"):
        plugin.engine.build_search_url("hello")


def test_explore_kinds_skip_header_lines_without_url():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "exploreUrl": "————分类————\n玄幻::https://example.com/list/1\n都市::https://example.com/list/2",
    })
    kinds = plugin.get_explore_kinds()
    assert kinds == [
        {"title": "————分类————", "url": ""},
        {"title": "玄幻", "url": "https://example.com/list/1"},
        {"title": "都市", "url": "https://example.com/list/2"},
    ]


def test_explore_json_kind_without_url_is_empty():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "exploreUrl": '[{"title":"Header"},{"title":"Fantasy","url":"/fantasy"}]',
    })
    kinds = plugin.get_explore_kinds()
    assert kinds[0]["url"] == ""
    assert kinds[1]["url"] == "/fantasy"


@pytest.mark.asyncio
async def test_fetch_book_skips_javascript_chapter_and_extracts_generic_tags():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleBookInfo": {
            "name": "h1@text",
        },
        "ruleToc": {
            "chapterList": "ul.chapters li",
            "chapterName": "a@text",
            "chapterUrl": "a@href",
        },
        "concurrentRate": "0",
    })
    html = (
        '<html><head><meta name="keywords" content="都市,爽文"></head><body>'
        '<h1>Book One</h1>'
        '<div class="tags"><a href="/tag/1">都市</a></div>'
        '<ul class="chapters">'
        '<li><a href="javascript:void(0);">默认</a></li>'
        '<li><a href="123/1.html">Chapter 1</a></li>'
        '</ul></body></html>'
    )

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://example.com/novel/123.html")

    assert len(book.chapters) == 1
    assert book.chapters[0].url == "https://example.com/novel/123/1.html"
    assert "都市" in book.tags
    assert "爽文" in book.tags


def test_rule_first_cover_url_returns_single_value():
    engine = YueduRuleEngine({"bookSourceUrl": "https://example.com"})
    html = (
        '<html><body>'
        '<img src="/template/logo.svg">'
        '<img src="/cover/123.jpg">'
        '<img src="/cover/banner.jpg">'
        '</body></html>'
    )

    assert engine._eval_rule_first(html, "img@src") == "/template/logo.svg"


def test_pick_cover_url_skips_placeholders_and_takes_first_real_image():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    value = (
        "/template/home/diyquge/images/caret-down.svg\n"
        "https://img.example.com/cover/123.webp\n"
        "https://img.example.com/banner.webp"
    )

    assert plugin._pick_cover_url(
        value,
        "https://example.com/novel/1.html",
    ) == "https://img.example.com/cover/123.webp"


def test_clean_tags_drops_title_author_fragments():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})

    result = plugin._clean_tags(
        ["官路之谁与争锋(卷帘西风666)", "卷帘西风666", "仙侠武侠"],
        title="官路之谁与争锋",
        author="卷帘西风666",
    )

    assert result == ["仙侠武侠"]


def test_parse_book_generic_extracts_author_from_meta_description():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    html = """
    <html><head>
      <meta name="description" content="《债与肉》由作家rizzwhistleblower创作。">
    </head><body><h1>债与肉</h1></body></html>
    """

    parsed = plugin._parse_book_generic(
        html,
        "https://example.com/novel/1.html",
    )

    assert parsed["author"] == "rizzwhistleblower"


def test_forum_page_prefers_labelled_novel_author_and_cleans_tags():
    plugin = YueduPlugin({
        "bookSourceName": "禁忌书屋",
        "bookSourceUrl": "https://www.cool18.com/bbs4",
    })
    html = """
    <html><head>
      <title>【异世界冒险】（1-8） 作者：shy li - 禁忌书屋 cool18 酷18</title>
      <meta name="author" content="发帖账号">
      <meta name="description" content="【异世界冒险】（1-8） 作者：shy li">
      <meta name="keywords" content="【异世界冒险】（1-8） 作者：shy li,酷18,cool18.com">
    </head><body>
      <h1>【异世界冒险】（1-8） 作者：shy li</h1>
      <div>标签：#奇幻 #后宫 #异世界</div>
    </body></html>
    """

    parsed = plugin._parse_book_generic(
        html,
        "https://www.cool18.com/bbs4/index.php?app=forum&act=threadview&tid=1",
    )

    assert parsed["title"] == "【异世界冒险】（1-8）"
    assert parsed["author"] == "shy li"
    assert parsed["tags"] == ["奇幻", "后宫", "异世界"]


@pytest.mark.asyncio
async def test_fetch_forum_book_overrides_rule_document_author():
    plugin = YueduPlugin({
        "bookSourceName": "禁忌书屋",
        "bookSourceUrl": "https://www.cool18.com/bbs4",
        "ruleBookInfo": {
            "name": ".main-title@text",
            "author": "meta[name=author]@content",
            "kind": "论坛帖子",
        },
        "ruleToc": {},
    })
    html = """
    <html><head>
      <title>【异世界冒险】 作者：shy li - 禁忌书屋 cool18 酷18</title>
      <meta name="author" content="发帖账号">
      <meta name="description" content="【异世界冒险】 作者：shy li">
    </head><body>
      <h1 class="main-title">【异世界冒险】 作者：shy li</h1>
      <div>标签：#奇幻 #异世界</div>
    </body></html>
    """

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book(
            "https://www.cool18.com/bbs4/index.php?app=forum&act=threadview&tid=1"
        )

    assert book.title == "【异世界冒险】"
    assert book.author == "shy li"
    assert "论坛帖子" not in book.tags
    assert book.tags == ["奇幻", "异世界"]


def test_split_kind_text_splits_metadata_labels():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})

    assert plugin._split_kind_text(
        "分类：都市 作者：张三 字数：10万"
    ) == ["都市", "张三"]


def test_find_toc_url_detects_all_chapters_link():
    plugin = YueduPlugin({"bookSourceUrl": "https://www.alicesw.com"})
    html = """
    <html><body>
      <a href="/novel/1.html">书名</a>
      <a href="/other/chapters/id/1.html">查看所有章节</a>
      <a href="https://www.ainvmei.com/">广告</a>
    </body></html>
    """

    assert plugin._find_toc_url(
        html,
        "https://www.alicesw.com/novel/1.html",
    ) == "https://www.alicesw.com/other/chapters/id/1.html"


def test_parse_book_generic_filters_nav_and_ad_links():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.alicesw.com",
        "bookUrlPattern": r"https?://www\.alicesw\.com/novel/\d+\.html",
    })
    html = """
    <html><body>
      <h1>书名</h1>
      <a href="/">首页</a>
      <a href="/original.html">原创</a>
      <a href="/all/order/update_time+desc.html">最新</a>
      <a href="/book/53181/a.html">第一章 测试</a>
      <a href="/book/53181/b.html">第二章 测试</a>
      <a href="/other/chapters/id/51859.html">查看所有章节</a>
      <a href="https://www.ainvmei.com/?rf=1">电子魅魔</a>
      <a href="https://alicesw.tw">繁體站</a>
      <a href="/novel/51859.html">书名</a>
    </body></html>
    """

    parsed = plugin._parse_book_generic(
        html,
        "https://www.alicesw.com/novel/51859.html",
    )

    assert [(c.title, c.url) for c in parsed["chapters"]] == [
        ("第一章 测试", "https://www.alicesw.com/book/53181/a.html"),
        ("第二章 测试", "https://www.alicesw.com/book/53181/b.html"),
    ]


def test_dedupe_chapters_keeps_canonical_url_for_duplicate_title():
    chapters = [
        SimpleNamespace(
            title="第八章 母授神功显真容",
            url="https://www.alicesw.com/book/53181/0.html",
        ),
        SimpleNamespace(
            title="第八章 母授神功显真容",
            url="https://www.alicesw.com/book/53181/4166062659822.html",
        ),
        SimpleNamespace(
            title="第七章 儿入千户母担忧",
            url="https://www.alicesw.com/book/53181/e27572c2118b8.html",
        ),
    ]

    result = YueduPlugin._dedupe_chapters(
        chapters,
        "https://www.alicesw.com/novel/51859.html",
    )

    assert [c.url for c in result] == [
        "https://www.alicesw.com/book/53181/4166062659822.html",
        "https://www.alicesw.com/book/53181/e27572c2118b8.html",
    ]


def test_is_blocked_page_detects_rate_limit():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    blocked = """
    <title>提示信息</title>
    <script>let msg = "访问异常，请稍后再试，请于 2026-08-12 10:42:15 后再试";</script>
    """

    assert plugin._is_blocked_page(blocked) is True
    assert plugin._is_blocked_page("<html><body>ok</body></html>") is False


@pytest.mark.asyncio
async def test_fetch_chapter_content_raises_on_empty_content():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleContent": {"content": "#missing@text"},
    })
    html = "<html><body><p>只有导航，没有正文</p></body></html>"

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        with pytest.raises(RuntimeError, match="empty content"):
            await plugin.fetch_chapter_content(
                SimpleNamespace(url="https://example.com/book/1.html")
            )


@pytest.mark.asyncio
async def test_cool18_android_rules_fall_back_to_forum_post_content():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://forum.example",
        "ruleBookInfo": {},
        "ruleToc": {
            "chapterList": "<js>org.jsoup.Jsoup.parse(result)</js>",
        },
        "ruleContent": {
            "content": "<js>Packages.org.jsoup.Jsoup.parse(result).text()</js>",
        },
    })
    html = """
    <html><head><title>Photo post - Cool18</title></head><body>
      <h1>Photo post</h1>
      <div id="content-section" class="content-section"><pre>
        First line
        <img data-src="https://cdn.example.com/1.jpg" alt="page 1">
        Last line
      </pre></div>
      <a href="/index.php?app=forum&act=userview&username=author">Author posts</a>
      <a href="/index.php?app=sys&act=threadmanage&tid=1">Manage</a>
    </body></html>
    """
    url = "https://forum.example/index.php?app=forum&act=threadview&tid=1"

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book(url)
        content = await plugin.fetch_chapter_content(book.chapters[0])

    assert [(chapter.title, chapter.url) for chapter in book.chapters] == [
        ("Photo post", url),
    ]
    assert "First line" in content
    assert "![page 1](https://cdn.example.com/1.jpg)" in content


@pytest.mark.asyncio
async def test_android_content_rule_falls_back_to_banshanren_chapter_container():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.banshanren.com",
        "ruleContent": {
            "content": "<js>var doc = org.jsoup.Jsoup.parse(result);</js>",
        },
    })
    html = """
    <div class="chapter_content_box">
      <h2>Chapter 1</h2>
      <p>First paragraph<span class="z">0</span></p>
      <p>Second paragraph</p>
    </div>
    """

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        content = await plugin.fetch_chapter_content(
            SimpleNamespace(url="https://www.banshanren.com/novel/book/1")
        )

    assert "First paragraph" in content
    assert "Second paragraph" in content


@pytest.mark.asyncio
async def test_discover_books_retains_explore_category_as_tag():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://forum.example",
        "exploreUrl": "Photos::/photos",
        "ruleExplore": {
            "bookList": "a.post",
            "bookName": "@text",
            "bookUrl": "@href",
        },
    })
    html = '<a class="post" href="/index.php?app=forum&act=threadview&tid=1">Set one</a>'

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        books = await plugin.discover_books()

    assert len(books) == 1
    assert books[0].tags == ["Photos"]


@pytest.mark.asyncio
async def test_empty_array_rules_from_yuedu_export_use_forum_fallback():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://forum.example",
        "ruleBookInfo": [],
        "ruleExplore": [],
        "ruleContent": [],
        "ruleToc": [],
    })
    html = '<h1>漫画图集</h1><div id="content-section"><pre>body</pre></div>'
    url = "https://forum.example/index.php?app=forum&act=threadview&tid=1"

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book(url)

    assert len(book.chapters) == 1
    assert set(book.tags) == {"漫画", "图集"}


@pytest.mark.asyncio
async def test_fetch_book_uses_auto_detected_full_toc_and_filters_junk():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.alicesw.com",
        "bookUrlPattern": r"https?://www\.alicesw\.com/novel/\d+\.html",
        "ruleBookInfo": {"name": "h1@text"},
        "ruleToc": {},
    })
    book_html = """
    <html><body>
      <h1>书名</h1>
      <a href="/other/chapters/id/123.html">查看所有章节</a>
    </body></html>
    """
    toc_html = """
    <html><body>
      <a href="/">首页</a>
      <a href="/original.html">原创</a>
      <a href="/book/1/a.html">第一章</a>
      <a href="/book/1/b.html">第二章</a>
      <a href="https://www.ainvmei.com/">电子魅魔</a>
    </body></html>
    """

    async def fake_get(url):
        if url == "https://www.alicesw.com/novel/123.html":
            return book_html
        if url == "https://www.alicesw.com/other/chapters/id/123.html":
            return toc_html
        raise AssertionError(f"unexpected url: {url}")

    with patch.object(plugin, "_get", fake_get):
        book = await plugin.fetch_book("https://www.alicesw.com/novel/123.html")

    assert [(c.title, c.url) for c in book.chapters] == [
        ("第一章", "https://www.alicesw.com/book/1/a.html"),
        ("第二章", "https://www.alicesw.com/book/1/b.html"),
    ]

# ---------------- Anti-bot / captcha page handling ----------------

def test_is_blocked_page_detects_captcha_verification_page():
    html = (
        '<div class="limit_box">系统检测到您访问异常'
        ' 输入验证码后可继续访问 输入验证码'
        ' 每一个搬山人的付出，都值得被珍视。</div>'
    )
    assert YueduPlugin._is_blocked_page(html) is True


def test_is_blocked_page_detects_rate_limit_page():
    html = '<div>请求过于频繁，请稍后再试</div>'
    assert YueduPlugin._is_blocked_page(html) is True


def test_is_blocked_page_weak_marker_needs_confirmation():
    # 访问异常 alone is not enough; a normal page mentioning it should pass.
    assert YueduPlugin._is_blocked_page(
        "<html>普通页面</html>"
    ) is False
    assert YueduPlugin._is_blocked_page(
        '<html><p>访问异常？</p></html>'
    ) is False
    assert YueduPlugin._is_blocked_page(
        '<html><p>访问异常 请稍后再试</p></html>'
    ) is True


def test_is_blocked_page_normal_chapter_page_not_flagged():
    # The real chapter page loads captcha assets but is NOT a block page.
    html = (
        '<html><head>'
        '<link rel="stylesheet" href="captcha.css">'
        '<script src="captcha.min.js"></script>'
        '</head><body>正文内容</body></html>'
    )
    assert YueduPlugin._is_blocked_page(html) is False


def test_content_is_blocked_rejects_captcha_text():
    text = "系统检测到您访问异常\n输入验证码后可继续访问\n输入验证码"
    assert YueduPlugin._content_is_blocked(text) is True
    assert YueduPlugin._content_is_blocked("正常的正文内容") is False


def test_clean_extracted_text_drops_comment_counters():
    text = (
        "第一段\n0\n第二段\n12\n第三段\n999\n"
        "1999年的故事\n"
    )
    cleaned = YueduPlugin._clean_extracted_text(text)
    assert "第一段" in cleaned
    assert "第二段" in cleaned
    assert "第三段" in cleaned
    assert "\n0\n" not in cleaned
    assert "\n12\n" not in cleaned
    assert "\n999\n" not in cleaned
    # 4-digit numbers (years) are preserved.
    assert "1999年的故事" in cleaned


@pytest.mark.asyncio
async def test_fetch_chapter_content_rejects_anti_bot_page():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleContent": {},
    })
    block_html = (
        '<html><body><div class="content">'
        "系统检测到您访问异常 输入验证码后可继续访问 输入验证码"
        '</div></body></html>'
    )
    url = "https://example.com/book/1/1.html"

    with patch.object(plugin, "_get", AsyncMock(return_value=block_html)):
        with pytest.raises(RuntimeError, match="anti-bot"):
            await plugin.fetch_chapter_content(
                SimpleNamespace(
                    source_chapter_id=url,
                    title="第一章",
                    url=url,
                    chapter_number=1,
                )
            )


@pytest.mark.asyncio
async def test_fetch_chapter_content_keeps_real_content_with_counters():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleContent": {},
    })
    html = (
        '<div class="chapter_content_box">'
        "<p>第一段<span class=\"z count_0\">0</span></p>"
        "<p>第二段<span class=\"z count_1\">12</span></p>"
        "</div>"
    )
    url = "https://example.com/book/1/1.html"

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        content = await plugin.fetch_chapter_content(
            SimpleNamespace(
                source_chapter_id=url,
                title="第一章",
                url=url,
                chapter_number=1,
            )
        )

    assert "第一段" in content
    assert "第二段" in content
    # Comment counters must not leak into the stored text.
    assert "\n0" not in content
    assert "\n12" not in content

@pytest.mark.asyncio
async def test_concurrent_chapters_keep_independent_page_context():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleContent": {"content": "@js:baseUrl"},
    })
    first_url = "https://example.com/book/1/first.html"
    second_url = "https://example.com/book/1/second.html"

    async def fake_get(url):
        # Force the first request to resume after the second request has
        # already entered the parser, reproducing the shared-engine race.
        if url == first_url:
            await asyncio.sleep(0.02)
        return "<html><body>chapter</body></html>"

    plugin._get = fake_get
    first, second = await asyncio.gather(
        plugin.fetch_chapter_content(SimpleNamespace(url=first_url, title="一")),
        plugin.fetch_chapter_content(SimpleNamespace(url=second_url, title="二")),
    )

    assert first == first_url
    assert second == second_url


# ---- JS-based explore / search URL support ----

def test_get_explore_kinds_evaluates_js_explore_url():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.boluomao.com",
        "exploreUrl": (
            "@js:\n"
            "var r=[{title:'男频',url:'/gender/boy/page/{{page}}/'},"
            "{title:'女频',url:'/gender/girl/page/{{page}}/'}];\n"
            "JSON.stringify(r);"
        ),
    })
    plugin.engine._try_eval_js = lambda code, raw=None, extra_context=None: (
        '[{"title":"男频","url":"/gender/boy/page/{{page}}/"},'
        '{"title":"女频","url":"/gender/girl/page/{{page}}/"}]'
    )
    kinds = plugin.get_explore_kinds()
    assert len(kinds) == 2
    assert kinds[0] == {"title": "男频", "url": "/gender/boy/page/{{page}}/"}
    assert kinds[1] == {"title": "女频", "url": "/gender/girl/page/{{page}}/"}


def test_get_explore_kinds_js_failure_returns_empty():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.boluomao.com",
        "exploreUrl": "@js:throw new Error('boom')",
    })
    plugin.engine._try_eval_js = lambda code, raw=None, extra_context=None: None
    assert plugin.get_explore_kinds() == []


def test_get_explore_kinds_plain_text_format_still_works():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "exploreUrl": "玄幻::/fenlei/1.html\n都市::/fenlei/2.html",
    })
    kinds = plugin.get_explore_kinds()
    assert len(kinds) == 2
    assert kinds[0]["url"] == "/fenlei/1.html"


def test_resolve_kind_url_strips_legado_options_suffix():
    plugin = YueduPlugin({"bookSourceUrl": "https://www.boluomao.com"})
    resolved = plugin._resolve_kind_url(
        "/gender/boy/page/{{page}}/,{\"webView\":true}", 2
    )
    assert resolved == "https://www.boluomao.com/gender/boy/page/2/"


def test_resolve_kind_url_evaluates_js_template():
    plugin = YueduPlugin({"bookSourceUrl": "https://www.boluomao.com"})
    def _eval(code, raw=None, extra_context=None):
        return "" if "''" in code else "https://www.boluomao.com/tag/x/"
    plugin.engine._try_eval_js = _eval
    assert plugin._resolve_kind_url("@js:1+1", 1) == (
        "https://www.boluomao.com/tag/x/"
    )
    assert plugin._resolve_kind_url("@js:return ''", 1) == ""


def test_fetch_explore_uses_resolved_js_kind_url():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.boluomao.com",
        "bookUrlPattern": "https?://www\\.boluomao\\.com/book/\\d+\\.html",
        "exploreUrl": "@js:var r=[{title:'男频',url:'/gender/boy/page/{{page}}/'}];JSON.stringify(r);",
    })
    html = '<div class="book-list"><a href="/book/123.html">书名</a></div>'

    async def fake_get(url):
        assert url == "https://www.boluomao.com/gender/boy/page/1/"
        return html

    plugin._get = fake_get
    items = asyncio.run(plugin.fetch_explore(page=1))
    assert items
    urls = [str(i.get("bookUrl") or i.get("url") or "") for i in items]
    assert any(u.endswith("/book/123.html") for u in urls)


def test_search_books_with_js_search_url():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.boluomao.com",
        "bookUrlPattern": "https?://www\\.boluomao\\.com/book/\\d+\\.html",
        "searchUrl": "@js:(function(){return 'https://www.boluomao.com/search?q=' + key;})()",
        "ruleSearch": {
            "bookList": ".[?(@.title)]",
            "name": "title",
            "author": "author",
            "bookUrl": "url",
        },
    })

    def fake_eval(code, raw=None, extra_context=None):
        key = (extra_context or {}).get("key", "")
        return "https://www.boluomao.com/search?q=" + key

    plugin.engine._try_eval_js = fake_eval

    async def fake_get(url):
        assert "q=测试" in url
        return (
            '[{"title":"测试书","author":"作者A",'
            '"url":"https://www.boluomao.com/book/100.html"}]'
        )

    plugin._get = fake_get
    results = asyncio.run(plugin.search_books("测试"))
    assert results
    assert results[0]["name"] == "测试书"
    assert results[0]["bookUrl"].endswith("/book/100.html")


def test_search_books_js_url_failure_returns_empty():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "searchUrl": "@js:throw new Error('no signing')",
    })
    plugin.engine._try_eval_js = lambda code, raw=None, extra_context=None: None
    assert asyncio.run(plugin.search_books("keyword")) == []


def test_jsonpath_filter_expression():
    engine = YueduRuleEngine({"bookSourceUrl": "https://example.com"})
    data = [
        {"title": "A", "vip": True, "price": 10},
        {"title": "B", "vip": False, "price": 0},
        {"title": "C", "vip": True, "price": 5},
    ]
    assert [i["title"] for i in engine._jsonpath(data, ".[?(@.title)]")] == ["A", "B", "C"]
    assert [i["title"] for i in engine._jsonpath(data, "$[?(@.vip == true)]")] == ["A", "C"]
    assert [i["title"] for i in engine._jsonpath(data, "$[?(@.price > 3)]")] == ["A", "C"]
    assert [i["title"] for i in engine._jsonpath(data, ".[?(@.title == 'B')]")] == ["B"]


def test_chapter_context_provided_to_rule_engine():
    from types import SimpleNamespace

    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    chapter = SimpleNamespace(title="第1章", url="https://example.com/1.html", tags="")
    plugin.engine.set_chapter_context(
        {"title": chapter.title, "url": chapter.url, "tag": chapter.tags}
    )
    ctx = plugin.engine._build_js_context()
    assert ctx["chapter"]["title"] == "第1章"
    assert ctx["chapter"]["url"] == chapter.url
    assert ctx["url"] == "https://example.com"


def test_js_runtime_shim_supports_legado_apis():
    """Integration: the Node.js jsoup shim must handle the APIs used by
    sources like 菠萝猫 (jsoup toArray/text) and UAA (cache/Get/Put,
    java.md5Encode/hexDecodeToString, expression completion values)."""
    import shutil

    from app.crawler.plugins.yuedu.js_runtime import JsRuntime

    if shutil.which("node") is None:
        pytest.skip("Node.js not available")
    rt = JsRuntime.get_instance()
    try:
        if not rt.start_sync():
            pytest.skip("Node.js runtime failed to start")
        ctx = {
            "baseUrl": "https://www.boluomao.com",
            "bookSourceUrl": "https://www.boluomao.com",
            "bookSourceName": "菠萝猫",
            "key": "测试",
            "page": 2,
        }
        r = rt.eval_js_sync(
            "var doc = org.jsoup.Jsoup.parse(result);"
            "var ps = doc.select('div.content p').toArray();"
            "var t=[]; for (var i=0;i<ps.length;i++){ t.push(ps[i].text()); }"
            "t.join('\\n');",
            "<div class='content'><p>一</p><p>二</p></div>",
            context=ctx,
        )
        assert r == "一\n二"
        r = rt.eval_js_sync(
            "Put('k','v9');"
            "cache.put('tmp','1',10);"
            "var a = cache.get('tmp'); cache.delete('tmp');"
            "var b = cache.get('tmp');"
            "java.md5Encode('abc') + '|' + java.hexDecodeToString('6869') "
            "+ '|' + Get('k') + '|' + a + '|' + b;",
            None,
            context=ctx,
        )
        assert r == "900150983cd24fb0d6963f7d28e17f72|hi|v9|1|"
        r = rt.eval_js_sync(
            "var res = java.get('https://www.boluomao.com/', "
            "{'User-Agent':'x'}); typeof res.body;",
            None,
            context=ctx,
        )
        assert r == "function"
        r = rt.eval_js_sync(
            "var x = [{title:'A',url:'/a/'}]; JSON.stringify(x);",
            None,
            context=ctx,
        )
        assert r == '[{"title":"A","url":"/a/"}]'
    finally:
        JsRuntime.reset_instance()


# ---------- DNS pollution bypass & anti-bot page detection ----------

GOEDGE_CAPTCHA_HTML = """
<!DOCTYPE html><html><head><title>身份验证</title></head><body>
<form method="POST" id="captcha-form">
<input type="hidden" name="GOEDGE_WAF_CAPTCHA_ID" value="dd27afc513cc36d5"/>
<div class="ui-image"><img id="ui-captcha-image" src="/WAF/VERIFY/CAPTCHA?info=xxx"/></div>
<p class="ui-prompt">请输入上面的验证码</p>
<input type="text" name="GOEDGE_WAF_CAPTCHA_CODE" id="GOEDGE_WAF_CAPTCHA_CODE"/>
</form><address>请求ID: 123456</address></body></html>
"""


def test_is_blocked_page_detects_goedge_waf_captcha():
    assert YueduPlugin._is_blocked_page(GOEDGE_CAPTCHA_HTML) is True


def test_is_blocked_page_detects_generic_identity_verification():
    html = "<html><head><title>身份验证</title></head><body>请输入上面的验证码</body></html>"
    assert YueduPlugin._is_blocked_page(html) is True


def test_is_blocked_page_does_not_flag_normal_page():
    html = "<html><head><title>乱伦小说</title></head><body><div class='list'>book</div></body></html>"
    assert YueduPlugin._is_blocked_page(html) is False


def test_looks_polluted_detects_loopback_resolution():
    # 127.0.0.1 / ::1 / 0.0.0.0 are the classic GFW poisoning answers.
    assert YueduPlugin._looks_polluted("localhost") is True
    assert YueduPlugin._looks_polluted("127.0.0.1") is True


def test_doh_rewrite_uses_cached_ip():
    plugin = YueduPlugin({"bookSourceUrl": "https://www.alicesw.com"})
    plugin.__class__._doh_cache["www.alicesw.com"] = {
        "ip": "38.46.217.34",
        "expires": time.time() + 100,
    }
    rewritten = plugin._doh_rewrite("https://www.alicesw.com/lists/65.html")
    assert rewritten == (
        "https://38.46.217.34/lists/65.html",
        "www.alicesw.com",
    )


def test_doh_rewrite_returns_none_without_cache():
    plugin = YueduPlugin({"bookSourceUrl": "https://www.example.com"})
    assert plugin._doh_rewrite("https://www.example.com/a.html") is None


@pytest.mark.asyncio
async def test_resolve_via_doh_caches_and_skips_poisoned_answers():
    plugin = YueduPlugin({"bookSourceUrl": "https://www.example.com"})
    plugin.__class__._doh_cache.pop("www.example.com", None)

    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self._closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            self._closed = True

        async def get(self, url, params=None, headers=None):
            calls["n"] += 1
            if calls["n"] == 1:
                # First provider returns only poisoned answers.
                return FakeResponse({"Answer": [{"data": "127.0.0.1"}]})
            return FakeResponse({"Answer": [{"data": "93.184.216.34"}]})

    with patch("httpx.AsyncClient", FakeClient):
        ip = await plugin._resolve_via_doh("www.example.com")
    assert ip == "93.184.216.34"
    assert calls["n"] == 2
    # Second call hits the cache.
    with patch("httpx.AsyncClient", FakeClient):
        ip2 = await plugin._resolve_via_doh("www.example.com")
    assert ip2 == "93.184.216.34"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_fetch_explore_propagates_blocked_kind_error():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.boluomao.com",
        "exploreUrl": "男生::/gender/boy/page/{{page}}/",
    })

    async def fake_get(url):
        raise RuntimeError("Site returned an anti-bot/captcha page: " + url)

    with patch.object(plugin, "_get", fake_get):
        with pytest.raises(RuntimeError, match="anti-bot"):
            await plugin.fetch_explore(page=1)


@pytest.mark.asyncio
async def test_fetch_explore_reports_js_explore_url_without_kinds():
    plugin = YueduPlugin({
        "bookSourceUrl": "UAA小说xh",
        "exploreUrl": "<js>\neval(String(Reload('https://qyyuapi.com/qt/js/UAA小说/exploreUrl.js')));\n</js>",
    })
    with pytest.raises(RuntimeError, match="Legado"):
        await plugin.fetch_explore(page=1)
