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
    # The rule starts with ``//``, so Legado parses it as XPath
    # (``AnalyzeRule.kt``: "//XPath特征很明显,无需配置单独的识别标头"), where
    # positions are 1-based.  Reading ``li[1]`` as a 0-based Legado index used
    # to pick the *second* ``li``, which made 绅士漫画 start every album at the
    # second image (its cover entry is the first ``li``).
    assert entries[0]["chapterUrl"] == "/photos-view-id-1.html"
    assert entries[0]["chapterName"] == "全话阅读"


def test_xpath_position_predicate_is_one_based():
    """``//li[1]``/``//li[2]`` must select the first/second element."""
    engine = YueduRuleEngine({"bookSourceUrl": "https://www.example.com/"})
    html = (
        '<div class="gallary_wrap tb"><ul>'
        '<li class="tb" id="a">一</li>'
        '<li class="tb" id="b">二</li>'
        '<li class="tb" id="c">三</li>'
        '</ul></div>'
    )

    first = engine._get_elements(
        html, "//div[@class='gallary_wrap tb']/ul/li[1]"
    )
    second = engine._get_elements(
        html, "//div[@class='gallary_wrap tb']/ul/li[2]"
    )

    assert [el.get("id") for el in first] == ["a"]
    assert [el.get("id") for el in second] == ["b"]
    # ``li[0]`` is not valid XPath, but sources write it meaning "the first".
    assert [el.get("id") for el in engine._get_elements(html, "//ul/li[0]")] == ["a"]


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


LIST_SEPARATOR_HTML = """
<html><body>
  <h3 class='post-title entry-title' itemprop='name'>
    <a href='/2022/02/a.html'>猎美陷阱</a>
  </h3>
  <h3 class='post-title entry-title' itemprop='name'>
    <a href='/2022/01/b.html'>姐姐的屁股</a>
  </h3>
  <div class='fallback-only'><a href='/2021/12/c.html'>欲梦迷蝶</a></div>
</body></html>
"""


def test_list_rule_or_fallback_uses_the_first_matching_selector():
    """中文成人文学网's ``bookList`` is ``h3 a||.post-title a||article h3 a``.

    The whole rule used to be handed to soupsieve, which raised
    ``SelectorSyntaxError: Invalid character '|'``; the swallowed error made
    every discover category parse 0 books and the full-site task fail with
    "书源未返回可同步的书籍".
    """
    engine = _engine()

    items = engine._get_elements(LIST_SEPARATOR_HTML, "h3 a||.post-title a||article h3 a")

    assert len(items) == 2
    assert items[0].get_text(strip=True) == "猎美陷阱"
    # The ``||`` chain stops at the first fragment that matched, exactly like
    # Legado's ``AnalyzeByJSoup.getElements`` (``if (el.size > 0) break``).
    assert engine._get_elements(
        LIST_SEPARATOR_HTML, ".missing a||.fallback-only a",
    )[0].get_text(strip=True) == "欲梦迷蝶"


def test_list_rule_and_concatenates_fragments():
    """Icu's ``bookList`` joins two containers with ``&&``."""
    engine = _engine()

    items = engine._get_elements(LIST_SEPARATOR_HTML, "h3 a&&.fallback-only a")

    assert [el.get_text(strip=True) for el in items] == ["猎美陷阱", "姐姐的屁股", "欲梦迷蝶"]


def test_list_rule_percent_interleaves_fragments():
    engine = _engine()

    items = engine._get_elements(LIST_SEPARATOR_HTML, "h3 a%%.fallback-only a")

    assert [el.get_text(strip=True) for el in items] == ["猎美陷阱", "欲梦迷蝶", "姐姐的屁股"]


def test_list_rule_separator_inside_brackets_is_kept_whole():
    engine = _engine()
    html = '<html><body><a href="x||y">A</a><a href="z">B</a></body></html>'

    items = engine._get_elements(html, "a[href*='x||y']")

    assert [el.get_text(strip=True) for el in items] == ["A"]


# ---------------------------------------------------------------------------
# A fragment after the first one must survive a bracketed group (codex-handoff
# section 8 / docs/legado-rule-spec-diff.md A-3).
#
# The test above only covers the *first* fragment, which is emitted by
# ``_split_head`` and was always correct.  Every fragment after the first goes
# through ``_split_tail``, which advanced its fragment start past the text it had
# only meant to *skip over* while hunting for the separator -- so the fragment
# was dropped.  ``bookList``/``chapterList`` share the same analyzer, so such a
# rule silently returned nothing.
# ---------------------------------------------------------------------------


def test_split_rule_keeps_a_bracketed_non_first_fragment():
    # The separator sits *inside* the group: it must not split the rule at all.
    assert _RuleAnalyzer('a||b[x="||"]||c').split_rule("&&", "||", "%%") \
        == ["a", 'b[x="||"]', "c"]


def test_split_rule_keeps_a_bracketed_non_first_fragment_without_inner_separator():
    # The group merely precedes the separator.  This case is broader than the
    # one above: it dropped the fragment even though no separator was inside.
    assert _RuleAnalyzer("a||b[x]||c").split_rule("&&", "||", "%%") \
        == ["a", "b[x]", "c"]


def test_split_element_steps_keeps_a_bracketed_step():
    """``div.x@a[@href]/b`` is two ``@`` steps; neither may be lost."""
    engine = _engine()

    assert engine._split_element_steps("div.x@a[@href]/b") \
        == ["div.x", "a[@href]/b"]


def test_list_rule_keeps_a_bracketed_middle_fragment_that_matches():
    """End-to-end: the mid fragment is the one that selects the elements."""
    engine = _engine()
    html = (
        '<html><body><div class="mid"><a href="/m">M</a></div></body></html>'
    )

    items = engine._get_elements(html, ".no1||.mid a[href]||.no2")

    assert [el.get_text(strip=True) for el in items] == ["M"]


def test_list_rule_strips_the_replace_regex_suffix_before_selecting():
    """``##pattern##replacement`` is not part of the selector (A-5).

    Legado strips it before parsing -- ``AnalyzeRule.kt:707-709`` is literally
    ``rule = ruleStrS[0].trim()``.  Leaving it in the list rule meant a ``|``
    inside the pattern was taken for an extra fallback separator (NovelHub's
    ``SEPARATORS`` carries a bare ``|``), which shredded the rule and made the
    whole ``bookList``/``chapterList`` return nothing.
    """
    engine = _engine()
    html = (
        '<html><body><ul class="list">'
        "<li>a</li><li>b</li></ul></body></html>"
    )

    assert [
        el.get_text(strip=True)
        for el in engine._get_elements(html, r".list li##\s+|\s+")
    ] == ["a", "b"]


def test_list_rule_without_a_suffix_is_unaffected():
    """Control for the case above: the plain selector behaves identically."""
    engine = _engine()
    html = (
        '<html><body><ul class="list">'
        "<li>a</li><li>b</li></ul></body></html>"
    )

    assert [
        el.get_text(strip=True) for el in engine._get_elements(html, ".list li")
    ] == ["a", "b"]


# ---------------------------------------------------------------------------
# XPath field rules must honour ``&&`` / ``||`` (docs/legado-rule-spec-diff.md
# A-4).  Legado's ``AnalyzeByXPath.getString`` (AnalyzeByXPath.kt:133-154) splits
# the rule *before* it reaches the XPath engine.  Passing the combined string to
# lxml raised XPathEvalError, the CSS fallback raised SelectorSyntaxError, and
# the field came back empty -- so an XPath fallback never fell back.
# ---------------------------------------------------------------------------

XPATH_HTML = (
    '<html><body><div id="a">AAA</div><div id="b">BBB</div></body></html>'
)


def test_xpath_or_falls_back_to_the_second_fragment():
    engine = _engine()

    assert engine._eval_xpath(
        XPATH_HTML, "//div[@id='missing']||//div[@id='b']",
    ) == "BBB"


def test_xpath_or_takes_the_first_fragment_that_yields_a_value():
    engine = _engine()

    assert engine._eval_xpath(
        XPATH_HTML, "//div[@id='a']||//div[@id='b']",
    ) == "AAA"


def test_xpath_and_concatenates_fragments_with_newlines():
    engine = _engine()

    assert engine._eval_xpath(
        XPATH_HTML, "//div[@id='a']&&//div[@id='b']",
    ) == "AAA\nBBB"


def test_xpath_single_fragment_is_unaffected():
    """Control: the plain XPath path keeps working."""
    engine = _engine()

    assert engine._eval_xpath(XPATH_HTML, "//div[@id='b']") == "BBB"


# ---------------------------------------------------------------------------
# JSONPath ``{$.rule}`` inner rules (docs/legado-rule-spec-diff.md M-1).
# Legado substitutes them first and only falls back to reading the whole rule as
# JSONPath when nothing was substituted (AnalyzeByJSonPath.kt:35-48); only the
# fallback existed here, so a rule reaching across JSON fields resolved to
# nothing.
# ---------------------------------------------------------------------------

JSON_DOC = {"data": {"id": "42"}, "tags": ["p", "q"], "n": 7}


def test_jsonpath_inner_rule_is_substituted():
    engine = _engine()

    assert engine._eval_json(JSON_DOC, "{$.data.id}") == "42"


def test_jsonpath_inner_rule_inside_a_longer_template():
    engine = _engine()

    assert engine._eval_json(JSON_DOC, "id-{$.data.id}") == "id-42"


def test_jsonpath_without_inner_rule_still_reads_directly():
    """Control: the plain JSONPath path is unchanged."""
    engine = _engine()

    assert engine._eval_json(JSON_DOC, "$.data.id") == "42"


def test_jsonpath_list_result_is_newline_joined_not_a_python_repr():
    """D-03: ``str(val)`` leaked ``['p', 'q']`` into the field.

    Legado joins a list result with newlines
    (``ob.joinToString("\\n")``).
    """
    engine = _engine()

    assert engine._eval_json(JSON_DOC, "$.tags") == "p\nq"


def test_chapter_list_rule_with_fallback_produces_chapters():
    engine = YueduRuleEngine({
        "bookSourceUrl": "https://example.com",
        "ruleToc": {
            "chapterList": ".missing a||.fallback-only a",
            "chapterName": "text",
            "chapterUrl": "href",
        },
    })
    engine.set_page_url("https://example.com/book/1/")

    chapters = engine.parse_toc(LIST_SEPARATOR_HTML)

    assert len(chapters) == 1
    assert chapters[0]["chapterUrl"] == "/2021/12/c.html"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_java_connect_returns_a_response_with_get_body():
    """Icu's exploreUrl does ``java.connect(url).getBody()``.

    ``java.connect`` did not exist in the shim, so the call threw inside the
    source's own ``try/catch``; the category list came back as an error string
    and discovery ended with "发现规则是 Legado JS 脚本，当前环境无法执行".
    """
    engine = _engine()

    value = engine._try_eval_js(
        "var __saved = __nhCurlRaw;"
        "__nhCurlRaw = function (url, method, body, headers) {"
        "  return 'BODY:' + url + ':' + (headers && headers['User-Agent']);"
        "};"
        "var out;"
        "try {"
        "  globalThis.__nhSetSourceConfig({header: '{\"User-Agent\":\"UA-1\"}'});"
        "  out = java.connect('https://example.com/so').getBody();"
        "} finally { __nhCurlRaw = __saved; }"
        "out;",
        "",
    )

    assert value == "BODY:https://example.com/so:UA-1"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_jsoup_select_first_and_equals_are_available():
    engine = _engine()

    value = engine._try_eval_js(
        "var doc = org.jsoup.Jsoup.parse("
        "'<div class=\"box\"><span class=\"t\">A</span><span>B</span></div>');"
        "var box = doc.selectFirst('.box');"
        "var spans = box.select('span');"
        "spans.get(0).equals(spans.get(0)) + '|' + spans.get(0).equals(spans.get(1))"
        " + '|' + box.selectFirst('.t').text();",
        "",
    )

    assert value == "true|false|A"


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


# ---------------------------------------------------------------------------
# JsEncodeUtils: digests / HMACs / htmlFormat
# (docs/legado-rule-spec-diff.md C-22, C-23)
#
# These were missing from the shim entirely, so any book source that signs an API
# request (the common case for JSON-backed sources) failed with a TypeError.
#
# The expected values below are **published test vectors**, not values captured
# from this implementation -- a hash test that only asserts self-consistency
# proves nothing:
#   * MD5("abc")      -- RFC 1321 appendix A.5
#   * SHA-1("abc")    -- FIPS 180-4 / common test vector
#   * SHA-256("abc")  -- FIPS 180-4 / common test vector
#   * HMAC-SHA256(key="Jefe", data="what do ya want for nothing?")
#                     -- RFC 4231 test case 2
# ---------------------------------------------------------------------------

