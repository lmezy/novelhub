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
    assert engine._substitute(
        "https://example.com/sort/%257B%257Bpage%257D%257D/", page="4"
    ) == "https://example.com/sort/4/"


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


def test_js_chapter_list_rule_can_build_chapters_from_book_variable():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "ruleToc": {
            "chapterList": "@js:[{\"name\": book.name || \"正文\", \"url\": baseUrl}]",
            "chapterName": "name",
            "chapterUrl": "url",
        },
    })
    plugin.engine.set_book({
        "name": "My Book",
        "author": "Me",
        "url": "https://example.com/book/1",
    })
    plugin.engine.set_page_url("https://example.com/book/1")

    toc = plugin.engine.parse_toc("<html><body>irrelevant</body></html>")

    assert toc == [{
        "chapterName": "My Book",
        "chapterUrl": "https://example.com/book/1",
    }]


def test_book_id_from_url_strips_options_suffix():
    assert YueduPlugin._book_id_from_url(
        'https://www.yaoluku.com/book/57076/,{"webView":true}'
    ) == "https://www.yaoluku.com/book/57076"


def test_is_book_url_ignores_options_suffix():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.yaoluku.com",
        "bookUrlPattern": r"https?://www\.yaoluku\.com/book/\d+",
    })
    assert plugin._is_book_url(
        'https://www.yaoluku.com/book/57076/,{"webView":true}',
        require_pattern=True,
    )


def test_book_url_pattern_does_not_match_chapter_url():
    r"""A loose ``bookUrlPattern`` like ``book/\d+`` must not classify a
    chapter URL under ``/book/{id}/{chapter}.html`` as another book page.
    Otherwise ``_is_chapter_url`` rejects every chapter and the book syncs
    with zero chapters (要撸小说 / yaoluku.com)."""
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.yaoluku.com",
        "bookUrlPattern": r"https?://www\.yaoluku\.com/book/\d+",
    })
    book_url = "https://www.yaoluku.com/book/35979/"
    chapter_url = "https://www.yaoluku.com/book/35979/399068.html"
    assert plugin._is_book_url(book_url, require_pattern=True) is True
    assert plugin._is_book_url(chapter_url, require_pattern=True) is False
    assert plugin._is_chapter_url(chapter_url, book_url) is True


def test_css_attribute_selector_not_parsed_as_legado_index():
    """A rule like ``a[href*='next']@href`` must use the CSS attribute
    selector, not be mis-parsed as a Legado index (which would select every
    child element and make ``nextContentUrl`` fetch nav/javascript links)."""
    eng = YueduRuleEngine({
        "bookSourceUrl": "https://example.com",
        "ruleContent": {
            "content": "",
            "nextContentUrl": "a[href*='next']@href",
        },
    })
    html = (
        "<html><body>"
        "<a href='/book/1/next'>下一章</a>"
        "<a href='/book/1/2.html'>第2章</a>"
        "<a href=\"javascript:alert('x')\">noop</a>"
        "</body></html>"
    )
    book_url = "https://example.com/book/1/"
    eng.set_page_url(book_url)
    nexts = eng.get_next_content_urls(html, book_url)
    assert "https://example.com/book/1/next" in nexts
    assert "https://example.com/book/1/2.html" not in nexts
    assert not any("javascript" in u for u in nexts)


def test_css_attribute_selector_extracts_meta_and_attr():
    """CSS attribute selectors for metadata (og:novel:book_name, author links)
    must extract the target field instead of returning all children."""
    eng = YueduRuleEngine({"bookSourceUrl": "https://example.com"})
    html = (
        "<html><head>"
        "<meta property='og:novel:book_name' content='书名'>"
        "<meta property='og:novel:author' content='作者名'>"
        "</head><body>"
        "<div class='li_bottom'><a href='/author/1/'>作者名</a></div>"
        "</body></html>"
    )
    name = eng._eval_field(html, "meta[property='og:novel:book_name']@content")
    assert name == "书名"
    author = eng._eval_field(html, "div.li_bottom a[href^='/author/']@text")
    assert author == "作者名"


