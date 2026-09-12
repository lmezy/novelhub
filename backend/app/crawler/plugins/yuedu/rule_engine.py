"""YueDu (Legado) book source rule engine.

Faithful Python port of Legado's AnalyzeRule + AnalyzeUrl + RuleAnalyzer.

Supports the full YueDu rule DSL:
- Mode prefixes: @CSS:, @XPath:, @Json:, @@ (raw CSS), / (XPath auto-detect)
- Nested rules: {{expression}} templates
- Regex capture groups within ## replacement patterns
- @put:{...} variable storage
- Rule combinators: && (all), || (first match), %% (interleave)
- Content post-processing: replaceRegex, sourceRegex
- init rule for book info preprocessing
"""

import json
import logging
import re
from typing import Any, Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.crawler.plugins.yuedu.js_runtime import (
    JsRuntime,
    try_eval_js_pattern,
    try_eval_format_js,
)

logger = logging.getLogger(__name__)

# Legado source authors write attribute selectors without quotes because
# jsoup tolerates it (``a[href*=/post/][href$=.html]``).  soupsieve/BeautifulSoup
# raises "Malformed attribute selector" on those, and the rule engine only had
# a silent fallback, so such sources discovered zero books with no explanation.
_UNQUOTED_ATTR_VALUE_RE = re.compile(
    r"\[\s*([A-Za-z_][-\w:]*)\s*(\^=|\$=|\*=|~=|\|=|=)\s*([^\]\"'=][^\]]*?)\s*\]"
)

# Legado hands the rule engine the book/chapter object that the current
# request already carries; ``{{book.name}}`` / ``{{chapter.title}}`` read it.
_CONTEXT_OBJECT_NAMES = ("book", "chapter")


class RuleUnbalancedError(ValueError):
    """A rule's ``[]`` / ``()`` group is not balanced.

    Legado's ``RuleAnalyzer.splitRule`` throws ``"...后未平衡"`` in this case.
    This port ignored the failed balance scan and re-split from the same
    position, which recurred until Python raised ``RecursionError`` (or spun
    forever in the tail scanner).  Callers that cannot use the rule should
    catch this instead of losing the whole book.
    """


def normalize_css_selector(selector: str) -> str:
    """Quote unquoted attribute values so soupsieve accepts the selector."""
    text = str(selector or "")
    if "[" not in text:
        return text

    def _quote(match: re.Match) -> str:
        attr, op, value = match.group(1), match.group(2), match.group(3).strip()
        if not value or value[0] in "'\"":
            return match.group(0)
        if "'" in value:
            value = '"' + value.replace('"', '\\"') + '"'
        else:
            value = "'" + value + "'"
        return f"[{attr}{op}{value}]"

    return _UNQUOTED_ATTR_VALUE_RE.sub(_quote, text)


class _RuleAnalyzer:
    """Port of Legado's RuleAnalyzer -- splits combined rules while respecting
    balanced brackets, quotes, and nested structures."""

    def __init__(self, text: str, code_balance: bool = False):
        self._queue = text
        self._pos = 0
        self._start = 0
        self._start_x = 0
        self._rule: list[str] = []
        self._step = 0
        self.elements_type = ""
        self._chomp_balanced = (
            self._chomp_code_balanced if code_balance else self._chomp_rule_balanced
        )

    def reset_pos(self) -> None:
        self._pos = 0
        self._start_x = 0
        self._rule = []

    def split_rule(self, *separators: str) -> list[str]:
        self._rule = []
        self.elements_type = separators[0]
        self._split_head(separators)
        return self._rule

    def _split_head(self, separators: tuple[str, ...]) -> None:
        q = self._queue
        pos = self._pos
        start_x = self._start_x
        r: list[str] = []

        first = -1
        first_sep = ""
        for sep in separators:
            idx = q.find(sep, pos)
            if idx != -1 and (first == -1 or idx < first):
                first = idx
                first_sep = sep

        if first == -1:
            r.append(q[start_x:])
            self._rule = r
            return

        end = first
        st = self._find_any(pos, "[", "(")
        if st != -1 and st < end:
            self._pos = st
            next_ch = "]" if q[st] == "[" else ")"
            if not self._chomp_balanced(q[st], next_ch):
                # Legado aborts the split here.  Re-splitting from the same
                # position would recurse until the stack overflows.
                raise RuleUnbalancedError(f"{q[:st]}后未平衡")
            self._start = self._pos
            self._split_head(separators)
            return

        r.append(q[start_x:end])
        self._rule = r
        self.elements_type = first_sep
        self._pos = end + len(first_sep)
        self._step = len(first_sep)
        self._split_tail(separators)

    def _split_tail(self, separators: tuple[str, ...]) -> None:
        q = self._queue
        pos = self._pos
        sep = self.elements_type
        step = self._step
        r = self._rule

        while True:
            idx = q.find(sep, pos)
            if idx == -1:
                r.append(q[pos:])
                break
            end = idx
            st = self._find_any(pos, "[", "(")
            if st != -1 and st < end:
                self._pos = st
                next_ch = "]" if q[st] == "[" else ")"
                if not self._chomp_balanced(q[st], next_ch):
                    raise RuleUnbalancedError(f"{q[:st]}后未平衡")
                if self._pos > end:
                    self._start = self._pos
                    self._split_tail(separators)
                    return
                pos = self._pos
                continue
            r.append(q[pos:end])
            pos = end + step

    def _find_any(self, pos: int, *chars: str) -> int:
        q = self._queue
        while pos < len(q):
            if q[pos] in chars:
                return pos
            pos += 1
        return -1

    def _chomp_rule_balanced(self, open_ch: str, close_ch: str) -> bool:
        q = self._queue
        pos = self._pos
        depth = 0
        in_sq = False
        in_dq = False
        while pos < len(q):
            c = q[pos]
            pos += 1
            if c == "'" and not in_dq:
                in_sq = not in_sq
            elif c == '"' and not in_sq:
                in_dq = not in_dq
            if in_sq or in_dq:
                continue
            if c == '\\':
                pos += 1
                continue
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
            if depth <= 0:
                self._pos = pos
                return True
        return False

    def _chomp_code_balanced(self, open_ch: str, close_ch: str) -> bool:
        q = self._queue
        pos = self._pos
        depth = 0
        other_depth = 0
        in_sq = False
        in_dq = False
        while pos < len(q):
            c = q[pos]
            pos += 1
            if c != '\\':
                if c == "'" and not in_dq:
                    in_sq = not in_sq
                elif c == '"' and not in_sq:
                    in_dq = not in_dq
                if in_sq or in_dq:
                    continue
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                elif depth == 0:
                    if c == open_ch:
                        other_depth += 1
                    elif c == close_ch:
                        other_depth -= 1
            else:
                pos += 1
            if depth <= 0 and other_depth <= 0:
                self._pos = pos
                return True
        return False

    def inner_rule(
        self,
        start_str: str,
        end_str: str,
        fn: Callable[[str], str | None],
    ) -> str:
        """Replace start_str...end_str patterns by calling fn on the inner text."""
        q = self._queue
        pos = self._pos
        start_x = self._start_x
        st_buf: list[str] = []
        sl = len(start_str)
        el = len(end_str)

        while True:
            idx = q.find(start_str, pos)
            if idx == -1:
                break
            pos = idx + sl
            end_idx = q.find(end_str, pos)
            if end_idx == -1:
                break
            inner = q[pos:end_idx]
            result = fn(inner)
            if result is None:
                pos = end_idx + el
                continue
            st_buf.append(q[start_x:idx])
            st_buf.append(result)
            pos = end_idx + el
            start_x = pos
            self._start_x = start_x
            self._pos = pos

        if start_x == 0:
            return q

        st_buf.append(q[self._start_x:])
        return "".join(st_buf)