MD5_ABC = "900150983cd24fb0d6963f7d28e17f72"
SHA1_ABC = "a9993e364706816aba3e25717850c26c9cd0d89d"
SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
HMAC_SHA256_JEFE = (
    "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
)


def _b64_of_hex(hex_digest: str) -> str:
    """Base64 of the raw bytes a hex digest denotes.

    Used to check the ``*Base64`` variants without hand-computing a second
    vector: once the hex form matches a published vector, this derivation is
    exact.
    """
    import base64

    return base64.b64encode(bytes.fromhex(hex_digest)).decode("ascii")


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_md5_encode16_is_the_middle_sixteen_hex_chars():
    """``MD5Utils.md5Encode16`` is ``md5Encode(str).substring(8, 24)``."""
    engine = _engine()

    assert engine._try_eval_js("java.md5Encode('abc')", "") == MD5_ABC
    assert engine._try_eval_js("java.md5Encode16('abc')", "") == MD5_ABC[8:24]


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_digest_hex_matches_published_vectors():
    engine = _engine()

    assert engine._try_eval_js("java.digestHex('abc', 'MD5')", "") == MD5_ABC
    assert engine._try_eval_js("java.digestHex('abc', 'SHA-1')", "") == SHA1_ABC
    assert engine._try_eval_js("java.digestHex('abc', 'SHA-256')", "") == SHA256_ABC
    # JCE spells it "SHA-1"; book sources also write "SHA1".
    assert engine._try_eval_js("java.digestHex('abc', 'SHA1')", "") == SHA1_ABC


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_digest_base64_str_is_the_base64_of_the_same_digest():
    engine = _engine()

    assert engine._try_eval_js(
        "java.digestBase64Str('abc', 'SHA-256')", ""
    ) == _b64_of_hex(SHA256_ABC)


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_hmac_hex_matches_rfc4231():
    engine = _engine()

    assert engine._try_eval_js(
        "java.HMacHex('what do ya want for nothing?', 'HmacSHA256', 'Jefe')", ""
    ) == HMAC_SHA256_JEFE


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_hmac_base64_is_the_base64_of_the_same_digest():
    engine = _engine()

    assert engine._try_eval_js(
        "java.HMacBase64('what do ya want for nothing?', 'HmacSHA256', 'Jefe')", ""
    ) == _b64_of_hex(HMAC_SHA256_JEFE)


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_html_format_runs_the_legado_pipeline():
    """`java.htmlFormat` is `HtmlFormatter.formatKeepImg` (utils/HtmlFormatter.kt).

    Expected value derived step by step from that file's pipeline: entities
    collapsed, block tags to newlines, comments and non-img tags stripped,
    full-width indent applied, ``<img>`` preserved.
    """
    engine = _engine()

    assert engine._try_eval_js(
        "java.htmlFormat('<div>a&nbsp;b</div><p>c</p><!--x-->"
        "<span>d</span><img src=\"/i.png\">')",
        "",
    ) == "\u3000\u3000a b\n\u3000\u3000c\n\u3000\u3000d<img src=\"/i.png\">"


# ---------------------------------------------------------------------------
# Symmetric crypto (docs/legado-rule-spec-diff.md C-23)
#
# Legado's `createSymmetricCrypto` returns a hutool SymmetricCrypto; here it is a
# small object with encrypt/encryptBase64/encryptHex/decrypt/decryptStr.  The
# cipher itself is checked against the **FIPS-197 / NIST SP 800-38A** vector, so a
# wrong algorithm mapping or a padding mistake cannot pass unnoticed; the
# round-trips are only there to prove the mode/IV wiring does not throw.
#
# Known gap: hutool's key/IV normalisation for invalid lengths lives in hutool
# (a gradle dependency, not in this repository) and is not replicated.
# ---------------------------------------------------------------------------

_AES_KEY_HEX = "000102030405060708090a0b0c0d0e0f"
_AES_KEY_STR = "0123456789abcdef"  # 16 ASCII chars -> 16 UTF-8 bytes -> AES-128


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_aes_128_ecb_matches_the_fips197_vector():
    """FIPS-197 appendix C.1 / NIST SP 800-38A: the cipher itself is correct."""
    engine = _engine()

    value = engine._try_eval_js(
        "java.createSymmetricCrypto('AES/ECB/NoPadding',"
        " Buffer.from('" + _AES_KEY_HEX + "','hex'), null)"
        ".encryptHex(Buffer.from('00112233445566778899aabbccddeeff','hex'));",
        "",
    )

    assert value == "69c4e0d86a7b0430d8cdb78070b4c55a"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_aes_pkcs5padding_round_trip():
    engine = _engine()

    assert engine._try_eval_js(
        "var c = java.createSymmetricCrypto('AES/ECB/PKCS5Padding', '"
        + _AES_KEY_STR + "', null);"
        "c.decryptStr(c.encryptBase64('hello world'));",
        "",
    ) == "hello world"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_aes_cbc_round_trip_uses_the_iv():
    engine = _engine()

    assert engine._try_eval_js(
        "var c = java.createSymmetricCrypto('AES/CBC/PKCS5Padding',"
        " Buffer.from('" + _AES_KEY_HEX + "','hex'),"
        " Buffer.from('" + _AES_KEY_HEX + "','hex'));"
        "c.decryptStr(c.encryptBase64('hello cbc'));",
        "",
    ) == "hello cbc"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_decrypt_auto_detects_hex_and_base64():
    """`SymmetricCryptoAndroid.decrypt` uses hex when the input looks like hex."""
    engine = _engine()

    js = (
        "var c = java.createSymmetricCrypto('AES/ECB/PKCS5Padding', '"
        + _AES_KEY_STR + "', null);"
        "c.decryptStr(c.encryptHex('detect me')) + '|' +"
        "c.decryptStr(c.encryptBase64('detect me'));"
    )

    assert engine._try_eval_js(js, "") == "detect me|detect me"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_3des_round_trip():
    """3DES is still available under OpenSSL 3 (`des-ede3-*`)."""
    engine = _engine()

    assert engine._try_eval_js(
        "var c = java.createSymmetricCrypto('DESede/ECB/PKCS5Padding',"
        " Buffer.from('0123456789abcdef0123456789abcdef0123456789abcdef','hex'),"
        " null);"
        "c.decryptStr(c.encryptBase64('hello 3des'));",
        "",
    ) == "hello 3des"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_aes_encode_to_string_decrypts_like_legado():
    """Faithful port of a Legado quirk, pinned so it cannot be "tidied" away.

    `aesEncodeToString` is documented as "encrypt AES to String" but its body is
    `.decryptStr(data)`.  Book sources were written against that, so this must
    keep decrypting; `aesEncodeToBase64String` is the one that encrypts.
    """
    engine = _engine()

    js = (
        "var k = '" + _AES_KEY_STR + "';"
        "var t = 'AES/ECB/PKCS5Padding';"
        "var c = java.createSymmetricCrypto(t, k, null);"
        "var b64 = c.encryptBase64('plain');"
        "java.aesEncodeToString(b64, k, t, '') + '|' +"
        "(java.aesEncodeToBase64String('plain', k, t, '') === b64);"
    )

    assert engine._try_eval_js(js, "") == "plain|true"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_single_des_fails_with_a_readable_reason():
    """OpenSSL 3 put single DES behind its legacy provider, which Node leaves off.

    The point of the test is that the failure is *readable and catchable* -- a
    book source's own try/catch (and the crawler log) get the reason, instead of
    "java.desDecodeToString is not a function" or a silent empty field.
    """
    engine = _engine()

    value = engine._try_eval_js(
        "var msg;"
        "try {"
        "  java.createSymmetricCrypto('DES/ECB/PKCS5Padding',"
        "    Buffer.from('0123456789abcdef','hex'), null).encryptBase64('x');"
        "  msg = 'no-error';"
        "} catch (e) { msg = String(e && e.message ? e.message : e); }"
        "msg;",
        "",
    )

    assert value != "no-error"
    assert "legacy provider" in value


# ---------------------------------------------------------------------------
# Asymmetric crypto (docs/legado-rule-spec-diff.md, item E)
#
# `java.createAsymmetricCrypto` is Legado's `AsymmetricCrypto(transformation)`,
# i.e. hutool 5.8.22 (JsEncodeUtils.kt:77-81).  Every expectation below comes
# from hutool's own source -- KeyUtil, BaseAsymmetric, RSAPadding, CipherWrapper,
# AsymmetricEncryptor/Decryptor -- plus three independent oracles:
#
#   * Wycheproof `rsa_pkcs1_2048_test.json`: published RSAES-PKCS1-v1_5
#     decryption vectors.  The whole file (33 keys, 67 tests -- 42 valid and 25
#     invalid, covering InvalidPkcs1Padding, Sslv23Padding, InvalidCiphertext-
#     Format and CVE-2021-3580) was swept against this shim and matched 67/67.
#     tcId 3 and tcId 9 are the two kept here.
#   * pyca/cryptography (OpenSSL) for the OAEP ciphertext.
#   * pure-Python `pow()` for the NoPadding case *and* for every hand-built
#     padding block, so the shim's own PKCS#1 validation is what is tested.
#
# The hand-built blocks exist because OpenSSL cannot be the oracle for invalid
# PKCS#1 padding: its RSA_PKCS1_PADDING decryption implements implicit rejection
# and returns a pseudo-random buffer where Java throws BadPaddingException.  A
# book source with a try/catch fallback would silently receive garbage, so the
# shim does the padding itself on top of RSA_NO_PADDING.
# ---------------------------------------------------------------------------

