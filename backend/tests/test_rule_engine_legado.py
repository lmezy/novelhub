import shutil

import pytest

from app.crawler.plugins.yuedu.js_runtime import try_eval_js_pattern
from app.crawler.plugins.yuedu.rule_engine import (
    RuleUnbalancedError,
    YueduRuleEngine,
    _RuleAnalyzer,
    normalize_css_selector,
)


def _engine():
    return YueduRuleEngine({"bookSourceUrl": "https://example.com"})


HTML = """
<html><body>
  <div class="site-scroll__list">
    <div class="col-12">
      <h5>Book A</h5>
      <a href="/book/1.html">Read A</a>
    </div>
    <div class="col-12">
      <h5>Book B</h5>
      <a href="/book/2.html">Read B</a>
    </div>
  </div>
  <div class="other">
    <div class="col-12"><h5>Other</h5></div>
  </div>
  <select class="site-selector">
    <option value="0">Volume 0</option>
    <option value="1">Volume 1</option>
    <option value="2">Volume 2</option>
  </select>
  <p class="text-muted">连载中</p>
</body></html>
"""


def test_legado_chain_selector_scopes_children():
    engine = _engine()
    items = engine._get_elements(HTML, ".site-scroll__list@.col-12")
    assert len(items) == 2
    assert items[0].h5.get_text() == "Book A"


def test_normalize_css_selector_quotes_unquoted_attribute_values():
    # 風月文學網 h528 writes `a[href*=/post/][href$=.html]`; soupsieve rejects
    # the unquoted value, which silently produced zero books before.
    assert normalize_css_selector("a[href*=/post/]") == "a[href*='/post/']"
    assert normalize_css_selector("a[href$=.html]") == "a[href$='.html']"
    assert normalize_css_selector("div[class~=a b]") == "div[class~='a b']"
    # Already-quoted values and non-attribute selectors stay untouched.
    assert normalize_css_selector("a[href*='/post/']") == "a[href*='/post/']"
    assert normalize_css_selector('a[href*="/post/"]') == 'a[href*="/post/"]'
    assert normalize_css_selector("ul#list > li.item a") == "ul#list > li.item a"
    assert normalize_css_selector("li[0]") == "li[0]"


def test_explore_list_rule_with_unquoted_attribute_selector():
    engine = YueduRuleEngine({
        "bookSourceUrl": "http://www.h528.com",
        "ruleExplore": {
            "bookList": "a[href*=/post/][href$=.html]",
            "name": "text",
            "bookUrl": "href",
        },
    })
    html = """
    <html><body>
      <a href="/post/123.html">人妻小说 A</a>
      <a href="/post/456.html">家庭亂倫 B</a>
      <a href="/tag/x.html">标签</a>
    </body></html>
    """

    items = engine.parse_explore_results(html)

    assert [item["name"] for item in items] == ["人妻小说 A", "家庭亂倫 B"]
    assert [item["bookUrl"] for item in items] == [
        "/post/123.html",
        "/post/456.html",
    ]


def test_legado_pseudo_selectors_and_attr_chain():
    engine = _engine()
    assert engine._eval_rule_str(HTML, "div.col-12@h5@text") == "Book A\nBook B\nOther"
    assert engine._eval_rule_str(HTML, "tag.a@href") == "/book/1.html\n/book/2.html"


def test_legado_index_and_exclusion():
    engine = _engine()
    assert engine._eval_rule_str(HTML, "option@text") == "Volume 0\nVolume 1\nVolume 2"
    assert engine._eval_rule_str(HTML, "option!1@text") == "Volume 0\nVolume 2"
    assert engine._eval_rule_str(HTML, "option.1@text") == "Volume 1"


def test_legado_reverse_list_prefix():
    engine = _engine()
    items = engine._get_elements(HTML, "-option")
    assert [el.get_text() for el in items] == ["Volume 2", "Volume 1", "Volume 0"]


def test_bracket_index_selector():
    engine = _engine()
    assert engine._eval_rule_str(HTML, "option[1,2]@text") == "Volume 1\nVolume 2"
    assert engine._eval_rule_str(HTML, "option[-1]@text") == "Volume 2"


def test_substitute_page_arithmetic():
    engine = _engine()
    assert engine._substitute(
        "https://example.com/?o={{(page-1)*12}}",
        key="x",
        page="3",
    ) == "https://example.com/?o=24"


