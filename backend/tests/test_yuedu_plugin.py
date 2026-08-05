from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import json

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


def test_substitute_inner_rules_keeps_rules_without_templates():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    assert plugin.engine._substitute_inner_rules("a@text", "<html></html>") == "a@text"


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