_RSA_PKCS8_B64 = (
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCzUQorzUzmRMW1lK5QWeErLwVL"
    "ZY1dpZWaL98YcbgIvD3z5ijSeS5RqtXBJLQ72kU9ylzeS88o571O/7oMtLdCu7bVoBPLY9GqOong"
    "JifvU5i1LAz9l9IIq+uNfJvOC76wGaht21ib6ymlt0v4YQdcZ3yB1DDwMMJlJHr508kUDMtlMJ0H"
    "4K3B79Fc8X57BV19o4aORkjMOhgPDuf44eexgJijORtM5xYemNV6+KlH4gGkY+LWu8qAWeVwbp3+"
    "2PSFZGX/pxLtGqGOiI0S3GqgnOlez8qDzFsLFdsJyGR/XVJMDy52IKNBa5YjytwPCXr1cyYcmMhA"
    "CqEq845DythNAgMBAAECggEAGlAtDupse2niHVg5EB9wVFbtDvhS+0f+IQcfVMXzPIzrBmxi1yfj"
    "LSbFgTcyn4nTGVMlt5UmTBldhUcvdQfb0JYdKVH5NaJrNPCsJNFUkOESiptxOJFbx9v6j+OWNXEx"
    "xUOunJhQc2jZzrCMHGGYo+2nrqGFoOl2zULCLQDwA9nxnZbqTJr8v+FEHMyALPsGifWdgExqTk9A"
    "TBUXR0XtbLi8iO8LM7oNKoDjXkO8kPNQBS5yAW51sA01ejgcnA1GcGnKZgiHyYd2Y0n8xDRgtKpR"
    "a84Hnt2HuhZDB7dSwnftlSitO6C/GHc0ntO3lmpsJAEQQJv00PreDGj9rdhH/QKBgQDsElzzfjEK"
    "L/RiY7my4GKdY5AAXsiJE9T7cb1N2FYSRJiq66mD17or2ULmTSI/63ojr01gXv7qa9cNOa/pnTWj"
    "qhXnShdod4CTvg7dSo0Jst723Jtn/4V2RiXC4ZI220xAHOMKJXLT7LT5abetGcUiwC13RGVnbho3"
    "dsVNYkg0iwKBgQDCdCq82Yl71LC2cflz/IKo+Eq/VwX/iN1BlIYjr+ncpg3GVDOQdn/q6+tTlXbu"
    "i/phtfy8qUp873WgkVDFQPqWlN2ABK0jcYyIkEkhk2nJn0RY1K/BSPbwffhzJKltnPezhd2GIkFK"
    "GDL58pRG8FDC1aZAdkncQatw4js9zCLJhwKBgQCWqXmNJQJjQAu2J3NCiBYn4Hzs35EYewG4n/Rz"
    "FBiKfCD7JIABVtLIXVZm6N9s7/n5gE3frYD/V2feVuzAKccr9scX359k2q/Cms+dx5CPmgrWfiDo"
    "lJk2zLoY0CGixP67BDSaKyBHxJAThbbl0MaR0RizP4GAKzKsJy7wnkL61QKBgAVU9BsLh/aKRXIr"
    "O+DPSrHhZQNMGpEAKrjynp754tq2/ueyRVuvtCA36dL35TPzSKFHQS/XIIC+fCYz9dgCyRw55rzs"
    "4+Z15ZmVAzxVc3Ag2tnosw0EuCit+5MErVShGjWk9QcJh2rFsRgja6dqTXyaKR3ZYHsWneHRgjhW"
    "kZmfAoGAHGQBidm/6MYjgzIQp2xCDG9E5ddg4lmRbOwq4rFWRWlg/ZXidHZgw4lWIlDwVQSc+rfl"
    "wwOVSThKeiquscgk069wlIKoz5tYcCKgCx8HIttQ8zyybcIN0iRdUmXfYe4pg8k4whZ9zuEh/EtE"
    "ecI35yjPYzq2CowOzQT85+O6pVk="
)
_RSA_SPKI_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAs1EKK81M5kTFtZSuUFnhKy8FS2WNXaWV"
    "mi/fGHG4CLw98+Yo0nkuUarVwSS0O9pFPcpc3kvPKOe9Tv+6DLS3Qru21aATy2PRqjqJ4CYn71OY"
    "tSwM/ZfSCKvrjXybzgu+sBmobdtYm+sppbdL+GEHXGd8gdQw8DDCZSR6+dPJFAzLZTCdB+Ctwe/R"
    "XPF+ewVdfaOGjkZIzDoYDw7n+OHnsYCYozkbTOcWHpjVevipR+IBpGPi1rvKgFnlcG6d/tj0hWRl"
    "/6cS7RqhjoiNEtxqoJzpXs/Kg8xbCxXbCchkf11STA8udiCjQWuWI8rcDwl69XMmHJjIQAqhKvOO"
    "Q8rYTQIDAQAB"
)
# Wycheproof rsa_pkcs1_2048_test.json group 0: tcId 3 is a valid vector (its
# message is the hex of "Test"), tcId 9 carries InvalidPkcs1Padding.
_WY_PKCS1_VALID_CT = (
    "4501b4d669e01b9ef2dc800aa1b06d49196f5a09fe8fbcd037323c60eaf027bfb98432be4e4a2"
    "6c567ffec718bcbea977dd26812fa071c33808b4d5ebb742d9879806094b6fbeea63d25ea314"
    "1733b60e31c6912106e1b758a7fe0014f075193faa8b4622bfd5d3013f0a32190a95de61a3"
    "604711bc62945f95a6522bd4dfed0a994ef185b28c281f7b5e4c8ed41176d12d9fc1b837e6"
    "a0111d0132d08a6d6f0580de0c9eed8ed105531799482d1e466c68c23b0c222af7fc12ac27"
    "9bc4ff57e7b4586d209371b38c4c1035edd418dc5f960441cb21ea2bedbfea86de0d7861e8"
    "1021b650a1de51002c315f1e7c12debe4dcebf790caaa54a2f26b149cf9e77d"
)
_WY_PKCS1_VALID_MSG = "54657374"
_WY_PKCS1_INVALID_CT = (
    "6e0d507f66e16d4b7373a504c6d48692aaa541fdd59eeb5d4a2cd91f6000ce9b5734a232d654"
    "1a78729ac82152d3a30b51950a24ae379a108ed20fa4ec7542fe2281c2dd5de685564d15182f"
    "3c73e9c0135ebc993f5acd240a343d3257997582328c31be215c7349375406aa78a3ac3532"
    "7226839bee2f1a4a0f8e6e06986cb33806c93e0b0c1d6cfd23f4a68c1f2a38c74b8df70f28"
    "0984a840c710c52279034d04f61e313d4bcd8b3b5c58468a44565a1acb2eefc6d49044be71"
    "63e64ed84b5e7991ecba274a3a7ee4defb842a86ac4cbf2d3bfc9cf870ae025a3e2fbc7759"
    "16a59579763c06eb84ad8edd1d03787e609ad446de43ebed16330ab06716fa73"
)
# OAEP, produced by pyca/cryptography with the same key.
_OAEP_SHA1_MSG = "novelhub-oaep-sha1"
_OAEP_SHA1_CT = (
    "842f5e73bc3f563efdc0dab81371509b1f9c27d774bc87cc9522eb0a075d4547395fc6286279"
    "3a6bea162d6f41893357a666e886e9f7761f9e7a676219c94990004f44be7bad2afd78515b44"
    "e0b7acf0280bcb893e6a80b69d14a2f7b9431cd4b752fdced130b5ca2960e15f22f3edb613c9"
    "9da390b70344f5bc45fa32f5cef32f75f609a51fac19390185ee712a3f491789d269f85623"
    "06a2d115fcfe236709666e5a8aedc6571399801ad795eb59960eba9118a36b8835674bae6c"
    "91e557672464df4ab4cb259823f409a0432d11c7236d68b04090413ebe23fc7a4cd550becf"
    "575b595b7a58a53a2628c9f2aed7547469e3d8a4b2498e89ee24134a5a4210"
)
# NoPadding: a 256-byte block and its raw RSA public-key transform, from
# pure-Python pow().
_NOPAD_BLOCK_HEX = (
    "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425"
    "262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f404142434445464748494a4b"
    "4c4d4e4f505152535455565758595a5b5c5d5e5f606162636465666768696a6b6c6d6e6f70"
    "7172737475767778797a7b7c7d7e7f808182838485868788898a8b8c8d8e8f909192939495"
    "969798999a9b9c9d9e9fa0a1a2a3a4a5a6a7a8a9aaabacadaeafb0b1b2b3b4b5b6b7b8b9ba"
    "bbbcbdbebfc0c1c2c3c4c5c6c7c8c9cacbcccdcecfd0d1d2d3d4d5d6d7d8d9dadbdcdddedf"
    "e0e1e2e3e4e5e6e7e8e9eaebecedeeeff0f1f2f3f4f5f6f7f8f9fafbfcfdfeff"
)
_NOPAD_CT = (
    "839fb895812a8082e5880beded340993fd73a55477fd9e50585d1a09bd4e4d67d61564d03b39"
    "66c68b17928d571a2b87784bc35a31a763ae84c31d121f8f8b8dde41cff90706c52a7fdf6af"
    "db1d29cd2682f5f2d50b3d5f6faf101027ed28ae0bbb2518662a3d5edc7e065c076015901d37"
    "18376418ccab61ac9c5678906ae0139689bcee5b0f109dc3959d376d145c39d15a9d6a3bb34f"
    "93879cda9d2858b2308128d82e581df1c46c4e716c586681c2023c57776660577af28325bda8"
    "d200bdc1934497c3ad32cfde37600c79cbf6ea616e10a5ba4875598b708295d5fab5f0fe95f2"
    "d5eaac3a02ee9675534cc60b86aaa8b31c7d44e00567176060d15b241"
)
# Modulus and public exponent, so the test file can raise a block to a
# ciphertext itself and craft any padding case it likes.
_RSA_N_HEX = (
    "b3510a2bcd4ce644c5b594ae5059e12b2f054b658d5da5959a2fdf1871b808bc3df3e628d279"
    "2e51aad5c124b43bda453dca5cde4bcf28e7bd4effba0cb4b742bbb6d5a013cb63d1aa3a89e0"
    "2627ef5398b52c0cfd97d208abeb8d7c9bce0bbeb019a86ddb589beb29a5b74bf861075c677c"
    "81d430f030c265247af9d3c9140ccb65309d07e0adc1efd15cf17e7b055d7da3868e4648cc3a"
    "180f0ee7f8e1e7b18098a3391b4ce7161e98d57af8a947e201a463e2d6bbca8059e5706e9dfe"
    "d8f4856465ffa712ed1aa18e888d12dc6aa09ce95ecfca83cc5b0b15db09c8647f5d524c0f2e"
    "7620a3416b9623cadc0f097af573261c98c8400aa12af38e43cad84d"
)
_RSA_E_HEX = "10001"
# A P-256 PKCS#8 key, to check that a key of the wrong algorithm is refused.
_EC_PKCS8_B64 = (
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgzFEtvsByckhwcE/3iIUB81ZrPtSv"
    "y/GMpSga3GzVF7KhRANCAATaPNf0nqUZsOsokhh/1ipHCL3yYKnwprFVLCdSaTRU/PeEpkoVg4KD"
    "4t9PpAOQwdqefpjxdN+ww9ajULMd2n4x"
)

_RSA_N = int(_RSA_N_HEX, 16)
_RSA_E = int(_RSA_E_HEX, 16)
_RSA_K = (_RSA_N.bit_length() + 7) // 8  # 256, an RSA-2048 modulus


def _rsa_public_raw(block: bytes) -> bytes:
    """Raise one raw block with the fixture's public exponent, in pure Python.

    The ciphertext the shim receives is therefore built without OpenSSL, so an
    assertion about what the shim does with it is a statement about the shim's
    own PKCS#1 handling rather than about OpenSSL's.
    """
    return pow(int.from_bytes(block, "big"), _RSA_E, _RSA_N).to_bytes(_RSA_K, "big")


def _rsa_js(body: str, transformation: str = "RSA") -> str:
    """A JS fragment with ``c`` bound to a cipher holding the fixture's keys."""
    return (
        "var c = java.createAsymmetricCrypto('" + transformation + "');"
        "c.setPrivateKey(java.base64DecodeToByteArray('" + _RSA_PKCS8_B64 + "'));"
        "c.setPublicKey(java.base64DecodeToByteArray('" + _RSA_SPKI_B64 + "'));" + body
    )