def test_js_content_rule_supports_src_and_base64_decode():
    """要撸小说 ruleContent uses ``String(src)`` + ``java.base64Decode`` to
    decode a base64-encoded chapter body; both must be supported by the shim."""
    import base64 as _b64
    import shutil

    if shutil.which("node") is None:
        pytest.skip("node.js not available")

    story = "这是正文内容，用于测试 base64 解码。结尾。"
    encoded = _b64.b64encode(story.encode("utf-8")).decode("ascii")
    html = (
        '<html><body><div id="box" encoded="%s"></div></body></html>' % encoded
    )
    rule = (
        "@js:\n"
        "var m = String(src).match(/encoded\\s*=\\s*\"([^\"]+)\"/);\n"
        "if (m) { String(java.base64Decode(m[1])); } else { \"\"; }\n"
    )
    eng = YueduRuleEngine({
        "bookSourceUrl": "https://example.com",
        "ruleContent": {"content": rule},
    })
    eng.set_page_url("https://example.com/book/1/")
    out = eng.parse_content(html)
    assert story in out


@pytest.mark.asyncio
async def test_js_runtime_handles_large_result():
    """Long chapter bodies must not trip the Node subprocess 64KiB readline
    limit (``asyncio.LimitOverrunError`` / "chunk exceed the limit")."""
    import shutil

    if shutil.which("node") is None:
        pytest.skip("node.js not available")

    story = ("很长很长的正文。" * 4000)  # ~ 72 KiB of text
    rule = "@js: String(src)"
    eng = YueduRuleEngine({
        "bookSourceUrl": "https://example.com",
        "ruleContent": {"content": rule},
    })
    eng.set_page_url("https://example.com/book/1/")
    out = eng.parse_content(story)
    assert len(out) >= len(story) - 10


def test_looks_like_upstream_error():
    assert YueduPlugin._looks_like_upstream_error(
        "<html><body>Web server is returning an unknown error Error code 520</body></html>"
    ) is True
    assert YueduPlugin._looks_like_upstream_error(
        "<html><title>Error 520</title><body>cf-ray: abc</body></html>"
    ) is True
    assert YueduPlugin._looks_like_upstream_error(
        "<html><body><h1>鬼父：母女花丧失</h1><div>正文</div></body></html>"
    ) is False


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


@pytest.mark.asyncio
async def test_fetch_cover_strips_url_options_suffix():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "concurrentRate": "0",
    })
    captured: dict[str, object] = {}

    class FakeClient:
        async def get(self, url, headers=None):
            captured["url"] = url
            request = httpx.Request("GET", url)
            return httpx.Response(
                200,
                request=request,
                content=b"x" * 256,
                headers={"content-type": "image/jpeg"},
            )

    with (
        patch.object(plugin, "_get_http_client", AsyncMock(return_value=FakeClient())),
        patch.object(plugin, "_capture_cookie_jar"),
        patch("asyncio.sleep", AsyncMock()),
        patch(
            "app.services.proxy_config.get_proxy_config",
            return_value=ProxyConfig(enabled=False),
        ),
    ):
        result = await plugin.fetch_cover(
            'https://example.com/cover/1.jpg,{"webView":true}'
        )

    assert captured["url"] == "https://example.com/cover/1.jpg"
    assert result is not None
    assert result[1] == "image/jpeg"


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


