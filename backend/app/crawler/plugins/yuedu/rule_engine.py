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
            self._chomp_balanced(q[st], next_ch)
            if self._pos > end:
                self._start = self._pos
                self._split_head(separators)
                return
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
                self._chomp_balanced(q[st], next_ch)
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

        if start_x == self._pos and start_x == 0:
            return ""

        st_buf.append(q[self._start_x:])
        return "".join(st_buf)

class YueduRuleEngine:
    """Evaluates YueDu book source rules against HTML or JSON responses."""

    SEPARATORS = ("&&", "||", "%%")
    JS_PATTERN = re.compile(
        r"<js>[\s\S]*?</js>|@js:[^\n]*",
        re.IGNORECASE,
    )

    def __init__(self, source_config: dict[str, Any]):
        self.config = source_config
        self.base_url: str = source_config.get("bookSourceUrl", "")
        self._variables: dict[str, str] = {}
        self._is_json_context: bool = False
        self._js_runtime: "JsRuntime | None" = None

    def _get_js_runtime(self) -> "JsRuntime":
        if self._js_runtime is None:
            self._js_runtime = JsRuntime.get_instance()
        return self._js_runtime

    # ---- Public API ----

    def build_search_url(self, keyword: str, page: int = 1) -> str:
        template = self.config.get("searchUrl", "")
        if not template:
            if self.base_url:
                return self._substitute(self.base_url, key=keyword, page=str(page))
            raise ValueError("No searchUrl defined in source config")
        return self._substitute(template, key=keyword, page=str(page))

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

    def get_next_content_url(self, html_or_json: str) -> str | None:
        rules = self.config.get("ruleContent", {})
        next_rule = rules.get("nextContentUrl", "")
        if not next_rule:
            return None
        return self._eval_rule_str(html_or_json, next_rule, is_url=True)

    def get_next_toc_url(self, html_or_json: str) -> str | None:
        rules = self.config.get("ruleToc", {})
        next_rule = rules.get("nextTocUrl", "")
        if not next_rule:
            return None
        return self._eval_rule_str(html_or_json, next_rule, is_url=True)

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
            for field in field_names:
                rule = rules.get(field, "")
                if rule:
                    entry[field] = self._eval_field(item, rule)

            fmt_js = rules.get("formatJs", "")
            if fmt_js and "chapterName" in entry and entry["chapterName"]:
                entry["chapterName"] = self._try_format_js(fmt_js, entry["chapterName"])

            results.append(entry)
        return results

    def _extract_book_info(self, raw: str, rules: dict[str, Any]) -> dict[str, Any]:
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
                info[field] = self._eval_field(raw, rule)
        return info

    def _extract_content(self, raw: str, rules: dict[str, Any]) -> str:
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
            return raw

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

        parsed = self._try_parse_json(raw)
        if parsed is not None:
            self._is_json_context = True
            try:
                result = self._jsonpath(parsed, rule)
                if isinstance(result, list):
                    return result
                return [result] if result is not None else []
            finally:
                self._is_json_context = False

        self._is_json_context = False
        soup = BeautifulSoup(raw, "lxml")
        elements = soup.select(rule)
        return list(elements)

    def _eval_rule_str(self, raw: str | Any, rule: str, is_url: bool = False) -> str:
        result = self._eval_field(raw, rule)
        if result is None:
            return ""
        if isinstance(result, list):
            result = "\n".join(str(r) for r in result)
        result = str(result)
        if is_url and result.strip():
            return urljoin(self.base_url, result)
        return result

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
        rules = analyzer.split_rule(*self.SEPARATORS)
        elem_type = analyzer.elements_type
        results: list[list[str]] = []
        for rl in rules:
            rl = rl.strip()
            if not rl:
                continue
            temp = self._eval_css_single(soup, rl)
            if temp:
                results.append(temp)
                if elem_type == "||":
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
        css_part, attr_suffix = self._split_css_attr(rule)
        if css_part:
            elements = soup.select(css_part)
        else:
            elements = [soup] if isinstance(soup, Tag) else []
        if not elements:
            return None
        results: list[str] = []
        for el in elements:
            val = self._extract_css_value(el, attr_suffix)
            if val:
                results.append(val)
        return results if results else None

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
        rules = analyzer.split_rule(*self.SEPARATORS)
        elem_type = analyzer.elements_type
        results: list[str] = []
        for rl in rules:
            rl = rl.strip()
            if not rl:
                continue
            val = self._jsonpath(raw, rl)
            resolved = str(val) if val is not None else ""
            if resolved:
                results.append(resolved)
                if elem_type == "||":
                    break
        if not results:
            return None
        return "\n".join(results)

    def _eval_xpath(self, raw: Any, rule: str) -> Any:
        soup = self._ensure_soup(raw)
        if soup is None:
            return None
        try:
            from lxml import etree
            tree = etree.HTML(str(raw))
            elements = tree.xpath(rule)
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
            return "\n".join(t for t in texts if t) if texts else None
        except Exception:
            results = soup.select(rule)
            return "\n".join(r.get_text("\n", strip=True) for r in results) if results else None

    def _apply_replace_regex(self, text: str, rule: str) -> str:
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
        return text

    def _try_eval_js(self, js_code: str, raw: Any) -> Any:
        """Evaluate JavaScript against raw input.
        
        Uses real Node.js JS runtime when available; falls back to
        pattern-based evaluation for simple operations.
        """
        code = js_code.strip()
        if not code:
            return raw

        # Fast path: try pattern-based evaluation first
        pattern_result = try_eval_js_pattern(code, raw)
        if pattern_result is not None:
            return pattern_result

        # If pattern failed, try the real JS runtime for complex expressions
        try:
            runtime = self._get_js_runtime()
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(runtime.eval_js(code, raw))
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
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(runtime.eval_js(js_code, input_value))
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
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(runtime.eval_js(code, value))
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
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(runtime.eval_js(code, html))
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
        def _resolve_template(inner: str) -> str | None:
            inner = inner.strip()
            if inner.startswith("@") or inner.startswith("$.") or inner.startswith("//"):
                result = self._eval_field(raw, inner)
                return str(result) if result is not None else ""
            if inner in self._variables:
                return self._variables[inner]
            js_result = self._try_eval_js(inner, raw)
            if js_result is not None:
                return str(js_result)
            return ""
        analyzer = _RuleAnalyzer(rule)
        return analyzer.inner_rule("{{", "}}", _resolve_template)

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

    def decode_cover(self, image_bytes: bytes) -> bytes | None:
        """Execute coverDecodeJs on cover image bytes."""
        code = self.config.get("coverDecodeJs", "")
        if not code or not code.strip():
            return image_bytes

        try:
            runtime = self._get_js_runtime()
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(runtime.eval_bytes(code, image_bytes))
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
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(runtime.eval_bytes(code, image_bytes))
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
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(
                runtime.eval_js_with_context(code, book_data)
            )
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
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(runtime.eval_js(js_code, page_html))
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