def _rsa_eval(body: str, transformation: str = "RSA") -> str:
    return _engine()._try_eval_js(_rsa_js(body, transformation), "")


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_decrypts_the_published_pkcs1_vector_and_rejects_the_invalid_one():
    """Wycheproof tcId 3 decrypts; tcId 9 (InvalidPkcs1Padding) raises."""
    value = _rsa_eval(
        "var once = function (hexCt) {"
        "  try { return c.decryptStr(java.hexDecodeToByteArray(hexCt), false); }"
        "  catch (e) { return 'threw: ' + e.message; }"
        "};"
        "once('" + _WY_PKCS1_VALID_CT + "') + '|'"
        "+ once('" + _WY_PKCS1_INVALID_CT + "');"
    )

    good, bad = value.split("|", 1)

    assert good == bytes.fromhex(_WY_PKCS1_VALID_MSG).decode()
    # The whole point of doing the padding by hand: Java throws here, OpenSSL
    # would have returned a pseudo-random buffer.
    assert bad.startswith("threw: ")
    assert "BadPaddingException" in bad


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_pkcs1_padding_rules_match_java():
    """`sun.security.rsa.RSAPadding.unpadV15`, including its 8-byte PS rule.

    Each block is raised to a ciphertext with pure-Python ``pow()``, so these
    cases cover the rules the published vectors cannot isolate.
    """

    def block(block_type: int, ps_len: int, message: bytes, fill: int = 0x41) -> bytes:
        assert 2 + ps_len + 1 + len(message) == _RSA_K
        return bytes([0, block_type]) + bytes([fill]) * ps_len + b"\x00" + message

    cases = [
        # PS may be longer than 8 bytes; the message is whatever follows the
        # first zero byte.
        ("ps 251", block(2, 251, b"ok"), b"ok"),
        # ... and exactly 8 is the boundary that Java accepts.
        ("ps 8", block(2, 8, b"m" * 245), b"m" * 245),
        # ... while 7 is the boundary it rejects.
        ("ps 7", block(2, 7, b"m" * 246), None),
        # No 0x00 separator at all.
        ("no separator", bytes([0, 2]) + b"\x41" * (_RSA_K - 2), None),
        # A type 1 block (`00 01 FF..FF 00 M`) is what a *public* key decrypts,
        # not a private one.
        ("type 1 for private key", block(1, 8, b"m" * 245, fill=0xFF), None),
        # The header's first byte must be 0x00.
        ("header 01 02", bytes([1, 2]) + b"\x41" * (_RSA_K - 2), None),
        # A zero *after* the separator belongs to the message, not to the
        # separator search.
        (
            "zero inside the message",
            bytes([0, 2]) + b"\x41" * 12 + b"\x00" + b"\x00\x01\x02" + b"\x41" * (_RSA_K - 18),
            b"\x00\x01\x02" + b"\x41" * (_RSA_K - 18),
        ),
    ]

    value = _rsa_eval(
        "var cases = [" + ",".join("'" + _rsa_public_raw(b).hex() + "'" for _, b, _ in cases) + "];"
        "var out = [];"
        "for (var i = 0; i < cases.length; i++) {"
        "  try {"
        "    out.push('ok:' + c.decrypt(java.hexDecodeToByteArray(cases[i]), false).toString('hex'));"
        "  } catch (e) { out.push('err'); }"
        "}"
        "out.join(';');"
    )
    got = value.split(";")

    assert len(got) == len(cases)
    for (label, _block, expected), actual in zip(cases, got):
        assert actual == ("err" if expected is None else "ok:" + expected.hex()), label


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_pkcs1_plaintext_limit_is_k_minus_11():
    """`RSAPadding.padV15` allows at most `k - 11` bytes -- two header bytes, at
    least 8 padding bytes and the separator -- and JCE reports the overflow as
    IllegalBlockSizeException rather than silently emitting a bad block."""
    limit = _RSA_K - 11
    value = _rsa_eval(
        "var why = function (fn) { try { fn(); return 'no-error'; }"
        "  catch (e) { return e.message; } };"
        "c.encrypt('x'.repeat(" + str(limit) + "), true).length + '|'"
        "+ why(function () { c.encrypt('x'.repeat(" + str(limit + 1) + "), true); });"
    )
    length, message = value.split("|", 1)

    assert length == str(_RSA_K)
    assert str(limit) in message
    assert "IllegalBlockSizeException" in message


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_pkcs1_block_type_follows_the_key_that_owns_the_operation():
    """`RSACipher.engineInit`: type 2 for encrypt+public / decrypt+private,
    type 1 for encrypt+private / decrypt+public.  Decrypting a type 2 block
    with the public key must therefore fail, and vice versa."""
    value = _rsa_eval(
        "var d = function (hexCt, usePublic) {"
        "  try { return c.decryptStr(java.hexDecodeToByteArray(hexCt), usePublic); }"
        "  catch (e) { return 'err'; }"
        "};"
        "var pub = c.encryptHex('x', true);"
        "var priv = c.encryptHex('x', false);"
        "d(pub, false) + '|' + d(pub, true) + '|' + d(priv, true) + '|' + d(priv, false);"
    )

    assert value == "x|err|x|err"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_default_transformation_is_pkcs1padding():
    """A bare "RSA" means RSA/ECB/PKCS1Padding, as in JCE, and the names are
    case-insensitive."""
    names = ["RSA", "RSA/ECB/PKCS1Padding", "RSA/NONE/PKCS1Padding", "rsa/ecb/pkcs1padding"]
    value = _engine()._try_eval_js(
        "var out = [];"
        "var names = [" + ",".join("'" + n + "'" for n in names) + "];"
        "for (var i = 0; i < names.length; i++) {"
        "  try {"
        "    var x = java.createAsymmetricCrypto(names[i]);"
        "    x.setPrivateKey(java.base64DecodeToByteArray('" + _RSA_PKCS8_B64 + "'));"
        "    out.push(x.decryptStr(java.hexDecodeToByteArray('" + _WY_PKCS1_VALID_CT + "'), false));"
        "  } catch (e) { out.push('threw: ' + e.message); }"
        "}"
        "out.join('|');",
        "",
    )

    assert value == "|".join([bytes.fromhex(_WY_PKCS1_VALID_MSG).decode()] * len(names))


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_nopadding_is_the_raw_modular_transform():
    """`NoPadding` skips the padding entirely, in both directions.

    The ciphertext and the block both come from pure-Python `pow()`, so the
    equality is a cross-implementation check rather than a round-trip.
    """
    value = _rsa_eval(
        "c.decrypt(java.hexDecodeToByteArray('" + _NOPAD_CT + "'), false).toString('hex')"
        "+ '|' + c.encryptHex(java.hexDecodeToByteArray('" + _NOPAD_BLOCK_HEX + "'), true);",
        "RSA/ECB/NoPadding",
    )

    assert value == _NOPAD_BLOCK_HEX + "|" + _NOPAD_CT


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_oaep_works_only_from_the_spelling_without_with():
    """`OAEPPadding` is usable; every `OAEPWith…AndMGF1Padding` spelling is not.

    hutool's `KeyUtil.getAlgorithmAfterWith` keeps what follows the **last**
    "with", so "RSA/ECB/OAEPWithSHA-1AndMGF1Padding" becomes
    "SHA-1AndMGF1Padding" and fails in `KeyPairGenerator.getInstance` before a
    Cipher exists -- in Legado too.  Reproducing that quirk matters: it is why
    the OAEP digest/MGF1 pairing never has to be guessed.
    """
    value = _rsa_eval(
        "c.decryptStr(java.hexDecodeToByteArray('" + _OAEP_SHA1_CT + "'), false);",
        "RSA/ECB/OAEPPadding",
    )
    assert value == _OAEP_SHA1_MSG

    for spelling, mangled in (
        ("RSA/ECB/OAEPWithSHA-1AndMGF1Padding", "SHA-1AndMGF1Padding"),
        ("RSA/ECB/OAEPWithSHA-256AndMGF1Padding", "SHA-256AndMGF1Padding"),
    ):
        message = _engine()._try_eval_js(
            "var out;"
            "try { java.createAsymmetricCrypto('" + spelling + "'); out = 'constructed'; }"
            "catch (e) { out = e.message; }"
            "out;",
            "",
        )
        assert message != "constructed"
        # The name hutool derives from the spelling is what has to fail: the
        # message must name it, otherwise a "helpful" fix to getAlgorithmAfterWith
        # would quietly turn this into an unsupported-padding error instead.
        assert '解析为 "' + mangled + '"' in message


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_use_public_key_defaults_to_the_public_key():
    """Kotlin `usePublicKey: Boolean? = true` with
    `when (usePublicKey) { true -> PublicKey; else -> PrivateKey }`.

    `@JvmOverloads` gives Rhino a one-argument overload for the omitted case, so
    an omitted argument means the *public* key while an explicit `null` or
    `false` means the private one.  A source that sets only a private key and
    then calls `decrypt(data)` therefore uses a random public key -- in Legado
    too (see the next test).
    """
    value = _rsa_eval(
        "var ct = c.encryptBase64('x', true);"
        "var st = c.encryptBase64('y', false);"
        "var one = function (fn) { try { return fn(); } catch (e) { return 'err'; } };"
        "one(function () { return c.decryptStr(ct); }) + '|'"
        "+ one(function () { return c.decryptStr(ct, null); }) + '|'"
        "+ one(function () { return c.decryptStr(ct, false); }) + '|'"
        "+ one(function () { return c.decryptStr(st, true); }) + '|'"
        "+ one(function () { return c.decryptStr(st, undefined); });"
    )

    assert value == "err|x|x|y|err"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_keys_are_pkcs8_or_spki_der_and_the_getters_round_trip():
    """`KeyUtil.generatePrivateKey` wraps the bytes in a `PKCS8EncodedKeySpec`
    and `setPrivateKey(String)` is `key.encodeToByteArray()` -- plain UTF-8, with
    no PEM or Base64 handling -- so a PEM string fails in Legado as well."""
    value = _rsa_eval(
        "var why = function (fn) { try { fn(); return 'no-error'; }"
        "  catch (e) { return e.message; } };"
        "(c.getPublicKeyBase64() === '" + _RSA_SPKI_B64 + "') + '|'"
        "+ (c.getPrivateKeyBase64() === '" + _RSA_PKCS8_B64 + "') + '|'"
        "+ why(function () {"
        "    java.createAsymmetricCrypto('RSA').setPrivateKey('-----BEGIN PRIVATE KEY-----');"
        "  }) + '|'"
        "+ why(function () {"
        "    java.createAsymmetricCrypto('RSA')"
        "      .setPrivateKey(java.base64DecodeToByteArray('" + _EC_PKCS8_B64 + "'));"
        "  }) + '|'"
        "+ why(function () {"
        "    java.createAsymmetricCrypto('RSA').setPrivateKey(Buffer.from('nope', 'utf-8'));"
        "  });"
    )
    ok_pub, ok_priv, pem, wrong_type, garbage = value.split("|", 4)

    assert (ok_pub, ok_priv) == ("true", "true")
    assert "PEM" in pem
    assert "ec" in wrong_type and "RSA" in wrong_type
    assert "PKCS#8" in garbage


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_constructor_generates_a_pair_eagerly_like_legado():
    """`BaseAsymmetric.init` calls `initKeys()` as soon as both keys are null,
    so every `createAsymmetricCrypto` generates a 1024-bit pair first
    (`KeyUtil.DEFAULT_KEY_SIZE`).

    Two consequences are pinned here: only setting the private key leaves that
    random public key in place (so a flagless `decrypt` cannot work), and
    `setXxxKey(null)` is how the key really becomes absent.
    """
    value = _rsa_eval(
        "var g = java.createAsymmetricCrypto('RSA');"
        "var pub = g.getPublicKeyBase64();"
        "var pair = g.decryptStr(g.encryptBase64('x', true), false);"
        "var only = java.createAsymmetricCrypto('RSA');"
        "only.setPrivateKey(java.base64DecodeToByteArray('" + _RSA_PKCS8_B64 + "'));"
        "var ct = c.encryptBase64('x', true);"
        "var flagless = function () { try { return only.decryptStr(ct); }"
        "  catch (e) { return 'err'; } };"
        "(pub !== '" + _RSA_SPKI_B64 + "') + '|'"
        "+ (g.getPrivateKeyBase64() !== '" + _RSA_PKCS8_B64 + "') + '|'"
        # A 1024-bit RSA SubjectPublicKeyInfo is 162 DER bytes.
        "+ java.base64DecodeToByteArray(pub).length + '|'"
        "+ pair + '|' + flagless() + '|' + only.decryptStr(ct, false) + '|'"
        "+ (function () {"
        "    var n = java.createAsymmetricCrypto('RSA');"
        "    n.setPublicKey(null);"
        "    try { n.encrypt('x', true); return 'no-error'; }"
        "    catch (e) { return e.message; }"
        "  })();"
    )
    fresh_pub, fresh_priv, der_len, pair, flagless, flagged, npe = value.split("|", 6)

    assert (fresh_pub, fresh_priv, der_len) == ("true", "true", "162")
    assert pair == "x"
    assert flagless == "err"
    assert flagged == "x"
    # BaseAsymmetric.getKeyByType, message verbatim.
    assert npe == "Public key must not null when use it !"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_string_arguments_follow_the_encrypt_decrypt_asymmetry():
    """`AsymmetricEncryptor.encrypt(String, …)` is `StrUtil.utf8Bytes(data)`,
    while `AsymmetricDecryptor.decrypt(String, …)` first runs the string through
    `SecureUtil.decode` (hex when it is all hex digits, else Base64).  A ByteArray
    is taken raw, which is what Kotlin's `is ByteArray -> String(decrypt(…))`
    branch relies on."""
    value = _rsa_eval(
        "var b64 = c.encryptBase64('明文-abc', true);"
        "var hexCt = c.encryptHex('明文-abc', true);"
        "var why = function (fn) { try { fn(); return 'no-error'; }"
        "  catch (e) { return e.message; } };"
        "c.decryptStr(b64, false) + '|'"
        "+ c.decryptStr(hexCt, false) + '|'"
        "+ c.decryptStr(java.hexDecodeToByteArray(hexCt), false) + '|'"
        "+ why(function () { c.encrypt(123, true); });"
    )

    assert value == "明文-abc|明文-abc|明文-abc|Unexpected input type"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_rsa_block_size_splits_the_data_only_when_it_is_set():
    """hutool's `doFinal` is a single operation while the block size is < 0 --
    which is the case for RSA on Legado, since `Cipher.getBlockSize()` only
    returns a size when BouncyCastle is on the classpath.  Setting one switches
    to `doFinalWithBlock`, and 0 must not loop forever the way it does in Java."""
    value = _rsa_eval(
        "var one = c.encrypt('y'.repeat(200), true).length;"
        "var defaultSize = c.getEncryptBlockSize() + ',' + c.getDecryptBlockSize();"
        "var s = java.createAsymmetricCrypto('RSA');"
        "s.setPrivateKey(java.base64DecodeToByteArray('" + _RSA_PKCS8_B64 + "'));"
        "s.setPublicKey(java.base64DecodeToByteArray('" + _RSA_SPKI_B64 + "'));"
        "s.setEncryptBlockSize(128);"
        "s.setDecryptBlockSize(256);"
        "var two = s.encrypt('y'.repeat(200), true).length;"
        "var round = s.decryptStr(s.encrypt('y'.repeat(200), true), false) === 'y'.repeat(200);"
        "var z = java.createAsymmetricCrypto('RSA');"
        "z.setPublicKey(java.base64DecodeToByteArray('" + _RSA_SPKI_B64 + "'));"
        "z.setEncryptBlockSize(0);"
        "var zero = z.encrypt('y'.repeat(200), true).length;"
        "defaultSize + '|' + one + '|' + two + '|' + round + '|' + zero;"
    )

    assert value == "-1,-1|256|512|true|256"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_create_asymmetric_crypto_rejects_what_legado_rejects():
    """Only RSA can be constructed, for two separate reasons that both apply to
    Legado: it ships no BouncyCastle (so `Cipher.getInstance` fails for anything
    else) and hutool's `getAlgorithmAfterWith` mangles any spelling containing
    "with"."""
    cases = {
        "EC": "EC",
        # hutool folds ECDSA/SM2/ECIES onto EC before looking it up.
        "SM2": '"EC"',
        "RSA/CBC/PKCS1Padding": "ECB",
        "RSA/ECB": "变换需形如",
        "RSA/ECB/ISO9796-1Padding": "不支持补码方式",
    }
    expressions = []
    for transformation in cases:
        expressions.append(
            "(function () {"
            "  try { java.createAsymmetricCrypto('" + transformation + "'); return 'no-error'; }"
            "  catch (e) { return e.message; }"
            "})()"
        )
    value = _engine()._try_eval_js("[" + ",".join(expressions) + "].join('\\n');", "")

    for (transformation, needle), message in zip(cases.items(), value.split("\n")):
        assert "no-error" != message, transformation
        assert needle in message, transformation


