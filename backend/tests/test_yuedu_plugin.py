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