def test_build_headers_merges_source_and_session_cookies():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "header": '{"Cookie":"isSimplified=1"}',
    })
    plugin.set_cookie("session=abc")

    assert plugin._build_headers()["Cookie"] == "isSimplified=1; session=abc"


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stale_error",
    [httpx.ReadTimeout("read timed out"), httpx.ReadError("connection reset")],
)
async def test_get_resets_pooled_client_after_transport_error_and_succeeds(
    stale_error,
):
    """A stale pooled socket must not burn three 60s read timeouts.

    要撸小说 could only be reached through the Clash/mihomo proxy, and every
    time that proxy restarted the worker's pooled connection hung until the
    read timeout.  The request now drops the client and retries on a fresh
    connection instead of failing.
    """
    YueduPlugin._clients.clear()
    YueduPlugin._transport_bad_until.clear()
    YueduPlugin._transport_preferred.clear()
    plugin = YueduPlugin({
        "bookSourceUrl": "https://stale-proxy.example.com",
        "concurrentRate": "0",
    })

    calls = {"n": 0}
    reset_calls: list[str | None] = []

    class FakeResponse:
        status_code = 200
        headers = {}
        text = "<html>ok</html>"
        content = b"<html>ok</html>"

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def get(self, url, headers=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise stale_error
            return FakeResponse()

        async def aclose(self):
            return None

    async def fake_reset(proxy):
        reset_calls.append(proxy)

    try:
        with (
            patch.object(
                plugin,
                "_get_http_client",
                AsyncMock(return_value=FakeClient()),
            ),
            patch.object(plugin, "_reset_http_client", side_effect=fake_reset),
            patch("asyncio.sleep", AsyncMock()),
            patch(
                "app.services.proxy_config.get_proxy_config",
                return_value=ProxyConfig(
                    enabled=True,
                    https_proxy="http://127.0.0.1:27890",
                    http_proxy="http://127.0.0.1:27890",
                ),
            ),
        ):
            html = await plugin._get("https://stale-proxy.example.com/page")
    finally:
        YueduPlugin._clients.clear()
        YueduPlugin._transport_bad_until.clear()
        YueduPlugin._transport_preferred.clear()

    assert html == "<html>ok</html>"
    assert calls["n"] == 2
    assert reset_calls == ["http://127.0.0.1:27890"]


def test_ordered_transports_prefers_last_success_and_demotes_failures():
    YueduPlugin._transport_bad_until.clear()
    YueduPlugin._transport_preferred.clear()
    plugin = YueduPlugin({"bookSourceUrl": "https://order.example.com"})
    proxy = "http://127.0.0.1:27890"

    try:
        assert plugin._ordered_transports(proxy)[0] == proxy

        plugin._mark_transport_success(None)
        assert plugin._ordered_transports(proxy)[0] is None

        plugin._mark_transport_success(proxy)
        assert plugin._ordered_transports(proxy)[0] == proxy

        # A proxy that just hung is tried after direct, but is still retried
        # (some sources are only reachable through the proxy).
        plugin._mark_transport_failure(proxy)
        assert plugin._ordered_transports(proxy)[0] is None
        assert proxy in plugin._ordered_transports(proxy)
    finally:
        YueduPlugin._transport_bad_until.clear()
        YueduPlugin._transport_preferred.clear()


@pytest.mark.asyncio
async def test_get_uses_browser_fallback_for_http_block_response():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "concurrentRate": "0",
    })
    request = httpx.Request("GET", "https://example.com/page")
    blocked_response = httpx.Response(403, request=request)
    captured: dict[str, object] = {}

    class FakeClient:
        async def get(self, url, headers=None):
            return blocked_response

    async def fake_browser(url, web_js="", **kwargs):
        captured.update(url=url, web_js=web_js, options=kwargs)
        return "<html><body>browser page</body></html>"

    with (
        patch.object(plugin, "_get_http_client", AsyncMock(return_value=FakeClient())),
        patch.object(plugin, "_get_with_web_js", fake_browser),
        patch("asyncio.sleep", AsyncMock()),
        patch(
            "app.services.proxy_config.get_proxy_config",
            return_value=ProxyConfig(enabled=False),
        ),
    ):
        html = await plugin._get("https://example.com/page")

    assert html == "<html><body>browser page</body></html>"
    assert captured["url"] == "https://example.com/page"
    assert captured["options"]["fallback_http"] is False


@pytest.mark.asyncio
async def test_post_uses_browser_fallback_for_http_block_response():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "concurrentRate": "3",
    })
    request = httpx.Request("POST", "https://example.com/search")
    blocked_response = httpx.Response(403, request=request)
    captured: dict[str, object] = {}

    class FakeClient:
        async def post(self, url, headers=None, data=None, json=None, content=None):
            return blocked_response

    async def fake_browser(url, web_js="", **kwargs):
        captured.update(url=url, web_js=web_js, options=kwargs)
        return "<html><body>browser page</body></html>"

    with (
        patch.object(plugin, "_get_http_client", AsyncMock(return_value=FakeClient())),
        patch.object(plugin, "_get_with_web_js", fake_browser),
        patch("asyncio.sleep", AsyncMock()),
        patch(
            "app.services.proxy_config.get_proxy_config",
            return_value=ProxyConfig(enabled=False),
        ),
    ):
        html = await plugin._post("https://example.com/search", body="q=x")

    assert html == "<html><body>browser page</body></html>"
    assert captured["url"] == "https://example.com/search"
    assert captured["options"]["fallback_http"] is False


def test_split_options_suffix_extracts_webview():
    plugin = YueduPlugin({"bookSourceUrl": "https://yaoluku.example.com"})
    clean, options = plugin._split_options_suffix(
        "https://yaoluku.example.com/book/123/,{\"webView\":true}"
    )
    assert clean == "https://yaoluku.example.com/book/123/"
    assert options is not None
    assert options["web_view"] is True
    # A plain URL is untouched.
    assert plugin._split_options_suffix("https://yaoluku.example.com/book/123/") == (
        "https://yaoluku.example.com/book/123/",
        None,
    )


