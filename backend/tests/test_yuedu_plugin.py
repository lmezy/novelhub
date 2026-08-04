from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

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


@pytest.mark.asyncio
async def test_get_falls_back_to_direct_when_proxy_unreachable():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "concurrentRate": "0",
    })

    proxy_calls: list[str | None] = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            proxy = self.kwargs.get("proxy")
            proxy_calls.append(proxy)
            if proxy:
                raise httpx.ConnectError("proxy down", request=None)
            self.response = SimpleNamespace(
                status_code=200,
                headers={},
                text="<html>ok</html>",
                raise_for_status=lambda: None,
            )
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return self.response

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

    assert html == "<html>ok</html>"
    assert proxy_calls[0] == "http://127.0.0.1:1"
    assert proxy_calls[-1] is None
