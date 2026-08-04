from app.crawler.plugins.yuedu.js_runtime import try_eval_js_pattern
from app.crawler.plugins.yuedu.rule_engine import YueduRuleEngine


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
