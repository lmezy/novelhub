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