# ---------------------------------------------------------------------------
# Signature (docs/legado-rule-spec-diff.md, item E)
#
# `java.createSign` is Legado's `Sign(algorithm)`, i.e. hutool 5.8.22, whose
# `help/crypto/Sign.kt` adds only the four ByteArray/String `setXxxKey` overloads
# to hutool's `Sign` (surface: `sign`, `signHex`, `verify`).  Two facts from
# hutool's source shape these expectations:
#
#   * `SecureUtil.createSignature` hands the string straight to
#     `Signature.getInstance`, so the JCE names are the spec -- including
#     `SHA256WithRSA/PSS`, which `SignAlgorithm` itself comments as "需要 BC 库
#     加入支持", and Legado ships no BouncyCastle.
#   * `Sign.init` builds the `Signature` *before* `super.init()`, and the latter
#     generates a key pair when both keys are null.
#
# Oracles: pyca/cryptography for the RSA PKCS#1 v1.5 signatures (deterministic,
# so the bytes can be compared for equality), for the ECDSA and DSA signatures,
# and Wycheproof `rsa_signature_2048_sha256_test.json` for a **published** verify
# vector plus an InvalidSignature one.
# ---------------------------------------------------------------------------

_SIG_MSG = "novelhub-signature"
_SIG_RSA_SHA256_HEX = (
    "aae8448e1f8931f96dac76b512a05e4b75423c478b02012fef79133ddf602947479a23d17a4ad"
    "a60965fb0a88750696ae3efbbb99b08a161c1b7603a616e37382dc6bec6eb7b88761e35edc91"
    "03dd124d5495dafcc2c787d962b2b0c62e1ad4260b0a70fff273e9775d8304e12458707cc9e1"
    "f746a64b61b6be724233861b66a306612bac4603a355501d00b3041a6bc86363097f24aec016"
    "760dc983f0c10ad38eca3107738a98957054e24b3c4ecc80c376a93ccf59294d79e92b47db62"
    "c614285beb986b57d334dae353c3f13767ebe4104586beceb4a88b10b42c29935819b9dbab3c"
    "41dd62ca05d9e2de009460d07ecfb7405f7328971134d3ab8e41db6"
)
_SIG_RSA_SHA1_HEX = (
    "8bc9405b99ae888a51be884769a07c24c77c0459610067ec33e33591387c1b671e55bea9c8a8"
    "4a3d922bd9f3a21bf34485e5cb7c8930278f38ec78a4dc5339a8f00f2a48058592199f90cb72"
    "765b35c064ec145ccc12d4bb3f733de698764be72adc0a71830c696d2ceb6b79ec1adc70d873"
    "75f76c4a5569d898ea83afc06b31657a593c2d2844508b5d522f61e3f097e2a28d88074d1c5b"
    "c5d4b10bb3f28eb5c7461c65db08a88d4b384f51eb9ad0c868e0b66211198db53f710e2a41cb"
    "f15da2c4dd687cd03446182784781268e8ae2d328262bcadbe782132464b849be170652565e3"
    "a40f7d9c53d6bd5b12d4844d4cd3bd370ce06276c73096182f105c4c"
)
_SIG_RSA_MD5_HEX = (
    "2dc74661a97d4d7fd5722e43c21848a6f15808452cb478dc06dc7364711de1b96ac3e7af8bb2"
    "30816f4b7fa1dda920a27103a1e3a8162b7179a778ac0852415e355ff77a04394bc78f4d6f90"
    "3c16193b395f5c2c73b53d0be65f1a44acf16d2aae9d6a57590092a37da74ca40cbac13dca8e"
    "142f4887ca3cb79b7af35a7acb60d6bf2b2eb6ee4679ad145a928a60be123a8baaa88c6bd5b7"
    "3560cb1ed9d4a348a651ff67f63bcc0eadeddd91f87d549003637039b90e2cd51caf64543cc9"
    "6f628689dba16c4316e9aed61588c57a0afab9e216c7c501033e9da6e1512b6107ff45fdf4a8"
    "97e33fb293a6b557121bedf19d6695e097257738deeaac86a9f6adfc"
)
# A P-256 key pair plus two ECDSA-SHA256 signatures over the same message (the
# second over a different message, so it must be rejected).
_EC_SPKI_B64 = (
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAELWODuD4ho7rjlwdgXhciEwr4ERVsZQSZInse+CoA"
    "Dtrd3dOUBVyRE2b33+Y39LFWiBX3aoDAzIulWoVC1OeQRA=="
)
_EC_PKCS8_B64 = (
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg8PHODUGwByfgwVSYRYLiHD3I5tkb"
    "jpsF7rxgxcmu3+WhRANCAAQtY4O4PiGjuuOXB2BeFyITCvgRFWxlBJkiex74KgAO2t3d05QFXJET"
    "Zvff5jf0sVaIFfdqgMDMi6VahULU55BE"
)
_SIG_EC_SHA256_HEX = (
    "3045022100baacd5d0784e7f4a50c63e55e8f32c68db9d69b5c040aff4e5ac84027498d94f"
    "0220338ece5875e8bd1f42922e837e22f251f3e2f282ec73103190c21ae1544739c3"
)
_SIG_EC_SHA256_OTHER_HEX = (
    "3046022100c5d7aa5fc6b8e2d219db7d0cba51f8c772d4ebc319b7028b4724a6265bd3c8ce"
    "022100dc83fad4f806529109e8fb02edf79266582c3984618ebe42c643e73b1b6d53b2"
)
# A DSA-1024 key pair (q is fixed at 160 bits when L = 1024) and a SHA-1
# signature over the same message.
_DSA_PKCS8_B64 = (
    "MIIBSwIBADCCASwGByqGSM44BAEwggEfAoGBAO4ZhSPO577xTsFDvJMEsM19DC+T2rdC/LEfKSfP"
    "V51VI9vyD9U8IpW/535s2StRpJ9QhnoTIUVNktIJL9Wvv/2Yd38ChMLinB/cGzP5nz8qNJvu1vHF"
    "R7US3Fz1IXvZmdCPmwWeL0N+hx2AK3oiGRxwTXsUoQRXKDSOH7qbkWJhAhUAj6mT3Fk7/EQaDWiP"
    "e87wlLG8Ty8CgYEAjHCGDPn3CtieJF8N0Hb1oAQLF0q+H1uSg2BJFXn0IItYsI6RIbRQ6uUbnckd"
    "x4dQsA4Tv+v8w9Wn8SWN4vNmD362JgJVtNLobcIj8SR+1azwMwIOjX8YbReD3sGg0BmO1wSEHrrr"
    "i2jy4MwUKv+T1WYA/AM17cppAhpsOjLrf5kEFgIUPPZbCrHoc6c+STOZcfe8Bf3VOHs="
)
_DSA_SPKI_B64 = (
    "MIIBtzCCASwGByqGSM44BAEwggEfAoGBAO4ZhSPO577xTsFDvJMEsM19DC+T2rdC/LEfKSfPV51V"
    "I9vyD9U8IpW/535s2StRpJ9QhnoTIUVNktIJL9Wvv/2Yd38ChMLinB/cGzP5nz8qNJvu1vHFR7US"
    "3Fz1IXvZmdCPmwWeL0N+hx2AK3oiGRxwTXsUoQRXKDSOH7qbkWJhAhUAj6mT3Fk7/EQaDWiPe87w"
    "lLG8Ty8CgYEAjHCGDPn3CtieJF8N0Hb1oAQLF0q+H1uSg2BJFXn0IItYsI6RIbRQ6uUbnckdx4dQ"
    "sA4Tv+v8w9Wn8SWN4vNmD362JgJVtNLobcIj8SR+1azwMwIOjX8YbReD3sGg0BmO1wSEHrrri2jy4"
    "MwUKv+T1WYA/AM17cppAhpsOjLrf5kDgYQAAoGAWb+Gv7iBVvgWmL6xFL3o/N7oDsvN0SFKJ5JivWpS"
    "G2M8/vPhaR5DCR0femfap404Gowt3VAZnYlGuK33KzYNlpqLERdj5dHQ6XxdcPLki7zdyvgI7Shm"
    "vph6yTKfDytZ5nF1J+OE5Zg04xVtvoz4mJzecMIY4NsrPCPAh1tzL6I="
)
_SIG_DSA_SHA1_HEX = (
    "302c021462f0b056a8dd63a7416b5f2bc0fadc934c757fb602141399f5c7d4e0cda7c504"
    "dce0e6112b536e6b8e2d"
)
# Wycheproof rsa_signature_2048_sha256_test.json group 0: a published
# RSASSA-PKCS1-v1_5 verify vector and an InvalidSignature one.
_WY_SIG_SPKI_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAorRRoH0KpfluRVZxUTVQUUqKW0YuvvcX"
    "CU+h/ugiJOY3+XRtP3yv0xh42AMltu9aFwD2WQO0aUKeidbqyIRQl7WrOTGJ25JRLtincRoSU/rN"
    "IPecFegkfz0+QuRuSMmOJUov6XZTE6A+/48X4aApOXofomqNzib0kO2BKZYV2YFMItphBCjgnH2W"
    "WFlCZvXAIdD87KCNlFoSvoLeTR7Oa0wDFFtdNJXU7VQR64eNrwX9evw+Ca2g8RJkIvWQl1oZaYFvS"
    "GmLy7obTZyuedRg2Pn4Xnl1AF2bwixOWsD3waRdElaaYoB9O5oC5aUw53MGb0U9H1tMLpz3ggKD9"
    "0K51QIDAQAB"
)
_WY_SIG_VALID_MSG = "54657374"
_WY_SIG_VALID_SIG = (
    "264491e844c119f14e425c03282139a558dcdaeb82a4628173cd407fd319f9076eaebc0dd87a"
    "1c22e4d17839096886d58a9d5b7f7aeb63efec56c45ac7bead4203b6886e1faa90e028ec0ae"
    "094d46bf3f97efdd19045cfbc25a1abda2432639f9876405c0d68f8edbf047c12a454f7681d"
    "5d5a2b54bd3723d193dbad4338baad753264006e2d08931c4b8bb79aa1c9cad10eb6605f87c"
    "5831f6e2b08e002f9c6f21141f5841d92727dd3e1d99c36bc560da3c9067df99fcaf818941"
    "f72588be33032bad22caf6704223bb114d575b6d02d9d222b580005d930e8f40cce9f672ee"
    "bb634a20177d84351627964b83f2053d736a84ab1a005f63bd5ba943de6205c"
)
_WY_SIG_INVALID_MSG = "313233343030"
_WY_SIG_INVALID_SIG = (
    "a2b451a07d0aa5f96e455671513550514a8a5b462ebef717094fa1fee82224e637f9746d3f7c"
    "afd31878d80325b6ef5a1700f65903b469429e89d6eac8845097b5ab393189db92512ed8a77"
    "11a1253facd20f79c15e8247f3d3e42e46e48c98e254a2fe9765313a03eff8f17e1a029397a"
    "1fa26a8dce26f490ed81299615d9814c22da610428e09c7d9658594266f5c021d0fceca08d9"
    "45a12be82de4d1ece6b4c03145b5d3495d4ed5411eb878daf05fd7afc3e09ada0f1126422f5"
    "90975a1969816f48698bcbba1b4d9cae79d460d8f9f85e7975005d9bc22c4e5ac0f7c1a45d"
    "12569a62807d3b9a02e5a530e773066f453d1f5b4c2e9cf7820283f742b9d4"
)


def _flip_hex_nibble(value: str) -> str:
    """Change the first hex digit, to corrupt a published signature."""
    return format(int(value[0], 16) ^ 1, "x") + value[1:]


def _signer(
    algorithm: str,
    private_b64: str | None = None,
    public_b64: str | None = None,
    name: str = "c",
) -> str:
    """A JS fragment building a Signer named `name`, with the given keys."""
    js = "var " + name + " = java.createSign('" + algorithm + "');"
    if private_b64:
        js += name + ".setPrivateKey(java.base64DecodeToByteArray('" + private_b64 + "'));"
    if public_b64:
        js += name + ".setPublicKey(java.base64DecodeToByteArray('" + public_b64 + "'));"
    return js