@pytest.mark.asyncio
async def test_get_dispatches_webview_suffix_to_browser():
    plugin = YueduPlugin({"bookSourceUrl": "https://yaoluku.example.com"})
    captured: dict[str, object] = {}

    async def fake_browser(url, web_js="", **kwargs):
        captured.update(url=url, web_js=web_js, kwargs=kwargs)
        return "<html><body>ok</body></html>"

    with patch.object(plugin, "_get_with_web_js", fake_browser):
        html = await plugin._get(
            "https://yaoluku.example.com/book/123/,{\"webView\":true}"
        )

    assert html == "<html><body>ok</body></html>"
    # The suffix must not be part of the requested URL.
    assert captured["url"] == "https://yaoluku.example.com/book/123/"


@pytest.mark.asyncio
async def test_get_forces_browser_when_url_requests_webview():
    """A ``,{"webView":true}`` suffix must never fall back to plain HTTP."""
    plugin = YueduPlugin({"bookSourceUrl": "https://yaoluku.example.com"})
    captured: dict[str, object] = {}

    async def fake_browser(url, web_js="", **kwargs):
        captured.update(
            url=url,
            web_js=web_js,
            fallback_http=kwargs.get("fallback_http"),
        )
        return "<html><body>ok</body></html>"

    with patch.object(plugin, "_get_with_web_js", fake_browser):
        html = await plugin._get(
            "https://yaoluku.example.com/book/123/,{\"webView\":true}"
        )

    assert html == "<html><body>ok</body></html>"
    assert captured["url"] == "https://yaoluku.example.com/book/123/"
    # webView means the site only serves the page to a browser; a HTTP fallback
    # would just re-request a challenge page and hide the real cause.
    assert captured["fallback_http"] is False


@pytest.mark.asyncio
async def test_get_with_web_js_forces_browser_for_webview(monkeypatch):
    """webView URLs must use the browser and never silently fall back to HTTP."""
    plugin = YueduPlugin({"bookSourceUrl": "https://yaoluku.example.com"})

    async def fake_get(url, **kwargs):
        raise AssertionError("webView URL must not fall back to plain HTTP")

    plugin._get = fake_get  # type: ignore[assignment]

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright" or name == "playwright.async_api":
            raise ImportError("playwright not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="Playwright is not installed"):
        await plugin._get_with_web_js(
            "https://yaoluku.example.com/book/123/,{\"webView\":true}"
        )


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


@pytest.mark.asyncio
async def test_search_books_resolves_relative_post_url_and_preserves_charset():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://yaoluku.example/",
        "searchUrl": '/search/,{"method":"POST","charset":"gbk","body":"searchkey={{key}}"}',
        "ruleSearch": {
            "bookList": "div.result",
            "name": "a@text",
            "bookUrl": "a@href",
        },
        "bookUrlPattern": r"https?://yaoluku\.example/book/\d+/",
        "concurrentRate": "0",
    })
    captured: dict[str, object] = {}

    async def fake_post(url, body=None, headers=None, charset=None):
        captured.update(url=url, body=body, headers=headers, charset=charset)
        return '<div class="result"><a href="/book/1/">Book</a></div>'

    with patch.object(plugin, "_post", fake_post):
        items = await plugin.search_books("hello")

    assert captured["url"] == "https://yaoluku.example/search/"
    assert captured["charset"] == "gbk"
    assert captured["body"] == "searchkey=hello"
    assert items[0]["bookUrl"] == "https://yaoluku.example/book/1/"


def test_response_text_honors_source_charset_before_utf8_fallback():
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(
        200,
        content="繁體中文書名".encode("big5"),
        headers={"content-type": "text/html"},
        request=request,
    )
    assert YueduPlugin._response_text(response, "big5") == "繁體中文書名"


def test_book_url_pattern_accepts_www_alias():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://yaoluku.example",
        "bookUrlPattern": r"https?://yaoluku\.example/book/\d+/",
    })
    assert plugin._is_book_url(
        "https://www.yaoluku.example/book/123/",
        require_pattern=True,
    ) is True


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