class YueduRuleEngine:
    """Evaluates YueDu book source rules against HTML or JSON responses."""

    # Legado splits on &&/||/%%.  Many exported sources also use single `|`
    # as a fallback separator, so accept it too.
    SEPARATORS = ("&&", "||", "|", "%%")
    JS_PATTERN = re.compile(
        r"<js>[\s\S]*?</js>|@js:[^\n]*",
        re.IGNORECASE,
    )

    def __init__(self, source_config: dict[str, Any]):
        self.config = source_config
        self.base_url: str = source_config.get("bookSourceUrl", "")
        self._variables: dict[str, str] = {}
        self._chapter_context: dict[str, Any] | None = None
        # Legado's ``java.getString``/``src`` resolve against the content that
        # is being parsed (a list item element, or the page), not against the
        # previous rule step; see ``_extract_list``.
        self._js_content: Any = None
        self._is_json_context: bool = False
        self._js_runtime: "JsRuntime | None" = None

    def _get_js_runtime(self) -> "JsRuntime":
        if self._js_runtime is None:
            self._js_runtime = JsRuntime.get_instance()
        return self._js_runtime

    def set_chapter_context(self, chapter: dict[str, Any] | None) -> None:
        """Provide the current chapter object so content JS can read
        fields like ``chapter.title`` / ``chapter.tag`` (Legado passes
        the chapter into content rule evaluation)."""
        self._chapter_context = dict(chapter) if chapter else None

    def _build_js_context(self, extra_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Assemble the variable context injected into JS evaluation."""
        base_url = self._variables.get("baseUrl", self.base_url)
        book_url = self._variables.get("bookUrl", self.base_url)
        context: dict[str, Any] = {
            "baseUrl": base_url,
            "bookUrl": book_url,
            "sourceUrl": self.base_url,
            "url": self._variables.get("url", book_url),
            "book": self._variables.get("book", {}),
        }
        if self._chapter_context:
            context["chapter"] = self._chapter_context
        if extra_context:
            context.update(extra_context)
        return context

    def set_book(self, book: dict[str, Any] | None) -> None:
        """Expose the parsed book object as the Legado ``book`` JS variable.

        TOC/content rules commonly reference ``book.name`` / ``book.author``
        (e.g. the SiS source builds a single-entry TOC with ``book.name``).
        """
        self._variables["book"] = book or {}

    # ---- Public API ----
    def build_search_url(self, keyword: str, page: int = 1) -> str:
        template = self.config.get("searchUrl", "")
        if not template:
            raise ValueError("No searchUrl defined in source config")
        url = self._substitute(template, key=keyword, page=str(page))
        # Legacy Legado placeholders still appear in many exported sources.
        url = url.replace("searchKey", keyword).replace("searchPage", str(page))
        return url

    def build_explore_url(self, page: int = 1) -> str:
        template = self.config.get("exploreUrl", "")
        if not template:
            return ""
        return self._substitute(template, page=str(page))

    def build_book_url(self, book_id: str) -> str:
        return urljoin(self.base_url, book_id)

    def is_book_detail_url(self, url: str) -> bool:
        """Check if a URL matches the bookUrlPattern (direct detail page)."""
        pattern = self.config.get("bookUrlPattern", "")
        if not pattern or not pattern.strip():
            return False
        try:
            return bool(re.search(pattern, url))
        except re.error:
            return False

    def parse_search_results(self, html_or_json: str) -> list[dict[str, Any]]:
        return self._extract_list(html_or_json, self.config.get("ruleSearch", {}))

    def parse_book_info(self, html_or_json: str) -> dict[str, Any]:
        return self._extract_book_info(html_or_json, self.config.get("ruleBookInfo", {}))

    def parse_explore_results(self, html_or_json: str) -> list[dict[str, Any]]:
        return self._extract_list(html_or_json, self.config.get("ruleExplore", {}))

    def parse_toc(self, html_or_json: str) -> list[dict[str, Any]]:
        return self._extract_list(html_or_json, self.config.get("ruleToc", {}))

    def parse_content(self, html_or_json: str) -> str:
        return self._extract_content(html_or_json, self.config.get("ruleContent", {}))

    def set_page_url(self, url: str) -> None:
        """Set the URL of the page currently being parsed.

        Legado rules commonly use ``@js:baseUrl`` for forum-style sources,
        where one post is both a book and its only chapter. ``baseUrl`` in
        that context is the current response URL, rather than the source
        homepage URL.
        """
        self._variables["baseUrl"] = url
        self._variables["bookUrl"] = url

    def get_next_content_urls(
        self,
        html_or_json: str,
        current_url: str | None = None,
    ) -> list[str]:
        rules = self.config.get("ruleContent", {})
        next_rule = rules.get("nextContentUrl", "")
        if not next_rule:
            return []
        return self._eval_rule_list(
            html_or_json,
            next_rule,
            is_url=True,
            base_url=current_url,
        )

    def get_next_content_url(
        self,
        html_or_json: str,
        current_url: str | None = None,
    ) -> str | None:
        urls = self.get_next_content_urls(html_or_json, current_url)
        return urls[0] if urls else None

    def get_next_toc_urls(
        self,
        html_or_json: str,
        current_url: str | None = None,
    ) -> list[str]:
        rules = self.config.get("ruleToc", {})
        next_rule = rules.get("nextTocUrl", "")
        if not next_rule:
            return []
        return self._eval_rule_list(
            html_or_json,
            next_rule,
            is_url=True,
            base_url=current_url,
        )

    def get_next_toc_url(
        self,
        html_or_json: str,
        current_url: str | None = None,
    ) -> str | None:
        urls = self.get_next_toc_urls(html_or_json, current_url)
        return urls[0] if urls else None

    # ---- Internal extraction ----

    def _extract_list(self, raw: str, rules: dict[str, Any]) -> list[dict[str, Any]]:
        list_rule = rules.get("bookList", "") or rules.get("chapterList", "")
        if not list_rule:
            return []

        items = self._eval_list_rule(raw, list_rule)
        if not items:
            return []

        field_names = [
            "name", "author", "bookUrl", "coverUrl", "intro", "kind",
            "lastChapter", "wordCount", "chapterName", "chapterUrl",
            "updateTime", "isVolume", "isVip", "isPay",
        ]

        results: list[dict[str, Any]] = []
        for item in items:
            entry: dict[str, Any] = {}
            # Legado evaluates ``java.getString(...)`` inside a field rule
            # against the item element (its ``AnalyzeRule`` content), so
            # sources such as 绅士漫画 read sibling nodes from the card while
            # the step-visible ``result`` is just a sub-string of it.
            self._js_content = item
            for field in field_names:
                rule = rules.get(field, "")
                if rule:
                    if field in ("bookUrl", "chapterUrl", "coverUrl"):
                        entry[field] = self._eval_rule_first(item, rule)
                    else:
                        entry[field] = self._eval_field(item, rule)

            fmt_js = rules.get("formatJs", "")
            if fmt_js and "chapterName" in entry and entry["chapterName"]:
                entry["chapterName"] = self._try_format_js(fmt_js, entry["chapterName"])

            results.append(entry)
        return results

    def _extract_book_info(self, raw: str, rules: dict[str, Any]) -> dict[str, Any]:
        self._js_content = raw
        init_rule = rules.get("init", "")
        if init_rule:
            narrowed = self._eval_field(raw, init_rule)
            if narrowed:
                raw = str(narrowed) if not isinstance(narrowed, str) else narrowed

        info: dict[str, Any] = {}
        for field in (
            "name", "author", "coverUrl", "intro", "kind",
            "tocUrl", "lastChapter", "wordCount", "status",
            "updateTime", "canReName", "downloadUrls",
        ):
            rule = rules.get(field, "")
            if rule:
                if field in ("coverUrl", "tocUrl"):
                    info[field] = self._eval_rule_first(raw, rule)
                else:
                    info[field] = self._eval_field(raw, rule)
        return info

    def _extract_content(self, raw: str, rules: dict[str, Any]) -> str:
        self._js_content = raw
        content_rule = rules.get("content", "")
        title_rule = rules.get("title", "")
        if title_rule:
            extracted = self._eval_rule_str(raw, title_rule)
            if extracted:
                self._variables["contentTitle"] = extracted

        if not content_rule:
            return raw

        content = self._eval_rule_str(raw, content_rule)
        if not content:
            return ""

        replace_regex = rules.get("replaceRegex", "")
        if replace_regex:
            content = self._apply_replace_regex(content, replace_regex)

        source_regex = rules.get("sourceRegex", "")
        if source_regex:
            try:
                m = re.search(source_regex, content)
                if m:
                    content = m.group(0)
            except re.error:
                pass

        return content

    # ---- Low-level evaluation ----

    def _eval_list_rule(self, raw: str, rule: str) -> list[Any]:
        if not rule:
            return []

        # ``chapterList``/``bookList`` JS steps read sibling nodes through
        # ``java.getString``; Legado resolves those against this element.
        self._js_content = raw
        reverse = False
        if rule.startswith("-"):
            reverse = True
            rule = rule[1:]
        elif rule.startswith("+"):
            rule = rule[1:]

        # Legado lets chapterList/bookList be an `<js>`/`@js:` expression that
        # returns an ARRAY of objects (e.g. SiS builds a single-entry TOC with
        # `[{name: book.name || "正文", url: baseUrl}]`).  Evaluate it here;
        # otherwise such sources yield zero chapters and only the generic
        # scanner (which may mis-parse single-post sites) gets a chance.
        js_code = self._js_code_from_rule(rule)
        if js_code is not None:
            try:
                js_result = self._try_eval_js(js_code, raw)
            except Exception:
                js_result = None
            if isinstance(js_result, list):
                return list(reversed(js_result)) if reverse else js_result
            if isinstance(js_result, dict):
                return [js_result]

        parsed = self._try_parse_json(raw)
        if parsed is not None:
            self._is_json_context = True
            try:
                result = self._jsonpath(parsed, rule)
                if isinstance(result, list):
                    return list(reversed(result)) if reverse else result
                return [result] if result is not None else []
            finally:
                self._is_json_context = False

        self._is_json_context = False
        # Legado also allows a JS step *after* an element rule, e.g. 绅士漫画's
        # ``chapterList``: ``//div[@class='gallary_wrap tb']/ul/li[1]@js:...``.
        # The script mostly prepares values with ``java.put`` and returns
        # ``result`` unchanged, so the selected elements survive.  Without this
        # the list was empty and the TOC fell back to scanning every anchor on
        # the page (which picked up the uploader's ``cdn-cgi/l/email-protection``
        # link as the only "chapter").
        js_pos = rule.find("@js:")
        if js_pos > 0:
            head = rule[:js_pos].strip()
            js_code = rule[js_pos + 4:].strip()
            elements = self._get_elements(raw, head) if head else []
            if not elements or not js_code:
                return elements
            source_html = "\n".join(str(element) for element in elements)
            try:
                js_result = self._try_eval_js(js_code, source_html)
            except Exception:
                js_result = None
            if isinstance(js_result, list):
                return list(reversed(js_result)) if reverse else js_result
            if isinstance(js_result, dict):
                return [js_result]
            if js_result is None or str(js_result).strip() == source_html.strip():
                # The script only populated variables (java.put) and echoed its
                # input; keep the elements it ran against.
                return list(reversed(elements)) if reverse else elements
            return list(reversed([js_result])) if reverse else [js_result]

        return self._get_elements(raw, rule)

    def _js_code_from_rule(self, rule: str) -> str | None:
        """Extract the JavaScript body from a rule that is a JS expression."""
        text = (rule or "").strip()
        if text.startswith("@js:"):
            return text[4:].strip()
        if text.startswith("<js>") and text.rstrip().endswith("</js>"):
            return text[4:-5].strip()
        if text.startswith("<js"):
            return text[4:].strip()
        return None

    def _get_elements(self, raw: Any, rule: str) -> list[Tag]:
        """Port of Legado AnalyzeByJSoup getElements with @ chains and indexes."""
        if not rule:
            return []
        reverse = False
        if rule.startswith("-"):
            reverse = True
            rule = rule[1:]
        elif rule.startswith("+"):
            rule = rule[1:]
        if rule.lower().startswith("@css:"):
            rule = rule[5:].strip()

        root = self._ensure_soup(raw)
        if root is None:
            return []

        elements: list[Tag] = [root]
        for segment in self._split_element_steps(rule):
            elements = self._select_elements_chain(elements, segment)
        if reverse:
            elements.reverse()
        return elements

    @staticmethod
    def _split_element_steps(rule: str) -> list[str]:
        """Split a rule chain on ``@`` the way Legado does.

        ``@`` separates rule steps, but XPath-ish attribute selectors contain
        one as well: ``//div[@class='gallary_wrap']/ul/li``.  Legado's
        ``RuleAnalyzer.splitRule("@")`` skips separators inside balanced
        ``[]``/``()`` pairs (and quotes).  A naive ``str.split("@")`` tore that
        rule into ``//div`` + ``class='gallary_wrap']/ul/li``, so every source
        whose ``bookList`` / ``chapterList`` used ``[@class=...]`` parsed to
        **zero** books or chapters -- e.g. 绅士漫画 (wn09.shop), whose catalog
        rule is exactly ``//div[@class='gallary_wrap']/ul/li``.
        """
        try:
            steps = _RuleAnalyzer(rule).split_rule("@")
        except Exception:
            steps = rule.split("@")
        return [step.strip() for step in steps if step.strip()]

    def _select_elements_chain(
        self,
        elements: list[Tag],
        rule: str,
    ) -> list[Tag]:
        if rule.startswith("@@"):
            rule = rule[2:]
            selected: list[Tag] = []
            for el in elements:
                selected.extend(el.select(normalize_css_selector(rule)))
            return selected

        before, split, indexes = self._parse_legado_index(rule)
        selected: list[Tag] = []
        for el in elements:
            if before:
                base = self._legado_before_elements(el, before)
            else:
                base = [c for c in getattr(el, "children", []) if isinstance(c, Tag)]
            if indexes:
                selected.extend(self._apply_legado_indexes(base, indexes, split))
            else:
                selected.extend(base)
        return selected

    @staticmethod
    def _parse_legado_index(rule: str) -> tuple[str, str, list[Any]]:
        """Split a Legado element rule into selector, index mode, and indexes."""
        rule = rule.strip()
        if rule.endswith("]"):
            start = rule.rfind("[")
            if start != -1:
                inner = rule[start + 1:-1].strip()
                # Rules ending in `]` are not always Legado indexes.  A CSS
                # attribute selector such as ``a[href*='next']``,
                # ``meta[property='og:title']`` or ``a[href^='/author/']``
                # also ends in ``]``.  Legado indexes are numeric only
                # (``[0]``, ``[1:3]``, ``[0,!1]``, ``[1,2,3]``); if the bracket
                # body contains letters/operators it is a CSS selector, so hand
                # the whole rule to the CSS engine instead of mis-parsing it as
                # an index (which would select every child element).
                if not re.fullmatch(r"[\d\s,:!-]*", inner or ""):
                    return rule, " ", []
                before = rule[:start].rstrip()
                split = "!"
                if inner.startswith("!"):
                    inner = inner[1:]
                else:
                    split = "."
                indexes: list[Any] = []
                for part in inner.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if ":" in part:
                        segs = part.split(":")

                        def _int(v: str) -> int | None:
                            try:
                                return int(v) if v.strip() else None
                            except ValueError:
                                return None

                        start_i = _int(segs[0]) if len(segs) > 0 else None
                        end_i = _int(segs[1]) if len(segs) > 1 else None
                        step = _int(segs[2]) if len(segs) > 2 else None
                        indexes.append((start_i, end_i, step or 1))
                    else:
                        try:
                            indexes.append(int(part))
                        except ValueError:
                            pass
                if not indexes:
                    split = " "
                return before, split, indexes

        m = re.match(r"^(.*?)([.!:])(-?\d+)$", rule)
        if m:
            return m.group(1).rstrip(), m.group(2), [int(m.group(3))]
        return rule, " ", []

    @staticmethod
    def _legado_before_elements(el: Tag, before: str) -> list[Tag]:
        before = before.strip()
        if before in ("children", "children."):
            return [c for c in getattr(el, "children", []) if isinstance(c, Tag)]
        if re.search(r"[\u4e00-\u9fff]", before) and not re.match(
            r"^(?:tag|class|id|text|children)\.",
            before,
        ):
            return YueduRuleEngine._text_matching_elements(el, before)
        parts = before.split(".", 1)
        if len(parts) == 2:
            kind, value = parts
            if kind == "class":
                return el.find_all(class_=value)
            if kind == "tag":
                return el.find_all(value)
            if kind == "id":
                return el.find_all(id=value)
            if kind == "text":
                return [
                    node.parent for node in el.find_all(string=True)
                    if node.parent is not None and value in str(node)
                ]
        # Legado book sources are free to write ``bookList`` / ``chapterList``
        # in XPath syntax ("//div[@class='gallary_wrap']/ul/li").  soupsieve
        # rejects a leading "/", and the exception below used to swallow the
        # rule and return "no elements" -- so such sources silently discovered
        # 0 books / 0 chapters.  Translate the subset of XPath that appears in
        # list rules into CSS and let soupsieve do the selection.
        if before.startswith(("/", ".")) or "//" in before:
            css = YueduRuleEngine._xpath_list_rule_to_css(before)
            if css:
                try:
                    return el.select(css)
                except Exception:
                    pass
        try:
            return el.select(normalize_css_selector(before))
        except Exception:
            # Legacy text rules such as `作者：@text` use a plain label as the
            # selector. Jsoup/Legado treat that as a text search, so fall back
            # to elements whose own text contains the label.
            return YueduRuleEngine._text_matching_elements(el, before)

    @staticmethod
    def _xpath_list_rule_to_css(rule: str) -> str | None:
        """Translate an XPath-ish element list rule into a CSS selector.

        Only the shape that book sources actually use in ``bookList`` /
        ``chapterList`` is supported -- child/descendant steps, attribute
        predicates (``[@class='x']``, ``[@href]``) and numeric position
        (``li[1]`` -> ``li:nth-of-type(1)``).  Anything else (axes, ``text()``,
        ``contains()``, nested predicates) returns ``None`` so callers keep
        their previous behaviour instead of mis-selecting elements.
        """
        text = (rule or "").strip()
        if text.lower().startswith("@xpath:"):
            text = text[7:].strip()
        if not text.startswith(("/", ".")):
            return None
        # Drop the leading "/", "//", "./" or ".//" prefix.
        text = re.sub(r"^\.?//?", "", text, count=1).strip()
        if not text:
            return None

        steps: list[tuple[str, str]] = []
        buffer = ""
        combinator = " "
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            if char == "[":
                end = text.find("]", index)
                if end == -1:
                    return None
                buffer += text[index:end + 1]
                index = end + 1
                continue
            if char == "/":
                end = index
                while end < length and text[end] == "/":
                    end += 1
                if not buffer.strip():
                    return None
                steps.append((combinator, buffer.strip()))
                buffer = ""
                combinator = " " if end - index > 1 else " > "
                index = end
                continue
            buffer += char
            index += 1
        if buffer.strip():
            steps.append((combinator, buffer.strip()))
        if not steps:
            return None

        css_parts: list[str] = []
        for position, (comb, step) in enumerate(steps):
            converted = YueduRuleEngine._xpath_step_to_css(step)
            if converted is None:
                return None
            css_parts.append(("" if position == 0 else comb) + converted)
        return "".join(css_parts)

    @staticmethod
    def _xpath_step_to_css(step: str) -> str | None:
        match = re.fullmatch(
            r"(?P<tag>[A-Za-z][\w-]*|\*)?(?P<preds>(?:\[[^\]]*\])*)",
            step.strip(),
        )
        if not match:
            return None
        css = match.group("tag") or "*"
        for predicate in re.findall(r"\[([^\]]*)\]", match.group("preds") or ""):
            predicate = predicate.strip()
            if predicate.startswith("@"):
                body = predicate[1:].strip()
                attr = re.fullmatch(
                    r"(?P<attr>[\w:.-]+)(?P<rest>\s*(?:[!^$*~|]?=)\s*\S.*)?",
                    body,
                )
                if not attr:
                    return None
                css += f"[{body}]"
                continue
            if re.fullmatch(r"\d+", predicate):
                css += f":nth-of-type({predicate})"
                continue
            return None
        return css

    @staticmethod
    def _text_matching_elements(el: Tag, needle: str) -> list[Tag]:
        """Find elements containing a plain-text label like `作者：`."""
        needle = needle.strip()
        if not needle:
            return []

        matches: list[Tag] = []
        for descendant in el.find_all(True):
            own_text = "".join(
                str(child)
                for child in descendant.children
                if isinstance(child, str)
            )
            if needle in own_text:
                matches.append(descendant)

        if matches:
            return matches

        for descendant in el.find_all(True):
            if needle in descendant.get_text(" ", strip=False):
                matches.append(descendant)
        return matches

    @staticmethod
    def _apply_legado_indexes(
        elements: list[Tag],
        indexes: list[Any],
        split: str,
    ) -> list[Tag]:
        length = len(elements)
        chosen: set[int] = set()
        ordered: list[int] = []

        for index in indexes:
            if isinstance(index, int):
                i = index if index >= 0 else index + length
                if 0 <= i < length and i not in chosen:
                    chosen.add(i)
                    ordered.append(i)
            elif isinstance(index, tuple):
                start, end, step = index
                start = 0 if start is None else (start if start >= 0 else start + length)
                end = length - 1 if end is None else (end if end >= 0 else end + length)
                step = step or 1
                if step < 0:
                    step = -step
                start = max(0, min(length - 1, start))
                end = max(0, min(length - 1, end))
                rng = (
                    range(start, end + 1, step)
                    if start <= end
                    else range(start, end - 1, -step)
                )
                for i in rng:
                    if 0 <= i < length and i not in chosen:
                        chosen.add(i)
                        ordered.append(i)

        if split == "!":
            return [elements[i] for i in range(length) if i not in chosen]
        if split == ".":
            return [elements[i] for i in ordered]
        return list(elements)

    def _eval_rule_str(
        self,
        raw: str | Any,
        rule: str,
        is_url: bool = False,
        base_url: str | None = None,
    ) -> str:
        result = self._eval_field(raw, rule)
        if result is None:
            return ""
        if isinstance(result, list):
            result = "\n".join(str(r) for r in result)
        result = str(result)
        if is_url and result.strip():
            return urljoin(base_url or self.base_url, result)
        return result

    def _eval_rule_first(self, raw: Any, rule: str) -> str:
        """Return only the first matched value for scalar URL fields.

        Legado resolves URL fields with getString0/getString(isUrl=true),
        which takes the first match. Joining every img@src into one string
        makes cover/toc URLs unusable and can exceed DB column limits.
        """
        result = self._eval_field(raw, rule)
        if result is None:
            return ""
        if isinstance(result, list):
            values = [str(v).strip() for v in result if str(v).strip()]
            return values[0] if values else ""
        return next(
            (line.strip() for line in str(result).splitlines() if line.strip()),
            "",
        )

    def _eval_rule_list(
        self,
        raw: str | Any,
        rule: str,
        is_url: bool = False,
        base_url: str | None = None,
    ) -> list[str]:
        result = self._eval_field(raw, rule)
        if result is None:
            return []
        if isinstance(result, list):
            values = [str(v).strip() for v in result if str(v).strip()]
        else:
            values = [line.strip() for line in str(result).splitlines() if line.strip()]
        if is_url:
            return [urljoin(base_url or self.base_url, v) for v in values]
        return values

    def _eval_field(self, raw: Any, rule: str) -> Any:
        if not rule:
            return None
        rule, put_map = self._split_put(rule)
        for k, v in put_map.items():
            val = self._eval_field(raw, v)
            self._variables[k] = str(val) if val is not None else ""
        rule = self._substitute_inner_rules(rule, raw)
        if not rule:
            return None
        # Split rule into chain of fragments and evaluate in sequence
        fragments = self._split_rule_chain(rule)
        current = raw
        for frag in fragments:
            frag = frag.strip()
            if not frag:
                continue
            current = self._eval_with_mode(current, frag)
            if current is None:
                break
        return current

    def _eval_with_mode(self, raw: Any, rule: str) -> Any:
        if rule.startswith("@js:"):
            return self._try_eval_js(rule[4:].strip(), raw)
        if rule.startswith("@put:"):
            return None
        if rule.lower().startswith("@xpath:"):
            return self._eval_xpath(raw, rule[7:])
        if rule.lower().startswith("@json:"):
            return self._eval_json(raw, rule[6:])
        if rule.lower().startswith("@css:"):
            return self._eval_css(raw, rule[5:])
        if rule.startswith("@@"):
            return self._eval_css(raw, rule[2:])
        if rule.startswith("/"):
            return self._eval_xpath(raw, rule)
        if rule.startswith("$.") or rule.startswith("$["):
            parsed = self._try_parse_json(raw if isinstance(raw, str) else str(raw))
            if parsed is not None:
                raw = parsed
            return self._jsonpath(raw, rule)
        if self._is_json_context or isinstance(raw, (dict, list)):
            if not isinstance(raw, (dict, list)):
                parsed = self._try_parse_json(raw if isinstance(raw, str) else str(raw))
                if parsed is not None:
                    raw = parsed
            return self._jsonpath(raw, rule)
        return self._eval_css(raw, rule)

    def _split_rule_chain(self, rule: str) -> list[str]:
        """Split a rule string into a chain of mode-specific fragments.

        Ported from Legado AnalyzeRule.splitSourceRule.
        Splits by <js>...</js> and @js: patterns so each fragment
        can be evaluated in sequence with output feeding the next.
        """
        # ``@js:`` is allowed to contain multi-line JavaScript. Treating it
        # as a single fragment preserves simple expression rules such as
        # ``@js:\n\"https://site/book/{{$.id}}\"``.
        js_start = rule.find("@js:")
        if js_start >= 0:
            before_js = rule[:js_start].strip()
            js_fragment = rule[js_start:].strip()
            return ([before_js] if before_js else []) + [js_fragment]

        fragments: list[str] = []
        js_iter = self.JS_PATTERN.finditer(rule)
        start = 0
        for m in js_iter:
            if m.start() > start:
                non_js = rule[start:m.start()].strip()
                if non_js:
                    fragments.append(non_js)
            js_code = m.group(0)
            if js_code.startswith("<js>") and js_code.endswith("</js>"):
                fragments.append("@js:" + js_code[4:-5].strip())
            elif js_code.startswith("<js"):
                fragments.append("@js:" + js_code[4:].strip())
            else:
                fragments.append(js_code)
            start = m.end()
        if start < len(rule):
            remaining = rule[start:].strip()
            if remaining:
                fragments.append(remaining)
        return fragments if fragments else [rule]

    def _eval_css(self, raw: Any, rule: str) -> Any:
        soup = self._ensure_soup(raw)
        if soup is None:
            return None
        analyzer = _RuleAnalyzer(rule)
        separators = ("&&", "||", "%%") if "##" in rule else self.SEPARATORS
        try:
            rules = analyzer.split_rule(*separators)
            elem_type = analyzer.elements_type
        except RuleUnbalancedError:
            # A rule whose ``[]``/``()`` group never closes cannot be split
            # the way Legado would (it throws there).  Keep the field usable
            # by evaluating the whole text as a single selector instead of
            # failing the book: nothing matches, so the caller falls back to
            # its own heuristics.
            logger.debug(
                "Unbalanced yuedu rule, evaluating as a single fragment: {}",
                rule[:160],
            )
            rules = [rule]
            elem_type = ""
        results: list[list[str]] = []
        for rl in rules:
            rl = rl.strip()
            if not rl:
                continue
            if rl.startswith(("http://", "https://")):
                temp = [rl]
            else:
                try:
                    temp = self._eval_css_single(soup, rl)
                except Exception:
                    temp = None
            if temp:
                results.append(temp)
                if elem_type in ("||", "|"):
                    break
        if not results:
            return None
        if elem_type == "%%":
            interleaved: list[str] = []
            max_len = max(len(r) for r in results)
            for i in range(max_len):
                for r in results:
                    if i < len(r):
                        interleaved.append(r[i])
            return "\n".join(interleaved) if interleaved else None
        all_r: list[str] = []
        for r in results:
            all_r.extend(r)
        return "\n".join(all_r) if all_r else None

    def _eval_css_single(self, soup: BeautifulSoup | Tag, rule: str) -> list[str] | None:
        if not rule:
            return [soup.get_text("\n", strip=True)]
        # Legacy text/regex rules like `作者：(.*?)\s` have no @attr suffix.
        # Try them as regexes against the visible text before treating the
        # string as a CSS selector.
        if "@" not in rule and re.search(r"\([^()]*[.+*?][^()]*\)", rule):
            try:
                match = re.search(rule, soup.get_text(" ", strip=True))
                if match:
                    return [match.group(1) if match.lastindex else match.group(0)]
            except re.error:
                pass
        parts = rule.split("@")
        elements: list[Tag] = [soup]
        attr_suffix = "text"
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            if i == len(parts) - 1:
                attr_suffix = part
            else:
                elements = self._select_elements_chain(elements, part)
        if not elements:
            return None
        raw_attr = attr_suffix
        attr_suffix = attr_suffix.split("##")[0].strip()
        results: list[str] = []
        for el in elements:
            val = self._extract_css_value(el, attr_suffix)
            if "##" in raw_attr:
                val = self._apply_replace_regex(val, raw_attr)
            if val:
                results.append(val)
        if not results:
            return None
        seen: set[str] = set()
        unique: list[str] = []
        for val in results:
            if val not in seen:
                seen.add(val)
                unique.append(val)
        return unique

    @staticmethod
    def _split_css_attr(rule: str) -> tuple[str, str]:
        depth = 0
        for i, ch in enumerate(rule):
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "@" and depth == 0:
                return rule[:i], rule[i + 1:]
        return rule, "text"

    @staticmethod
    def _extract_css_value(el: Tag, attr: str) -> str:
        attr = attr.strip().lower()
        if not attr or attr == "text":
            return el.get_text("\n", strip=True)
        if attr == "textnodes":
            texts = [t.strip() for t in el.find_all(string=True, recursive=False) if t.strip()]
            return "\n".join(texts)
        if attr == "owntext":
            texts = [t.strip() for t in el.find_all(string=True, recursive=False) if t.strip()]
            return "\n".join(texts)
        if attr == "html":
            for tag in el.find_all(["script", "style"]):
                tag.decompose()
            return el.decode_contents()
        if attr == "all":
            return str(el)
        val = el.get(attr)
        return val if val else ""

    def _eval_json(self, raw: Any, rule: str) -> Any:
        if isinstance(raw, str):
            parsed = self._try_parse_json(raw)
            if parsed is not None:
                raw = parsed
        if not isinstance(raw, (dict, list)):
            return None
        analyzer = _RuleAnalyzer(rule, code_balance=True)
        separators = ("&&", "||", "%%") if "##" in rule else self.SEPARATORS
        try:
            rules = analyzer.split_rule(*separators)
            elem_type = analyzer.elements_type
        except RuleUnbalancedError:
            # Same fallback as ``_eval_css``: an unbalanced rule must not take
            # the whole book down with it.
            logger.debug(
                "Unbalanced yuedu JSON rule, treating as one fragment: {}",
                rule[:160],
            )
            rules = [rule]
            elem_type = ""
        results: list[str] = []
        for rl in rules:
            rl = rl.strip()
            if not rl:
                continue
            val = self._jsonpath(raw, rl)
            resolved = str(val) if val is not None else ""
            if resolved:
                results.append(resolved)
                if elem_type in ("||", "|"):
                    break
        if not results:
            return None
        return "\n".join(results)

    def _eval_xpath(self, raw: Any, rule: str) -> Any:
        soup = self._ensure_soup(raw)
        if soup is None:
            return None
        # A Legado field rule may append a regex transform to an XPath selector
        # (``//div[@class='x']/img/@src##^//##https://``).  Feeding the whole
        # string to lxml raised XPathEvalError, and the CSS fallback then raised
        # SelectorSyntaxError out of ``_eval_xpath`` -- one such cover rule
        # aborted the whole book sync.  Split the transform off, evaluate the
        # selector, then post-process each value like Legado does.
        selector, transform = self._split_xpath_transform(rule)
        try:
            from lxml import etree
            tree = etree.HTML(str(raw))
            elements = tree.xpath(selector)
            if not elements:
                return None
            texts = []
            for el in elements:
                if isinstance(el, str):
                    texts.append(el.strip())
                elif hasattr(el, "text_content"):
                    texts.append(el.text_content().strip())
                else:
                    texts.append((el.text or "").strip())
            if transform:
                texts = [self._apply_replace_regex(t, transform) for t in texts]
            return "\n".join(t for t in texts if t) if texts else None
        except Exception:
            try:
                results = soup.select(normalize_css_selector(selector))
            except Exception:
                return None
            if not results:
                return None
            texts = [r.get_text("\n", strip=True) for r in results]
            if transform:
                texts = [self._apply_replace_regex(t, transform) for t in texts]
            return "\n".join(t for t in texts if t) or None

    @staticmethod
    def _split_xpath_transform(rule: str) -> tuple[str, str]:
        """Split ``selector##pattern##replacement`` into its two parts."""
        text = str(rule or "")
        if "##" not in text:
            return text, ""
        selector, _, transform = text.partition("##")
        return selector.strip(), "##" + transform

    def _apply_replace_regex(self, text: str, rule: Any) -> str:
        if isinstance(rule, list):
            for item in rule:
                text = self._apply_replace_regex(text, item)
            return text
        if not isinstance(rule, str):
            return text
        parts = rule.split("##")
        if len(parts) >= 3:
            pattern = parts[1]
            replacement = parts[2]
            replace_first = len(parts) > 3
            try:
                if replace_first:
                    return re.sub(pattern, replacement, text, count=1)
                return re.sub(pattern, replacement, text)
            except re.error:
                return text
        if len(parts) == 2:
            try:
                return re.sub(parts[1], "", text)
            except re.error:
                return text
        if rule.strip():
            try:
                return re.sub(rule, "", text)
            except re.error:
                return text
        return text

    def _try_eval_js(self, js_code: str, raw: Any, extra_context: dict | None = None) -> Any:
        """Evaluate JavaScript against raw input.
        
        Uses real Node.js JS runtime when available; falls back to
        pattern-based evaluation for simple operations.
        """
        code = js_code.strip()
        if not code:
            return raw

        # Many exports use @js as a URL template expression, not a statement
        # block. Node's function wrapper returns undefined for a bare string
        # expression, while Legado uses the expression value.
        try:
            expression = json.loads(code)
        except (TypeError, ValueError, json.JSONDecodeError):
            expression = None
        if isinstance(expression, str):
            return expression

        # ``baseUrl`` is a Legado-provided page-context variable.
        compact = code.rstrip(";").strip()
        if compact in {"baseUrl", "(baseUrl)", "return baseUrl"}:
            return self._variables.get("baseUrl", self.base_url)

        # Android-only cover rules often fetch an encrypted image with OkHttp
        # and decrypt it through ``Packages.javax.crypto``. NovelHub performs
        # the request itself and handles the declarative AES-CBC portion in
        # ``fetch_cover``; keep the preceding URL-rule result here.
        if (
            isinstance(raw, str)
            and "AES/CBC/PKCS5Padding" in code
            and "Packages.javax.crypto" in code
        ):
            return raw

        # Fast path: try pattern-based evaluation first.  Only simple
        # expressions qualify; full scripts (statements, variables,
        # org.jsoup / java calls) go straight to the JS runtime so a
        # pattern like ``String(result)`` cannot swallow the whole rule.
        is_simple = not re.search(
            r"[;{}]|\bvar\b|\bfunction\b|\bif\b|\bfor\b|\bwhile\b|org\.|java\.|Packages\.|jsoup",
            code,
        )
        pattern_result = None
        if is_simple:
            pattern_result = try_eval_js_pattern(code, raw)
        if pattern_result is not None:
            return pattern_result

        # If pattern failed, try the real JS runtime for complex expressions
        try:
            runtime = self._get_js_runtime()
            result = runtime.eval_js_sync(
                code,
                raw,
                context=self._build_js_context(extra_context),
                content=self._js_content,
            )
            if result is not None:
                return result
        except Exception:
            pass

        # Last resort: return input as-is if it's a string
        return None if not isinstance(raw, str) else raw
    
    def _eval_js_sync(self, js_code: str, input_value: Any = None) -> Any:
        """Synchronous wrapper for real JS evaluation."""
        try:
            runtime = self._get_js_runtime()
            return runtime.eval_js_sync(js_code, input_value)
        except Exception:
            return None
    
    def _try_format_js(self, js_code: str, value: str) -> str:
        """Evaluate formatJs for TOC entry formatting."""
        code = js_code.strip()
        if not code:
            return value

        # Fast path: pattern-based
        pattern_result = try_eval_format_js(code, value)
        if pattern_result != value:
            return pattern_result

        # Real JS runtime
        try:
            runtime = self._get_js_runtime()
            result = runtime.eval_js_sync(
                code,
                value,
                context={
                    "baseUrl": self._variables.get("baseUrl", self.base_url),
                    "bookUrl": self._variables.get("bookUrl", self.base_url),
                },
            )
            if isinstance(result, str):
                return result
        except Exception:
            pass

        return value

    def eval_login_check_js(self, js_code: str, html: str) -> bool:
        """Evaluate loginCheckJs to determine login status."""
        code = js_code.strip()
        if not code:
            return True

        # For login check, use real JS runtime
        try:
            runtime = self._get_js_runtime()
            result = runtime.eval_js_sync(code, html)
            if result is not None:
                result_str = str(result).strip().lower()
                if any(w in result_str for w in ("login", "logout", "redirect", "false", "0", "null", "undefined")):
                    return False
                return True
        except Exception:
            pass

        # Fallback to pattern evaluation
        result = try_eval_js_pattern(code, html)
        if result is not None:
            result_str = str(result).strip().lower()
            if any(w in result_str for w in ("login", "logout", "redirect", "false", "0", "null", "undefined")):
                return False
            return True
        return True


    def _split_put(self, rule: str) -> tuple[str, dict[str, str]]:
        put_map: dict[str, str] = {}
        def _replacer(m: re.Match) -> str:
            json_str = m.group(1)
            try:
                d = json.loads(json_str)
                if isinstance(d, dict):
                    put_map.update({str(k): str(v) for k, v in d.items()})
                return ""
            except json.JSONDecodeError:
                try:
                    cleaned = re.sub(r"(\w+):", r'"\1":', json_str)
                    d = json.loads(cleaned)
                    if isinstance(d, dict):
                        put_map.update({str(k): str(v) for k, v in d.items()})
                except (json.JSONDecodeError, ValueError):
                    pass
                return ""
        cleaned = re.sub(r"@put:\s*(\{[^}]+\})", _replacer, rule, flags=re.IGNORECASE)
        return cleaned.strip(), put_map

    def _substitute_inner_rules(self, rule: str, raw: Any) -> str:
        if "{{" not in rule or "}}" not in rule:
            return rule

        def _resolve_template(inner: str) -> str | None:
            inner = inner.strip()
            if inner.startswith("@") or inner.startswith("$.") or inner.startswith("//"):
                result = self._eval_field(raw, inner)
                return str(result) if result is not None else ""
            variable = self._lookup_variable(inner)
            if variable is not None:
                return variable
            js_result = self._try_eval_js_value(inner, raw)
            if js_result is not None:
                return str(js_result)
            return ""
        analyzer = _RuleAnalyzer(rule)
        return analyzer.inner_rule("{{", "}}", _resolve_template)

    def _lookup_variable(self, path: str) -> str | None:
        """Resolve ``book.name`` / ``chapter.title`` style template references.

        Legado's inner rules read the *known* book/chapter object, never the
        page that is being parsed.  ``None`` means "not a variable reference",
        which lets the caller try JS; a resolved-but-empty path returns ``""``
        so the field stays empty instead of falling back to the whole page.
        """
        name, _, rest = path.partition(".")
        if name in self._variables:
            current: Any = self._variables[name]
        elif name == "chapter" and self._chapter_context is not None:
            current = self._chapter_context
        elif rest and name in _CONTEXT_OBJECT_NAMES:
            # ``book.name`` before any book is known (``fetch_book`` only has
            # the URL at that point): stay empty.
            return ""
        else:
            return None
        if not rest:
            return None if isinstance(current, (dict, list)) else str(current)
        for part in rest.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return ""
        return "" if current is None else str(current)

    def _try_eval_js_value(self, js_code: str, raw: Any) -> Any:
        """Evaluate JS for a ``{{...}}`` template, never echoing the input.

        ``_try_eval_js`` returns its input as a last resort, which is right at
        the end of a rule chain but wrong inside a template: ``{{book.name}}``
        without a book context resolved to the whole HTML page, and that page
        was then evaluated as a CSS selector (in 绅士漫画's case, until the
        stack overflowed and the book failed to sync).
        """
        code = js_code.strip()
        if not code:
            return None
        try:
            expression = json.loads(code)
        except (TypeError, ValueError, json.JSONDecodeError):
            expression = None
        if isinstance(expression, str):
            return expression
        pattern_result = try_eval_js_pattern(code, raw)
        if pattern_result is not None:
            return pattern_result
        try:
            runtime = self._get_js_runtime()
            result = runtime.eval_js_sync(
                code,
                raw,
                context=self._build_js_context(),
                content=self._js_content,
            )
        except Exception:
            return None
        # The runtime returns its input when the expression evaluates to
        # ``undefined`` (correct for a rule chain, wrong for a template).
        if isinstance(raw, str) and result == raw:
            return None
        return result

    def get_variable(self, key: str) -> str:
        return self._variables.get(key, "")

    def put_variable(self, key: str, value: str) -> None:
        self._variables[key] = value

    @staticmethod
    def _ensure_soup(raw: Any) -> BeautifulSoup | Tag | None:
        if isinstance(raw, Tag):
            return raw
        if isinstance(raw, BeautifulSoup):
            return raw
        if isinstance(raw, str):
            return BeautifulSoup(raw, "lxml")
        return None

    @staticmethod
    def _try_parse_json(raw: Any) -> Any:
        if isinstance(raw, (dict, list)):
            return raw
        if not isinstance(raw, str):
            return None
        raw = raw.strip()
        if raw.startswith("{") or raw.startswith("["):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return None
        return None

    def _substitute(self, template: str, **kwargs: str) -> str:
        result = template
        # Imported sources sometimes URL-encode Legado placeholders.
        # Decode only known placeholders so legitimate encoded URL values
        # remain unchanged.
        encoded_placeholders = {
            "%7B%7Bpage%7D%7D": "{{page}}",
            "%7b%7bpage%7d%7d": "{{page}}",
            "%257B%257Bpage%257D%257D": "{{page}}",
            "%257b%257bpage%257d%257d": "{{page}}",
            "%7B%7BsearchPage%7D%7D": "{{searchPage}}",
            "%7b%7bsearchpage%7d%7d": "{{searchPage}}",
            "%257B%257BsearchPage%257D%257D": "{{searchPage}}",
            "%257b%257bsearchpage%257d%257d": "{{searchPage}}",
            "%7B%7BsearchKey%7D%7D": "{{searchKey}}",
            "%7b%7bsearchkey%7d%7d": "{{searchKey}}",
            "%257B%257BsearchKey%257D%257D": "{{searchKey}}",
            "%257b%257bsearchkey%257d%257d": "{{searchKey}}",
        }
        for encoded, placeholder in encoded_placeholders.items():
            result = result.replace(encoded, placeholder)
        result = result.replace("{{Url()}}", self.base_url)
        result = result.replace("{{baseUrl}}", self.base_url)
        for k, v in kwargs.items():
            result = result.replace(f"{{{{{k}}}}}", str(v))
        def _page_replacer(m: re.Match) -> str:
            pages = [p.strip() for p in m.group(1).split(",")]
            page_str = kwargs.get("page", "1")
            try:
                page_num = int(page_str)
                if 1 <= page_num <= len(pages):
                    return pages[page_num - 1]
                return pages[-1]
            except ValueError:
                return pages[0]
        result = re.sub(r"<([^>]+)>", _page_replacer, result)
        def _page_expr_replacer(m: re.Match) -> str:
            expr = m.group(0)
            match = re.fullmatch(r"\{\{\s*page\s*([+-])\s*(\d+)\s*\}\}", expr)
            if not match:
                return m.group(0)
            try:
                page_num = int(kwargs.get("page", "1"))
                amount = int(match.group(2))
                value = page_num + amount if match.group(1) == "+" else page_num - amount
                return str(max(1, value))
            except ValueError:
                return m.group(0)
        result = re.sub(
            r"\{\{\s*page\s*([+-])\s*(\d+)\s*\}\}",
            _page_expr_replacer,
            result,
        )
        def _math_replacer(m: re.Match) -> str:
            expr = m.group(1).strip().replace("page", kwargs.get("page", "1"))
            if re.fullmatch(r"[0-9+\-*/().\s]+", expr):
                try:
                    return str(int(eval(expr, {"__builtins__": {}}, {})))
                except Exception:
                    pass
            return m.group(0)
        result = re.sub(
            r"\{\{\s*([0-9+\-*/().\s]*page[0-9+\-*/().\s]*)\s*\}\}",
            _math_replacer,
            result,
        )
        result = result.replace("{{searchPage}}", kwargs.get("page", "1"))
        result = result.replace("{{searchKey}}", kwargs.get("key", ""))
        def _var_replacer(m: re.Match) -> str:
            var = m.group(1)
            if var in self._variables:
                return self._variables[var]
            return m.group(0)
        result = re.sub(r"\{\{(\w+)\}\}", _var_replacer, result)
        return result

    def _jsonpath(self, obj: Any, path: str) -> Any:
        if obj is None:
            return None
        if path.startswith("@@"):
            path = "$" + path[2:]
        elif path.startswith("@"):
            path = "$" + path[1:]
        filter_match = re.match(r"^[.$]?\s*\[\?\s*\((.*)\)\s*\]\s*$", path.strip())
        if filter_match:
            expr = filter_match.group(1)
            if isinstance(obj, list):
                return [item for item in obj if self._jsonpath_filter(item, expr)]
            return []
        if path == "$" or path == "$.":
            return obj
        if path.startswith("$."):
            path = path[2:]
        elif path.startswith("$["):
            path = path[1:]
        segments = re.split(r"(?<!\\)\.", path)
        current = obj
        for seg in segments:
            if current is None:
                return None
            seg = seg.replace("\\.", ".")
            regex_parts = seg.split("##")
            seg = regex_parts[0]
            array_match = re.match(r"^(\w+)?\[(\*|\d+|[:,\d]+)\]$", seg)
            if array_match:
                key = array_match.group(1)
                idx = array_match.group(2)
                if key and isinstance(current, dict):
                    current = current.get(key)
                if idx == "*":
                    if isinstance(current, list):
                        return current
                    return [current] if current is not None else []
                elif idx.isdigit():
                    if isinstance(current, list):
                        i = int(idx)
                        current = current[i] if i < len(current) else None
                continue
            if isinstance(current, dict):
                current = current.get(seg)
            elif isinstance(current, list):
                results = []
                for item in current:
                    if isinstance(item, dict):
                        v = item.get(seg)
                        if v is not None:
                            results.append(v)
                if results:
                    if all(not isinstance(r, (dict, list)) for r in results):
                        return "\n".join(str(r) for r in results if r is not None)
                    return results
                return None
            else:
                return None
        if len(regex_parts) > 1 and isinstance(current, str):
            for rp in regex_parts[1:]:
                if rp.strip():
                    current = self._apply_replace_regex_simple(current, rp.strip())
        return current

    # ---- JS runtime support for book source JS fields ----
    def _jsonpath_filter(self, item: Any, expr: str) -> bool:
        """Evaluate a JSONPath filter expression like ``@.title`` or
        ``@.vip == true`` against a single list item."""
        expr = expr.strip()
        if not expr:
            return bool(item)
        for part in re.split(r"\s*&&\s*", expr):
            part = part.strip()
            if not part:
                continue
            if not self._jsonpath_filter_atom(item, part):
                return False
        return True

    def _jsonpath_filter_atom(self, item: Any, expr: str) -> bool:
        if not isinstance(item, dict):
            return bool(item)
        m = re.match(r"@\s*(?:\.([A-Za-z_][\w]*)|\[\s*'([^']+)'\s*\]|\[\s*\"([^\"]+)\"\s*\])", expr)
        if not m:
            return bool(item)
        key = m.group(1) or m.group(2) or m.group(3)
        rest = expr[m.end():].strip()
        value = item.get(key)
        if not rest:
            return bool(value)
        opm = re.match(r"(!=|==|>=|<=|>|<|=)\s*(.+)", rest)
        if not opm:
            return bool(value)
        op, rhs = opm.group(1), opm.group(2).strip().strip("\'\"")
        if rhs in ("true", "false"):
            rhs_val = rhs == "true"
        elif re.fullmatch(r"-?\d+(\.\d+)?", rhs):
            rhs_val = float(rhs)
        else:
            rhs_val = rhs
        try:
            if op in ("==", "="): return value == rhs_val
            if op == "!=": return value != rhs_val
            if op == ">": return value > rhs_val
            if op == ">=": return value >= rhs_val
            if op == "<": return value < rhs_val
            if op == "<=": return value <= rhs_val
        except TypeError:
            return False
        return False


    def decode_cover(self, image_bytes: bytes) -> bytes | None:
        """Execute coverDecodeJs on cover image bytes."""
        code = self.config.get("coverDecodeJs", "")
        if not code or not code.strip():
            return image_bytes

        try:
            runtime = self._get_js_runtime()
            result = runtime.eval_bytes_sync(code, image_bytes)
            if result is not None:
                return result
        except Exception:
            pass

        # Fallback: try common base64 decode if image bytes look encoded
        try:
            text = image_bytes.decode("utf-8", errors="ignore").strip()
            if text and not text.startswith("<") and not text.startswith("\x89"):
                import base64
                return base64.b64decode(text)
        except Exception:
            pass

        return image_bytes

    def decode_content_image(self, image_bytes: bytes) -> bytes | None:
        """Execute imageDecode on in-content image bytes."""
        content_rules = self.config.get("ruleContent", {})
        code = content_rules.get("imageDecode", "")
        if not code or not code.strip():
            return image_bytes

        try:
            runtime = self._get_js_runtime()
            result = runtime.eval_bytes_sync(code, image_bytes)
            if result is not None:
                return result
        except Exception:
            pass

        return image_bytes

    def get_web_js(self) -> str:
        """Get webJs from the content rule configuration."""
        content_rules = self.config.get("ruleContent", {})
        return content_rules.get("webJs", "")

    def get_pre_update_js(self) -> str:
        """Get preUpdateJs from the TOC rule configuration."""
        toc_rules = self.config.get("ruleToc", {})
        return toc_rules.get("preUpdateJs", "")

    def run_pre_update_js(self, book_data: dict[str, Any]) -> dict[str, Any]:
        """Execute preUpdateJs against book data before TOC update."""
        code = self.get_pre_update_js()
        if not code or not code.strip():
            return book_data

        try:
            runtime = self._get_js_runtime()
            context_data = dict(book_data)
            if "url" not in context_data:
                context_data["url"] = book_data.get("bookUrl", "")
            result = runtime.eval_js_with_context_sync(code, context_data)
            if isinstance(result, dict):
                return result
        except Exception:
            pass

        return book_data

    def eval_web_js(self, js_code: str, page_html: str) -> str:
        """Evaluate webJs against page HTML (browser context replacement).

        For server-side usage, webJs is primarily evaluated through
        Playwright. This method provides a non-browser fallback.
        """
        if not js_code or not js_code.strip():
            return page_html

        try:
            runtime = self._get_js_runtime()
            result = runtime.eval_js_sync(js_code, page_html)
            if isinstance(result, str):
                return result
        except Exception:
            pass

        return page_html

    @staticmethod
    def _apply_replace_regex_simple(text: str, transform: str) -> str:
        if "##" in transform:
            parts = transform.split("##", 1)
            pattern = parts[0]
            replacement = parts[1] if len(parts) > 1 else ""
            try:
                return re.sub(pattern, replacement, text)
            except re.error:
                return text
        return text