def _sign_eval(body: str, algorithm: str = "SHA256withRSA") -> str:
    return _engine()._try_eval_js(
        _signer(algorithm, _RSA_PKCS8_B64, _RSA_SPKI_B64) + body, ""
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_sign_rsa_pkcs1_v1_5_matches_an_independent_implementation():
    """RSASSA-PKCS1-v1_5 is deterministic, so the signature bytes can be
    compared for equality with pyca/cryptography's, digest by digest."""
    assert _sign_eval("c.signHex('" + _SIG_MSG + "');") == _SIG_RSA_SHA256_HEX
    assert _sign_eval(
        "c.signHex('" + _SIG_MSG + "');", "SHA1withRSA"
    ) == _SIG_RSA_SHA1_HEX
    assert _sign_eval(
        "c.signHex('" + _SIG_MSG + "');", "MD5withRSA"
    ) == _SIG_RSA_MD5_HEX
    # JCE algorithm names are case-insensitive.
    assert _sign_eval(
        "c.signHex('" + _SIG_MSG + "');", "sha256withrsa"
    ) == _SIG_RSA_SHA256_HEX
    # `sign(byte[])` is the raw data, `sign(String)` its UTF-8 bytes.
    assert _sign_eval(
        "var hex = c.signHex('" + _SIG_MSG + "');"
        "var buf = c.sign(Buffer.from('" + _SIG_MSG + "', 'utf-8')).toString('hex');"
        "var wide = c.signHex('明文');"
        "var wideBuf = c.signHex(java.strToBytes('明文', 'UTF-8'));"
        "(hex === buf) + '/' + (wide === wideBuf);"
    ) == "true/true"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_sign_verifies_the_published_wycheproof_vector():
    """A published RSASSA-PKCS1-v1_5 vector verifies, and Wycheproof's
    InvalidSignature case does not."""
    value = _engine()._try_eval_js(
        _signer("SHA256withRSA", None, _WY_SIG_SPKI_B64)
        + "var ok = c.verify(java.hexDecodeToByteArray('" + _WY_SIG_VALID_MSG + "'),"
          " java.hexDecodeToByteArray('" + _WY_SIG_VALID_SIG + "'));"
        + "var publishedBad = c.verify(java.hexDecodeToByteArray('"
        + _WY_SIG_INVALID_MSG + "'), java.hexDecodeToByteArray('" + _WY_SIG_INVALID_SIG + "'));"
        # Corrupting one nibble of the good signature must stop it verifying.
        + "var corrupted = c.verify(java.hexDecodeToByteArray('" + _WY_SIG_VALID_MSG + "'),"
          " java.hexDecodeToByteArray('" + _flip_hex_nibble(_WY_SIG_VALID_SIG) + "'));"
        # ... as must a signature of the wrong length.
        + "var short_ = c.verify(java.hexDecodeToByteArray('" + _WY_SIG_VALID_MSG + "'),"
          " java.hexDecodeToByteArray('00'));"
        + "ok + '|' + publishedBad + '|' + corrupted + '|' + short_;",
        "",
    )

    assert value == "true|false|false|false"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_sign_ecdsa_and_dsa_verify_a_foreign_signature():
    """ECDSA and DSA signatures are randomised, so the check is that a signature
    made by pyca/cryptography verifies here, that a signature over a different
    message does not, and that our own signatures round-trip."""
    value = _engine()._try_eval_js(
        _signer("SHA256withECDSA", _EC_PKCS8_B64, _EC_SPKI_B64, "ec")
        + _signer("SHA1withDSA", _DSA_PKCS8_B64, _DSA_SPKI_B64, "dsa")
        + _signer("SHA256withDSA", _DSA_PKCS8_B64, _DSA_SPKI_B64, "dsa256")
        + "var msg = Buffer.from('" + _SIG_MSG + "', 'utf-8');"
        "var ecOk = ec.verify(msg, java.hexDecodeToByteArray('" + _SIG_EC_SHA256_HEX + "'));"
        "var ecOther = ec.verify(msg, java.hexDecodeToByteArray('"
        + _SIG_EC_SHA256_OTHER_HEX + "'));"
        "var ecRound = ec.verify(msg, ec.sign(msg));"
        "var dsaOk = dsa.verify(msg, java.hexDecodeToByteArray('" + _SIG_DSA_SHA1_HEX + "'));"
        "var dsaRound = dsa.verify(msg, dsa.sign(msg));"
        "var dsa256Round = dsa256.verify(msg, dsa256.sign(msg));"
        "[ecOk, ecOther, ecRound, dsaOk, dsaRound, dsa256Round].join('|');",
        "",
    )

    assert value == "true|false|true|true|true|true"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_create_sign_rejects_what_legado_rejects():
    """The names hutool's `SignAlgorithm` cannot deliver, and the families that
    need BouncyCastle -- which Legado does not ship."""
    cases = {
        # `SignAlgorithm.NONEwithRSA` -- no digest at all.
        "NONEwithRSA": "摘要",
        # MD2 is not in OpenSSL 3 either.
        "MD2withRSA": "摘要",
        # SignAlgorithm marks these three as "需要 BC 库加入支持".
        "SHA256WithRSA/PSS": "BC",
        # The JDK alias for PSS, whose salt length JCE picks for itself.  The
        # needle has to be wording only this branch produces: the generic
        # message echoes the algorithm name, which already contains "RSAandMGF1".
        "SHA256withRSAandMGF1": "PSS 签名",
        # No digest, and Node needs the one-shot API for it.
        "Ed25519": "with",
        "SM3withSM2": "摘要",
        "totally-bogus": "with",
    }
    expressions = [
        "(function () {"
        "  try { java.createSign('" + name + "'); return 'no-error'; }"
        "  catch (e) { return e.message; }"
        "})()"
        for name in cases
    ]
    value = _engine()._try_eval_js("[" + ",".join(expressions) + "].join('\\n');", "")

    for (name, needle), message in zip(cases.items(), value.split("\n")):
        assert message != "no-error", name
        assert needle in message, name


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_sign_keys_and_the_constructed_key_pair():
    """`Sign` inherits its key handling from `BaseAsymmetric`, so a Signer built
    with no keys gets a fresh pair (`KeyUtil.DEFAULT_KEY_SIZE` again), and
    `setXxxKey(null)` is the way a key really becomes absent."""
    value = _engine()._try_eval_js(
        _signer("SHA256withRSA", _RSA_PKCS8_B64, _RSA_SPKI_B64)
        + "var why = function (fn) { try { fn(); return 'no-error'; }"
          "  catch (e) { return e.message; } };"
        "var out = [];"
        "out.push(c.getPrivateKeyBase64() === '" + _RSA_PKCS8_B64 + "');"
        "out.push(c.getPublicKeyBase64() === '" + _RSA_SPKI_B64 + "');"
        "out.push(c.setPrivateKey(java.base64DecodeToByteArray('" + _RSA_PKCS8_B64 + "')) === c);"
        "out.push(why(function () {"
        "  java.createSign('SHA256withRSA').setPrivateKey('-----BEGIN PRIVATE KEY-----');"
        "}));"
        "out.push(why(function () {"
        "  java.createSign('SHA256withRSA')"
        "    .setPrivateKey(java.base64DecodeToByteArray('" + _EC_PKCS8_B64 + "'));"
        "}));"
        "out.push(why(function () {"
        "  java.createSign('SHA256withECDSA')"
        "    .setPrivateKey(java.base64DecodeToByteArray('" + _RSA_PKCS8_B64 + "'));"
        "}));"
        "out.push(why(function () {"
        "  var s = java.createSign('SHA256withRSA');"
        "  s.setPrivateKey(null);"
        "  s.sign('x');"
        "}));"
        "out.push(why(function () {"
        "  var s = java.createSign('SHA256withRSA');"
        "  s.setPublicKey(null);"
        "  s.verify(Buffer.from('x'), Buffer.from('y'));"
        "}));"
        "out.push(why(function () { java.createSign('SHA256withRSA').sign(123); }));"
        "out.join('\\n');",
        "",
    )
    (
        priv_round,
        pub_round,
        chained,
        pem,
        ec_into_rsa,
        rsa_into_ec,
        no_priv,
        no_pub,
        bad_input,
    ) = value.split("\n")

    assert (priv_round, pub_round, chained) == ("true", "true", "true")
    assert "PEM" in pem
    assert "ec" in ec_into_rsa and "RSA" in ec_into_rsa
    assert "rsa" in rsa_into_ec and "EC" in rsa_into_ec
    # BaseAsymmetric.getKeyByType, messages verbatim.
    assert no_priv == "Private key must not null when use it !"
    assert no_pub == "Public key must not null when use it !"
    assert bad_input == "Unexpected input type"

    # With no keys at all, a 1024-bit pair is generated before anything is set.
    generated = _engine()._try_eval_js(
        "var out = [];"
        "var algos = ['SHA256withRSA', 'SHA256withECDSA', 'SHA1withDSA'];"
        "for (var i = 0; i < algos.length; i++) {"
        "  var s = java.createSign(algos[i]);"
        "  var pub = s.getPublicKeyBase64();"
        "  var msg = Buffer.from('x');"
        "  out.push((pub !== null && s.getPrivateKeyBase64() !== null)"
        "    + ':' + java.base64DecodeToByteArray(pub).length"
        "    + ':' + s.verify(msg, s.sign(msg)));"
        "}"
        # A 1024-bit RSA SubjectPublicKeyInfo is 162 DER bytes.
        "out.join('|');",
        "",
    )

    rsa_entry, ec_entry, dsa_entry = generated.split("|")

    # 1024-bit RSA and P-256 SubjectPublicKeyInfo have a fixed DER size; a DSA one
    # is a few bytes either side because DER integers gain a leading zero
    # whenever their top bit is set.
    assert rsa_entry == "true:162:true"
    assert ec_entry == "true:91:true"
    assert dsa_entry.startswith("true:") and dsa_entry.endswith(":true")
    assert 440 <= int(dsa_entry.split(":")[1]) <= 446


# ---------------------------------------------------------------------------
# Legado byte / charset / URL helpers (C-22 remainder)
#
# `java.toURL` is the interesting one: Legado parses with `java.net.URL`, whose
# behaviour differs from the WHATWG URL in two ways that a naive port would miss,
# and both are pinned below.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_str_to_bytes_and_bytes_to_str_round_trip():
    engine = _engine()

    assert engine._try_eval_js(
        "java.bytesToStr(java.strToBytes('中文abc'));", ""
    ) == "中文abc"
    # The charset-taking overload is in the signature too.
    assert engine._try_eval_js(
        "java.bytesToStr(java.strToBytes('abc', 'UTF-8'), 'UTF-8');", ""
    ) == "abc"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_hex_encode_to_string_and_decode_to_bytes():
    engine = _engine()

    assert engine._try_eval_js(
        "java.hexEncodeToString('abc') + '|' +"
        "java.bytesToStr(java.hexDecodeToByteArray('616263'));",
        "",
    ) == "616263|abc"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_base64_decode_to_byte_array_nulls_a_blank_input():
    """Legado returns null for a blank string (`isNullOrBlank`)."""
    engine = _engine()

    assert engine._try_eval_js(
        "String(java.base64DecodeToByteArray('   ') === null) + '|' +"
        "java.bytesToStr(java.base64DecodeToByteArray('aGVsbG8='));",
        "",
    ) == "true|hello"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_random_uuid_is_a_lowercase_v4():
    engine = _engine()

    assert engine._try_eval_js(
        "/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}"
        "-[0-9a-f]{12}$/.test(java.randomUUID());",
        "",
    ) is True


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_to_url_matches_java_url_semantics():
    """`+` decodes to a space (`URLDecoder`), and host/origin/pathname split as in JsURL."""
    engine = _engine()

    js = (
        "var u = java.toURL('https://a.b/c/d?x=1&y=%E4%B8%AD&z=a+b#f');"
        "u.host + '|' + u.origin + '|' + u.pathname + '|' + u.searchParams.z;"
    )

    assert engine._try_eval_js(js, "") == "a.b|https://a.b|/c/d|a b"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_to_url_keeps_an_explicit_default_port():
    """Java's `URL.getPort()` returns 80 for `http://x:80`; the WHATWG URL drops it.

    Legado builds `origin` from `URL.getPort()`, so the port has to be read off
    the raw string -- otherwise `http://x:80` silently becomes `http://x`.
    """
    engine = _engine()

    assert engine._try_eval_js(
        "java.toURL('http://x:80/p').origin + '|' +"
        "java.toURL('http://x/p').origin;",
        "",
    ) == "http://x:80|http://x"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_to_url_resolves_against_a_base_url():
    engine = _engine()

    assert engine._try_eval_js(
        "var u = java.toURL('/rel', 'https://a.b/base/');"
        "u.origin + u.pathname;",
        "",
    ) == "https://a.b/rel"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_an_unsupported_charset_raises_a_readable_error():
    """GBK/GB2312/Big5 need iconv; Node's Buffer cannot do them.

    A clear error beats silently returning mojibake for a Chinese source.
    """
    engine = _engine()

    value = engine._try_eval_js(
        "var msg;"
        "try { java.strToBytes('x', 'GBK'); msg = 'no-error'; }"
        "catch (e) { msg = String(e && e.message ? e.message : e); }"
        "msg;",
        "",
    )

    assert value != "no-error"
    assert "iconv" in value


# ---------------------------------------------------------------------------
# `java.toNumChapter` (JsExtensions.kt:916-924) -- normalises 「第N章」.
#
# The expected values come from reading Legado's code, not from this
# implementation: `stringToInt` = `fullToHalf` → strip whitespace →
# `Integer.parseInt` → else `chineseNumToInt`, and the result is rebuilt from the
# three capture groups of `(第)(.+?)(章)`.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_to_num_chapter_normalises_arabic_and_chinese_numerals():
    engine = _engine()

    js = (
        "java.toNumChapter('第一章') + '|' +"
        "java.toNumChapter('第十二章') + '|' +"
        "java.toNumChapter('第 7 章') + '|' +"
        "java.toNumChapter('第十章') + '|' +"
        "java.toNumChapter('第两章');"
    )

    assert engine._try_eval_js(js, "") == "第1章|第12章|第7章|第10章|第2章"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_to_num_chapter_converts_full_width_digits():
    """`fullToHalf` maps ！..～ (U+FF01..U+FF5E) down by 65248."""
    engine = _engine()

    assert engine._try_eval_js(
        "java.toNumChapter('第１２３章');", ""
    ) == "第123章"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_to_num_chapter_uses_kotlin_integer_division():
    """「一千二」 is 1200: `tmpNum * prev / 10` truncates in Kotlin."""
    engine = _engine()

    assert engine._try_eval_js(
        "java.toNumChapter('第一千二章') + '|' +"
        "java.toNumChapter('第一千零二十五章');",
        "",
    ) == "第1200章|第1025章"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_to_num_chapter_returns_input_when_the_pattern_does_not_match():
    engine = _engine()

    assert engine._try_eval_js(
        "java.toNumChapter('没有数字') + '|' +"
        "String(java.toNumChapter(null));",
        "",
    ) == "没有数字|null"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_to_num_chapter_drops_text_outside_the_match():
    """Legado quirk, pinned: only the three groups are kept.

    ``(第)(.+?)(章)`` matches the first 「第…章」 and the result is rebuilt from
    the groups, so everything else in the string is discarded --
    ``第1章和第2章`` becomes ``第1章``.
    """
    engine = _engine()

    assert engine._try_eval_js(
        "java.toNumChapter('第1章和第2章');", ""
    ) == "第1章"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_to_num_chapter_yields_minus_one_for_an_unmapped_character():
    """`chineseNumToInt` returns -1 when `ChnMap` has no entry, so 「第X章」 -> 第-1章.

    Faithful to Legado (`runCatching { … }.getOrDefault(-1)`), and the reason a
    caller should not treat the output as always numeric.
    """
    engine = _engine()

    assert engine._try_eval_js(
        "java.toNumChapter('第X章');", ""
    ) == "第-1章"


# ---------------------------------------------------------------------------
# `java.timeFormat` / `java.timeFormatUTC` (JsExtensions.kt:512-525)
#
# `timeFormat` is `AppConst.dateFormat.format(Date(time))` and `AppConst.dateFormat`
# is the fixed pattern "yyyy/MM/dd HH:mm" (AppConst.kt:38).  `timeFormatUTC` is
# `SimpleDateFormat(format)` with `timeZone = SimpleTimeZone(sh, "UTC")`, and
# SimpleTimeZone's rawOffset is in **milliseconds** -- pinned below, because
# treating `sh` as hours would be a silent divergence from Legado.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_time_format_utc_offset_is_milliseconds():
    engine = _engine()

    js = (
        "java.timeFormatUTC(0, 'yyyy/MM/dd HH:mm', 0) + '|' +"
        "java.timeFormatUTC(0, 'yyyy/MM/dd HH:mm', 28800000) + '|' +"
        # SimpleTimeZone(8) is an 8-MILLISECOND offset, i.e. still ~UTC.
        "java.timeFormatUTC(0, 'yyyy/MM/dd HH:mm', 8);"
    )

    assert engine._try_eval_js(js, "") == (
        "1970/01/01 00:00|1970/01/01 08:00|1970/01/01 00:00"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_time_format_utc_handles_rollover_and_sub_second_fields():
    engine = _engine()

    # 2021-12-31T16:30:45.123Z at UTC+8 is 2022-01-01 00:30:45.123.
    js = (
        "var t = Date.UTC(2021, 11, 31, 16, 30, 45, 123);"
        "java.timeFormatUTC(t, 'yyyy/MM/dd HH:mm:ss.SSS', 28800000) + '|' +"
        "java.timeFormatUTC(t, 'yyyy-MM-dd hh:mm a', 28800000) + '|' +"
        "java.timeFormatUTC(t, 'yy/M/d', 0);"
    )

    assert engine._try_eval_js(js, "") == (
        "2022/01/01 00:30:45.123|2022-01-01 12:30 AM|21/12/31"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_time_format_utc_honours_java_quoting():
    """A `'...'` section is a literal, and non-pattern letters pass through."""
    engine = _engine()

    js = (
        "var t = Date.UTC(2021, 11, 31, 16, 30, 45, 123);"
        "java.timeFormatUTC(t, \"yyyy'年'MM'月'dd'日'\", 28800000) + '|' +"
        "java.timeFormatUTC(0, 'yyyy/MM/dd 周', 0);"
    )

    assert engine._try_eval_js(js, "") == "2022年01月01日|1970/01/01 周"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_time_format_utc_rejects_a_localised_month_name():
    """`MMM` is the localised month name, which needs per-locale tables.

    Raising a readable error beats emitting the numeric month where Legado would
    emit e.g. "Jan".
    """
    engine = _engine()

    value = engine._try_eval_js(
        "var msg;"
        "try { java.timeFormatUTC(0, 'MMM d', 0); msg = 'no-error'; }"
        "catch (e) { msg = String(e && e.message ? e.message : e); }"
        "msg;",
        "",
    )

    assert value != "no-error"
    assert "月份名" in value


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_time_format_uses_the_runtime_local_clock():
    """`timeFormat` has no offset argument: it formats in the runtime's timezone.

    Compared against Python's own local-time conversion of the same instant, so
    the assertion holds whatever TZ the test runs under.
    """
    import time as _time

    instant_ms = 1640971845123  # 2021-12-31T16:30:45.123Z
    expected = _time.strftime(
        "%Y/%m/%d %H:%M", _time.localtime(int(instant_ms / 1000))
    )
    engine = _engine()

    assert engine._try_eval_js("java.timeFormat(%d);" % instant_ms, "") == expected


# ---------------------------------------------------------------------------
# Source identity reaching JS (docs/js-http-request-side.md, tier 1).
#
# The shim exposes `source.bookSourceUrl`, `source.bookSourceName`, … as getters
# over `__nhSourceConfig`, but only `sourceUrl` was ever injected, so all of them
# read back as "".  `bookSourceUrl` also feeds the Referer of JS-issued requests.
# ---------------------------------------------------------------------------

_SOURCE_CONFIG = {
    "bookSourceUrl": "https://s.test",
    "bookSourceName": "测试源",
    "bookSourceGroup": "分组A",
    # 0 is the *text* source type -- a legitimate falsy value.
    "bookSourceType": 0,
    "bookUrlPattern": r"https://s.test/book/\d+",
    "customOrder": 7,
    "loginUrl": "https://s.test/login",
    "searchUrl": "https://s.test/search?q={{key}}",
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_source_identity_keys_reach_the_js_context():
    engine = YueduRuleEngine(dict(_SOURCE_CONFIG))

    assert engine._try_eval_js(
        "source.bookSourceUrl + '|' + source.bookSourceName + '|'"
        " + source.bookSourceGroup + '|' + source.loginUrl + '|'"
        " + source.customOrder;",
        "",
    ) == "https://s.test|测试源|分组A|https://s.test/login|7"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_source_book_source_type_keeps_a_falsy_zero():
    """`bookSourceType` is 0 for a text source, not "".

    The getters used to be `__nhSourceConfig[key] || ''`, so a source comparing
    `source.bookSourceType === 0` failed.
    """
    engine = YueduRuleEngine(dict(_SOURCE_CONFIG))

    assert engine._try_eval_js(
        "String(source.bookSourceType) + '|'"
        " + String(source.bookSourceType === 0);",
        "",
    ) == "0|true"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_reload_sends_the_source_url_as_referer():
    """`Reload()` builds its Referer from `bookSourceUrl` (jsoup_shim.js:1892).

    NOTE: that is the `Reload` path only.  `java.ajax` / `java.connect` /
    `java.get` / `java.post` set no Referer at all, which is part of tier 2 in
    docs/js-http-request-side.md.
    """
    engine = YueduRuleEngine({"bookSourceUrl": "https://s.test"})

    value = engine._try_eval_js(
        "var __saved = __nhCurlRaw;"
        "__nhCurlRaw = function (url, method, body, headers) {"
        "  return 'REF:' + (headers && headers['Referer']);"
        "};"
        "var out;"
        "try { out = Reload('https://s.test/so'); }"
        "finally { __nhCurlRaw = __saved; }"
        "out;",
        "",
    )

    assert value == "REF:https://s.test"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_the_header_rule_is_exposed_raw_like_legados_source_field():
    """Tier 1 deliberately did NOT inject `header`; tier 2 now injects it **raw**.

    An evaluated header cannot be produced on the Python side: the rule may be a
    script, and evaluating it from `_build_js_context` (which runs before the JS
    evaluation) would recurse forever.  So the raw rule is injected and the shim
    evaluates it in-process -- which also means `source.header` returns the rule
    as written, exactly like Legado's source field.
    """
    rule = '@js:JSON.stringify({"User-Agent":"UA-1"})'
    engine = YueduRuleEngine({"bookSourceUrl": "https://s.test", "header": rule})

    assert engine._try_eval_js("source.header;", "") == rule


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_context_keys_do_not_leak_but_put_variables_survive():
    """Stale context must not survive, but `Put()` variables must.

    The Node subprocess is persistent, and the bootstrap used to *merge* the
    context, so a key present in one evaluation stayed readable in the next.
    `chapter` is the one that bites: `_build_js_context` only injects it when a
    chapter context exists, so a later chapter-less evaluation read back the
    previous chapter's title/url -- the same class of bug as codex-handoff
    section 9 ("上一本书的上下文串味").
    """
    engine = _engine()

    first = engine._try_eval_js(
        "globalThis.__nhSetVars({chapter: {title: '第一章'}});"
        "Put('keptVar', 'kept');"
        "String(source.get('chapter') ? 'chapter-seen' : 'no-chapter');",
        "",
    )
    assert first == "chapter-seen"

    # The second call's bootstrap injects the engine context, which has no
    # `chapter`: the stale object must be gone while Put()'s value survives.
    second = engine._try_eval_js(
        "String(source.get('chapter') ? 'chapter-seen' : 'no-chapter') + '|'"
        " + Get('keptVar');",
        "",
    )
    assert second == "no-chapter|kept"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_source_config_keys_do_not_leak_between_evaluations():
    """Same clearing rule for the source config the `source.*` getters read."""
    with_login = YueduRuleEngine({
        "bookSourceUrl": "https://s.test",
        "loginUrl": "https://s.test/login",
    })
    assert with_login._try_eval_js("source.loginUrl;", "") == "https://s.test/login"

    # A different source, same persistent subprocess: the old loginUrl must not
    # be visible through its getter.
    without_login = YueduRuleEngine({"bookSourceUrl": "https://s.test"})

    assert without_login._try_eval_js(
        "String(source.loginUrl) + '|' + source.bookSourceUrl;", ""
    ) == "|https://s.test"


# ---------------------------------------------------------------------------
# Referer default for JS-issued requests (docs/js-http-request-side.md §6.1).
#
# `Reload()` already built one from the source URL, but the `java.*` path never
# did: only `java.connect` went through `__nhSourceHeaders`, while `java.get` /
# `java.post` / `java.ajax` passed the caller's headers straight through.
#
# The default now lives in `__nhFinalHeaders`, called at the top of
# `__nhCurlRaw` -- the single funnel every request uses.  That placement is why
# the tests are split: the transformation is unit-tested directly, the wiring is
# proved by stubbing `__nhCurlRaw`, and one structural assertion closes the gap
# (a stub replaces `__nhCurlRaw`, so it cannot observe that call).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_java_requests_default_the_referer_to_the_source_url():
    engine = YueduRuleEngine({"bookSourceUrl": "https://s.test"})

    js = (
        "globalThis.__nhSetSourceConfig({bookSourceUrl: 'https://s.test'});"
        "JSON.stringify(__nhFinalHeaders({})) + '|' +"
        # A caller-supplied Referer must win.
        "JSON.stringify(__nhFinalHeaders({Referer: 'https://other/'})) + '|' +"
        # Other headers survive untouched.
        "JSON.stringify(__nhFinalHeaders({'User-Agent': 'UA'}));"
    )

    assert engine._try_eval_js(js, "") == (
        '{"Referer":"https://s.test"}'
        '|{"Referer":"https://other/"}'
        '|{"User-Agent":"UA","Referer":"https://s.test"}'
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_no_referer_is_added_when_the_source_url_is_empty():
    engine = _engine()

    assert engine._try_eval_js(
        "globalThis.__nhSetSourceConfig({bookSourceUrl: ''});"
        "JSON.stringify(__nhFinalHeaders({}));",
        "",
    ) == "{}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_every_java_http_entry_point_reaches_curl_raw():
    """The four entry points all funnel through `__nhCurlRaw`.

    Together with `__nhFinalHeaders` being applied there (asserted structurally
    below) this is what makes the Referer default reach every path -- the stub
    itself cannot observe the transformation, because it replaces the function
    that performs it.
    """
    engine = YueduRuleEngine({"bookSourceUrl": "https://s.test"})

    js = (
        "var __saved = __nhCurlRaw; var __seen = [];"
        "__nhCurlRaw = function (url, method, body, headers) {"
        "  __seen.push(method + ' ' + url); return 'STUB';"
        "};"
        "try {"
        "  java.get('https://s.test/a');"
        "  java.post('https://s.test/b', 'x=1');"
        "  java.ajax('https://s.test/c');"
        "  java.connect('https://s.test/d').getBody();"
        "} finally { __nhCurlRaw = __saved; }"
        "__seen.join('|');"
    )

    assert engine._try_eval_js(js, "") == (
        "GET https://s.test/a|POST https://s.test/b"
        "|GET https://s.test/c|GET https://s.test/d"
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_curl_raw_applies_the_default_headers():
    """Structural check closing the one gap a stub cannot cover.

    If this ever fails, the Referer default silently stops applying to the
    `java.*` path while every other test still passes.
    """
    engine = _engine()

    assert engine._try_eval_js(
        "String(__nhCurlRaw).indexOf('__nhFinalHeaders(headers)') >= 0;", ""
    ) is True


# ===========================================================================
# Previously "待裁决" deviations -- decided 2026-09-19.
#
# These four were ad-hoc differences from Legado that had no test coverage.  They
# were first pinned as *characterisation* tests (asserting current behaviour with
# a docstring explaining Legado's), and the decision was then taken to **align**
# them.  The assertions below are now the Legado values.
#
# `test_text_join_stays_newline_on_purpose` is the one deviation kept on purpose;
# its docstring records why, so nobody "fixes" it back to a space.
# ===========================================================================


def test_html_returns_outer_html_like_legado():
    """M-8（已对齐）：`@html` 返回**外层** HTML，含元素自身标签。

    此前返回 `decode_contents()`（内层），会让 `@html` 丢掉元素标签；
    Legado 的 `html` 分支是「删 script/style → `outerHtml()`」。
    """
    engine = _engine()

    assert engine._eval_css("<div class='x'>hi</div>", ".x@html") \
        == '<div class="x">hi</div>'


def test_html_still_strips_script_and_style():
    """M-8 的边界：外层化之后，删 script/style 的语义必须保留。"""
    engine = _engine()

    assert engine._eval_css(
        "<div class='x'>a<script>bad()</script><style>.y{}</style>b</div>",
        ".x@html",
    ) == '<div class="x">ab</div>'


def test_owntext_joins_with_spaces_like_legado():
    """M-9（已对齐）：`@ownText` 用**空格**连接，`@textNodes` 用换行。

    两者此前被实现成完全相同（都 `\\n`）——那是抄漏，不是设计：
    Legado 的 `ownText` 是 jsoup 的 `ownText()`（空白规范化后空格连接），
    `textNodes` 才是逐个 trim 后 `\\n` 连接。
    """
    engine = _engine()

    assert engine._eval_css("<div>a<br>b</div>", "div@ownText") == "a b"
    assert engine._eval_css("<div>a<br>b</div>", "div@textNodes") == "a\nb"


def test_duplicate_text_is_kept_like_legado():
    """M-10（已对齐）：去重只作用于**属性**分支，文本取值不去重。

    此前去重施加在结果集合上、对所有模式生效，于是两个内容相同的 `<span>`
    在 `span@text` 下只返回 1 条 —— **静默吞掉合法重复**（列表页同名项、
    正文重复段落），比多一条更难排查。Legado 只在 `getResultLast` 的
    `else`（属性）分支里去重。
    """
    engine = _engine()

    assert engine._eval_css(
        "<div><span>same</span><span>same</span></div>", "span@text"
    ) == "same\nsame"


def test_attribute_values_are_still_deduplicated():
    """M-10 的边界：属性分支的去重必须保留（那是 Legado 的真实行为）。"""
    engine = _engine()

    assert engine._eval_css(
        '<i data-x="v"></i><i data-x="v"></i>', "i@data-x"
    ) == "v"


def test_multivalued_attribute_returns_the_full_string():
    """D-13（已对齐）：多值属性返回完整字符串，而不是让整条字段为空。

    bs4 对 `class`/`rel` 这类属性返回 list；此前它直接流到调用方抛
    `TypeError` 被吞 → 字段为 `None`。Legado 的 `attr()` 返回原始串，
    所以 `class="body strikeout"` 应当是 `'body strikeout'`。
    """
    engine = _engine()

    assert engine._eval_css(
        '<div class="body strikeout">x</div>', ".body@class"
    ) == "body strikeout"


def test_text_join_stays_newline_on_purpose():
    """M-7：**有意保留的偏差** —— `@text` 用 `\\n` 连接，Legado 用空格。

    已决定不对齐：jsoup 的 `Element.text()` 会规范化空白并用空格连接，
    而 `\\n` 对正文/简介更有用（NovelHub 的阅读路径按行处理）。改回空格是倒退，
    所以保留现状并在代码里注明是有意偏差。

    ⚠️ 如果你把这条改成 `'a b'`，请先确认你不是在无意中改掉一个决定。
    """
    engine = _engine()

    assert engine._eval_css("<div>a<br>b</div>", "div@text") == "a\nb"


# ---------------------------------------------------------------------------
# The source's `header` rule now reaches JS-issued requests (tier 2).
#
# Legado seeds every `java.*` request from the book source's `header` field, which
# may be plain JSON or a script.  Only `java.connect` used to merge it, and only in
# its JSON form, so a source's declared UA never applied on the `java.*` path --
# some sites answer that with their mobile page (codex-handoff section 8).
#
# The rule is evaluated inside the shim (`__nhParseHeaders`), which is why the
# assertions call it directly: `__nhCurlRaw` applies it, and a stub of `__nhCurlRaw`
# replaces the function that carries it out.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_a_json_header_rule_reaches_js_requests():
    engine = YueduRuleEngine({"header": '{"User-Agent":"UA-JSON"}'})

    assert engine._try_eval_js(
        "__nhFinalHeaders({})['User-Agent'];", ""
    ) == "UA-JSON"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_a_script_header_rule_is_evaluated_in_process():
    """Both spellings Legado sources use: `@js:` and `<js>…</js>`."""
    engine = YueduRuleEngine({
        "header": '@js:JSON.stringify({"User-Agent":"UA-AT"})',
    })
    assert engine._try_eval_js("__nhFinalHeaders({})['User-Agent'];", "") == "UA-AT"

    tagged = YueduRuleEngine({
        "header": '<js>JSON.stringify({"User-Agent":"UA-TAG"})</js>',
    })
    assert tagged._try_eval_js("__nhFinalHeaders({})['User-Agent'];", "") == "UA-TAG"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_a_header_script_can_use_the_well_known_bindings():
    """`baseUrl` and friends are passed in explicitly.

    A module-level shim function cannot see the `var baseUrl` the bootstrap
    declares inside the user-code closure, so the script is run through
    `new Function(...)` with those names bound.
    """
    engine = YueduRuleEngine({
        "bookSourceUrl": "https://s.test",
        "header": '@js:JSON.stringify({"X-From": baseUrl})',
    })

    assert engine._try_eval_js(
        "__nhFinalHeaders({})['X-From'];", ""
    ) == "https://s.test"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_a_caller_supplied_header_wins_over_the_source_rule():
    engine = YueduRuleEngine({"header": '{"User-Agent":"UA-SRC","X-A":"1"}'})

    assert engine._try_eval_js(
        "JSON.stringify(__nhFinalHeaders({'User-Agent': 'UA-CALL'}));", ""
    ) == '{"User-Agent":"UA-CALL","X-A":"1"}'


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_a_broken_header_rule_is_ignored_rather_than_failing_the_request():
    engine = YueduRuleEngine({"header": '@js:throw new Error("boom")'})

    assert engine._try_eval_js(
        "JSON.stringify(__nhFinalHeaders({}));", ""
    ) == "{}"


# ---------------------------------------------------------------------------
# The user-imported Cookie now reaches JS-issued requests.
#
# It cannot come from the book source JSON -- Legado's schema has no cookie field
# (which is exactly why the AI diagnosis missed it, codex-handoff section 27) --
# so the plugin pushes it to the engine.  Nothing used to seed the shim's jar, so
# `java.getCookie()` always returned "" and a Cookie-authenticated source whose
# rules fetch through `java.*` could never get its content: the same symptom class
# as sections 8/19, where the fix only covered the Python path.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_the_imported_cookie_reaches_js_requests():
    engine = _engine()
    engine.set_configured_cookie("uid=1; cf_clearance=abc")

    assert engine._try_eval_js(
        "__nhFinalHeaders({})['Cookie'];", ""
    ) == "uid=1; cf_clearance=abc"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_get_cookie_returns_the_imported_cookie():
    """`java.getCookie()` used to be permanently empty."""
    engine = _engine()
    engine.set_configured_cookie("uid=1")

    assert engine._try_eval_js("java.getCookie();", "") == "uid=1"
    assert engine._try_eval_js("source.getCookie();", "") == "uid=1"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_a_script_set_cookie_is_appended_after_the_imported_one():
    """`setCookie` keeps its append semantics; the imported cookie is replaced.

    The jar is cleared first: it is deliberately **per source** (see
    `test_session_cookies_do_not_leak_between_sources`), and every test here uses
    the same `bookSourceUrl`, so it would otherwise persist between them.
    """
    engine = _engine()
    engine.set_configured_cookie("uid=1")

    assert engine._try_eval_js(
        "__nhCookieJar.length = 0;"
        "java.setCookie('session=9');java.getCookie();",
        "",
    ) == "uid=1; session=9"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_a_caller_supplied_cookie_wins_over_the_imported_one():
    engine = _engine()
    engine.set_configured_cookie("uid=1")

    assert engine._try_eval_js(
        "__nhCookieJar.length = 0;"
        "__nhFinalHeaders({Cookie: 'caller=2'})['Cookie'];",
        "",
    ) == "caller=2"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_no_cookie_header_without_an_imported_cookie():
    """Control: a source with no Cookie must not gain an empty header."""
    engine = _engine()

    assert engine._try_eval_js(
        "__nhCookieJar.length = 0;"
        "String(__nhFinalHeaders({})['Cookie']);",
        "",
    ) == "undefined"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_the_plugin_pushes_its_cookie_to_the_engine():
    """`set_cookie` must reach the engine, end to end."""
    from app.crawler.plugins.yuedu import YueduPlugin

    plugin = YueduPlugin({"bookSourceUrl": "https://s.test"})
    plugin.set_cookie("uid=1; cf_clearance=abc")

    assert plugin.engine is not None
    assert plugin.engine._configured_cookie == "uid=1; cf_clearance=abc"
    assert plugin.engine._try_eval_js(
        "__nhCookieJar.length = 0; java.getCookie();", ""
    ) == "uid=1; cf_clearance=abc"


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not available")
def test_session_cookies_do_not_leak_between_sources():
    """`java.setCookie` state is scoped to the source that wrote it.

    `JsRuntime` is a singleton, so every source shares one Node subprocess and one
    module-level jar.  Without clearing it on a source change, a session cookie
    written while syncing source A would be sent on source B's requests.
    """
    source_a = YueduRuleEngine({"bookSourceUrl": "https://a.test"})
    source_a._try_eval_js("java.setCookie('fromA=1');", "")
    assert source_a._try_eval_js("java.getCookie();", "") == "fromA=1"

    # A different source must not inherit it.
    source_b = YueduRuleEngine({"bookSourceUrl": "https://b.test"})

    assert source_b._try_eval_js("java.getCookie();", "") == ""