def test_js_pattern_string_result_match():
    assert try_eval_js_pattern(
        "String(result).match(/连载中|连载完结/)",
        "状态：连载中",
    ) == "连载中"


def test_css_rule_regex_suffix():
    engine = _engine()
    assert engine._eval_rule_str(
        HTML,
        "p.text-muted@text##连载中|连载完结##STATUS",
    ) == "STATUS"


def test_real_source_style_rules():
    config = {
        "bookSourceUrl": "https://dogemanga.com",
        "ruleSearch": {
            "bookList": ".site-scroll__list@.col-12",
            "name": "tag.h5@text",
            "bookUrl": "tag.a.0@href",
            "lastChapter": "tag.p@text",
            "wordCount": "tag.li.1@text##.*連載狀態：|最快更新：",
        },
        "ruleBookInfo": {
            "name": "tag.h4@text",
            "author": ".site-card__brief@text",
            "kind": "p.text-muted@text@js:String(result).match(/連載中|連載完結/)",
        },
        "ruleToc": {
            "chapterList": "-class.site-selector@option!0",
            "chapterName": "text",
            "chapterUrl": "value",
        },
    }
    engine = YueduRuleEngine(config)
    html = """
    <html><body>
      <div class="site-scroll__list">
        <div class="col-12">
          <h5>Book One</h5>
          <a href="/manga/1">Read</a>
          <p>Chapter 10</p>
          <ul><li>0</li><li>連載狀態：10萬字</li></ul>
        </div>
      </div>
      <div class="site-card">
        <h4>Book One</h4>
        <div class="site-card__brief">Author One</div>
        <p class="text-muted">連載中</p>
      </div>
      <select class="site-selector">
        <option value="0">Volume 0</option>
        <option value="1">Chapter 1</option>
        <option value="2">Chapter 2</option>
      </select>
    </body></html>
    """
    search = engine.parse_search_results(html)
    assert search[0]["name"] == "Book One"
    assert search[0]["bookUrl"] == "/manga/1"
    assert search[0]["lastChapter"] == "Chapter 10"
    assert search[0]["wordCount"] == "10萬字"

    info = engine.parse_book_info(html)
    assert info["name"] == "Book One"
    assert info["author"] == "Author One"
    assert info["kind"] == "連載中"

    toc = engine.parse_toc(html)
    assert [c["chapterName"] for c in toc] == ["Chapter 1", "Chapter 2"]
    assert [c["chapterUrl"] for c in toc] == ["1", "2"]
def test_element_list_rule_keeps_xpath_attribute_selectors_whole():
    """Regression: 绅士漫画's catalog rule is ``//div[@class='...']/ul/li``.

    ``@`` separates Legado rule steps, so a naive ``str.split("@")`` tore the
    attribute selector apart and the engine found no elements at all -- every
    source whose ``bookList``/``chapterList`` used ``[@class=...]`` parsed to
    zero books or chapters.
    """
    engine = YueduRuleEngine({"bookSourceUrl": "https://www.example.com/"})
    html = (
        '<div class="gallary_wrap"><ul>'
        '<li><a href="/album/1.html">书一</a></li>'
        '<li><a href="/album/2.html">书二</a></li>'
        '</ul></div>'
    )

    elements = engine._get_elements(html, "//div[@class='gallary_wrap']/ul/li")

    assert len(elements) == 2
    assert elements[0].find("a")["href"] == "/album/1.html"


def test_xpath_element_rule_is_translated_to_css():
    assert (
        YueduRuleEngine._xpath_list_rule_to_css(
            "//div[@class='gallary_wrap']/ul/li"
        )
        == "div[class='gallary_wrap'] > ul > li"
    )
    # Descendant steps, attribute existence, positions.
    assert (
        YueduRuleEngine._xpath_list_rule_to_css("//div[@class='a']//li")
        == "div[class='a'] li"
    )
    assert YueduRuleEngine._xpath_list_rule_to_css("//li[1]") == "li:nth-of-type(1)"
    assert YueduRuleEngine._xpath_list_rule_to_css("//a[@href]") == "a[href]"
    # Anything the translator cannot express must stay unsupported so the
    # caller keeps its previous behaviour instead of mis-selecting elements.
    for unsupported in (
        "//a/@href",
        "//h3[contains(text(),'简介')]",
        "//li[.//a]",
        "//div/following-sibling::ul",
        "div.book-list",
    ):
        assert YueduRuleEngine._xpath_list_rule_to_css(unsupported) is None