def test_resolve_kind_updates_options_url_after_page_substitution():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    resolved, options = plugin._resolve_kind(
        "/sort/{{page}}/,{\"webView\":true}", 3
    )
    assert resolved == "https://example.com/sort/3/"
    assert options["url"] == resolved


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
            "java.setContent(result); java.getString('.page-content@tag.p.0@html');",
            "<div class='page-content'><p>正文</p></div>",
            context=ctx,
        )
        assert r == "正文"
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


def test_is_challenge_page_detects_cloudflare():
    html = (
        "<html><head><title>Just a moment...</title></head>"
        "<body><div class=\"cf-chl\">Checking your browser</div></body></html>"
    )
    assert YueduPlugin._is_challenge_page(html) is True
    # A clean rendered page must not be treated as a pending challenge.
    assert YueduPlugin._is_challenge_page(
        "<html><head><title>某小说</title></head><body>正文内容</body></html>"
    ) is False


def test_is_blocked_page_detects_cloudflare_challenge():
    html = (
        "<html><head><title>Attention Required! | Cloudflare</title>"
        "<script src=\"/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page\"></script>"
        "</head><body>Attention Required! | Cloudflare</body></html>"
    )
    assert YueduPlugin._is_blocked_page(html) is True


@pytest.mark.asyncio
async def test_wait_for_challenge_waits_until_content_ready():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})

    contents = [
        "<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>",
        "<html><body><div>real content</div></body></html>",
    ]
    page = SimpleNamespace(
        content=AsyncMock(side_effect=contents),
        wait_for_timeout=AsyncMock(),
        reload=AsyncMock(),
    )
    context = SimpleNamespace(cookies=AsyncMock(return_value=[]))

    html = await plugin._wait_for_challenge(context, page, "https://example.com/x", timeout=30)

    assert html == "<html><body><div>real content</div></body></html>"
    assert page.wait_for_timeout.await_count == 1
    page.reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_for_challenge_captures_session_cookies():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})
    captured: dict[str, object] = {}

    def fake_capture(cookies):
        captured["cookies"] = cookies

    page = SimpleNamespace(
        content=AsyncMock(return_value="<html><body>ok</body></html>"),
        wait_for_timeout=AsyncMock(),
        reload=AsyncMock(),
    )
    context = SimpleNamespace(
        cookies=AsyncMock(return_value=[{"name": "cf_clearance", "value": "abc"}])
    )

    with patch.object(plugin, "_capture_playwright_cookies", side_effect=fake_capture):
        html = await plugin._wait_for_challenge(
            context, page, "https://example.com/x", timeout=30
        )

    assert html == "<html><body>ok</body></html>"
    assert captured["cookies"] == [{"name": "cf_clearance", "value": "abc"}]


@pytest.mark.asyncio
async def test_fetch_explore_webview_uses_browser():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://yaoluku.example.com",
        "exploreUrl": "最新::/sort/{{page}}/,{\"webView\":true}",
    })
    captured: dict[str, object] = {}
    items = [{"bookUrl": "https://yaoluku.example.com/book/1", "name": "书"}]

    async def fake_browser(url, web_js="", **kwargs):
        captured.update(url=url, web_js=web_js, kwargs=kwargs)
        return "<html><body><li>book</li></body></html>"

    with (
        patch.object(plugin, "_explore_items_from_html", return_value=items),
        patch.object(plugin, "_get_with_web_js", fake_browser),
    ):
        result = await plugin.fetch_explore(page=1)

    assert captured["url"] == "https://yaoluku.example.com/sort/1/"
    assert captured["web_js"] == ""
    assert [i["bookUrl"] for i in result] == ["https://yaoluku.example.com/book/1"]


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
async def test_fetch_explore_reports_upstream_catalog_error():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "exploreUrl": "最新::/sort/{{page}}/",
    })

    async def fake_get(url):
        request = httpx.Request("GET", url)
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("403 Forbidden", request=request, response=response)

    with patch.object(plugin, "_get", fake_get):
        with pytest.raises(RuntimeError, match="目录暂时不可访问"):
            await plugin.fetch_explore(page=1)


@pytest.mark.asyncio
async def test_fetch_explore_reports_js_explore_url_without_kinds():
    plugin = YueduPlugin({
        "bookSourceUrl": "UAA小说xh",
        "exploreUrl": "<js>\neval(String(Reload('https://qyyuapi.com/qt/js/UAA小说/exploreUrl.js')));\n</js>",
    })
    with pytest.raises(RuntimeError, match="Legado"):
        await plugin.fetch_explore(page=1)


