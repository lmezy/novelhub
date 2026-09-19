"""Discover/search/book-list entry points for the YueDu plugin.

Split out of the ``YueduPlugin`` god class.  ``exploreUrl`` handling
(Legado's category browsing), the pagination-template learning that
recovers a source whose category URL has no ``{{page}}`` placeholder,
and keyword search.

The pagination learning exists because a source that hard-codes one
category URL returns the same page for every page number; de-duplicating
then looks like 'the TOC ended after one page'.
"""

from app.crawler.base import RemoteShelfBook
from app.crawler.plugins.yuedu.common import logger
from html import unescape as html_unescape
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urljoin
from urllib.parse import urlsplit
from urllib.parse import urlunsplit
import asyncio
import json
import os
import re


class ExploreMixin:
    """Methods extracted from ``YueduPlugin``."""

    def get_explore_kinds(self) -> list[dict[str, str]]:
        """Parse exploreUrl to get discover categories.

        Returns list of {title, url} dicts. Ported from
        BookSourceExtensions.exploreKinds().  JS-based exploreUrl rules
        (``<js>`` / ``@js:``) are evaluated first so sources like 菠萝猫 /
        UAA that generate their category list in JavaScript work.
        """
        explore_url = self.config.get("exploreUrl", "")
        if not explore_url or not explore_url.strip():
            return []

        rule_str = explore_url.strip()

        # If it starts with <js> or @js:, evaluate the script and parse
        # the returned JSON array of {title, url} kinds.
        if rule_str.startswith("<js>") or rule_str.startswith("@js:"):
            return self._parse_explore_js(rule_str)

        # Try to parse as JSON array directly
        if rule_str.startswith("["):
            kinds = self._parse_explore_json(rule_str)
            if kinds:
                return kinds

        # Plain text format: lines of "Title::URL" or "Title"
        kinds = []
        for line in rule_str.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "::" in line:
                title, url = line.split("::", 1)
                kinds.append({"title": title.strip(), "url": url.strip()})
            else:
                # Header/separator lines have no URL; fetch_explore skips them.
                kinds.append({"title": line, "url": ""})
        return kinds
    def _parse_explore_js(self, text: str) -> list[dict[str, str]]:
        """Evaluate a <js>/@js: exploreUrl script into a kinds array.

        Never raises: unsupported JS returns an empty list so the caller
        falls through to the generic ranking-page fallback instead of
        failing the whole discover task with an 'Unsupported URL' error.
        """
        if text.startswith("<js>") and text.endswith("</js>"):
            code = text[4:-5].strip()
        elif text.startswith("<js"):
            code = text[4:].strip()
        elif text.startswith("@js:"):
            code = text[4:].strip()
        else:
            code = text
        try:
            result = self.engine._try_eval_js(
                code, None, extra_context={"page": "1"},
            )
        except Exception as exc:
            logger.warning(
                "exploreUrl JS evaluation failed for %s: %s",
                self.config.get("bookSourceName", self.config.get("bookSourceUrl", "?")),
                exc,
            )
            return []
        if result is None:
            # A Legado JS exploreUrl that needs the full Android runtime
            # (window/document/jsoup/remote obfuscated scripts) evaluates to
            # nothing here. Surface that instead of silently returning an
            # empty discovery result.
            logger.warning(
                "exploreUrl JS for %s produced no result; the source may "
                "require the full Legado JS runtime (remote <js>/@js: rules)",
                self.config.get("bookSourceName", self.config.get("bookSourceUrl", "?")),
            )
            return []
        if isinstance(result, str):
            result = result.strip()
            if not result:
                return []
            try:
                parsed = json.loads(result)
            except (ValueError, TypeError):
                return []
            if not isinstance(parsed, list):
                return []
            result = parsed
        if not isinstance(result, list):
            return []
        kinds = []
        for item in result:
            if isinstance(item, dict) and item.get("url"):
                kinds.append({
                    "title": str(item.get("title", "")),
                    "url": str(item.get("url", "")),
                })
        return kinds
    def _parse_explore_json(self, text: str) -> list[dict[str, str]]:
        """Try to parse explore kind JSON array."""
        if text.startswith("<js>") and text.endswith("</js>"):
            text = text[4:-5]
        elif text.startswith("@js:"):
            text = text[4:]
        text = text.strip()
        if text.startswith("["):
            try:
                items = json.loads(text)
                if isinstance(items, list):
                    return [
                        {"title": item.get("title", ""), "url": item.get("url", "")}
                        for item in items if isinstance(item, dict)
                    ]
            except (json.JSONDecodeError, ValueError):
                pass
        return []
    async def fetch_explore(self, url: str | None = None, page: int = 1) -> list[dict[str, Any]]:
        """Fetch explore/discover results.

        Ported from WebBook.exploreBookAwait. Uses ruleExplore
        (or falls back to ruleSearch) for parsing.
        If no explore URL is configured, tries common ranking pages.
        JS-based explore URLs are resolved so a source whose exploreUrl is
        a ``<js>`` / ``@js:`` script never crashes with an unsupported URL.
        """
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")
        if url:
            explore_url = self.engine._substitute(url, page=str(page))
            explore_url = self._resolve_kind_url(explore_url, page)
            if not explore_url:
                return []
            explore_kind = self._explore_kind_for_url(explore_url, page)
            return await self._fetch_explore_url(explore_url, explore_kind)

        kinds = self.get_explore_kinds()
        if kinds:
            blocked_errors: list[str] = []
            unavailable_errors: list[str] = []
            transport_errors: list[str] = []

            async def _fetch_kind(kind: dict[str, Any]) -> list[dict[str, Any]]:
                kind_url = str(kind.get("url", "")).strip()
                if not kind_url:
                    return []
                try:
                    resolved, options = self._resolve_kind(kind_url, page)
                except Exception as exc:
                    logger.warning(f"Explore kind URL failed: {kind_url} ({exc})")
                    return []
                if not resolved:
                    return []
                try:
                    items = await self._fetch_kind_items(
                        kind_url=kind_url,
                        resolved=resolved,
                        page=page,
                        explore_kind=kind.get("title", ""),
                        options=options,
                    )
                    if not items and page <= 1:
                        # A first catalog page that parses to nothing is the
                        # signature of a silent failure (proxy/site served an
                        # error or empty page with HTTP 200).  Record it so the
                        # log explains why the task saw "0 books" -- including
                        # what the site answered, since the same code works
                        # again minutes later when it was a transient hiccup.
                        logger.warning(
                            "Explore kind %s returned no books on page %s: %s [%s]",
                            kind.get("title", kind_url),
                            page,
                            resolved,
                            self._explore_page_diagnostics.get(
                                resolved,
                                "page not captured",
                            ),
                        )
                    return items
                except Exception as exc:
                    message = str(exc)
                    described = (
                        f"{type(exc).__name__}: {message}"
                        if message
                        else type(exc).__name__
                    )
                    if (
                        "anti-bot" in message.lower()
                        or "captcha" in message.lower()
                        or "验证码" in message
                        or "身份验证" in message
                    ):
                        blocked_errors.append(described)
                    response = getattr(exc, "response", None)
                    status_code = getattr(response, "status_code", None)
                    if status_code is not None or any(
                        marker in message.lower()
                        for marker in ("403", "404", "408", "429", "500", "502", "503", "504", "520")
                    ):
                        unavailable_errors.append(described)
                    elif not any(
                        marker in message.lower()
                        for marker in ("anti-bot", "captcha", "验证码", "身份验证")
                    ):
                        # Timeouts / dropped connections / empty httpx errors.
                        transport_errors.append(described)
                    logger.warning(
                        "Explore kind failed: %s (%s)",
                        kind.get("title", kind_url),
                        described,
                    )
                    return []

            # A source's catalog categories are independent pages.  Fetching
            # them one after another left the source's own rate limiter idle
            # between requests (wait for a page, then wait for the interval);
            # running a few at once keeps the limiter busy without letting the
            # site see a higher request rate, because ``_sleep_rate_limit``
            # still spaces every request start for this source.
            try:
                explore_concurrency = max(
                    1, int(os.getenv("YUEDU_EXPLORE_CONCURRENCY", "4") or 4)
                )
            except (TypeError, ValueError):
                explore_concurrency = 4
            semaphore = asyncio.Semaphore(explore_concurrency)

            async def _bounded(kind: dict[str, Any]) -> list[dict[str, Any]]:
                async with semaphore:
                    return await _fetch_kind(kind)

            grouped = await asyncio.gather(*(_bounded(kind) for kind in kinds))
            results = [item for items in grouped for item in items]
            if not results and blocked_errors:
                # Every discover category was gated by an anti-bot / captcha
                # page. Surface the first one so crawl tasks show a real
                # error instead of a misleading "0 books found".
                raise RuntimeError(blocked_errors[0])
            if not results and unavailable_errors:
                raise RuntimeError(
                    "书源目录暂时不可访问：" + unavailable_errors[0]
                )
            if not results and transport_errors:
                # A short network/proxy outage used to surface as the
                # misleading "书源未返回可同步的书籍"; say what actually
                # happened so the task can be retried meaningfully.
                raise RuntimeError(
                    "书源目录暂时无法访问（网络/代理错误，请稍后重试）："
                    + transport_errors[0]
                )
            return results

        explore_url_rule = str(self.config.get("exploreUrl", "") or "").strip()
        if (
            explore_url_rule
            and (explore_url_rule.startswith("<js") or explore_url_rule.startswith("@js:"))
        ):
            # A JS exploreUrl that produced no kinds: either the script needs
            # the full Legado Android runtime, or it failed to evaluate.
            # Report it so the user is not left wondering why 0 books were
            # discovered.
            raise RuntimeError(
                "该书源的发现规则是 Legado JS 脚本（<js>/@js:），当前环境无法执行；"
                "请在 Legado 中搜索书籍后通过书源搜索/手动链接同步，"
                f"或更换该网站的其他书源。source={self.display_name}"
            )

        explore_url = self.engine.build_explore_url(page=page)
        if not explore_url:
            # Fallback: try common ranking/category pages
            fallback_paths = [
                "/rank.html", "/top.html", "/ranking.html",
                "/sort.html", "/allvisit.html", "/top/allvisit_{}.html",
                "/ph.html", "/category.html", "/fenlei.html",
                "/list.html", "/quanben.html", "/wanben.html",
            ]
            import re
            for path in fallback_paths:
                try:
                    # Replace {} with page number
                    path_with_page = path.format(page) if "{}" in path else path
                    candidate = urljoin(self.base_url, path_with_page)
                    html = await self._get(candidate)
                    items = self._explore_items_from_html(html, candidate)
                    if items:
                        logger.info(f"Discovered books via fallback: {candidate}")
                        return items
                except Exception:
                    continue
            logger.warning(f"No explore URL for {self.display_name}")
            return []
        explore_url = self._resolve_kind_url(explore_url, page)
        if not explore_url:
            logger.warning(f"Explore URL unresolved for {self.display_name}")
            return []
        return await self._fetch_explore_url(explore_url)
    def _resolve_kind_url(self, kind_url: str, page: int) -> str:
        """Resolve an explore/search URL that may be a JS template or carry
        a Legado URL-options suffix (`,{...}`).  Returns a plain http(s)
        URL or "" when the value cannot be resolved."""
        resolved, _options = self._resolve_kind(kind_url, page)
        return resolved
    def _explore_page_base(self, kind_url: str) -> str:
        """Page-1 URL of an explore kind, used as the pagination cache key."""
        key = str(kind_url or "")
        if key in self._explore_kind_bases:
            return self._explore_kind_bases[key]
        try:
            resolved, _options = self._resolve_kind(kind_url, 1)
        except Exception:
            resolved = ""
        base = (resolved or "").rstrip("/")
        # Resolving a ``<js>`` explore rule twice per page is wasteful; the
        # page-1 URL of a kind never changes within one plugin instance.
        self._explore_kind_bases[key] = base
        return base
    @classmethod
    def _detect_page_template(cls, base_url: str, html: str) -> str | None:
        """Infer a pagination URL template from the links of a catalog page.

        Only a real "page 2" link that stays under the current catalog URL is
        accepted, so a nav link to another category can never be mistaken for
        the pagination pattern.
        """
        base = (base_url or "").rstrip("/")
        if not base or not html:
            return None
        base_parts = urlsplit(base_url)
        base_path = base_parts.path.rstrip("/")
        links: set[str] = set()
        for href in re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, re.I):
            href = html_unescape(href.strip())
            if not href or href.startswith(("javascript:", "#", "mailto:")):
                continue
            links.add(urljoin(base_url, href))
        for link in sorted(links):
            parts = urlsplit(link)
            if (parts.scheme, parts.netloc) != (
                base_parts.scheme,
                base_parts.netloc,
            ):
                continue
            # Query pagination (``?page=2``); the path stays the same.
            if parts.query and parts.path.rstrip("/") == base_path:
                params = parse_qsl(parts.query, keep_blank_values=True)
                for index, (key, value) in enumerate(params):
                    if key.lower() in ("page", "paged") and value == "2":
                        rewritten = list(params)
                        rewritten[index] = (key, "{page}")
                        return urlunsplit((
                            parts.scheme,
                            parts.netloc,
                            parts.path,
                            urlencode(rewritten, safe="{page}"),
                            "",
                        ))
                continue
            # Path pagination (``/page/2``, ``/index_2.html``).
            for pattern in cls._PAGE_PATH_RES:
                match = pattern.match(parts.path)
                if not match or match.group("num") != "2":
                    continue
                if match.group("prefix").rstrip("/") != base_path:
                    continue
                template_path = (
                    match.group("prefix")
                    + match.group("sep")
                    + "{page}"
                    + match.group("suffix")
                )
                return urlunsplit((
                    parts.scheme,
                    parts.netloc,
                    template_path,
                    "",
                    "",
                ))
        return None
    def _remember_page_template(self, page_url: str, html: str) -> None:
        """Cache the pagination template discovered on a catalog page."""
        base = (page_url or "").rstrip("/")
        if not base or base in self._explore_page_templates:
            return
        try:
            template = self._detect_page_template(base, html)
        except Exception:  # pragma: no cover - never fail a fetch over this
            template = None
        if template:
            self._explore_page_templates[base] = template
    def _page_url_from_template(self, template: str, page: int) -> str:
        return template.replace("{page}", str(page))
    @staticmethod
    def _page_candidates(base_url: str, page: int) -> list[str]:
        """Common catalog pagination shapes, tried when nothing was learned."""
        base = base_url.rstrip("/")
        return [
            f"{base}/page/{page}",
            f"{base}/page/{page}/",
            f"{base}?page={page}",
            f"{base}&page={page}",
            f"{base}/index_{page}.html",
            f"{base}/list_{page}.html",
        ]
    @staticmethod
    def _is_missing_page_error(exc: BaseException) -> bool:
        """Whether an exception means "that page does not exist"."""
        response = getattr(exc, "response", None)
        return getattr(response, "status_code", None) in (404, 410)
    async def _fetch_kind_items(
        self,
        kind_url: str,
        resolved: str,
        page: int,
        explore_kind: str,
        options: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Fetch one catalog page, paging URLs that lack a page placeholder."""
        base = self._explore_page_base(kind_url)
        if page <= 1 or not base or resolved.rstrip("/") != base:
            # Either the first page, or the source rule already paginates
            # itself (``{{page}}`` / JS template), so use it verbatim.
            return await self._fetch_explore_url(
                resolved,
                explore_kind=explore_kind,
                options=options,
                learn_page_template=(page <= 1),
            )

        template = self._explore_page_templates.get(base)
        if template:
            candidate = self._page_url_from_template(template, page)
            try:
                return await self._fetch_explore_url(
                    candidate,
                    explore_kind=explore_kind,
                    options=self._options_for_url(options, candidate),
                )
            except Exception as exc:
                if self._is_missing_page_error(exc):
                    # Past the last page: the catalog is exhausted.
                    return []
                raise

        # No template learned yet (e.g. a task resumed straight at page > 1):
        # probe the common paging shapes until one returns books.
        last_error: Exception | None = None
        for candidate in self._page_candidates(base, page):
            try:
                items = await self._fetch_explore_url(
                    candidate,
                    explore_kind=explore_kind,
                    options=self._options_for_url(options, candidate),
                )
            except Exception as exc:
                if not self._is_missing_page_error(exc):
                    last_error = last_error or exc
                continue
            if items:
                self._explore_page_templates[base] = re.sub(
                    r"\d+/?$", "{page}", candidate
                )
                return items
        if last_error is not None:
            raise last_error
        return []
    def _resolve_kind(
        self,
        kind_url: str,
        page: int,
    ) -> tuple[str, dict[str, Any] | None]:
        """Resolve an explore/search URL into (request URL, URL options).

        Handles ``<js>`` / ``@js:`` templates (evaluated with ``page`` in
        scope), ``{{page}}`` placeholders, and the Legado ``,{...}`` option
        suffix so webView / method / headers options survive resolution.
        """
        text = str(kind_url or "").strip()
        if not text:
            return "", None
        if text.startswith("<js>") or text.startswith("@js:"):
            text = self._resolve_url_template(text, page=page)
            if not text:
                return "", None
        options = self._parse_url_options(text)
        request_url = options["url"] if options else text
        request_url = self.engine._substitute(request_url, page=str(page))
        if not request_url.startswith(("http://", "https://")):
            request_url = self._make_absolute(request_url, self.base_url)
        if not request_url.startswith(("http://", "https://")):
            return "", None
        # Keep URL options in sync with the resolved URL. Otherwise
        # _fetch_explore_url uses the original ``{{page}}`` value from the
        # options object and httpx sends it as ``%7B%7Bpage%7D%7D``.
        if options is not None:
            options["url"] = request_url
        return request_url, options
    async def _fetch_explore_url(
        self,
        explore_url: str,
        explore_kind: str = "",
        options: dict[str, Any] | None = None,
        learn_page_template: bool = False,
    ) -> list[dict[str, Any]]:
        if options is None:
            options = self._parse_url_options(explore_url)
        request_url = options["url"] if options else explore_url
        request_url = self._make_absolute(request_url, self.base_url)
        if options and (options.get("web_view") or options.get("web_js")):
            html = await self._get_with_web_js(
                request_url,
                options.get("web_js") or self.engine.get_web_js(),
            )
        elif options and str(options.get("method", "GET")).upper() == "POST":
            post_kwargs = {
                "body": options.get("body"),
                "headers": options.get("headers") or {},
            }
            if options.get("charset"):
                post_kwargs["charset"] = options["charset"]
            html = await self._post(request_url, **post_kwargs)
        else:
            if options and options.get("charset"):
                html = await self._get(request_url, charset=options["charset"])
            else:
                html = await self._get(request_url)
        if learn_page_template:
            self._remember_page_template(request_url, html)
        items = self._explore_items_from_html(html, request_url)
        if not items:
            self._explore_page_diagnostics[request_url] = (
                self._describe_fetched_page(html)
            )
        if not explore_kind:
            return items
        return [
            {**item, "exploreKind": explore_kind}
            for item in items
        ]
    @staticmethod
    def _describe_fetched_page(html: str) -> str:
        """One-line summary of a fetched page, for empty-catalog warnings.

        A catalog page that parses to zero books is either a real end of the
        catalog or a silent failure (the proxy or site answered 200 with an
        error/blank document).  The byte count, title and first visible words
        tell those apart, which the URL alone never could.
        """
        text = str(html or "")
        title = ""
        match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        if match:
            title = " ".join(match.group(1).split())[:60]
        body = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", text, flags=re.I | re.S)
        body = " ".join(re.sub(r"<[^>]+>", " ", body).split())[:80]
        return f"bytes={len(text)} title={title!r} text={body!r}"
    def _explore_kind_for_url(self, url: str, page: int) -> str:
        """Recover a configured category when the caller selects one URL."""
        selected = url.rstrip("/")
        for kind in self.get_explore_kinds():
            kind_url = str(kind.get("url") or "").strip()
            if not kind_url:
                continue
            try:
                resolved, _options = self._resolve_kind(kind_url, page)
            except Exception:
                continue
            if resolved and resolved.rstrip("/") == selected:
                return str(kind.get("title") or "")
        return ""
    def _explore_items_from_html(
        self,
        html: str,
        page_url: str,
    ) -> list[dict[str, Any]]:
        """Parse an explore page and fall back to generic anchor scanning."""
        explore_rules = self.config.get("ruleExplore", {})
        if explore_rules.get("bookList", ""):
            items = self.engine.parse_explore_results(html)
        else:
            items = self.engine.parse_search_results(html)
        usable_items = [
            item for item in items
            if str(item.get("bookUrl") or item.get("url") or "").strip()
        ]
        if usable_items:
            normalized_items = [
                self._normalize_explore_item(item, page_url)
                for item in usable_items
            ]
            # The source's own ``bookList`` rule already decided which links are
            # books, so without a discriminating ``bookUrlPattern`` the URL only
            # has to be a usable detail link on this site -- Legado accepts such
            # rules as written.  Running our path heuristic on top dropped whole
            # sites whose detail URLs are not in the ``/novel/123`` shape
            # (绅士漫画: ``/photos-index-aid-354422.html``, Icu: ``/xs_ls/39898``),
            # even though the rule matched them correctly, which surfaced as
            # "同步 0 本书".
            declared_pattern = self._book_url_pattern()
            filtered_items = []
            for item in normalized_items:
                book_url = self._make_absolute(
                    self._explore_item_url(item),
                    page_url,
                )
                if not book_url.startswith(("http://", "https://")):
                    continue
                if book_url.rstrip("/") == (page_url or "").rstrip("/"):
                    continue
                if declared_pattern and not self._is_book_url(
                    book_url,
                    require_pattern=True,
                ):
                    continue
                item["bookUrl"] = book_url
                filtered_items.append(item)
            if filtered_items:
                return filtered_items

        # Generic fallback for list/category pages whose configured rules no
        # longer match the live site.
        shelf_books = self._parse_bookshelf_html(html, base_url=page_url)
        return [
            {
                "bookUrl": book.url,
                "name": book.title,
                "author": self._clean_author(book.author) or "Unknown",
                "latestChapterTitle": book.latest_chapter_title,
            }
            for book in shelf_books
        ]
    @staticmethod
    def _explore_item_url(item: dict[str, Any]) -> str:
        return str(item.get("bookUrl") or item.get("url") or "").strip()
    def _normalize_explore_item(
        self,
        item: dict[str, Any],
        page_url: str | None,
    ) -> dict[str, Any]:
        """Resolve relative book URLs against the page that contained them."""
        normalized = dict(item)
        book_url = self._explore_item_url(normalized)
        if book_url:
            normalized["bookUrl"] = self._make_absolute(
                book_url,
                page_url or self.base_url,
            )
        return normalized
    async def discover_books(self, url: str | None = None, page: int = 1) -> list[RemoteShelfBook]:
        """Discover books from a source's explore/catalog pages.

        Wraps the existing fetch_explore method to return RemoteShelfBook
        objects compatible with the NovelSourcePlugin protocol.
        """
        items = await self.fetch_explore(url=url, page=page)
        link_base = self._make_absolute(url, self.base_url) if url else self.base_url
        books: list[RemoteShelfBook] = []
        declared_pattern = self._book_url_pattern()
        for item in items:
            book_url = self._explore_item_url(item)
            if not book_url:
                continue
            full_url = self._make_absolute(book_url, link_base)
            if not full_url.startswith(("http://", "https://")):
                continue
            # Only second-guess the source when it declares a
            # ``bookUrlPattern``; otherwise its ``bookList`` rule is the
            # authority (see ``_explore_items_from_html``).
            if declared_pattern and not self._is_book_url(
                full_url,
                require_pattern=True,
            ):
                continue
            books.append(RemoteShelfBook(
                source_book_id=self._book_id_from_url(full_url),
                title=str(item.get("name") or item.get("title") or book_url).strip() or "Unknown",
                author=self._clean_author(str(item.get("author") or "").strip()) or "Unknown",
                url=full_url,
                latest_chapter_title=item.get("latestChapterTitle"),
                tags=self._clean_tags(
                    self._split_kind_text(item.get("kind"))
                    + self._split_kind_text(
                        self._clean_listing_kind(item.get("exploreKind"))
                    ),
                    str(item.get("name") or item.get("title") or ""),
                    str(item.get("author") or ""),
                ),
            ))
        return books
    async def search_books(
        self,
        keyword: str,
        page: int = 1,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search remote books using the source's searchUrl/ruleSearch rules.

        Supports Legado URL options (POST JSON/body, headers, webView) and
        legacy searchKey/searchPage placeholders found in exported sources.
        """
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")

        keyword = (keyword or "").strip()
        if not keyword:
            return []

        search_url = self.engine.build_search_url(keyword, page)
        if search_url.startswith("<js>") or search_url.startswith("@js:"):
            search_url = self._resolve_url_template(
                search_url,
                key=keyword,
                page=page,
            )
            if not search_url:
                return []
        options = self._parse_url_options(search_url)
        request_url = options["url"] if options else search_url
        request_url = self._make_absolute(request_url, self.base_url)

        if options and str(options.get("method", "GET")).upper() == "POST":
            post_kwargs = {
                "body": options.get("body"),
                "headers": options.get("headers") or {},
            }
            if options.get("charset"):
                post_kwargs["charset"] = options["charset"]
            html = await self._post(request_url, **post_kwargs)
        else:
            web_js = (options or {}).get("web_js") or self.engine.get_web_js()
            if web_js or (options or {}).get("web_view"):
                html = await self._get_with_web_js(request_url, web_js)
            else:
                if options and options.get("charset"):
                    html = await self._get(request_url, charset=options["charset"])
                else:
                    html = await self._get(request_url)

        items = self.engine.parse_search_results(html)
        results = self._normalize_search_items(items, request_url)
        if not results:
            results = self._fallback_search_items(html, request_url)
        return results[:limit] if limit and limit > 0 else results
    def _normalize_search_items(
        self,
        items: list[dict[str, Any]],
        page_url: str | None,
    ) -> list[dict[str, Any]]:
        """Normalize ruleSearch entries and filter non-book links."""
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        link_base = self._make_absolute(page_url, self.base_url) if page_url else self.base_url
        # Same policy as discovery: the source's ``bookList`` rule is the
        # authority, and a host-only ``bookUrlPattern`` cannot filter anything.
        declared_pattern = self._book_url_pattern()

        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("title") or "").strip()
            book_url = str(item.get("bookUrl") or item.get("url") or "").strip()
            if not name or not book_url:
                continue
            full_url = self._make_absolute(book_url, link_base)
            if not full_url.startswith(("http://", "https://")):
                continue
            if declared_pattern and not self._is_book_url(
                full_url,
                require_pattern=True,
            ):
                continue
            key = full_url.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "name": name,
                "author": self._clean_author(str(item.get("author") or "").strip()) or "Unknown",
                "bookUrl": full_url,
                "coverUrl": self._pick_cover_url(item.get("coverUrl"), link_base) or None,
                "intro": str(item.get("intro") or "").strip() or None,
                "kind": str(item.get("kind") or "").strip() or None,
                "lastChapter": (
                    str(item.get("lastChapter") or item.get("latestChapterTitle") or "")
                    .strip() or None
                ),
                "wordCount": str(item.get("wordCount") or "").strip() or None,
            })
        return results
    def _fallback_search_items(
        self,
        html: str,
        page_url: str,
    ) -> list[dict[str, Any]]:
        """Fall back to generic list parsing when ruleSearch misses."""
        shelf_books = self._parse_bookshelf_html(html, base_url=page_url)
        return [
            {
                "name": book.title,
                "author": self._clean_author(book.author) or "Unknown",
                "bookUrl": book.url,
                "lastChapter": book.latest_chapter_title,
            }
            for book in shelf_books
        ]
    def get_search_check_keyword(self, default: str = "\u6211\u7684") -> str:
        """Get the check keyword for search validation.

        Ported from BookSource.getCheckKeyword.
        """
        search_rules = self.config.get("ruleSearch", {})
        ck = search_rules.get("checkKeyWord", "")
        if ck and ck.strip():
            return ck.strip()
        return default