def test_xpath_field_rule_applies_regex_transform():
    """``//div/img/@src##^//##https://`` used to raise instead of transforming."""
    engine = YueduRuleEngine({"bookSourceUrl": "https://www.example.com/"})
    html = '<div class="asTB"><img src="//img.example.com/a.jpg"></div>'

    assert engine._eval_xpath(
        html,
        "//div[@class='asTB']/img/@src##^//##https://",
    ) == "https://img.example.com/a.jpg"
    # An unsupported selector must return None rather than raise, so one bad
    # cover rule cannot abort a whole book sync.
    assert engine._eval_xpath(html, "//div[@class='x']/img/@src##////##x") is None


def test_list_rule_with_trailing_js_step_keeps_the_elements():
    """绅士漫画's ``chapterList`` ends in ``@js:`` that only fills java.put."""
    engine = YueduRuleEngine(
        {
            "bookSourceUrl": "https://www.example.com/",
            "ruleToc": {
                "chapterList": (
                    "//div[@class='gallary_wrap tb']/ul/li[1]@js:\n"
                    "java.put('imgInfoList', '[]');\nresult;"
                ),
                "chapterUrl": "//li//a/@href",
                "chapterName": "//li/text()@js:'全话阅读'",
            },
        }
    )
    html = (
        '<div class="gallary_wrap tb"><ul>'
        '<li class="tb"> 全话阅读 <a href="/photos-view-id-1.html">看图</a></li>'
        '<li class="tb"> 全话阅读 <a href="/photos-view-id-2.html">看图2</a></li>'
        '</ul></div>'
    )

    entries = engine.parse_toc(html)

    assert len(entries) == 1
    # ``[1]`` is Legado's element index (0-based), not an XPath position, so
    # this selects the second ``li`` -- the same element Legado would use.
    assert entries[0]["chapterUrl"] == "/photos-view-id-2.html"
    assert entries[0]["chapterName"] == "全话阅读"


def test_unbalanced_rule_raises_instead_of_recursing():
    """A rule with an unclosed ``[``/``(`` must fail fast, as Legado does."""
    with pytest.raises(RuleUnbalancedError):
        _RuleAnalyzer("a[href=foo|bar").split_rule("|")


def test_unbalanced_tail_rule_raises_instead_of_looping():
    """The tail scanner must not spin forever on the same unbalanced bracket."""
    with pytest.raises(RuleUnbalancedError):
        _RuleAnalyzer("a|b[c|d").split_rule("|")


def test_eval_css_tolerates_unbalanced_rule():
    engine = _engine()
    # Must not recurse/hang; the caller falls back to its own heuristics.
    assert engine._eval_css(HTML, "a[href=foo|bar") is None


def test_book_name_template_without_book_context_is_empty():
    """``{{book.name}}`` must never resolve to the page being parsed.

    The old fallback returned the raw HTML, which ``_eval_css`` then treated
    as a selector: 绅士漫画 (wn09.shop) books failed with ``maximum recursion
    depth exceeded``.
    """
    engine = _engine()
    raw = "<html><body><p>正文|未闭合[</p></body></html>"

    assert engine._substitute_inner_rules("{{book.name}}", raw) == ""

    engine.set_book({"name": "书名"})
    assert engine._substitute_inner_rules("{{book.name}}", raw) == "书名"


def test_inner_rule_still_resolves_js_context_names():
    """``{{sourceUrl}}``/``{{chapter.title}}`` keep working."""
    engine = YueduRuleEngine({"bookSourceUrl": "https://example.com"})
    raw = "<html><body><p>x</p></body></html>"

    assert engine._substitute_inner_rules("{{sourceUrl}}", raw) == "https://example.com"

    engine.set_chapter_context({"title": "第一章"})
    assert engine._substitute_inner_rules("{{chapter.title}}", raw) == "第一章"