def test_inside_navigation_detects_site_menu_blocks():
    soup = BeautifulSoup(
        '<nav class="container"><a href="/sort/1/1/">玄幻</a></nav>'
        '<div class="navigation"><a href="/sort/2/1/">武侠</a></div>'
        '<div class="book-tags"><a href="/tag/x/">修真</a></div>'
        '<div class="book-info"><a href="/author/1/">作者</a></div>',
        "lxml",
    )

    assert YueduPlugin._inside_navigation(soup.select_one("nav a")) is True
    assert YueduPlugin._inside_navigation(soup.select_one(".navigation a")) is True
    assert YueduPlugin._inside_navigation(soup.select_one(".book-tags a")) is False
    assert YueduPlugin._inside_navigation(soup.select_one(".book-info a")) is False


def test_clean_tags_drops_numeric_ids_and_status_words():
    plugin = YueduPlugin({"bookSourceUrl": "https://example.com"})

    tags = plugin._clean_tags(
        ["1013798727695077372", "1234", "连载", "完本", "玄幻奇幻"],
        "画壁",
        "念湫",
    )

    assert tags == ["玄幻奇幻"]


def test_clean_listing_kind_drops_ranking_titles():
    assert YueduPlugin._clean_listing_kind("周排行") == ""
    assert YueduPlugin._clean_listing_kind("新作榜") == ""
    assert YueduPlugin._clean_listing_kind("最近更新") == ""
    assert YueduPlugin._clean_listing_kind("全部小说") == ""
    assert YueduPlugin._clean_listing_kind("都市") == "都市"
    assert YueduPlugin._clean_listing_kind("乱伦") == "乱伦"


@pytest.mark.asyncio
async def test_discover_books_ignores_ranking_titles_as_tags():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.banshanren.com",
        "bookUrlPattern": r"https://www\.banshanren\.com/book/\d+\.html",
        "concurrentRate": "0",
    })
    items = [{
        "name": "Book",
        "author": "Author",
        "bookUrl": "https://www.banshanren.com/book/123.html",
        "exploreKind": "周排行",
        "kind": "奇幻玄幻",
    }]

    with patch.object(plugin, "fetch_explore", AsyncMock(return_value=items)):
        books = await plugin.discover_books()

    assert len(books) == 1
    assert books[0].tags == ["奇幻玄幻"]


@pytest.mark.asyncio
async def test_fetch_book_rule_category_wins_over_nav_menu_tags():
    """要撸小说 的书籍标签曾变成站点导航（书库/武侠/都市…）。

    The generic scraper used to append the ``<nav>`` category menu on top of
    the rule's real category (``ruleBookInfo.kind``).
    """
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.yaoluku.com",
        "concurrentRate": "0",
        "ruleBookInfo": {
            "kind": "meta[property='og:novel:category']@content",
        },
        "ruleToc": {
            "chapterList": "ul#chapters li",
            "chapterName": "a@text",
            "chapterUrl": "a@href",
        },
    })
    html = """<html><head>
      <meta property="og:novel:category" content="玄幻奇幻">
      <meta name="keywords" content="画壁,念湫,玄幻奇幻,连载">
    </head><body>
      <nav class="container">
        <a href="/sort/">书库</a>
        <a href="/sort/1/1/">玄幻</a>
        <a href="/sort/2/1/">武侠</a>
        <a href="/sort/3/1/">都市</a>
      </nav>
      <div class="navigation"><a href="/sort/4/1/">科幻</a></div>
      <h1>画壁</h1>
      <div class="author">念湫</div>
      <ul id="chapters">
        <li><a href="/book/56443/1.html">第1章</a></li>
        <li><a href="/book/56443/2.html">第2章</a></li>
      </ul>
    </body></html>"""

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://www.yaoluku.com/book/56443/")

    assert book.tags == ["玄幻奇幻"]
    assert len(book.chapters) == 2


@pytest.mark.asyncio
async def test_fetch_book_falls_back_to_generic_tags_without_kind_rule():
    """Sources without a kind rule still get keyword metadata as tags."""
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "concurrentRate": "0",
        "ruleToc": {
            "chapterList": "ul#chapters li",
            "chapterName": "a@text",
            "chapterUrl": "a@href",
        },
    })
    html = """<html><head>
      <meta name="keywords" content="画壁,念湫,玄幻奇幻">
    </head><body>
      <nav class="container"><a href="/sort/1/1/">玄幻</a></nav>
      <h1>画壁</h1>
      <div class="author">念湫</div>
      <ul id="chapters">
        <li><a href="/book/56443/1.html">第1章</a></li>
      </ul>
    </body></html>"""

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://example.com/book/56443/")

    assert "玄幻奇幻" in book.tags
    assert "玄幻" not in book.tags


