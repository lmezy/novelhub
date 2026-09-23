"""URL semantics and templates for the YueDu (Legado) plugin.

Split out of the ``YueduPlugin`` god class.  Deciding whether a link is
a book page, a chapter page or site navigation is the single most
load-bearing judgement in the crawler, and getting it wrong is how a
book ends up with 0 chapters or with a page of unrelated recommendations
as its TOC.  Also here: ``,{...}`` URL option suffixes, ``{{page}}``
templates, and option parsing.
"""

from app.crawler.plugins.yuedu.selectors import CHAPTER_PATH_SEGMENTS
from app.crawler.plugins.yuedu.selectors import NAV_PATH_SEGMENTS
from app.crawler.plugins.yuedu.selectors import _HOST_ONLY_URL_PATTERN
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlparse
from urllib.parse import urlunparse
import json
import re


class UrlsMixin:
    """Methods extracted from ``YueduPlugin``."""

    @staticmethod
    def _is_nav_path(path: str, nav: str) -> bool:
        """Match a nav path as a segment, not as a substring."""
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.endswith(".html"):
            path = path[:-5]
        return path == nav or path.startswith(nav + "/")
    @staticmethod
    def _same_book_shape(url: str, book_url: str) -> bool:
        """Whether ``url`` is a sibling of ``book_url`` (same dir/extension).

        Forum-style sources keep a book and its chapters in one flat directory
        (h528: ``/post/29144.html`` and ``/post/29145.html``), so pattern-free
        chapter detection needs the book URL as context to tell a chapter from
        another book's detail page.
        """
        candidate = urlparse(url)
        book = urlparse(book_url)
        if candidate.netloc.lower() != book.netloc.lower():
            return False
        candidate_dir = candidate.path.rsplit("/", 1)[0]
        book_dir = book.path.rsplit("/", 1)[0]
        if candidate_dir != book_dir:
            return False

        def _extension(path: str) -> str:
            name = path.rsplit("/", 1)[-1]
            return name.rsplit(".", 1)[1].lower() if "." in name else ""

        return _extension(candidate.path) == _extension(book.path)
    def _book_url_pattern(self) -> str:
        """The source's ``bookUrlPattern`` when it can actually discriminate.

        A pattern that only names the site (``https://host:port``) matches
        every URL on that host, so it can never tell a book page from a chapter
        or a category page.  Used as a *filter* it drops everything (our
        "the match must reach the end of the path" rule leaves the whole path
        unmatched), and taken literally it would call every chapter a book.
        Icu (hq555.icu) ships exactly such a pattern; Legado itself only uses
        ``bookUrlPattern`` to recognise links a user opens, so the honest
        answer is to treat it as "no pattern" and let the source's own
        ``bookList`` / ``chapterList`` rule stay in charge -- the same policy
        already used for sources that declare no pattern at all.
        """
        pattern = str(self.config.get("bookUrlPattern", "") or "").strip()
        if not pattern:
            return ""
        if _HOST_ONLY_URL_PATTERN.match(pattern):
            return ""
        return pattern
    def _is_book_url(self, url: str, require_pattern: bool = False) -> bool:
        """Check whether a URL points to a book detail page.

        When the source defines a discriminating `bookUrlPattern`, discovery
        uses it strictly for the same host so category/chapter links are not
        mistaken for books.
        """
        url = self._strip_url_options_suffix(url)
        pattern = self._book_url_pattern()
        if pattern and pattern.strip():
            try:
                # ``bookUrlPattern`` must only match a *book detail* URL, not a
                # URL that merely *starts with* the book page.  A loose, not
                # anchored pattern such as ``book/\d+`` also matches a chapter
                # URL (``/book/35979/399068.html``); that would make
                # ``_is_chapter_url`` reject every chapter and yield zero books'
                # worth of chapter content for sites like 要撸小说.  Require the
                # match to reach the end of the path (an optional trailing "/"
                # or query/fragment is allowed).
                matched = False
                for candidate in self._url_host_aliases(url):
                    m = re.search(pattern, candidate)
                    if m is None:
                        continue
                    remainder = candidate[m.end() :]
                    remainder = (
                        remainder.split("?", 1)[0]
                        .split("#", 1)[0]
                        .rstrip("/")
                    )
                    if remainder == "":
                        matched = True
                        break
                if matched:
                    return True
            except re.error:
                pass
            if require_pattern:
                same_host = (
                    urlparse(url).netloc.lower()
                    == urlparse(self.base_url).netloc.lower()
                )
                if same_host:
                    return False
        path = urlparse(url).path.lower()
        query = urlparse(url).query.lower()
        # Forum sources commonly use an index.php route with a thread id for
        # the book page, e.g. ``?app=forum&act=threadview&tid=123``.
        query_params = dict(
            part.split("=", 1) if "=" in part else (part, "")
            for part in query.split("&") if part
        )
        if (
            query_params.get("tid")
            and query_params.get("act", "").lower() in {
                "threadview", "thread", "viewthread",
            }
        ):
            return True
        segments = [seg for seg in path.split("/") if seg]
        # ``post``/``thread``/``topic`` cover forum-style sources whose book
        # page is a single post (風月文學網 h528 uses ``/post/29145.html``).
        for prefix in (
            "novel", "book", "read", "detail", "xiaoshuo",
            "post", "thread", "topic", "article", "story",
        ):
            if prefix not in segments:
                continue
            tail = segments[segments.index(prefix) + 1:]
            if len(tail) == 1 and tail[0]:
                return True
        return False
    @staticmethod
    def _url_host_aliases(url: str) -> list[str]:
        """Return the URL plus its www/non-www host equivalent.

        A number of exported sources use one spelling in ``bookUrlPattern``
        while the site redirects links to the other spelling.  Matching the
        equivalent host keeps discovery from dropping otherwise valid books.
        """
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if not hostname:
            return [url]
        aliases = [hostname]
        if hostname.lower().startswith("www."):
            aliases.append(hostname[4:])
        else:
            aliases.append("www." + hostname)
        result: list[str] = []
        for alias in aliases:
            netloc = alias
            if parsed.port:
                netloc += f":{parsed.port}"
            if parsed.username:
                credentials = parsed.username
                if parsed.password:
                    credentials += f":{parsed.password}"
                netloc = f"{credentials}@{netloc}"
            result.append(urlunparse(parsed._replace(netloc=netloc)))
        return list(dict.fromkeys(result))
    def _is_chapter_url(self, url: str, book_url: str) -> bool:
        """Filter out book-page, category, navigation, and ad links from a TOC."""
        url = self._strip_url_options_suffix(url)
        book_url = self._strip_url_options_suffix(book_url)
        abs_url = self._make_absolute(url, book_url or self.base_url)
        abs_book = self._make_absolute(book_url, self.base_url)
        if abs_url.rstrip("/") == abs_book.rstrip("/"):
            return False

        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            return False
        path = parsed.path.lower().split("?", 1)[0].rstrip("/")
        if not path or path == "/":
            return False

        # A book detail page is not a chapter, even if it sits under /book/.
        if self._is_book_url(abs_url, require_pattern=True):
            if self._book_url_pattern():
                # The source told us exactly what a book URL looks like.
                return False
            # Without a pattern the heuristic cannot tell a book page from a
            # post that is a chapter of the same series: on 風月文學網 h528 both
            # the book and its chapters are ``/post/<id>.html``.  A sibling URL
            # (same directory, same extension) is a chapter.
            if not self._same_book_shape(abs_url, abs_book):
                return False

        same_host = (
            parsed.netloc.lower() == urlparse(self.base_url).netloc.lower()
        )
        segments = [segment for segment in path.split("/") if segment]

        if same_host:
            if any(segment in NAV_PATH_SEGMENTS for segment in segments):
                return False
            if len(segments) >= 2:
                return True
            if parsed.query:
                # Forum thread URLs (``index.php?app=forum&act=threadview``)
                # live at the site root: the query string is the identity.
                # Only thread views are chapters -- author/profile/manage
                # links (``act=userview``/``threadmanage``/...) are site
                # chrome that the generic scanner must still reject, or a
                # forum book ends up with other users' pages as chapters.
                query_params = dict(
                    part.split("=", 1) if "=" in part else (part, "")
                    for part in parsed.query.lower().split("&")
                    if part
                )
                act = query_params.get("act", "")
                return act in {"threadview", "thread", "viewthread", "view"}
            # Single-segment album/page URLs (wn09's ``/photos-view-id-N.html``
            # sibling of ``/photos-index-aid-N.html``): the only signal is the
            # sibling shape -- same host, same directory, same extension, but a
            # different basename.  Without this, ``fetch_book``'s rule-less
            # fallback drops every such chapter and the book syncs 0 chapters
            # even though the TOC rule already extracted them; with a
            # discriminating ``bookUrlPattern`` the check above already
            # rejected real book pages.
            return self._same_book_shape(abs_url, abs_book)

        # External links are usually ads/mirror links on Chinese novel sites.
        # Only accept them when the path clearly looks like a chapter page.
        if len(segments) < 3:
            return False
        if any(segment in NAV_PATH_SEGMENTS for segment in segments):
            return False
        return any(segment in CHAPTER_PATH_SEGMENTS for segment in segments)
    def is_book_detail_url(self, url: str) -> bool:
        """Check if a URL matches the bookUrlPattern (direct detail page).

        When search results point directly to a book detail page,
        yuedu extracts book info immediately instead of listing items.
        """
        pattern = self.config.get("bookUrlPattern", "")
        if not pattern or not pattern.strip():
            return False
        try:
            return bool(re.search(pattern, url))
        except re.error:
            return False
    @staticmethod
    def _options_for_url(
        options: dict[str, Any] | None,
        candidate: str,
    ) -> dict[str, Any] | None:
        """Keep Legado URL options in sync with a rewritten catalog URL."""
        if options is None:
            return None
        return {**options, "url": candidate}
    def _resolve_url_template(
        self,
        template: str,
        key: str = "",
        page: int = 1,
        raw: Any = None,
    ) -> str:
        """Resolve a Legado URL template that may be a plain URL or JS.

        ``<js>`` / ``@js:`` templates are evaluated with ``key`` and ``page``
        in scope (like Legado's search/explore URL scripts).  The result may
        be a single URL, a JSON array of URLs (first is used), or a JSON
        object with a ``url`` field.
        """
        text = str(template or "").strip()
        if not text:
            return ""
        code = None
        if text.startswith("<js>") and text.endswith("</js>"):
            code = text[4:-5].strip()
        elif text.startswith("<js"):
            code = text[4:].strip()
        elif text.startswith("@js:"):
            code = text[4:].strip()
        if code is not None:
            try:
                result = self.engine._try_eval_js(
                    code,
                    raw,
                    extra_context={"key": key, "page": int(page)},
                )
            except Exception:
                return ""
            if result is None:
                return ""
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, str) and item.strip():
                        return item.strip()
                    if isinstance(item, dict) and item.get("url"):
                        return str(item["url"]).strip()
                return ""
            resolved = str(result).strip()
            if not resolved:
                return ""
            if resolved.startswith("["):
                try:
                    parsed = json.loads(resolved)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and item.get("url"):
                            return str(item["url"]).strip()
                        if isinstance(item, str) and item.strip():
                            return item.strip()
                    return ""
            return resolved
        return self.engine._substitute(text, key=key, page=str(page))
    @staticmethod
    def _parse_url_options(rule_url: str) -> dict[str, Any] | None:
        """Split a Legado URL option suffix (`,{...}`) from the request URL."""
        match = re.search(r"\s*,\s*(\{.*)$", rule_url, re.DOTALL)
        if not match:
            # Historical exception, not the rule: wn09's ``chapterUrl``
            # ``...@href##$##{"webView":true}`` drops the comma.  Legado reads
            # that as ``,{"webView":true}`` (the ``##$##`` separator vanishes
            # with the regex transform), so accept the comma-less form only
            # for a bare ``{"webView":...}`` tail.  Anything else keeps the
            # URL untouched -- a ``{...}`` at the end of a *path* is part of
            # the path itself (Legado's ``AnalyzeUrl.paramPattern`` is
            # ``\\s*,\\s*(?=\\{)``).
            bare = re.search(r"\s*(\{\"webView\".*\})\s*$", rule_url, re.DOTALL)
            if not bare:
                return None
            try:
                option = json.loads(bare.group(1))
            except (json.JSONDecodeError, ValueError):
                return None
            if not isinstance(option, dict) or set(option) != {"webView"}:
                return None
            match = bare
        base_url = rule_url[: match.start()].strip()
        option_text = match.group(1)
        try:
            option = json.loads(option_text)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(option, dict):
            return None

        headers = option.get("headers") or {}
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except (json.JSONDecodeError, ValueError):
                headers = {}
        if not isinstance(headers, dict):
            headers = {}

        body = option.get("body")
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False)
            headers.setdefault("Content-Type", "application/json")
        elif isinstance(body, str) and body.lstrip().startswith(("{", "[")):
            headers.setdefault("Content-Type", "application/json")

        return {
            "url": base_url,
            "method": str(option.get("method", "GET")),
            "headers": {str(k): str(v) for k, v in headers.items()},
            "body": body,
            "charset": option.get("charset") or option.get("encoding"),
            "web_view": bool(option.get("webView")),
            "web_js": option.get("webJs") or "",
        }
    def _split_options_suffix(self, url: str) -> tuple[str, dict[str, Any] | None]:
        """Return ``(clean_url, options)`` after splitting a Legado ``,{...}``
        URL-options suffix off a request URL.

        Sources commonly append ``,{"webView":true}`` (or ``,{"method":"POST",
        "body":...}``) to book/chapter/search URLs.  The consumer must not send
        that suffix as part of the path, so every fetch entry point funnels the
        URL through here and dispatches on the parsed options instead.
        """
        if not url:
            return url, None
        options = self._parse_url_options(url)
        if not options:
            return url, None
        return options.get("url", url), options
    @staticmethod
    def _strip_url_options_suffix(url: str) -> str:
        """Return the bare URL with any trailing ``,{...}`` Legado options dropped.

        Used in identity/classification helpers so a suffixed book URL is
        never treated as a distinct (and invalid) book key.
        """
        if not url:
            return url
        match = re.search(r"\s*,\s*(\{.*)$", url, re.DOTALL)
        bare_webview = False
        if not match:
            bare = re.search(r"\s*(\{\"webView\".*\})\s*$", url, re.DOTALL)
            if not bare:
                return url
            match = bare
            bare_webview = True
        try:
            parsed = json.loads(match.group(1))
        except (ValueError, TypeError):
            return url
        if not isinstance(parsed, dict):
            return url
        # Same historical exception as ``_parse_url_options``: accept the
        # comma-less form only for a bare ``{"webView":...}`` tail.
        if bare_webview and set(parsed) != {"webView"}:
            return url
        return url[: match.start()].strip()
    @staticmethod
    def _book_id_from_url(url: str) -> str:
        """Return a stable source book id from a book URL.

        Legado uses the full book URL as the book key. Keep the full
        normalized URL so build_book_url() can reconstruct it directly
        without depending on the bookUrlPattern.
        """
        return UrlsMixin._strip_url_options_suffix(url).rstrip("/") or url
    @staticmethod
    def _make_absolute(href: str, base: str) -> str:
        """Convert relative URL to absolute."""
        return urljoin(base, href)