def test_book_info_name_template_does_not_evaluate_the_page_as_css():
    engine = YueduRuleEngine(
        {
            "bookSourceUrl": "https://www.wn09.shop/",
            "ruleBookInfo": {"name": "{{book.name}}"},
        }
    )
    html = (
        "<html><head><title>页面标题</title></head>"
        "<body><p>正文|未闭合[</p></body></html>"
    )

    info = engine.parse_book_info(html)

    # Empty name lets ``fetch_book`` fall back to the page title.
    assert not info.get("name")


WN09_LIST_HTML = """
<html><body>
  <div class="gallary_wrap">
    <ul>
      <li>
        <div class="pic_box"><div>封面</div><div>图片说明</div></div>
        <div class="info">
          <div class="title"><a href="/photos-index-aid-1.html">[あるぷ] アモラルアイランド</a></div>
          <div class="info_col">2026-09-12, 49張圖片</div>
        </div>
      </li>
      <li>
        <div class="pic_box"><div>封面</div><div>图片说明</div></div>
        <div class="info">
          <div class="title"><a href="/photos-index-aid-2.html">[みな本] 訪問姦誘</a></div>
          <div class="info_col">2026-09-11, 245張圖片</div>
        </div>
      </li>
    </ul>
  </div>
</body></html>
"""


def _wn09_engine() -> YueduRuleEngine:
    return YueduRuleEngine({
        "bookSourceUrl": "https://www.wn09.shop/",
        "ruleExplore": {
            "bookList": "//div[@class='gallary_wrap']/ul/li",
            "name": "//div[@class='info']/div[@class='title']/a//text()",
            "bookUrl": "//div[@class='info']/div[@class='title']/a/@href",
            # The source reads the card again from inside the JS step; the
            # step's ``result`` is only a sub-string of the card.
            "kind": (
                "//div[@class='pic_box']/div[2]/text()@js:\n"
                "result = Array.from(result)\n"
                "var pages = java.getString(\n"
                "  \"//li/div[@class='info']/div[@class='info_col']/text()\")\n"
                "var imgNum = pages.split('，')[0].match(/\\d+(?=張圖片)/g)[0]\n"
                "result.push(imgNum+'P')\n"
                "result"
            ),
        },
    })


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_java_get_string_supports_xpath_against_the_item_element():
    """绅士漫画's ``kind`` rule reads the card with ``java.getString(XPath)``.

    Two things used to break it: the shim only understood CSS, and the JS
    content was the previous step's text instead of the list item, so the rule
    threw ``Cannot read properties of null (reading '0')`` and the ``49P`` tag
    was lost.
    """
    items = _wn09_engine().parse_explore_results(WN09_LIST_HTML)

    # The source pushes the page count onto ``Array.from(result)``, so the
    # value is a list (Legado produces the same shape); what matters is that
    # the XPath lookup found the card text instead of throwing.
    kinds = [str(item["kind"]) for item in items]
    assert "49P" in kinds[0]
    assert "245P" in kinds[1]
    assert items[0]["name"] == "[あるぷ] アモラルアイランド"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_java_get_web_view_ua_is_available():
    """要撸小说's ``header`` rule calls ``java.getWebViewUA()``."""
    engine = _engine()

    value = engine._try_eval_js("java.getWebViewUA()", "")

    assert isinstance(value, str) and "Android" in value


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_java_get_reads_the_value_written_by_java_put():
    """绅士漫画's ``ruleContent`` reads ``java.get('imgInfoList')``.

    ``java.get(key)`` is Legado's variable store (``java.put``), not an HTTP
    call; treating it as HTTP made ``JSON.parse`` see an empty body and throw
    ``Unexpected end of JSON input`` instead of returning the image list.
    """
    engine = _engine()

    value = engine._try_eval_js(
        "java.put('images', JSON.stringify([{name: '001'}]));"
        " JSON.parse(java.get('images'))[0].name",
        "",
    )

    assert value == "001"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_java_get_string_supports_attribute_and_regex_transform():
    engine = _engine()
    engine._js_content = (
        '<div class="box"><a href="/book/1.html">第一本</a></div>'
    )

    assert engine._try_eval_js(
        'java.getString("//div[@class=\'box\']/a/@href")', ""
    ) == "/book/1.html"
    assert engine._try_eval_js(
        'java.getString("//div[@class=\'box\']/a/text()##第一##第1")', ""
    ) == "第1本"