@pytest.mark.asyncio
async def test_fetch_book_noisy_kind_rule_still_uses_generic_tags():
    """A kind rule that only returns noise must not blank out the tags.

    禁忌书屋 declares ``ruleBookInfo.kind = 论坛帖子`` (a constant forum
    label); the book still gets the page's real keywords/tags.
    """
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.cool18.com/bbs4",
        "bookSourceName": "禁忌书屋",
        "concurrentRate": "0",
        "ruleBookInfo": {"kind": "论坛帖子"},
        "ruleToc": {
            "chapterList": "ul#chapters li",
            "chapterName": "a@text",
            "chapterUrl": "a@href",
        },
    })
    html = """<html><head>
      <meta name="keywords" content="标题,作者,都市,调教">
    </head><body>
      <h1>标题</h1>
      <div class="author">作者</div>
      <div class="tags"><a href="/tag/1/">制服</a></div>
      <ul id="chapters">
        <li><a href="/bbs4/thread-1.html">第1章</a></li>
      </ul>
    </body></html>"""

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://www.cool18.com/bbs4/thread-1.html")

    assert "论坛帖子" not in book.tags
    assert {"都市", "调教", "制服"} <= set(book.tags)


@pytest.mark.asyncio
async def test_fetch_book_does_not_use_single_char_author_as_tag():
    """A one-character pen name is not a tag.

    ``要撸小说`` exposes ``求生游戏…`` by author "竹"; the short name is
    rejected as the author but still appears in the page's keyword list.
    """
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.yaoluku.com",
        "concurrentRate": "0",
        "ruleBookInfo": {
            "name": "meta[property='og:novel:book_name']@content",
            "author": "meta[property='og:novel:author']@content",
            "kind": "meta[property='og:novel:category']@content",
        },
        "ruleToc": {
            "chapterList": "ul#chapters li",
            "chapterName": "a@text",
            "chapterUrl": "a@href",
        },
    })
    html = """<html><head>
      <meta property="og:novel:book_name" content="求生游戏">
      <meta property="og:novel:author" content="竹">
      <meta property="og:novel:category" content="精品其他">
      <meta name="keywords" content="求生游戏,竹,精品其他,连载">
    </head><body>
      <h1>求生游戏</h1>
      <ul id="chapters"><li><a href="/book/1/1.html">第1章</a></li></ul>
    </body></html>"""

    with patch.object(plugin, "_get", AsyncMock(return_value=html)):
        book = await plugin.fetch_book("https://www.yaoluku.com/book/56508/")

    assert book.tags == ["精品其他"]


def test_is_book_url_accepts_forum_post_urls():
    """風月文學網 h528 book pages are ``/post/29145.html``.

    The fallback heuristic only knew novel/book/read/detail/xiaoshuo, so its
    whole catalogue was filtered out during discovery.
    """
    plugin = YueduPlugin({"bookSourceUrl": "http://www.h528.com"})

    assert plugin._is_book_url("http://www.h528.com/post/29145.html") is True
    assert plugin._is_book_url("http://www.h528.com/thread/29145.html") is True
    assert plugin._is_book_url(
        "http://www.h528.com/post/category/%e4%ba%ba%e5%a6%bb%e7%86%9f%e5%a5%b3"
    ) is False
    assert plugin._is_book_url("http://www.h528.com/") is False


def test_is_chapter_url_accepts_sibling_posts_without_pattern():
    """h528 keeps the book and its chapters in the same flat directory."""
    plugin = YueduPlugin({"bookSourceUrl": "http://www.h528.com"})

    assert plugin._is_chapter_url(
        "http://www.h528.com/post/29144.html",
        "http://www.h528.com/post/29145.html",
    ) is True
    assert plugin._is_chapter_url(
        "http://www.h528.com/post/category/abc",
        "http://www.h528.com/post/29145.html",
    ) is False


def test_is_chapter_url_rejects_other_book_when_pattern_is_set():
    plugin = YueduPlugin({
        "bookSourceUrl": "https://www.yaoluku.com",
        "bookUrlPattern": r"https://www\.yaoluku\.com/book/\d+",
    })

    assert plugin._is_chapter_url(
        "https://www.yaoluku.com/book/35979/399068.html",
        "https://www.yaoluku.com/book/35979/",
    ) is True
    # Another book's detail page must never be treated as a chapter.
    assert plugin._is_chapter_url(
        "https://www.yaoluku.com/book/56508/",
        "https://www.yaoluku.com/book/56443/",
    ) is False


