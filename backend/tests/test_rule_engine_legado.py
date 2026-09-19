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
