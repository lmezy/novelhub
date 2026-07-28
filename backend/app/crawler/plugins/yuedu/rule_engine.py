"""YueDu book source rule engine.

Interprets common YueDu rule patterns:
- JSONPath rules (e.g., $.data.list[*])
- CSS selector rules (e.g., #chapter-grid-container a)
- Template substitution (e.g., {{Url()}}/search?keyword={{key}})
- Simple @js: blocks (limited support for common patterns)

Complex JS transformations are skipped with a warning logged.
"""

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class YueduRuleEngine:
    """Evaluates YueDu book source rules against HTML or JSON responses."""

    def __init__(self, source_config: dict[str, Any]):
        self.config = source_config
        self.base_url: str = source_config.get("bookSourceUrl", "")

    # ---- Public API ----

    def build_search_url(self, keyword: str, page: int = 1) -> str:
        """Build the search URL from the searchUrl template."""
        template = self.config.get("searchUrl", "")
        if not template:
            raise ValueError("No searchUrl defined in source config")
        return self._substitute(template, key=keyword, page=str(page))

    def build_explore_url(self, page: int = 1) -> str:
        """Build the explore/discover URL."""
        template = self.config.get("exploreUrl", "")
        if not template:
            raise ValueError("No exploreUrl defined in source config")
        return self._substitute(template, page=str(page))

    def build_book_url(self, book_id: str) -> str:
        """Build a book detail page URL."""
        return urljoin(self.base_url, book_id)

    def parse_search_results(self, html_or_json: str) -> list[dict[str, Any]]:
        """Parse search results into a list of book dicts."""
        rules = self.config.get("ruleSearch", {})
        return self._extract_list(html_or_json, rules)

    def parse_book_info(self, html_or_json: str) -> dict[str, Any]:
        """Parse book detail page into a dict with name, author, intro, etc."""
        rules = self.config.get("ruleBookInfo", {})
        return self._extract_info(html_or_json, rules)

    def parse_toc(self, html_or_json: str) -> list[dict[str, Any]]:
        """Parse table of contents into a list of chapter dicts."""
        rules = self.config.get("ruleToc", {})
        return self._extract_list(html_or_json, rules)

    def parse_content(self, html_or_json: str) -> str:
        """Parse chapter content into a text string."""
        rules = self.config.get("ruleContent", {})
        content_rule = rules.get("content", "")
        if not content_rule:
            return html_or_json  # fallback: raw HTML
        return self._extract_text(html_or_json, content_rule)

    # ---- Internal: rule evaluation ----

    def _extract_list(self, raw: str, rules: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract a list of items using the list rule (bookList), then extract fields."""
        list_rule = rules.get("bookList", "")
        items = self._eval_list_rule(raw, list_rule)

        results: list[dict[str, Any]] = []
        for item in items:
            entry: dict[str, Any] = {}
            for field in ("name", "author", "bookUrl", "coverUrl", "intro", "kind",
                          "chapterName", "chapterUrl", "updateTime"):
                rule = rules.get(field, "")
                if rule:
                    entry[field] = self._eval_field(item, rule, raw)
            results.append(entry)
        return results

    def _extract_info(self, raw: str, rules: dict[str, Any]) -> dict[str, Any]:
        """Extract book info fields."""
        init_rule = rules.get("init", "")
        if init_rule:
            raw = self._eval_field(raw, init_rule, raw) or raw

        info: dict[str, Any] = {}
        for field in ("name", "author", "coverUrl", "intro", "kind", "tocUrl", "status"):
            rule = rules.get(field, "")
            if rule:
                info[field] = self._eval_field(raw, rule, raw)
        return info

    def _extract_text(self, raw: str, rule: str) -> str:
        """Extract a single text value."""
        return self._eval_field(raw, rule, raw) or ""

    # ---- Low-level evaluation ----

    def _eval_list_rule(self, raw: str, rule: str) -> list[Any]:
        """Evaluate a list extraction rule against the raw response."""
        if not rule:
            return [raw]

        # Try to parse as JSON if it looks like JSON
        parsed = self._try_parse_json(raw)

        if parsed is not None:
            # JSONPath rule
            if rule.startswith("$."):
                return self._jsonpath(parsed, rule)
            return [parsed]

        # CSS selector rule
        soup = BeautifulSoup(raw, "lxml")
        elements = soup.select(rule)
        return elements

    def _eval_field(self, raw: Any, rule: str, original_raw: str = "") -> Any:
        """Evaluate a single field extraction rule."""
        if not rule:
            return None

        # Handle @js: prefix - skip complex JS, try simple patterns
        if rule.startswith("@js:"):
            return self._try_eval_js(rule[4:].strip(), raw, original_raw)

        # Handle {{...}} template expressions
        rule = self._substitute(rule)

        # JSONPath
        if rule.startswith("$.") or rule.startswith("@"):
            if not isinstance(raw, (dict, list)):
                parsed = self._try_parse_json(raw if isinstance(raw, str) else str(raw))
                if parsed is not None:
                    raw = parsed
            return self._jsonpath(raw, rule)

        # CSS selector on HTML string or BeautifulSoup element
        if isinstance(raw, str):
            soup = BeautifulSoup(raw, "lxml")
        else:
            soup = raw

        # Handle selectors with attribute extraction
        if "@text" in rule:
            css_rule = rule.replace("@text", "").strip()
            el = soup.select_one(css_rule)
            return el.get_text(strip=True) if el else None

        if "@" in rule and "##" not in rule:
            parts = rule.rsplit("@", 1)
            css_rule = parts[0].strip()
            attr = parts[1].strip()
            el = soup.select_one(css_rule)
            return el.get(attr) if el else None

        # Handle ## regex replacement
        if "##" in rule:
            parts = rule.split("##")
            base_rule = parts[0]
            result = self._eval_field(raw, base_rule, original_raw) if base_rule else raw
            for transform in parts[1:]:
                if "##" in transform:
                    continue
                if result is not None:
                    result = self._apply_transform(str(result), transform.strip())
            return result

        # Default: CSS selector returning text
        el = soup.select_one(rule)
        return el.get_text(strip=True) if el else None

    def _try_eval_js(self, js_code: str, raw: Any, original_raw: str = "") -> Any:
        """Attempt to handle simple @js: patterns without a JS runtime."""
        code = js_code.strip()

        # Pattern: JSON.stringify(result)
        if "JSON.stringify" in code:
            logger.warning("Skipping complex @js: block")
            return None

        # Pattern: result.replace(...) or simple string ops
        if "result" in code:
            # Try simple regex replace: result.replace(/pattern/flags, replacement)
            replace_match = re.search(r"result\.replace\(\s*/(.+?)/(\w*)\s*,\s*['\"](.+?)['\"]\s*\)", code)
            if replace_match:
                pattern = replace_match.group(1)
                flags = replace_match.group(2)
                replacement = replace_match.group(3)
                if isinstance(raw, str):
                    return re.sub(pattern, replacement, raw, flags=0 if "g" in flags else 1)

        # Pattern: var $ = result; return $.field
        field_match = re.search(r'return\s+(?:result|(?:\$\.)?)(\w+)', code)
        if field_match:
            field = field_match.group(1)
            if isinstance(raw, dict) and field in raw:
                return raw[field]

        logger.warning(f"Cannot evaluate @js: block: {code[:100]}...")
        return None if not isinstance(raw, str) else raw

    # ---- Utility ----

    def _substitute(self, template: str, **kwargs: str) -> str:
        """Substitute {{key}} and {{Url()}} placeholders in a template."""
        result = template

        # {{Url()}} -> base URL
        result = result.replace("{{Url()}}", self.base_url)
        result = result.replace("{{baseUrl}}", self.base_url)
        result = result.replace("{{baseUrl}}", self.base_url)

        # {{key}} -> keyword
        for k, v in kwargs.items():
            result = result.replace(f"{{{{{k}}}}}", v)

        # {{$.field}} - leave as is (handled by _eval_field)
        return result

    def _jsonpath(self, obj: Any, path: str) -> Any:
        """Simple JSONPath evaluation supporting common patterns."""
        if path.startswith("@"):
            path = "$" + path[1:]

        # Handle  and @@ (legado aliases)
        path = path.replace("", "$")
        path = path.replace("@@", "@")

        # Remove leading $.
        if path == "$" or path == "$.":
            return obj

        segments = path.replace("$.", "").split(".")
        current = obj

        for seg in segments:
            if current is None:
                return None

            # Handle array indexing: [*] or [0]
            array_match = re.match(r"^(\w+)\[(\*|\d+)\]$", seg)
            if array_match:
                key = array_match.group(1)
                idx = array_match.group(2)
                if isinstance(current, dict) and key in current:
                    current = current[key]
                if idx == "*":
                    if isinstance(current, list):
                        return current
                    return current
                elif idx.isdigit():
                    if isinstance(current, list):
                        current = current[int(idx)] if int(idx) < len(current) else None
                    continue
                continue

            # Handle map with field selector: [*].field or just .field
            if seg == "*":
                continue

            # Handle ## regex in segment
            if "##" in seg:
                parts = seg.split("##")
                seg = parts[0]

            if isinstance(current, dict):
                current = current.get(seg)
            elif isinstance(current, list):
                # If we have a list, try to extract the field from each item
                # For single items like $.data.name where data is a dict
                pass
            else:
                return None

        return current

    def _try_parse_json(self, raw: str) -> Any:
        """Try to parse a string as JSON, return None if it fails."""
        if not isinstance(raw, str):
            return None
        raw = raw.strip()
        if raw.startswith("{") or raw.startswith("["):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return None
        return None

    def _apply_transform(self, value: str, transform: str) -> str:
        """Apply a simple string transform like regex replacement."""
        # regex: pattern##replacement
        if "##" in transform:
            parts = transform.split("##", 1)
            pattern = parts[0]
            replacement = parts[1] if len(parts) > 1 else ""
            return re.sub(pattern, replacement, value)

        # If transform is a regex pattern like (\d+)$, extract first match
        if transform.startswith("(") and transform.endswith(")$"):
            m = re.search(transform, value)
            return m.group(1) if m else value

        return value