def test_clean_book_title_keeps_first_line_of_multi_match_rule():
    plugin = YueduPlugin({"bookSourceUrl": "http://www.h528.com"})

    assert plugin._clean_book_title("疑愛6\n分站\n分類\n最新文章") == "疑愛6"
    # A single-line title is only whitespace-normalised.
    assert plugin._clean_book_title("  画壁【女出轨】  ") == "画壁【女出轨】"


@pytest.mark.asyncio
async def test_browser_semaphore_limits_concurrent_chromium(monkeypatch):
    monkeypatch.setenv("YUEDU_PLAYWRIGHT_CONCURRENCY", "2")
    YueduPlugin._browser_semaphores.clear()
    try:
        semaphore = YueduPlugin._browser_semaphore()
        assert YueduPlugin._browser_semaphore() is semaphore

        await semaphore.acquire()
        await semaphore.acquire()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(semaphore.acquire(), timeout=0.05)
        semaphore.release()
        await asyncio.wait_for(semaphore.acquire(), timeout=0.5)
    finally:
        YueduPlugin._browser_semaphores.clear()


@pytest.mark.asyncio
async def test_web_js_browser_path_retries_transient_failure():
    YueduPlugin._browser_semaphores.clear()
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "concurrentRate": "0",
    })
    calls = {"n": 0}

    async def fake_fetch(async_playwright, url, web_js, request_headers):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(
                "Site returned an empty browser page (网站返回了空白页): " + url
            )
        return "<html>ok</html>"

    try:
        with (
            patch.object(plugin, "_fetch_with_playwright", side_effect=fake_fetch),
            patch("asyncio.sleep", AsyncMock()),
        ):
            html = await plugin._get_with_web_js(
                "https://example.com/page",
                "",
                fallback_http=False,
            )
    finally:
        YueduPlugin._browser_semaphores.clear()

    assert html == "<html>ok</html>"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_web_js_captcha_is_not_retried_and_keeps_detail():
    YueduPlugin._browser_semaphores.clear()
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "concurrentRate": "0",
    })
    calls = {"n": 0}

    async def fake_fetch(async_playwright, url, web_js, request_headers):
        calls["n"] += 1
        raise RuntimeError(
            "Site returned an anti-bot/captcha page (网站要求验证码/人机验证): " + url
        )

    try:
        with (
            patch.object(plugin, "_fetch_with_playwright", side_effect=fake_fetch),
            patch("asyncio.sleep", AsyncMock()),
        ):
            with pytest.raises(RuntimeError, match="anti-bot/captcha"):
                await plugin._get_with_web_js(
                    "https://example.com/page",
                    "",
                    fallback_http=False,
                )
    finally:
        YueduPlugin._browser_semaphores.clear()

    # A captcha page will not clear by retrying immediately.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_get_does_not_fall_back_to_direct_on_definitive_404():
    """A 404 from the proxy is final; retrying direct only wasted 26s."""
    YueduPlugin._clients.clear()
    YueduPlugin._transport_bad_until.clear()
    YueduPlugin._transport_preferred.clear()
    plugin = YueduPlugin({
        "bookSourceUrl": "https://example.com",
        "concurrentRate": "0",
    })
    seen_proxies: list[str | None] = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            seen_proxies.append(kwargs.get("proxy"))

        async def get(self, url, headers=None):
            request = httpx.Request("GET", url)
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError(
                "404 Not Found",
                request=request,
                response=response,
            )

        async def aclose(self):
            return None

    try:
        with (
            patch("httpx.AsyncClient", FakeClient),
            patch("asyncio.sleep", AsyncMock()),
            patch(
                "app.services.proxy_config.get_proxy_config",
                return_value=ProxyConfig(
                    enabled=True,
                    https_proxy="http://127.0.0.1:27890",
                    http_proxy="http://127.0.0.1:27890",
                ),
            ),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await plugin._get("https://example.com/book/missing.html")
    finally:
        YueduPlugin._clients.clear()
        YueduPlugin._transport_bad_until.clear()
        YueduPlugin._transport_preferred.clear()

    assert seen_proxies == ["http://127.0.0.1:27890"]
