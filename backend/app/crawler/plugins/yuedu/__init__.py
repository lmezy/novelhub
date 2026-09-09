"""YueDu book source plugin -- interprets YueDu (Legado) source JSON to crawl novels.

Each instance is configured with a single YueDu book source JSON. The plugin
translates YueDu's rule-based definitions (JSONPath, CSS selectors, URL templates)
into NovelHub's NovelSourcePlugin protocol.

Usage:
    plugin = YueduPlugin(yuedu_source_json)
    book = await plugin.fetch_book(url)
    content = await plugin.fetch_chapter_content(chapter)
"""

import asyncio
import base64
import codecs
import json
import logging
import random
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook
from app.crawler.plugins.yuedu.js_runtime import JsRuntime, try_eval_js_pattern
from app.crawler.plugins.yuedu.rule_engine import YueduRuleEngine

logger = logging.getLogger(__name__)

# Common bookshelf URL patterns across Chinese novel sites
BOOKSHELF_PATH_CANDIDATES = [
    "/user/bookshelf",
    "/bookshelf",
    "/mybook",
    "/bookcase",
    "/user/favorites",
    "/favorites",
    "/user/collect",
    "/collect",
    "/mybooks",
    "/user/books",
    "/member/bookshelf",
    "/user",
    "/user/bookcase",
    "/user/shelf",
    "/home/bookcase",
    "/home/bookshelf",
    "/space/bookshelf",
    "/reader/bookshelf",
    "/book/shelf",
    "/books",
    "/my",
    "/ucenter/bookshelf",
    "/ucenter",
    "/center",
    "/personal",
]

# Common link text patterns that indicate a bookshelf link
BOOKSHELF_LINK_PATTERNS = [
    "书架", "我的书架", "bookshelf", "收藏", "我的收藏",
    "favorites", "my bookshelf", "bookcase", "书柜",
    "我的书柜", "追书", "我的追书",
]

# CSS selectors that commonly identify book entries on bookshelf pages
SHELF_ITEM_SELECTORS = [
    "li.book-item", "li.book-entry", "li.shelf-item",
    "div.book-item", "div.book-entry", "div.shelf-item",
    "tr.book-row", "tr.shelf-row",
    "li.bookshelf-item", "div.bookshelf-item",
    "li[class*='book']", "div[class*='book']",
    "li[class*='shelf']", "div[class*='shelf']",
    # Very generic fallback: any li or div with an anchor inside
    "li:has(a[href])", "div.book-card",
    # Additional patterns for Chinese novel sites
    "div.panel-body li", "ul.list-group li",
    "div.card li", "div.panel li",
    "table.table tr", "ul.novel-list li",
    "div[class*='shelf'] a[href]",
]

# Anchor patterns within a shelf item
SHELF_LINK_SELECTORS = [
    "a.book-title", "a[class*='title']", "a[class*='name']",
    "h3 a", "h2 a", "h4 a",
    "a:first-child", "a",
]

# Author patterns within a shelf item
SHELF_AUTHOR_SELECTORS = [
    "span.author", "span[class*='author']", "span[class*='writer']",
    ".book-author", ".author-name", "span:nth-child(2)",
    "p.author", "span[class*='by']",
]

GENERIC_BOOK_TITLE_SELECTORS = [
    "h1",
    ".book-name",
    ".book_name",
    ".novel-title",
    ".bookTitle",
    ".info h1",
    ".bookinfo h1",
    ".book_info h1",
    "meta[property='og:title']",
    "meta[name='og:title']",
]

GENERIC_BOOK_AUTHOR_SELECTORS = [
    "meta[property='og:novel:author']",
    "meta[name='author']",
    ".book-author",
    ".author",
    ".writer",
    ".info .author",
    ".bookinfo .author",
    ".book_info .author",
]

GENERIC_BOOK_DESC_SELECTORS = [
    "meta[name='description']",
    ".book-intro",
    ".book_intro",
    ".book-desc",
    ".intro",
    ".desc",
    ".book-description",
    "#intro",
]

GENERIC_CHAPTER_SELECTORS = [
    "div.book_newchap a",
    ".book_newchap a",
    "#chapters a",
    ".chapter-list a",
    ".listmain a",
    "ul.chapter-list a",
    "div.listmain a",
    "li.chapter-item a",
    "dd.chapter a",
    ".book-catalog a",
    "#catalog a",
    "#list a",
    "#content a",
    "[class*='chapter'] a",
    "[class*='catalog'] a",
    "[class*='list'] a",
]

GENERIC_BOOK_COVER_SELECTORS = [
    "meta[property='og:image']",
    "meta[name='og:image']",
    "meta[itemprop='image']",
    "img.book-cover",
    ".book-cover img",
    ".novel-cover img",
    ".book_info img",
    ".book-info img",
    ".bookinfo img",
    "#cover img",
    "img.cover",
    "img[class*='cover']",
]

TOC_LINK_TEXTS = {
    "查看所有章节",
    "查看全部章节",
    "全部章节",
    "章节列表",
    "章节目录",
    "查看目录",
    "所有章节",
    "目录",
}

TOC_LINK_PATH_RE = re.compile(
    r"/(?:other/chapters|chapters|booktoc|chapterlist|toc|book/chapters)(?:/|\.)",
    re.IGNORECASE,
)

CHAPTER_PATH_SEGMENTS = (
    "book",
    "read",
    "chapter",
    "chapters",
    "novel",
    "article",
    "content",
    "view",
    "show",
    "xiaoshuo",
    "txt",
    "files",
)

NAV_PATH_SEGMENTS = (
    "list",
    "lists",
    "sort",
    "rank",
    "top",
    "all",
    "order",
    "update",
    "finish",
    "wanben",
    "quanben",
    "allvisit",
    "lastupdate",
    "history",
    "bookcase",
    "bookshelf",
    "user",
    "users",
    "login",
    "register",
    "signup",
    "search",
    "category",
    "tag",
    "tags",
    "author",
    "about",
    "help",
    "faq",
    "contact",
    "index",
    "original",
    "other",
    "fenlei",
    "booklist",
)

TOC_NOISE_TITLES = {
    "首页",
    "原创",
    "最新",
    "电子魅魔",
    "Ai性伴侣",
    "色情游戏",
    "查看所有章节",
    "查看全部章节",
    "全部章节",
    "章节列表",
    "章节目录",
    "目录",
    "返回书页",
    "直达底部",
    "简体站",
    "繁體站",
    "发布页",
    "上一章",
    "下一章",
    "返回目录",
    "开始阅读",
}

STRONG_BLOCK_MARKERS = (
    "输入验证码后可继续访问",
    "验证码后可继续访问",
    "请完成验证",
    "安全验证",
    "人机验证",
    "滑动验证",
    "limit_box",
    "challenge-platform",
    "cf-chl",
    "访问过于频繁",
    "请求过于频繁",
    "操作过于频繁",
    "访问频率过高",
    "请求频率过高",
    "请稍后再试",
    # GoEdge WAF captcha gate (used by boluomao.com etc.)
    "goedge_waf",
    "goedge-waf",
    "请输入上面的验证码",
    "身份验证",
    # Generic WAF / challenge gates
    "waf_captcha",
    "captcha-gate",
    "verify/captcha",
    "安全网关",
    "访问被拒绝",
    "已被限制",
    "被限制访问",
    "ip 已被限制",
    "ip已被限制",
    # Cloudflare / Turnstile / generic JS challenge gates.  The rendered body
    # of a Cloudflare "Just a moment..." page carries these markers (and often
    # ``cf-chl``), so treat them as a hard block until the browser has a chance
    # to solve the challenge and reload.
    "just a moment",
    "managed challenge",
    "verify you are human",
    "cf-turnstile",
    "cf-challenge",
    "attention required",
    "请启用javascript",
    "请开启javascript",
    "浏览器安全检查",
    "正在验证您的浏览器",
    "正在检查您的浏览器",
)

# Weak markers need a confirmation phrase to avoid false positives on
# normal pages (e.g. a login dialog mentioning a captcha code).
WEAK_BLOCK_MARKERS = (
    "访问异常",
    "访问频繁",
    "请求频繁",
    "限流",
)


class YueduPlugin:
    """A NovelSourcePlugin implementation driven by a YueDu book source JSON."""

    name = "yuedu"
    _clients: dict[str | None, httpx.AsyncClient] = {}
    _rate_locks: dict[str, asyncio.Lock] = {}
    _rate_state: dict[str, dict[str, float | int]] = {}
    # DoH (DNS over HTTPS) cache for bypassing polluted system DNS.
    # {host: {"ip": ip, "expires": epoch_seconds}}
    _doh_cache: dict[str, dict[str, float | str]] = {}
    _doh_lock = asyncio.Lock()
    _doh_providers = (
        # Tencent public DNS works from CN networks; try it first.
        "https://doh.pub/dns-query",
        "https://cloudflare-dns.com/dns-query",
        "https://dns.google/resolve",
    )
    _doh_ttl = 300

    def __init__(self, source_config: dict[str, Any] | None = None):
        self.config = self._normalize_source_config(source_config)
        self.engine: YueduRuleEngine | None = None
        if self.config:
            self.engine = YueduRuleEngine(self.config)
        self.base_url: str = self.config.get("bookSourceUrl", "")
        self._cookie: str = ""
        self._client_lock = asyncio.Lock()

    @property
    def display_name(self) -> str:
        return self.config.get("bookSourceName", "yuedu")

    @property
    def source_group(self) -> str:
        return self.config.get("bookSourceGroup", "")

    def configure(self, config: dict[str, Any] | None) -> None:
        """Load a YueDu book source JSON configuration."""
        if not config:
            raise ValueError("YueduPlugin requires a valid book source JSON config")
        self.config = self._normalize_source_config(config)
        self.engine = YueduRuleEngine(self.config)
        self.base_url = self.config.get("bookSourceUrl", "")

    @staticmethod
    def _normalize_source_config(
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Accept exported YueDu sources that use [] for an empty rule map."""
        normalized = dict(config or {})
        for field in ("ruleSearch", "ruleExplore", "ruleBookInfo", "ruleToc", "ruleContent"):
            if not isinstance(normalized.get(field), dict):
                normalized[field] = {}
        return normalized

    async def _get_http_client(self, proxy: str | None) -> httpx.AsyncClient:
        """Reuse one AsyncClient per proxy so TLS/connections are pooled."""
        async with self._client_lock:
            client = self.__class__._clients.get(proxy)
            if client is None:
                client = httpx.AsyncClient(
                    timeout=httpx.Timeout(60.0, connect=5.0, write=15.0),
                    follow_redirects=True,
                    proxy=proxy,
                    trust_env=False,
                    # Proxy (Clash) TLS interception uses a local CA cert;
                    # browsers accept it interactively but httpx cannot.
                    verify=False,
                )
                self.__class__._clients[proxy] = client
            return client

    def build_book_url(self, book_id: str) -> str:
        """Reconstruct a book detail URL from a stored source book id."""
        if book_id.startswith(("http://", "https://")):
            return book_id
        if book_id.startswith("/"):
            return urljoin(self.base_url, book_id)
        if "/" in book_id:
            return urljoin(self.base_url.rstrip("/") + "/", book_id)

        prefix = "/novel/"
        pattern = self.config.get("bookUrlPattern", "")
        if pattern:
            match = re.search(r"https?://[^/]+(/[^?#]*?)(?:\.html|\{[^}]+\}|[^/]+)$", pattern)
            if match:
                prefix = match.group(1).rsplit("/", 1)[0] + "/"
        return f"{self.base_url.rstrip('/')}{prefix}{book_id}"

    # ---- Required: fetch_book ----

    async def fetch_book(self, url: str) -> RemoteBook:
        """Fetch book info + chapter list from a book page URL."""
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")

        # A source may append a Legado ``,{...}`` option suffix to the book URL
        # (e.g. 要撸小说 appends `,{"webView":true}` to every book link).  That
        # suffix must NOT become the book's identity: it corrupts baseUrl, makes
        # source_book_id/url comparisons fail, and flows into the DB.  Keep it
        # only for the page fetch (so `_get` can honor webView/JS), and use the
        # clean URL for every identity/base operation below.
        fetch_url = url
        identity_url, url_options = self._split_options_suffix(url)
        if not identity_url:
            identity_url = url

        # Rules may refer to Legado's page-scoped ``baseUrl``.
        self.engine.set_page_url(identity_url)

        # Use webJs-enabled fetch if the source has webJs configured
        web_js = self.engine.get_web_js()
        if web_js:
            html = await self._get_with_web_js(fetch_url, web_js)
        else:
            html = await self._get(fetch_url)
        if self._looks_like_upstream_error(html):
            raise RuntimeError(
                "Upstream server returned a transient 5xx error page "
                f"(Cloudflare/520 etc.): {fetch_url}"
            )
        info = self.engine.parse_book_info(html)

        # Run preUpdateJs before parsing TOC
        toc_data = {"bookUrl": identity_url, "baseUrl": self.base_url}
        self.engine.run_pre_update_js(toc_data)

        toc_url = str(info.get("tocUrl") or "").strip()
        if not toc_url:
            toc_url = self._find_toc_url(html, identity_url)
        if toc_url and not toc_url.startswith(("http://", "https://")):
            toc_url = self._make_absolute(toc_url, identity_url)
        if not toc_url:
            toc_url = identity_url

        if toc_url.rstrip("/") == identity_url.rstrip("/"):
            toc_html = html
        else:
            toc_html = await self._get(toc_url)

        self.engine.set_page_url(toc_url)
        # Legado exposes the parsed book as the ``book`` JS variable; TOC
        # rules such as SiS's single-entry list reference ``book.name``.
        self.engine.set_book({
            "name": str(info.get("name") or "").strip(),
            "author": str(info.get("author") or "").strip(),
            "url": identity_url,
            "bookUrl": identity_url,
            "baseUrl": self.base_url,
        })
        toc = self._resolve_toc_entries(
            self.engine.parse_toc(toc_html),
            toc_url,
        )
        android_toc_rule = self._uses_android_js_rule("ruleToc", "chapterList")
        if not toc and not android_toc_rule:
            # The configured ruleToc may be outdated. Fall back to the generic
            # chapter scanner on the real TOC page (book page or full list).
            generic_toc = self._parse_book_generic(toc_html, toc_url)
            toc = [
                {
                    "chapterName": chapter.title,
                    "chapterUrl": chapter.url,
                }
                for chapter in generic_toc["chapters"]
            ]

        # Follow nextTocUrl for paginated tables of contents
        max_toc_pages = 20
        seen_toc_urls = {toc_url}
        toc_pages_fetched = 1
        toc_semaphore = asyncio.Semaphore(self._thread_count())

        async def _fetch_toc_page(page_url: str) -> str:
            async with toc_semaphore:
                return await self._get(page_url)

        toc_pending = [
            next_toc_url
            for next_toc_url in self.engine.get_next_toc_urls(toc_html, toc_url)
            if next_toc_url not in seen_toc_urls
        ]
        seen_toc_urls.update(toc_pending)
        while toc_pending and toc_pages_fetched < max_toc_pages:
            batch = toc_pending
            toc_pending = []
            htmls = await asyncio.gather(
                *(_fetch_toc_page(page_url) for page_url in batch)
            )
            for page_url, page_html in zip(batch, htmls):
                if toc_pages_fetched >= max_toc_pages:
                    break
                toc_pages_fetched += 1
                toc.extend(
                    self._resolve_toc_entries(
                        self.engine.parse_toc(page_html),
                        page_url,
                    )
                )
                for next_toc_url in self.engine.get_next_toc_urls(
                    page_html,
                    page_url,
                ):
                    if next_toc_url not in seen_toc_urls:
                        seen_toc_urls.add(next_toc_url)
                        toc_pending.append(next_toc_url)

        generic = self._parse_book_generic(html, url)
        if not str(info.get("name") or "").strip():
            info["name"] = generic["title"]
        rule_author = self._clean_author(str(info.get("author") or "").strip())
        generic_author = self._clean_author(str(generic.get("author") or "").strip())
        labelled_author = self._clean_author(
            self._extract_labelled_author(BeautifulSoup(html, "lxml"))
        )
        if labelled_author and not self._looks_like_invalid_author(labelled_author):
            info["author"] = labelled_author
        elif not rule_author or self._looks_like_invalid_author(rule_author):
            info["author"] = generic_author or rule_author
        else:
            info["author"] = rule_author
        if not info.get("intro"):
            info["intro"] = generic["description"]
        if not info.get("status"):
            info["status"] = generic["status"]
        if not self._pick_cover_url(info.get("coverUrl"), url):
            info["coverUrl"] = self._pick_cover_url(generic.get("cover"), url)
        generic_tags = generic.get("tags") or []
        raw_kind = info.get("kind") or ""
        kind_tags = self._split_kind_text(raw_kind)
        book_title = self._clean_book_title(str(info.get("name") or "").strip()) or "Unknown"
        author = self._clean_author(str(info.get("author") or "").strip()) or "Unknown"
        tags = list(dict.fromkeys([
            *kind_tags,
            *generic_tags,
            *self._content_type_tags(
                book_title,
                info.get("intro"),
                generic.get("description"),
            ),
        ]))
        tags = self._clean_tags(tags, book_title, author)
        info["kind"] = ",".join(tags)
        info["name"] = book_title
        info["author"] = author

        chapters: list[RemoteChapter] = []
        self_chapter_title = ""
        chapter_num = 0
        seen_chapter_urls: set[str] = set()
        for ch in toc:
            # Skip volume headers
            is_volume = str(ch.get("isVolume", "")).strip().lower()
            if is_volume in ("true", "1", "yes"):
                continue

            title = ch.get("chapterName", f"Chapter {chapter_num + 1}")
            ch_url = ch.get("chapterUrl", "")
            if not ch_url:
                is_vip = str(ch.get("isVip", "")).strip().lower()
                is_pay = str(ch.get("isPay", "")).strip().lower()
                if is_vip in ("true", "1", "yes") or is_pay in ("true", "1", "yes"):
                    title = f"[VIP] {title}"
                continue
            if ch_url and not ch_url.startswith("http"):
                ch_url = self._make_absolute(ch_url, identity_url)
            if urlparse(ch_url).scheme not in ("http", "https"):
                continue
            title = str(title or "").strip() or f"Chapter {chapter_num + 1}"
            # Forum sources frequently model a post as a one-chapter book and
            # intentionally return the same URL from ruleToc. Keep it as a
            # fallback rather than discarding it as a book-detail URL.
            if ch_url.rstrip("/") == identity_url.rstrip("/"):
                self_chapter_title = self_chapter_title or title
                continue
            if not self._is_chapter_url(ch_url, identity_url):
                continue
            if (
                title in ("目录", "简介", "上一章", "下一章", "返回目录", "首页", "开始阅读")
                or title == book_title
            ):
                continue
            if ch_url in seen_chapter_urls:
                continue
            seen_chapter_urls.add(ch_url)
            chapter_num += 1
            chapters.append(RemoteChapter(
                source_chapter_id=ch_url,
                title=title,
                url=ch_url,
                chapter_number=chapter_num,
            ))

        if not chapters and self_chapter_title:
            chapters = [RemoteChapter(
                source_chapter_id=url,
                title=(
                    self_chapter_title
                    if self_chapter_title not in ("", "Chapter 1")
                    else book_title
                ),
                url=url,
                chapter_number=1,
            )]
        if not chapters and not android_toc_rule:
            chapters = generic["chapters"]
        if not chapters and self._has_forum_content(html):
            # Several Cool18-compatible sources use Android Jsoup in ruleToc.
            # The page itself is a complete post, so retain it as one chapter.
            chapters = [RemoteChapter(
                source_chapter_id=url,
                title=book_title,
                url=url,
                chapter_number=1,
            )]
        chapters = self._dedupe_chapters(chapters, identity_url)
        chapters = self._attach_next_urls(chapters)

        cover_url = self._pick_cover_url(info.get("coverUrl"), identity_url)
        description = info.get("intro", "")
        status = info.get("status", "")

        return RemoteBook(
            source_book_id=self._book_id_from_url(identity_url),
            title=book_title,
            author=author,
            description=description if description else None,
            status=status if status else None,
            chapters=chapters,
            tags=tags,
            cover_url=cover_url or None,
        )

    @staticmethod
    def _has_forum_content(html: str) -> bool:
        """Whether a forum post contains a readable post body."""
        try:
            return BeautifulSoup(html, "lxml").select_one(
                "#content-section, .content-section"
            ) is not None
        except Exception:
            return False

    @staticmethod
    def _content_type_tags(*values: Any) -> list[str]:
        """Keep non-novel forum posts discoverable in the existing library."""
        text = " ".join(str(value or "") for value in values).lower()
        tags: list[str] = []
        if "漫画" in text or "漫畫" in text:
            tags.append("漫画")
        if "写真" in text or "寫真" in text:
            tags.append("写真")
        if any(marker in text for marker in ("图集", "圖集", "图包", "圖包", "套图", "套圖")):
            tags.append("图集")
        return tags

    def _uses_android_js_rule(self, section: str, field: str) -> bool:
        """Identify Legado rules that need Android/JVM-only APIs.

        ``org.jsoup`` is emulated by the Node shim and ``java.*`` has a
        stub, so only raw ``Packages.*`` JVM calls remain unsupported.
        """
        rule = (self.config.get(section) or {}).get(field, "")
        text = str(rule or "")
        if "Packages." in text and "org.jsoup" not in text:
            return True
        return any(marker in text for marker in (
            "android.", "androidx.", "javax.swing.", "org.json.JSONObject",
        ))

    def _find_toc_url(self, html: str, book_url: str) -> str:
        """Find a full chapter-list URL from a book detail page.

        Many sites keep only the latest chapters on the book page and put the
        complete TOC on a separate page (for example `/other/chapters/id/1.html`
        or `/chapters/1.html`). The link text is usually "查看所有章节".
        """
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return ""

        candidates: list[str] = []
        for a_tag in soup.select("a[href]"):
            href = (a_tag.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "#")):
                continue
            text = a_tag.get_text(" ", strip=True).strip().lower()
            absolute = self._make_absolute(href, book_url or self.base_url)
            if not absolute.startswith(("http://", "https://")):
                continue
            if urlparse(absolute).path in ("", "/"):
                continue
            if absolute.rstrip("/") == self._make_absolute(
                book_url,
                self.base_url,
            ).rstrip("/"):
                continue
            if text and any(marker in text for marker in TOC_LINK_TEXTS):
                candidates.append(absolute)
                continue
            if TOC_LINK_PATH_RE.search(urlparse(absolute).path):
                candidates.append(absolute)

        # Prefer an explicit TOC path over a generic link text match.
        for candidate in candidates:
            if TOC_LINK_PATH_RE.search(urlparse(candidate).path):
                return candidate
        return candidates[0] if candidates else ""

    @staticmethod
    def _dedupe_chapters(
        chapters: list["RemoteChapter"],
        book_url: str,
    ) -> list["RemoteChapter"]:
        """Deduplicate chapters by URL and by repeated title on the same book.

        A broad generic TOC scanner often sees the same chapter twice (for
        example a "start reading" button plus the real list entry, or a link
        ending in `/0.html` alongside the canonical hash URL). When the same
        normalized title appears under the same parent path, keep the
        canonical-looking URL.
        """
        def normalized_url(url: str) -> str:
            return url.split("#", 1)[0].rstrip("/")

        def parent_path(url: str) -> str:
            path = urlparse(url).path.rstrip("/")
            return path.rsplit("/", 1)[0] if "/" in path else path

        def normalized_title(title: str) -> str:
            return re.sub(
                r"[\s《》「」『』〈〉（）【】\[\]\"'“”‘’]+",
                "",
                title or "",
            ).lower()

        seen_urls: set[str] = set()
        by_title: dict[str, list[RemoteChapter]] = {}
        result: list[RemoteChapter] = []

        for chapter in chapters:
            key = normalized_url(chapter.url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            title_key = normalized_title(chapter.title)
            if title_key:
                by_title.setdefault(title_key, []).append(chapter)
            result.append(chapter)

        final: list[RemoteChapter] = []
        seen_kept: set[str] = set()
        for chapter in result:
            if id(chapter) in seen_kept:
                continue
            title_key = normalized_title(chapter.title)
            group = by_title.get(title_key) or [chapter]
            same_path_groups: dict[str, list[RemoteChapter]] = {}
            for ch in group:
                same_path_groups.setdefault(parent_path(ch.url), []).append(ch)
            duplicate_group = next(
                (
                    same_group
                    for same_group in same_path_groups.values()
                    if len(same_group) > 1
                ),
                None,
            )
            if duplicate_group is None:
                final.append(chapter)
                seen_kept.add(id(chapter))
                continue

            def _canonical_score(ch: RemoteChapter) -> tuple[int, int]:
                path = urlparse(ch.url).path.rstrip("/")
                last = path.rsplit("/", 1)[-1].lower()
                if last in ("0", "0.html"):
                    return (0, len(path))
                if re.fullmatch(r"[0-9a-f]{8,}", last.rsplit(".", 1)[0], re.I):
                    return (2, len(path))
                return (1, len(path))

            best = max(duplicate_group, key=_canonical_score)
            if chapter is best:
                final.append(chapter)
                for ch in group:
                    seen_kept.add(id(ch))
            else:
                seen_kept.add(id(chapter))
        return final

    def _parse_book_generic(
        self,
        html: str,
        url: str,
    ) -> dict[str, Any]:
        """Parse a detail page using common novel-site patterns.

        This is a fallback for YueDu sources whose configured rules no longer
        match the live page. It extracts title/author/description and any
        chapter links so a source can still sync without a rule rewrite.
        """
        soup = BeautifulSoup(html, "lxml")
        title = ""
        for selector in GENERIC_BOOK_TITLE_SELECTORS:
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if value:
                title = str(value).strip()
                break

        if not title:
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(" ", strip=True)
                title = re.split(r"\s+[-_|]\s+", title, maxsplit=1)[0].strip()
        title = self._clean_book_title(title)

        # Prefer an explicitly labelled author from the page metadata/body.
        # Forum sources often use meta[name=author] for the post submitter,
        # while the novel's real author is embedded in the subject/description.
        author = self._extract_author_from_text(soup)
        for selector in GENERIC_BOOK_AUTHOR_SELECTORS:
            if author:
                break
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if value:
                author = str(value).strip()
                break
        author = self._clean_author(author)

        description = ""
        for selector in GENERIC_BOOK_DESC_SELECTORS:
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if value:
                description = str(value).strip()
                break

        cover = ""
        for selector in GENERIC_BOOK_COVER_SELECTORS:
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get("src") or el.get("data-src")
            if value:
                cover = str(value).strip()
                break
        if not cover:
            for img in soup.select("img[src]"):
                src = str(img.get("src") or "").strip()
                if not src or src.startswith("data:"):
                    continue
                abs_src = self._make_absolute(src, url)
                path = urlparse(abs_src).path.lower()
                if any(key in path for key in ("cover", "book", "novel")) and "favicon" not in path:
                    cover = src
                    break
        if cover:
            cover = self._make_absolute(cover, url)

        status = ""
        page_text = soup.get_text(" ", strip=True)
        for marker in ("已完结", "完结", "连载中", "连载"):
            if marker in page_text:
                status = "completed" if marker in ("已完结", "完结") else "ongoing"
                break

        tags: list[str] = []
        seen_tags: set[str] = set()
        page_site_markers = list(self._site_markers())
        site_name_meta = soup.select_one("meta[property='og:site_name']")
        if site_name_meta and site_name_meta.get("content"):
            page_site_markers.append(str(site_name_meta.get("content")).strip())
        document_title = soup.find("title")
        if document_title:
            title_text = document_title.get_text(" ", strip=True)
            if " - " in title_text:
                page_site_markers.extend(
                    part.strip()
                    for part in re.split(r"\s+", title_text.rsplit(" - ", 1)[-1])
                    if len(part.strip()) >= 2
                )
        normalized_site_markers = {
            re.sub(r"[^\w\u3400-\u9fff]+", "", marker).lower()
            for marker in page_site_markers
            if marker
        }

        def _add_tag(value: str) -> None:
            value = value.strip().strip("#").strip()
            if not value or len(value) > 20 or value.lower() in (
                "tags", "tag", "标签", "分类", "类别", "类型", "最新章节",
            ):
                return
            normalized = re.sub(r"[^\w\u3400-\u9fff]+", "", value).lower()
            if normalized in normalized_site_markers:
                return
            if value not in seen_tags:
                seen_tags.add(value)
                tags.append(value)

        meta_keywords = soup.find(
            "meta",
            attrs={"name": re.compile(r"^keywords$", re.I)},
        )
        if meta_keywords and meta_keywords.get("content"):
            content = str(meta_keywords["content"]).replace("，", ",")
            for part in content.split(","):
                _add_tag(part)

        for selector in (
            ".tags a",
            ".tag a",
            "[class*='tag'] a",
            "[class*='kind'] a",
            "[class*='category'] a",
        ):
            for link in soup.select(selector):
                _add_tag(link.get_text(" ", strip=True))

        # Legado forum sources commonly expose tags as plain text rather than
        # links, for example: `标签：#奇幻 #后宫 #异世界`.
        for match in re.finditer(
            r"(?:标签|標籤|关键词|關鍵詞)\s*[:：]\s*([^\n\r]{1,240})",
            soup.get_text("\n", strip=True),
            re.IGNORECASE,
        ):
            raw_tags = match.group(1)
            for part in re.split(r"[#＃,，、;；|\s]+", raw_tags):
                _add_tag(part)

        for link in soup.select("a[href]"):
            href = (link.get("href") or "").strip()
            if not href or href.startswith("javascript:"):
                continue
            abs_href = self._make_absolute(href, url)
            path = urlparse(abs_href).path.lower()
            if any(
                segment in path
                for segment in (
                    "/tag/", "/tags/", "/booktag/", "/booktags/",
                    "/category/", "/categories/", "/fenlei/", "/sort/",
                )
            ):
                _add_tag(link.get_text(" ", strip=True))

        chapter_links: list[Tag] = []
        for selector in GENERIC_CHAPTER_SELECTORS:
            links = soup.select(selector)
            if len(links) >= 2:
                chapter_links = links
                break

        if not chapter_links:
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").strip()
                if not href or href == "#" or href.startswith("javascript:"):
                    continue
                abs_url = self._make_absolute(href, url)
                if self._is_chapter_url(abs_url, url):
                    chapter_links.append(a)

        chapters: list[RemoteChapter] = []
        seen_urls: set[str] = set()
        chapter_number = 0
        skip_titles = {"目录", "简介", "上一章", "下一章", "返回目录", "首页"}
        for a in chapter_links:
            text = a.get_text(" ", strip=True)
            if not text or text in skip_titles or len(text) > 80:
                continue
            href = (a.get("href") or "").strip()
            if not href or href == "#" or href.startswith("javascript:"):
                continue
            abs_url = self._make_absolute(href, url)
            if abs_url in seen_urls:
                continue
            if not self._is_chapter_url(abs_url, url):
                continue
            seen_urls.add(abs_url)
            chapter_number += 1
            chapters.append(RemoteChapter(
                source_chapter_id=abs_url,
                title=text,
                url=abs_url,
                chapter_number=chapter_number,
            ))

        return {
            "title": title,
            "author": author,
            "description": description,
            "cover": cover,
            "status": status,
            "tags": self._clean_tags(tags, title, author),
            "chapters": chapters,
        }

    def _site_markers(self) -> list[str]:
        """Return source/site names that commonly pollute scraped metadata."""
        markers = [str(self.display_name or "").strip()]
        host = urlparse(self.base_url).netloc or ""
        if host:
            markers.append(host)
            if host.lower().startswith("www."):
                markers.append(host[4:])
            hostname = host[4:] if host.lower().startswith("www.") else host
            domain_label = hostname.split(".", 1)[0].strip()
            if len(domain_label) >= 3:
                markers.append(domain_label)
        markers = [m for m in markers if len(m) >= 2]
        return sorted(set(markers), key=len, reverse=True)

    def _clean_book_title(self, title: str | None) -> str:
        title = str(title or "").strip().strip("《》").strip()
        if not title:
            return ""
        for marker in self._site_markers():
            match = re.search(
                rf"[-_|\s(]*[^\-_|\n)]*{re.escape(marker)}[^\-_|\n)]*\)?$",
                title,
                re.IGNORECASE,
            )
            if not match or match.start() <= 0:
                continue
            cleaned = title[: match.start()].strip(" -_|")
            cleaned = re.sub(r"[-_|][^-_|]+$", "", cleaned).strip()
            if cleaned:
                title = cleaned
                break
        title = re.sub(
            r"[-_|]\s*(?:最新章节|全文阅读|免费阅读|小说|最新更新)\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        title = re.sub(
            r"\s+(?:作\s*者|著\s*者|author)\s*[:：].*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        return title

    @staticmethod
    def _extract_labelled_author(soup: BeautifulSoup) -> str:
        """Extract an author that the page explicitly identifies as the work's author."""
        for meta in soup.select(
            "meta[property='og:novel:author'], "
            "meta[name='og:novel:author'], "
            "meta[property='article:author']"
        ):
            content = (meta.get("content") or "").strip()
            if content:
                return content.split(",")[0].strip()

        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.get_text(strip=True))
                author = data.get("author") if isinstance(data, dict) else None
                if isinstance(author, dict):
                    author = author.get("name")
                if author:
                    return str(author).strip()
            except (json.JSONDecodeError, AttributeError, ValueError):
                continue

        description_texts: list[str] = []
        for meta in soup.select(
            "meta[name='description'], "
            "meta[property='og:description'], "
            "meta[name='keywords'], "
            "meta[property='og:title']"
        ):
            content = (meta.get("content") or "").strip()
            if content:
                description_texts.append(content)
        for text in description_texts:
            match = re.search(
                r"由(?:作家|作者|著者)[:：]?\s*([^，。,.、|]{1,60}?)(?:创作|撰写|著|提供|，|。|,|\.|$)",
                text,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()
            match = re.search(
                r"(?:作者|著者)[:：]\s*([^，。,.、|]{1,60})",
                text,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()

        for node in soup.find_all(string=True):
            if node.parent is not None and node.parent.name in ("script", "style"):
                continue
            text = str(node).strip()
            if not text or len(text) > 80:
                continue
            match = re.search(
                r"(?:作\s*者|作者|著者|author)\s*[:：]\s*([^\n<]{1,60})",
                text,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()

        return ""

    @staticmethod
    def _extract_author_from_text(soup: BeautifulSoup) -> str:
        """Author extraction with generic document-author metadata as fallback."""
        labelled = YueduPlugin._extract_labelled_author(soup)
        if labelled:
            return labelled
        # On forum pages this usually names the submitter, so it must lose to
        # any explicitly labelled author in the title, description, or body.
        for meta in soup.select("meta[name='author']"):
            content = (meta.get("content") or "").strip()
            if content:
                return content.split(",")[0].strip()
        return ""

    def _clean_author(self, author: str | None) -> str:
        text = str(author or "").strip()
        if not text:
            return ""
        candidates: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(
                r"^(?:作\s*者|作者|著者|author)\s*[:：]\s*",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            line = re.sub(
                r"(?:作\s*者|著者|author)\s*[:：]\s*.*$",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
            line = re.split(
                r"(?:字\s*数|状\s*态|分\s*类|类\s*别|更\s*新|最新章节|简介)\s*[:：]?",
                line,
                maxsplit=1,
            )[0].strip()
            line = re.sub(r"\s+", " ", line).strip(" -_|")
            for marker in self._site_markers():
                line = re.sub(
                    rf"\s*[-_|]?\s*{re.escape(marker)}.*$",
                    "",
                    line,
                    flags=re.IGNORECASE,
                ).strip()
            if line and not self._looks_like_invalid_author(line):
                candidates.append(line)
        if not candidates:
            return ""
        return min(candidates, key=len)[:120]

    @staticmethod
    def _looks_like_invalid_author(author: str | None) -> bool:
        text = str(author or "").strip()
        if not text or text.lower() in ("unknown", "未知", "暂无", "无"):
            return True
        if len(text) < 2:
            return True
        if re.fullmatch(r"\d{2,}", text):
            return True
        if re.search(r"(?:字数|状态|分类|类别)", text):
            return True
        return False

    @staticmethod
    def _split_kind_text(raw_kind: Any) -> list[str]:
        """Split a kind rule result into discrete tag candidates.

        Some sources return a whole info line such as
        `分类：都市 作者：某某 字数：10万`, which must not become one giant
        tag. Known metadata labels are turned into separators first.
        """
        if isinstance(raw_kind, (list, tuple)):
            parts: list[str] = []
            for value in raw_kind:
                parts.extend(YueduPlugin._split_kind_text(value))
            return parts
        text = str(raw_kind or "")
        text = re.sub(
            r"(?:分\s*类|类\s*别|类\s*型|字\s*数|状\s*态|作\s*者|著\s*者|更新\s*时间|简\s*介|最新章节)\s*[:：]?",
            "|",
            text,
            flags=re.IGNORECASE,
        )
        parts = [
            part.strip().strip("#").strip()
            for part in re.split(r"[,，、;；\n|]+", text)
            if part.strip().strip("#").strip()
        ]
        cleaned: list[str] = []
        for part in parts:
            if re.fullmatch(r"[\d.,]+\s*[万千]?(?:字|万|k|w)?", part, re.IGNORECASE):
                continue
            if part in ("未知", "暂无", "连载", "完结"):
                continue
            cleaned.append(part)
        return cleaned

    def _clean_tags(
        self,
        tags: list[str],
        title: str = "",
        author: str = "",
    ) -> list[str]:
        """Drop title/author/site noise that generic parsers add as tags."""
        def _normalize(value: str) -> str:
            return re.sub(
                r"[\s《》「」『』〈〉（）【】\[\]\"'“”‘’]+",
                "",
                value,
            ).lower()

        title_norm = _normalize(title) if title and title.lower() != "unknown" else ""
        author_norm = (
            _normalize(author) if author and author.lower() != "unknown" else ""
        )
        markers = self._site_markers()
        protected_tags = {"漫画", "漫畫", "写真", "寫真", "图集", "圖集"}
        noise = {
            "tags", "tag", "标签", "分类", "类别", "类型",
            "论坛", "论坛帖子", "帖子", "书源", "书籍",
            "最新章节", "最新章节列表", "全文阅读", "免费阅读", "阅读更多",
            "书友正在看", "大家都在看", "上一章", "下一章", "目录",
            "返回目录", "首页", "开始阅读", "小说", "本站",
        }
        title_noise = {"最新章节", "全文", "全文阅读", "免费阅读", "小说", "最新更新"}
        result: list[str] = []
        for tag in tags:
            tag = str(tag or "").strip().strip("#").strip()
            if not tag:
                continue
            normalized = _normalize(tag)
            if not normalized:
                continue
            if title_norm:
                if normalized == title_norm:
                    continue
                if title_norm in normalized:
                    remainder = _normalize(normalized.replace(title_norm, ""))
                    if not remainder:
                        continue
                    if author_norm and author_norm in remainder:
                        continue
                    if remainder in noise or any(
                        token in remainder for token in title_noise
                    ):
                        continue
            if author_norm:
                if normalized == author_norm:
                    continue
                if normalized in author_norm and len(normalized) >= 2:
                    continue
                if author_norm in normalized:
                    continue
            if tag.lower() in noise:
                continue
            if any(marker and marker.lower() in tag.lower() for marker in markers):
                continue
            if len(tag) > 20:
                continue
            result.append(tag)
        return list(dict.fromkeys(result))

    def _pick_cover_url(self, value: Any, base_url: str | None = None) -> str:
        """Pick the first usable cover URL from a rule result.

        Legado sources commonly use `img@src||fallback`, which can match every
        image on the page. A single scalar field must resolve to one URL, not a
        newline/concatenated list.
        """
        if isinstance(value, (list, tuple)):
            candidates = [str(v).strip() for v in value if str(v).strip()]
        else:
            candidates = [
                line.strip()
                for line in str(value or "").splitlines()
                if line.strip()
            ]
        base = base_url or self.base_url
        for candidate in candidates:
            if candidate.startswith(("data:", "javascript:", "about:")):
                continue
            absolute = self._make_absolute(candidate, base)
            if not absolute.startswith(("http://", "https://")):
                continue
            if self._looks_like_placeholder_cover(absolute):
                continue
            return absolute
        return ""

    @staticmethod
    def _looks_like_placeholder_cover(url: str) -> bool:
        path = urlparse(url).path.lower()
        name = path.rsplit("/", 1)[-1]
        return (
            "favicon" in path
            or path.endswith(".svg")
            or any(seg in path for seg in ("/template/", "/static/", "/images/", "/img/"))
            or name in (
                "logo.png", "logo.svg", "logo.jpg", "logo.webp",
                "default.jpg", "default.png", "nopic.jpg",
                "no-cover.jpg", "no-cover.png",
            )
        )

    # ---- Required: fetch_chapter_content ----

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        """Fetch and return the full text of a single chapter.

        Supports multi-page chapters via nextContentUrl rule.
        Uses webJs for JS-rendered pages when configured.
        """
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")

        # A plugin can fetch several chapters concurrently. The shared engine
        # stores page-scoped variables (baseUrl/bookUrl), so sharing it here
        # lets one in-flight request overwrite another chapter's context.
        # Use a private engine for the complete parse of this chapter.
        chapter_engine = YueduRuleEngine(self.config)
        chapter_engine.set_page_url(chapter.url)
        web_js = chapter_engine.get_web_js()
        if web_js:
            html = await self._get_with_web_js(chapter.url, web_js)
        else:
            html = await self._get(chapter.url)
        chapter_engine.set_chapter_context({
            "title": str(getattr(chapter, "title", "") or ""),
            "url": chapter.url,
            "tag": str(getattr(chapter, "tags", "") or ""),
        })
        generic_content = self._parse_chapter_content_generic(html)
        if self._uses_android_js_rule("ruleContent", "content"):
            content = generic_content
        else:
            try:
                content = chapter_engine.parse_content(html)
            except Exception:
                content = ""
            if not content or self._looks_like_rule_diagnostic(content, html):
                content = generic_content
        parts = [content] if content else []

        # Follow nextContentUrl for multi-page chapters
        max_pages = 20  # safety limit
        seen_content_urls = {chapter.url}
        content_semaphore = asyncio.Semaphore(self._thread_count())

        async def _fetch_content_page(page_url: str) -> str:
            async with content_semaphore:
                if web_js:
                    return await self._get_with_web_js(page_url, web_js)
                return await self._get(page_url)

        pending_content_urls = [
            url
            for url in chapter_engine.get_next_content_urls(html, chapter.url)
            if (
                url not in seen_content_urls
                and url.startswith(("http://", "https://"))
            )
        ]
        seen_content_urls.update(pending_content_urls)
        pages_fetched = 0
        while pending_content_urls and pages_fetched < max_pages:
            eligible = [
                url
                for url in pending_content_urls
                if not (
                    getattr(chapter, "next_url", None)
                    and url == chapter.next_url
                )
            ]
            batch = eligible[: max_pages - pages_fetched]
            pending_content_urls = eligible[max_pages - pages_fetched:]
            if not batch:
                break
            htmls = await asyncio.gather(
                *(_fetch_content_page(page_url) for page_url in batch)
            )
            for next_url, next_html in zip(batch, htmls):
                if pages_fetched >= max_pages:
                    break
                chapter_engine.set_page_url(next_url)
                if self._uses_android_js_rule("ruleContent", "content"):
                    next_part = self._parse_chapter_content_generic(next_html)
                else:
                    try:
                        next_part = chapter_engine.parse_content(next_html)
                    except Exception:
                        next_part = ""
                    if not next_part or self._looks_like_rule_diagnostic(next_part, next_html):
                        next_part = self._parse_chapter_content_generic(next_html)
                if next_part and next_part != next_html:
                    parts.append(next_part)
                pages_fetched += 1
                pending_content_urls.extend(
                    url
                    for url in chapter_engine.get_next_content_urls(next_html, next_url)
                    if (
                        url not in seen_content_urls
                        and url.startswith(("http://", "https://"))
                    )
                )
            seen_content_urls.update(pending_content_urls)

        content = "\n".join(parts)

        if content and ("<" in content or ">" in content):
            try:
                content = self._content_text_preserving_images(content)
            except Exception:
                pass

        if not content:
            content = self._parse_chapter_content_generic(html)
        content = content.strip()
        if self._content_is_blocked(content):
            raise RuntimeError(
                "Chapter content looks like an anti-bot/captcha page "
                f"(site asked for verification): {chapter.url}"
            )
        replace_rules = (self.config.get("ruleContent") or {}).get("replaceRegex", [])
        if replace_rules:
            content = chapter_engine._apply_replace_regex(content, replace_rules).strip()
        if not content:
            raise RuntimeError(
                f"Chapter returned empty content: {chapter.url}"
            )
        return content

    @staticmethod
    def _content_is_blocked(text: str) -> bool:
        """Reject extracted chapter text that is really an anti-bot page."""
        if not text:
            return False
        lowered = text.lower()
        strong = (
            "输入验证码后可继续访问",
            "请完成验证",
            "安全验证",
            "人机验证",
            "滑动验证",
            "验证码后可继续访问",
            "limit_box",
            "访问过于频繁",
            "请求过于频繁",
            "操作过于频繁",
            "请稍后再试",
        )
        if any(marker in lowered for marker in strong):
            return True
        if "访问异常" in lowered:
            return any(confirm in lowered for confirm in (
                "验证码", "继续访问", "稍后", "频繁", "限流",
            ))
        return False

    @staticmethod
    def _clean_extracted_text(text: str) -> str:
        """Drop comment-count artifacts (lines that are pure short digits).

        Many chapter sites render a ``<span class=\"z count_N\">0</span>``
        comment counter inside every paragraph; ``get_text`` would include
        the counter as its own line.  A real novel paragraph is never just
        a 1-3 digit number, so such lines are dropped.
        """
        kept = []
        for line in str(text or "").split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if re.fullmatch(r"\d{1,3}", stripped):
                continue
            kept.append(stripped)
        return "\n".join(kept).strip()

    @staticmethod
    def _looks_like_rule_diagnostic(content: str, html: str) -> bool:
        """Do not persist failed script output as a chapter body."""
        value = str(content or "").strip()
        if not value:
            return True
        if value == str(html or "").strip():
            return True
        return any(marker in value for marker in (
            "org.jsoup", "Packages.", "java.lang.", "ReferenceError:",
        ))

    def _parse_chapter_content_generic(self, html: str) -> str:
        """Extract readable text when the configured content rule misses."""
        soup = BeautifulSoup(html, "lxml")
        content_selectors = (
            "#content-section pre",
            "#content-section",
            ".content-section pre",
            ".content-section",
            ".chapter_content_box",
            "#content",
            "#chapter-content",
            ".page-content pre",
            ".page-content",
            "article",
            "div.content",
            ".chapter-content",
            ".read-content",
            ".reader-content",
            ".article",
            "main",
            ".post-content",
            ".entry-content",
            ".article-content",
            ".read-main",
            "#read-content",
            ".book-content",
            ".text-content",
            ".novel-content",
            ".readContent",
            ".Readarea",
            "#chapter",
            ".chapter",
            ".txt",
        )
        for selector in content_selectors:
            el = soup.select_one(selector)
            if el is None:
                continue
            for tag in el.find_all(["script", "style", "ins", "nav", "header", "footer"]):
                tag.decompose()
            text = self._clean_extracted_text(
                self._content_text_preserving_images(str(el))
            )
            if text:
                return text
        return ""

    @staticmethod
    def _content_text_preserving_images(content: str) -> str:
        """Convert parsed chapter HTML to text while keeping image references."""
        soup = BeautifulSoup(content, "lxml")
        for tag in soup.find_all(
            ["script", "style", "ins", "nav", "header", "footer", "iframe"],
        ):
            tag.decompose()
        for img in soup.find_all("img"):
            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-original")
                or ""
            ).strip()
            if not src:
                img.decompose()
                continue
            alt = img.get("alt") or ""
            img.replace_with(f"![{alt}]({src})")
        return YueduPlugin._clean_extracted_text(soup.get_text("\n", strip=True))

    # ---- Required: fetch_bookshelf ----

    async def fetch_bookshelf(self, cookie: str) -> list[RemoteShelfBook]:
        """Fetch user bookshelf by trying the configured URL or auto-detecting it.

        Strategy:
        1. Use bookshelf_url from config if available
        2. Otherwise try common bookshelf path patterns
        3. Parse the first successful response
        """
        self._cookie = cookie
        bookshelf_url = self.config.get("bookshelf_url", "")

        if bookshelf_url:
            # Use the stored bookshelf URL
            try:
                html = await self._get(bookshelf_url)
                books = self._parse_bookshelf_html(html)
                if books:
                    return books
            except Exception as e:
                logger.warning(f"Configured bookshelf URL failed: {e}")

        # Try common paths
        for path in BOOKSHELF_PATH_CANDIDATES:
            url = urljoin(self.base_url, path)
            try:
                html = await self._get(url)
                books = self._parse_bookshelf_html(html)
                if books:
                    # Save the working bookshelf URL for future use
                    self.config["bookshelf_url"] = url
                    logger.info(f"Auto-detected bookshelf URL: {url}")
                    return books
            except Exception:
                continue

        # Last resort: try the homepage (some sites show bookshelf there)
        try:
            html = await self._get(self.base_url)
            books = self._parse_bookshelf_html(html)
            if books:
                self.config["bookshelf_url"] = self.base_url
                return books
        except Exception:
            pass

        # Try auto-detection by scanning the homepage for bookshelf links
        try:
            detected_url = await self.detect_bookshelf_url()
            if detected_url:
                html = await self._get(detected_url)
                books = self._parse_bookshelf_html(html)
                if books:
                    self.config["bookshelf_url"] = detected_url
                    logger.info(f"Auto-detected bookshelf URL in fetch: {detected_url}")
                    return books
        except Exception:
            pass

        logger.warning(f"No bookshelf found for source {self.display_name}")
        return []

    # ---- Generic bookshelf HTML parser ----

    def _parse_bookshelf_html(
        self,
        html: str,
        base_url: str | None = None,
    ) -> list[RemoteShelfBook]:
        """Parse a bookshelf/list page using common CSS patterns."""
        soup = BeautifulSoup(html, "lxml")
        resolve_base = base_url or self.base_url
        items: list[Tag] = []

        # Try each selector pattern
        for selector in SHELF_ITEM_SELECTORS:
            candidates = soup.select(selector)
            if candidates:
                items = candidates
                break

        if not items:
            # Last resort: any link that looks like a book title
            for a_tag in soup.select("a[href]"):
                href = (a_tag.get("href") or "").strip()
                if not href or href in ("#", "/"):
                    continue
                txt = a_tag.get_text(strip=True)
                if txt and len(txt) >= 2 and len(txt) <= 60:
                    if any(seg in href.lower() for seg in ["/book/", "/novel/", "/read/", "/detail/", "/info/", "/article/", "/xiaoshuo/"]):
                        items.append(a_tag)
            if not items:
                return []

        books: list[RemoteShelfBook] = []
        for item in items:
            link_el = None
            if item.name == "a" and item.get("href"):
                link_el = item
            else:
                for sel in SHELF_LINK_SELECTORS:
                    link_el = item.select_one(sel)
                    if link_el and link_el.get("href"):
                        break

            if not link_el or not link_el.get("href"):
                continue

            href = link_el.get("href", "").strip()
            if not href or href == "#" or href == "/":
                continue

            title = link_el.get_text(strip=True)
            if not title or len(title) < 2:
                continue

            # Filter out navigation links
            skip_titles = {"首页", "上一页", "下一页", "末页", "home", "next", "prev", "login", "注册"}
            if title.lower() in {t.lower() for t in skip_titles}:
                continue

            # Try to find author
            author = "Unknown"
            for sel in SHELF_AUTHOR_SELECTORS:
                author_el = item.select_one(sel)
                if author_el:
                    author = self._clean_author(author_el.get_text(strip=True)) or "Unknown"
                    break

            # Try to find latest chapter title
            latest = None
            for sel in [
                "span.latest", "span[class*='latest']", "span[class*='update']",
                "span.new", "span[class*='new']",
                "p:last-child span", "span:last-child",
            ]:
                latest_el = item.select_one(sel)
                if latest_el:
                    txt = latest_el.get_text(strip=True)
                    if txt and len(txt) > 2 and txt != title:
                        latest = txt
                        break

            full_url = self._make_absolute(href, resolve_base)
            if not full_url.startswith(("http://", "https://")):
                continue
            if not self._is_book_url(full_url, require_pattern=True):
                continue

            # Skip non-book URLs: search, tag, category, author, user pages
            skip_patterns = ["/search/", "/tag/", "/tags/", "/category/", "/categories/",
                           "/author/", "/user/", "/users/", "/login", "/register",
                           "/signup", "/about", "/help", "/faq", "/contact"]
            if any(p in full_url.lower() for p in skip_patterns):
                continue
            nav_paths = ["/rank", "/top", "/sort", "/allvisit", "/lastupdate",
                         "/update", "/new", "/finish", "/quanben", "/wanben",
                         "/bookcase", "/bookshelf", "/history", "/index", "/list", "/page"]
            path = urlparse(full_url).path.lower()
            if any(self._is_nav_path(path, p) for p in nav_paths):
                continue

            book_id = self._book_id_from_url(full_url)
            path = urlparse(full_url).path.lower()
            last_segment = book_id.rsplit("/", 1)[-1] if "/" in book_id else book_id
            # Skip empty IDs or bare numeric category IDs, but keep numeric
            # book IDs under /novel/ or /book/ paths.
            if not last_segment or (
                last_segment.isdigit()
                and not any(
                    seg in path
                    for seg in ("/novel/", "/book/", "/read/", "/detail/")
                )
            ):
                continue

            books.append(RemoteShelfBook(
                source_book_id=book_id,
                title=title,
                author=author,
                url=full_url,
                latest_chapter_title=latest,
            ))

        # Merge in every book-looking anchor.  This catches list pages whose
        # wrapper markup is too generic for the item selectors above.
        seen_urls = {book.url for book in books}
        for book in self._parse_direct_anchor_books(soup, resolve_base):
            if book.url not in seen_urls:
                seen_urls.add(book.url)
                books.append(book)

        return books

    def _parse_direct_anchor_books(
        self,
        soup: BeautifulSoup,
        base_url: str,
    ) -> list[RemoteShelfBook]:
        """Scan all anchors for book-like links without relying on wrappers."""
        books: list[RemoteShelfBook] = []
        seen_urls: set[str] = set()
        skip_titles = {"首页", "上一页", "下一页", "末页", "home", "next", "prev", "login", "注册"}
        nav_paths = ["/rank", "/top", "/sort", "/allvisit", "/lastupdate",
                     "/update", "/new", "/finish", "/quanben", "/wanben",
                     "/bookcase", "/bookshelf", "/history", "/index", "/list", "/page"]
        skip_patterns = ["/search/", "/tag/", "/tags/", "/category/", "/categories/",
                         "/author/", "/user/", "/users/", "/login", "/register",
                         "/signup", "/about", "/help", "/faq", "/contact"]
        for a_tag in soup.select("a[href]"):
            href = (a_tag.get("href") or "").strip()
            if not href or href == "#" or href.startswith("javascript:"):
                continue
            full_url = self._make_absolute(href, base_url)
            if not full_url.startswith(("http://", "https://")):
                continue
            if not self._is_book_url(full_url, require_pattern=True):
                continue
            path = urlparse(full_url).path.lower()
            if any(self._is_nav_path(path, p) for p in nav_paths):
                continue
            if any(p in full_url.lower() for p in skip_patterns):
                continue
            if full_url in seen_urls:
                continue

            title = a_tag.get_text(" ", strip=True)
            if not title:
                title = a_tag.get("title") or a_tag.get("alt") or ""
            if not title and a_tag.parent is not None:
                heading = a_tag.parent.select_one("h3, h2, h4, .book-name, .book-title")
                title = heading.get_text(" ", strip=True) if heading else ""
            title = title.strip()
            if not title or len(title) < 2 or title.lower() in skip_titles:
                continue

            book_id = self._book_id_from_url(full_url)
            last_segment = book_id.rsplit("/", 1)[-1] if "/" in book_id else book_id
            if not last_segment or (
                last_segment.isdigit()
                and not any(seg in path for seg in ("/novel/", "/book/", "/read/", "/detail/"))
            ):
                continue

            author = "Unknown"
            if a_tag.parent is not None:
                for sel in SHELF_AUTHOR_SELECTORS:
                    author_el = a_tag.parent.select_one(sel)
                    if author_el:
                        author = self._clean_author(
                            author_el.get_text(" ", strip=True)
                        ) or "Unknown"
                        break

            seen_urls.add(full_url)
            books.append(RemoteShelfBook(
                source_book_id=book_id,
                title=title,
                author=author,
                url=full_url,
                latest_chapter_title=None,
            ))

        return books

    @staticmethod
    def _is_nav_path(path: str, nav: str) -> bool:
        """Match a nav path as a segment, not as a substring."""
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.endswith(".html"):
            path = path[:-5]
        return path == nav or path.startswith(nav + "/")

    def _is_book_url(self, url: str, require_pattern: bool = False) -> bool:
        """Check whether a URL points to a book detail page.

        When the source defines `bookUrlPattern`, discovery uses it strictly
        for the same host so category/chapter links are not mistaken for books.
        """
        url = self._strip_url_options_suffix(url)
        pattern = self.config.get("bookUrlPattern", "")
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
        for prefix in ("novel", "book", "read", "detail", "xiaoshuo"):
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
            return False

        same_host = (
            parsed.netloc.lower() == urlparse(self.base_url).netloc.lower()
        )
        segments = [segment for segment in path.split("/") if segment]

        if same_host:
            if len(segments) < 2:
                return False
            if any(segment in NAV_PATH_SEGMENTS for segment in segments):
                return False
            return True

        # External links are usually ads/mirror links on Chinese novel sites.
        # Only accept them when the path clearly looks like a chapter page.
        if len(segments) < 3:
            return False
        if any(segment in NAV_PATH_SEGMENTS for segment in segments):
            return False
        return any(segment in CHAPTER_PATH_SEGMENTS for segment in segments)

    # ---- Bookshelf URL auto-detection ----

    async def detect_bookshelf_url(self) -> str | None:
        """Scan the source homepage for a bookshelf link and return its URL."""
        try:
            html = await self._get(self.base_url)
            soup = BeautifulSoup(html, "lxml")

            # Look for navigation links whose text matches bookshelf patterns
            all_links = soup.find_all("a")
            for link in all_links:
                text = link.get_text(strip=True).lower()
                for pattern in BOOKSHELF_LINK_PATTERNS:
                    if pattern.lower() in text:
                        href = link.get("href", "").strip()
                        if href:
                            full_url = self._make_absolute(href, self.base_url)
                            logger.info(f"Detected bookshelf link: '{text}' -> {full_url}")
                            return full_url
        except Exception as e:
            logger.warning(f"Bookshelf URL detection failed: {e}")

        return None

    # ---- Login check ----

    def check_login_status(self, html: str) -> bool:
        """Check if the current login session is still valid.

        Uses loginCheckJs from the book source config. Returns True
        if login is valid, False if redirect/logout is detected.
        Now uses real JS runtime for loginCheckJs evaluation.
        """
        check_js = self.config.get("loginCheckJs", "")
        if not check_js or not check_js.strip():
            return True  # No check configured, assume valid

        if self.engine:
            return self.engine.eval_login_check_js(check_js, html)

        # Fallback to pattern evaluation
        try:
            result = try_eval_js_pattern(check_js, html)
            if result is not None:
                result_str = str(result).strip().lower()
                if any(w in result_str for w in ("login", "logout", "redirect", "false", "0", "null", "undefined")):
                    logger.warning(f"Login check failed for {self.display_name}: {result_str[:100]}")
                    return False
        except Exception as e:
            logger.warning(f"Login check error for {self.display_name}: {e}")

        return True  # Assume valid on error

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


    # ---- Explore / Discover ----

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
            results = []
            blocked_errors: list[str] = []
            unavailable_errors: list[str] = []
            for kind in kinds:
                kind_url = str(kind.get("url", "")).strip()
                if not kind_url:
                    continue
                try:
                    resolved, options = self._resolve_kind(kind_url, page)
                except Exception as exc:
                    logger.warning(f"Explore kind URL failed: {kind_url} ({exc})")
                    continue
                if not resolved:
                    continue
                try:
                    results.extend(await self._fetch_explore_url(
                        resolved,
                        explore_kind=kind.get("title", ""),
                        options=options,
                    ))
                except Exception as exc:
                    message = str(exc)
                    if (
                        "anti-bot" in message.lower()
                        or "captcha" in message.lower()
                        or "验证码" in message
                        or "身份验证" in message
                    ):
                        blocked_errors.append(message)
                    response = getattr(exc, "response", None)
                    status_code = getattr(response, "status_code", None)
                    if status_code is not None or any(
                        marker in message.lower()
                        for marker in ("403", "404", "408", "429", "500", "502", "503", "504", "520")
                    ):
                        unavailable_errors.append(message)
                    logger.warning(
                        f"Explore kind failed: {kind.get('title', kind_url)} ({exc})"
                    )
            if not results and blocked_errors:
                # Every discover category was gated by an anti-bot / captcha
                # page. Surface the first one so crawl tasks show a real
                # error instead of a misleading "0 books found".
                raise RuntimeError(blocked_errors[0])
            if not results and unavailable_errors:
                raise RuntimeError(
                    "书源目录暂时不可访问：" + unavailable_errors[0]
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

    async def _fetch_explore_url(
        self,
        explore_url: str,
        explore_kind: str = "",
        options: dict[str, Any] | None = None,
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
        items = self._explore_items_from_html(html, request_url)
        if not explore_kind:
            return items
        return [
            {**item, "exploreKind": explore_kind}
            for item in items
        ]

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
            filtered_items = [
                item for item in normalized_items
                if self._is_book_url(
                    self._explore_item_url(item),
                    require_pattern=True,
                )
            ]
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

    # ---- Optional: update_book ----

    async def discover_books(self, url: str | None = None, page: int = 1) -> list[RemoteShelfBook]:
        """Discover books from a source's explore/catalog pages.

        Wraps the existing fetch_explore method to return RemoteShelfBook
        objects compatible with the NovelSourcePlugin protocol.
        """
        items = await self.fetch_explore(url=url, page=page)
        link_base = self._make_absolute(url, self.base_url) if url else self.base_url
        books: list[RemoteShelfBook] = []
        for item in items:
            book_url = self._explore_item_url(item)
            if not book_url:
                continue
            full_url = self._make_absolute(book_url, link_base)
            if not full_url.startswith(("http://", "https://")):
                continue
            if not self._is_book_url(full_url, require_pattern=True):
                continue
            books.append(RemoteShelfBook(
                source_book_id=self._book_id_from_url(full_url),
                title=str(item.get("name") or item.get("title") or book_url).strip() or "Unknown",
                author=self._clean_author(str(item.get("author") or "").strip()) or "Unknown",
                url=full_url,
                latest_chapter_title=item.get("latestChapterTitle"),
                tags=self._clean_tags(
                    self._split_kind_text(item.get("kind"))
                    + self._split_kind_text(item.get("exploreKind")),
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
            if not self._is_book_url(full_url, require_pattern=True):
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

    @staticmethod
    def _parse_url_options(rule_url: str) -> dict[str, Any] | None:
        """Split a Legado URL option suffix (`,{...}`) from the request URL."""
        match = re.search(r"\s*,\s*(\{.*)$", rule_url, re.DOTALL)
        if not match:
            return None
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
        if not match:
            return url
        try:
            json.loads(match.group(1))
        except (ValueError, TypeError):
            return url
        return url[: match.start()].strip()

    async def update_book(self, url: str) -> RemoteBook | None:
        """Re-fetch a book to check for new chapters."""
        try:
            return await self.fetch_book(url)
        except Exception as e:
            logger.error(f"YueduPlugin.update_book failed: {e}")
            return None

    # ---- Cookie support ----

    def set_cookie(self, cookie: str) -> None:
        """Set cookie for authenticated requests."""
        self._cookie = cookie

    async def auto_login(self, username: str, password: str) -> str | None:
        """Attempt auto-login using the source loginUrl mechanism.

        Tries three approaches in order:
        1. Regex-based API login (fast path for simple java.post patterns)
        2. Node.js JS runtime (executes login JS with java.* stubs)
        3. Playwright form login (opens login page, fills form, submits)

        Falls back to None only if all three approaches fail.
        """
        from app.crawler.plugins.yuedu.login import YueduLoginParser
        from app.crawler.plugins.yuedu.js_runtime import JsRuntime

        js_runtime = None
        try:
            js_runtime = JsRuntime.get_instance()
        except Exception:
            pass

        parser = YueduLoginParser(self.config, js_runtime=js_runtime)

        # execute_login now has all three fallbacks built in
        cookie = await parser.execute_login(username, password)
        if cookie:
            return cookie

        logger.warning(f"All login methods failed for {self.display_name}")
        return None

    # ---- Helpers ----

    async def _get_with_web_js(
        self,
        url: str,
        web_js: str = "",
        *,
        request_headers: dict[str, str] | None = None,
        fallback_http: bool = True,
    ) -> str:
        """Fetch a page that requires JavaScript rendering (webJs).

        Uses Playwright to load the page in a headless browser,
        execute the webJs script, and return the resulting HTML.
        
        Falls back to plain HTTP GET if Playwright is unavailable
        or if the webJs execution fails.
        """
        import asyncio

        web_js = str(web_js or "").strip()
        clean_url, url_options = self._split_options_suffix(url)
        if url_options:
            # ``webView:true`` means the site only serves the page to a real
            # browser (e.g. 要撸小说 / forum sources).  A plain-HTTP fallback
            # would just re-request a challenge page, so force it off here
            # regardless of what the caller passed.
            if url_options.get("web_view"):
                fallback_http = False
            if url_options.get("web_js") and not web_js:
                web_js = str(url_options.get("web_js"))
            if url_options.get("headers"):
                merged_headers = dict(request_headers or {})
                merged_headers.update(url_options.get("headers"))
                request_headers = merged_headers
            url = clean_url

        # Try Playwright first
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning(
                "playwright not installed; falling back to plain HTTP for webJs"
            )
            if not fallback_http:
                raise RuntimeError(f"Playwright is not installed: {url}")
            return await self._get(url)

        try:
            async with async_playwright() as pw:
                launch_kwargs: dict[str, Any] = {
                    "headless": True,
                    "args": [
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                    ],
                }
                try:
                    from app.services.proxy_config import get_playwright_proxy
                    proxy = get_playwright_proxy()
                    if proxy:
                        launch_kwargs["proxy"] = proxy
                except Exception:
                    pass
                # Prefer the full Chromium build (new headless) over the
                # lightweight headless shell: Cloudflare/WAF fingerprint checks
                # pass far more often against a full browser.  If it is not
                # available, fall back to Playwright's default launch.
                try:
                    browser = await pw.chromium.launch(
                        **launch_kwargs,
                        channel="chromium",
                    )
                except Exception:
                    browser = await pw.chromium.launch(**launch_kwargs)
                try:
                    headers = dict(request_headers or self._build_headers())
                    user_agent = headers.pop("User-Agent", None)
                    # Cookie is installed through the browser cookie jar below.
                    cookie_header = self._merge_cookie_strings(
                        headers.pop("Cookie", ""),
                        self._cookie,
                    )
                    headers.pop("Connection", None)
                    context = await browser.new_context(
                        viewport={"width": 1280, "height": 720},
                        locale="zh-CN",
                        **({"user_agent": user_agent} if user_agent else {}),
                        extra_http_headers=headers,
                    )
                    # Mask common automation fingerprints so challenge pages
                    # do not immediately classify the browser as a headless bot.
                    try:
                        await context.add_init_script(
                            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                            "window.chrome=window.chrome||{runtime:{}};"
                            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
                            "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});"
                        )
                    except Exception:
                        pass
                    page = await context.new_page()

                    # Apply cookies if set
                    if cookie_header:
                        await context.add_cookies(
                            self._parse_cookies_for_playwright(cookie_header)
                        )

                    # WAF-protected sites often keep analytics sockets open forever;
                    # waiting for networkidle turns a usable page into a timeout.
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    # Cloudflare / WAF challenge pages ("Just a moment…") return
                    # before the JS challenge has solved itself.  Wait for the
                    # real page (and the resolved session cookies) before running
                    # any webJs or parsing the content.
                    rendered = await self._wait_for_challenge(
                        context,
                        page,
                        url,
                        timeout=25.0,
                    )
                    if not rendered:
                        raise RuntimeError(
                            "Site returned an empty browser page (网站返回了空白页): "
                            + url
                        )
                    if self._is_blocked_page(rendered):
                        # Even after waiting the challenge never cleared; surface
                        # a clear hint instead of parsing the WAF gate as content.
                        raise RuntimeError(
                            "Site returned an anti-bot/captcha page (网站要求验证码/人机验证，"
                            "请在浏览器中访问该网站通过验证后，把 Cookie 导入书源再同步): "
                            + url
                        )
                    self._capture_playwright_cookies(await context.cookies())

                    # Legado webJs may mutate the DOM or return the rendered
                    # HTML directly. Preserve both forms instead of discarding
                    # the script result.
                    if web_js:
                        try:
                            html = await page.evaluate(
                                f"(function(){{ var result=document.documentElement.outerHTML; "
                                f"var value=(function(){{ {web_js} }})(); "
                                f"return (typeof value === 'string' && value.trim()) "
                                f"? value : document.documentElement.outerHTML; }})()"
                            )
                        except Exception as e:
                            logger.warning(f"webJs execution error: {e}")
                            html = rendered
                    else:
                        html = rendered

                    if self._is_blocked_page(html):
                        raise RuntimeError(
                            "Site returned an anti-bot/captcha page (网站要求验证码/人机验证，"
                            "请在浏览器中访问该网站通过验证后，把 Cookie 导入书源再同步): "
                            + url
                        )

                    await context.close()
                    return html
                finally:
                    await browser.close()
        except Exception as e:
            logger.warning(
                f"Playwright webJs fetch failed for {url}: {e}; falling back to HTTP"
            )
            if not fallback_http and "anti-bot/captcha" in str(e):
                raise

        # Fallback: try evaluating webJs on plain HTTP response
        if not fallback_http:
            raise RuntimeError(f"Browser request failed: {url}")

        html = await self._get(url)
        if self.engine:
            result = self.engine.eval_web_js(web_js, html)
            if result and result != html:
                return result
        return html

    def _parse_cookies_for_playwright(
        self,
        cookie_header: str | None = None,
    ) -> list[dict[str, Any]]:
        """Parse cookie string into Playwright cookie format."""
        cookies = []
        cookie_header = str(cookie_header or self._cookie or "")
        if not cookie_header:
            return cookies
        cookie_url = self.base_url.split("##", 1)[0].rstrip("/") + "/"
        for part in cookie_header.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    # Let Playwright derive the host, including www/non-www.
                    "url": cookie_url,
                })
        return cookies

    def _capture_playwright_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Merge cookies set by a browser page into the in-memory cookie string.

        This runs regardless of ``enabledCookieJar`` so that a Cloudflare
        ``cf_clearance`` (and any other session cookie a challenge sets) is
        reused by subsequent plain-HTTP requests.  The merge only mutates the
        in-memory ``self._cookie``; it never writes to the DB cookie store.
        """
        existing = dict(
            (part.split("=", 1)[0].strip(), part.strip())
            for part in self._cookie.split(";")
            if "=" in part
        )
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            if not name:
                continue
            value = str(cookie.get("value") or "")
            existing[name] = f"{name}={value}"
        self._cookie = "; ".join(existing.values())

    def _build_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build request headers from source config, cookies, and per-request extras."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) Mobile Safari/537.36",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie

        http_user_agent = str(self.config.get("httpUserAgent", "") or "").strip()
        if http_user_agent:
            headers["User-Agent"] = http_user_agent

        header_rule = self.config.get("header", "")
        if header_rule:
            try:
                if "JSON.stringify" in header_rule:
                    m = re.search(r'JSON\.stringify\((\{.+?\})\)', header_rule, re.DOTALL)
                    if m:
                        custom_headers = json.loads(m.group(1))
                        headers.update(custom_headers)
                elif header_rule.startswith("{"):
                    custom_headers = json.loads(header_rule)
                    headers.update(custom_headers)
                elif header_rule.startswith("@js:") or "<js>" in header_rule:
                    js = header_rule
                    if js.startswith("@js:"):
                        js = js[4:]
                    m = re.search(r'JSON\.stringify\((\{.+?\})\)', js, re.DOTALL)
                    if not m:
                        m = re.search(
                            r'"(?:User-Agent|Content-Type|Cookie|Referer|Accept)[^}]*}',
                            js,
                            re.IGNORECASE,
                        )
                    if m:
                        try:
                            hdr_str = m.group(0)
                            if not hdr_str.startswith("{"):
                                hdr_str = "{" + hdr_str + "}"
                            hdr_str = re.sub(r'(\w+):', r'"\1":', hdr_str)
                            custom_headers = json.loads(hdr_str)
                            headers.update(custom_headers)
                        except (json.JSONDecodeError, ValueError):
                            pass
            except Exception:
                pass

        if extra:
            headers.update(extra)
        if self._cookie or headers.get("Cookie"):
            headers["Cookie"] = self._merge_cookie_strings(
                headers.get("Cookie", ""),
                self._cookie,
            )
        return headers

    @staticmethod
    def _merge_cookie_strings(*values: str) -> str:
        """Merge Cookie header values without dropping a session cookie."""
        merged: dict[str, str] = {}
        for value in values:
            for part in str(value or "").split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                name, cookie_value = part.split("=", 1)
                name = name.strip()
                if name:
                    merged[name] = f"{name}={cookie_value.strip()}"
        return "; ".join(merged.values())

    def _with_403_fallback(self, headers: dict[str, str]) -> dict[str, str]:
        """Retry 403 responses with a desktop UA and site Referer."""
        fallback = dict(headers)
        fallback["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        fallback.setdefault("Referer", self.base_url.rstrip("/") + "/")
        fallback.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        return fallback

    @staticmethod
    def _looks_polluted(host: str) -> bool:
        """Whether the system-resolved address for host is a loopback/placeholder.

        Chinese sites blocked by the GFW frequently resolve to 127.0.0.1 or
        0.0.0.0 (DNS poisoning). Treating those as unreachable triggers the
        DoH fallback in _get.
        """
        try:
            import socket
            for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
                ip = info[4][0]
                if ip in ("127.0.0.1", "0.0.0.0", "::1"):
                    return True
                if ip.startswith("127."):
                    return True
            return False
        except Exception:
            return False

    async def _resolve_via_doh(self, host: str) -> str | None:
        """Resolve a host through public DoH providers, with in-process cache.

        Returns the first A record found, or None when every provider fails.
        """
        async with self.__class__._doh_lock:
            cached = self.__class__._doh_cache.get(host)
            now = time.time()
            if cached and float(cached.get("expires", 0)) > now:
                return str(cached.get("ip") or "")

        import httpx as _httpx

        last_error: Exception | None = None
        for provider in self.__class__._doh_providers:
            try:
                params = {"name": host, "type": "A"}
                headers = {"accept": "application/dns-json"}
                async with _httpx.AsyncClient(
                    timeout=_httpx.Timeout(8.0),
                    verify=False,
                    trust_env=False,
                ) as client:
                    resp = await client.get(provider, params=params, headers=headers)
                    if resp.status_code != 200:
                        continue
                    payload = resp.json()
                    answers = payload.get("Answer") or []
                    for answer in answers:
                        data = str(answer.get("data", ""))
                        if data and data not in ("127.0.0.1", "0.0.0.0", "::1"):
                            async with self.__class__._doh_lock:
                                self.__class__._doh_cache[host] = {
                                    "ip": data,
                                    "expires": now + self.__class__._doh_ttl,
                                }
                            logger.info(
                                "DoH %s resolved %s -> %s",
                                provider,
                                host,
                                data,
                            )
                            return data
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            logger.warning("DoH resolution failed for %s: %s", host, last_error)
        return None

    def _doh_rewrite(self, url: str) -> tuple[str, str] | None:
        """Rewrite a URL to its DoH-resolved IP, keeping the original host.

        Returns (rewritten_url, original_host) or None when no cached IP
        exists (callers resolve via _resolve_via_doh first).
        """
        parts = urlparse(url)
        cached = self.__class__._doh_cache.get(parts.hostname or "")
        if not cached:
            return None
        ip = str(cached.get("ip") or "")
        if not ip:
            return None
        port = ":" + str(parts.port) if parts.port else ""
        rewritten = parts.scheme + "://" + ip + port + (parts.path or "")
        if parts.query:
            rewritten += "?" + parts.query
        if parts.fragment:
            rewritten += "#" + parts.fragment
        return rewritten, parts.hostname or ""

    def _parse_concurrent_rate(self) -> tuple[str, int, int] | None:
        """Parse Legado concurrentRate: "interval" or "count/window"."""
        rate = str(self.config.get("concurrentRate", "") or "").strip()
        if not rate or rate == "0":
            return None
        if "/" in rate:
            try:
                count = max(1, int(rate.split("/", 1)[0].strip()))
                window_ms = max(1, int(rate.split("/", 1)[1].strip()))
            except ValueError:
                return None
            return "window", count, window_ms
        try:
            interval_ms = max(1, int(rate))
        except ValueError:
            return None
        return "interval", 1, interval_ms

    @staticmethod
    def _thread_count() -> int:
        try:
            from app.core.config import sync_thread_count
            return sync_thread_count()
        except Exception:
            return 9

    @staticmethod
    def _rate_limit_disabled() -> bool:
        try:
            from app.core.config import settings
            return bool(getattr(settings, "SYNC_IGNORE_RATE_LIMIT", False))
        except Exception:
            return False

    async def _sleep_rate_limit(self) -> None:
        """Reserve a request slot based on the source concurrentRate.

        A plain integer rate means one request per interval, while
        "count/window" allows count starts per window milliseconds.  Sources
        without concurrentRate fall back to CRAWL_DELAY_MS when it is
        configured, matching Legado's unthrottled behavior by default.
        """
        if self._rate_limit_disabled():
            return
        spec = self._parse_concurrent_rate()
        if spec is None:
            try:
                from app.core.config import settings
                delay_ms = int(getattr(settings, "CRAWL_DELAY_MS", 0) or 0)
            except Exception:
                delay_ms = 0
            if delay_ms <= 0:
                return
            spec = ("interval", 1, delay_ms)
        mode, count, window_ms = spec
        key = self.base_url or "default"
        lock = self.__class__._rate_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.__class__._rate_locks[key] = lock
        state = self.__class__._rate_state.setdefault(
            key,
            {
                "interval_slot": 0.0,
                "window_start": 0.0,
                "window_used": 0,
                "total_requests": 0,
            },
        )
        async with lock:
            now = time.monotonic()
            if mode == "interval":
                wait_s = state["interval_slot"] + window_ms / 1000.0 - now
                # Add jitter so the request pattern is not a fixed cadence.
                wait_s += random.uniform(0.2, 0.6)
                if wait_s > 0:
                    await asyncio.sleep(wait_s)
                    now = time.monotonic()
                state["interval_slot"] = now
            else:
                window_s = window_ms / 1000.0
                if state["window_start"] + window_s <= now:
                    state["window_start"] = now
                    state["window_used"] = 0
                if state["window_used"] >= count:
                    wait_s = state["window_start"] + window_s - now
                    if wait_s > 0:
                        await asyncio.sleep(wait_s)
                        now = time.monotonic()
                        state["window_start"] = now
                        state["window_used"] = 0
                state["window_used"] += 1

            state["total_requests"] += 1
            total = state["total_requests"]
            try:
                from app.core.config import settings as crawl_settings
                cooldown_every = int(
                    getattr(crawl_settings, "SYNC_RATE_COOLDOWN_EVERY", 0) or 0
                )
                cooldown_seconds = float(
                    getattr(crawl_settings, "SYNC_RATE_COOLDOWN_SECONDS", 0) or 0
                )
            except Exception:
                cooldown_every = 0
                cooldown_seconds = 0
            if (
                cooldown_every > 0
                and cooldown_seconds > 0
                and total % cooldown_every == 0
            ):
                await asyncio.sleep(
                    cooldown_seconds + random.uniform(0, 1)
                )

    def _capture_cookie_jar(self, resp) -> None:
        """Collect Set-Cookie headers when the source enables its cookie jar."""
        if not self.config.get("enabledCookieJar", False):
            return
        set_cookies = resp.headers.get_list("set-cookie")
        if not set_cookies:
            return
        existing = dict(
            (p.split("=", 1)[0], p)
            for p in self._cookie.split("; ")
            if "=" in p
        )
        for sc in set_cookies:
            part = sc.split(";")[0].strip()
            if "=" in part:
                existing[part.split("=", 1)[0]] = part
        self._cookie = "; ".join(existing.values())

    def _request_charset(self, charset: str | None = None) -> str | None:
        """Get the source-declared response encoding, if one exists."""
        value = charset or self.config.get("charset") or self.config.get("pageCharset")
        if not value:
            value = self.config.get("encoding")
        value = str(value or "").strip()
        return value or None

    @staticmethod
    def _response_text(response: Any, charset: str | None = None) -> str:
        """Decode HTML using YueDu's charset option and response metadata.

        ``httpx.Response.text`` assumes UTF-8 for many responses without a
        charset header.  That turns the GBK/Big5 pages used by older Chinese
        sources into replacement characters before the rule engine sees them.
        Decode the raw bytes here while preserving UTF-8 as the normal path.
        """
        raw = getattr(response, "content", None)
        if isinstance(raw, str):
            return raw
        if not isinstance(raw, (bytes, bytearray)):
            return str(getattr(response, "text", "") or "")
        raw = bytes(raw)
        if not raw:
            return ""

        candidates: list[str] = []
        if charset:
            candidates.append(str(charset).strip())

        headers = getattr(response, "headers", {})
        content_type = str(headers.get("content-type", "") or "")
        header_match = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type, re.I)
        if header_match:
            candidates.append(header_match.group(1))

        if raw.startswith(codecs.BOM_UTF8):
            candidates.insert(0, "utf-8-sig")
        elif raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
            candidates.insert(0, "utf-16")
        else:
            meta_match = re.search(
                rb"(?:charset\s*=\s*|content-type[^>]*charset\s*=\s*)[\"']?([a-zA-Z0-9._-]+)",
                raw[:8192],
                re.I,
            )
            if meta_match:
                candidates.append(meta_match.group(1).decode("ascii", errors="ignore"))

        candidates.extend(("utf-8", "gb18030", "big5"))
        seen: set[str] = set()
        for candidate in candidates:
            try:
                codec = codecs.lookup(candidate).name
            except (LookupError, TypeError):
                continue
            if codec in seen:
                continue
            seen.add(codec)
            try:
                return raw.decode(codec)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    async def _post(
        self,
        url: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        charset: str | None = None,
    ) -> str:
        """HTTP POST with the same retry/proxy behavior as _get."""
        import asyncio
        import httpx

        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Unsupported URL: {url}")
        clean_url, url_options = self._split_options_suffix(url)
        if url_options:
            if url_options.get("headers"):
                merged = dict(headers or {})
                merged.update(url_options.get("headers"))
                headers = merged
            if url_options.get("body") is not None:
                body = url_options.get("body")
            if url_options.get("charset"):
                charset = url_options.get("charset")
            method = str(url_options.get("method", "GET")).upper()
            if method != "POST":
                # A URL option that isn't a POST should go through the matching
                # helper (GET / webView) rather than being force-POSTed.
                return await self._get(clean_url, charset=charset)
            url = clean_url
        await self._sleep_rate_limit()
        headers = self._build_headers(headers)

        proxy_url = None
        try:
            from app.services.proxy_config import get_proxy_config
            cfg = get_proxy_config()
            if cfg.enabled:
                proxy_url = cfg.https_proxy or cfg.http_proxy
        except Exception:
            pass

        async def _request(proxy: str | None) -> str:
            nonlocal headers
            last_error: httpx.HTTPError | None = None
            for attempt in range(3):
                try:
                    client = await self._get_http_client(proxy)
                    if isinstance(body, str):
                        content_type = headers.get("Content-Type", "").lower()
                        if "json" in content_type:
                            resp = await client.post(url, content=body, headers=headers)
                        else:
                            resp = await client.post(url, data=body, headers=headers)
                    elif body is None:
                        resp = await client.post(url, headers=headers)
                    else:
                        resp = await client.post(url, json=body, headers=headers)

                    if resp.status_code in (403, 520):
                        if attempt < 2:
                            headers = self._with_403_fallback(headers)
                            await asyncio.sleep(1.0 + random.uniform(0.5, 1.0))
                            continue
                        try:
                            browser_html = await self._get_with_web_js(
                                url,
                                request_headers=headers,
                                fallback_http=False,
                            )
                        except RuntimeError as exc:
                            if "anti-bot/captcha" in str(exc):
                                raise
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        except Exception as exc:
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        if browser_html:
                            return browser_html
                        raise RuntimeError(
                            "Site returned an anti-bot/captcha page (网站要求验证码/人机验证，"
                            "请在浏览器中访问该网站通过验证后，把 Cookie 导入书源再同步): "
                            + url
                        )
                    if resp.status_code in (429, 500, 502, 503, 504):
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.replace(".", "", 1).isdigit()
                            else 2 ** attempt
                        )
                        await asyncio.sleep(wait + random.uniform(0.5, 1.5))
                        continue
                    resp.raise_for_status()
                    self._capture_cookie_jar(resp)
                    text = self._response_text(resp, self._request_charset(charset))
                    if self._is_blocked_page(text):
                        if attempt == 0:
                            headers = self._with_403_fallback(headers)
                            await asyncio.sleep(1.0 + random.uniform(0.5, 1.5))
                            continue
                        try:
                            browser_html = await self._get_with_web_js(
                                url,
                                request_headers=headers,
                                fallback_http=False,
                            )
                        except RuntimeError as exc:
                            if "anti-bot/captcha" in str(exc):
                                raise
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        except Exception as exc:
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        if browser_html:
                            return browser_html
                        raise RuntimeError(
                            "Site returned an anti-bot/captcha page (网站要求验证码/人机验证，"
                            "请在浏览器中访问该网站通过验证后，把 Cookie 导入书源再同步): "
                            + url
                        )
                    return text
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

            if last_error is not None:
                raise last_error
            raise RuntimeError(f"Request failed after retries: {url}")

        proxies: list[str | None] = [None]
        if proxy_url:
            proxies.insert(0, proxy_url)

        last_error: httpx.HTTPError | None = None
        for proxy in proxies:
            try:
                return await _request(proxy)
            except httpx.RequestError as exc:
                last_error = exc
                if proxy is None:
                    raise
                logger.warning(
                    "Configured proxy %s unreachable (%s); retrying direct",
                    proxy_url,
                    exc,
                )
            except httpx.HTTPStatusError as exc:
                if proxy is None:
                    raise
                last_error = exc
                logger.warning(
                    "Configured proxy returned HTTP %s; retrying direct",
                    exc.response.status_code if exc.response is not None else "error",
                )

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Request failed after retries: {url}")

    @staticmethod
    def _is_blocked_page(html: str) -> bool:
        """Detect Chinese novel-site anti-bot / captcha / rate-limit pages.

        Many sites serve a ``limit_box`` / captcha page with HTTP 200 when
        they consider the request suspicious.  Both strong single markers
        (``输入验证码后可继续访问``, ``limit_box``, ...) and weak markers paired
        with a confirmation phrase (``访问异常`` + ``验证码``) are recognized
        so the page is never persisted as chapter content.
        """
        if not html:
            return False
        lowered = html.lower()
        if any(marker in lowered for marker in STRONG_BLOCK_MARKERS):
            return True
        confirmations = (
            "验证码", "继续访问", "稍后再试", "后再试", "频繁", "限流",
            "captcha", "challenge", "security",
        )
        for marker in WEAK_BLOCK_MARKERS:
            if marker in lowered:
                return any(confirm in lowered for confirm in confirmations)
        return False

    @staticmethod
    def _looks_like_upstream_error(html: str) -> bool:
        """Detect a Cloudflare / origin 5xx error page (e.g. "Error code 520 /
        Web server is returning an unknown error").  These are transient upstream
        failures — treating them as a book with 0 chapters produced misleading
        "no usable metadata" errors and wasted the whole sync on a transient blip,
        so callers should surface this as a retryable error instead.
        """
        if not html:
            return False
        lowered = html.lower()
        if "web server is returning an unknown error" in lowered:
            return True
        if "error code 5" in lowered:
            return True
        if ("cloudflare" in lowered and "error" in lowered) or "cf-error" in lowered:
            return True
        if re.search(r"\b(?:error|could not be found)\b[^<]{0,40}\b5\d{2}\b", lowered):
            return True
        return False

    @classmethod
    def _is_challenge_page(cls, html: str) -> bool:
        """Detect a Cloudflare / generic JS challenge gate that may still be
        solving itself.  Unlike :meth:`_is_blocked_page`, this is lenient and
        deliberately looks for Cloudflare's own markers so we know to wait for
        the challenge (and the resulting ``cf_clearance`` cookie) to resolve.
        It deliberately does **not** fall back to the strict WAF/anti-bot
        markers: a rate-limit or captcha gate will never solve itself, so we
        only wait for JS challenges that can auto-clear.  The caller applies
        :meth:`_is_blocked_page` after the wait to reject truly blocked pages.
        """
        if not html:
            return False
        lowered = html.lower()
        # Cloudflare sets the title to "Just a moment..." while a challenge runs.
        if re.search(r"<title[^>]*>\s*just a moment", lowered):
            return True
        if any(
            marker in lowered
            for marker in (
                "cf-chl",
                "cf-challenge",
                "cf-turnstile",
                "cf_chl_opt",
                "challenge-platform",
                "managed challenge",
                "verify you are human",
                "attention required",
                "browser check",
            )
        ):
            return True
        return False

    async def _wait_for_challenge(
        self,
        context: Any,
        page: Any,
        url: str,
        timeout: float = 25.0,
    ) -> str:
        """Wait for a Cloudflare/WAF JS challenge page to resolve itself.

        Cloudflare serves "Just a moment..." and then runs a JS challenge,
        sets a ``cf_clearance`` cookie, and reloads the page.  A single
        ``page.goto(..., "domcontentloaded")`` returns before that finishes, so
        plain ``page.content()`` captures the challenge gate.  This helper polls
        the live page until either the real content appears (challenge cleared)
        or the deadline passes.  It also performs one explicit ``reload()`` after
        a grace period, which is enough for most challenge flows.
        """
        start = time.monotonic()
        deadline = start + timeout
        last_html = ""
        reloaded = False
        while True:
            try:
                last_html = await page.content()
            except Exception:
                last_html = ""
            # Capture session cookies (cf_clearance etc.) as soon as they appear
            # so later plain-HTTP requests can reuse the cleared session.
            try:
                self._capture_playwright_cookies(await context.cookies())
            except Exception:
                pass
            if last_html and not (
                self._is_challenge_page(last_html)
                or self._is_blocked_page(last_html)
            ):
                return last_html
            if time.monotonic() >= deadline:
                return last_html
            await page.wait_for_timeout(1500)
            # Turnstile / slider challenges auto-solve only after the user widget
            # is ticked.  Best-effort click on the challenge checkbox.
            if "turnstile" in (last_html or "").lower():
                for frame in page.frames:
                    try:
                        checkbox = frame.query_selector("input[type=checkbox]")
                        if checkbox:
                            await checkbox.click(timeout=3000)
                            break
                    except Exception:
                        pass
            # After a grace period issue a single reload.  Cloudflare's challenge
            # runs once then reloads with the clearance cookie; an explicit reload
            # unblocks the rare case where the auto-reload navigation is missed.
            if not reloaded and (time.monotonic() - start) >= 8.0:
                try:
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                    reloaded = True
                except Exception:
                    pass

    async def _get(self, url: str, charset: str | None = None) -> str:
        """HTTP GET with cookie, headers from config, rate limiting, and cookie jar."""
        import asyncio
        import httpx

        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Unsupported URL: {url}")
        # A source may append ``,{"webView":true}`` / ``,{"method":"POST",...}``
        # to the URL.  Split that off and honor it instead of sending the suffix
        # as part of the path (which breaks /book/123/,{"webView":true} URLs).
        clean_url, url_options = self._split_options_suffix(url)
        if url_options:
            if url_options.get("web_view") or url_options.get("web_js"):
                return await self._get_with_web_js(
                    clean_url,
                    str(url_options.get("web_js") or ""),
                    request_headers=url_options.get("headers") or None,
                    # A ``,{"webView":true}`` suffix means the site *requires* a
                    # browser (e.g. 要撸小说 / forum sources).  Do NOT silently
                    # fall back to plain HTTP when the browser hits an anti-bot
                    # challenge: that only re-requests a page that will never
                    # render over HTTP and turns the real "needs cookie / JS"
                    # cause into a confusing "no usable metadata" error.  Keep
                    # the HTTP fallback for webJs-only rules, which Legado can
                    # still evaluate against the plain-HTTP response.
                    fallback_http=not bool(url_options.get("web_view")),
                )
            if str(url_options.get("method", "GET")).upper() == "POST":
                return await self._post(
                    clean_url,
                    body=url_options.get("body"),
                    headers=url_options.get("headers") or None,
                    charset=url_options.get("charset") or charset,
                )
            url = clean_url
            if url_options.get("charset"):
                charset = url_options.get("charset")
        await self._sleep_rate_limit()
        headers = self._build_headers()

        proxy_url = None
        try:
            from app.services.proxy_config import get_proxy_config
            cfg = get_proxy_config()
            if cfg.enabled:
                proxy_url = cfg.https_proxy or cfg.http_proxy
        except Exception:
            pass

        async def _request(proxy: str | None) -> str:
            nonlocal headers
            last_error: httpx.HTTPError | None = None
            # Set when a polluted system DNS forced a DoH-resolved IP rewrite.
            doh_target: tuple[str, str] | None = None
            for attempt in range(3):
                try:
                    client = await self._get_http_client(proxy)
                    req_url = url
                    req_headers = headers
                    if doh_target is not None:
                        req_url, original_host = doh_target
                        req_headers = dict(headers)
                        req_headers["Host"] = original_host
                    resp = await client.get(req_url, headers=req_headers)
                    if resp.status_code in (403, 520):
                        if attempt < 2:
                            headers = self._with_403_fallback(headers)
                            await asyncio.sleep(1.0 + random.uniform(0.5, 1.0))
                            continue
                        try:
                            browser_html = await self._get_with_web_js(
                                url,
                                request_headers=headers,
                                fallback_http=False,
                            )
                        except RuntimeError as exc:
                            if "anti-bot/captcha" in str(exc):
                                raise
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        except Exception as exc:
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        if browser_html:
                            return browser_html
                        # The WAF blocked plain HTTP *and* the browser could not
                        # clear the challenge.  Surface a clear hint instead of a
                        # bare httpx 403/520 that hides the real cause.
                        raise RuntimeError(
                            "Site returned an anti-bot/captcha page (网站要求验证码/人机验证，"
                            "请在浏览器中访问该网站通过验证后，把 Cookie 导入书源再同步): "
                            + url
                        )
                    if resp.status_code in (429, 500, 502, 503, 504):
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.replace(".", "", 1).isdigit()
                            else 2 ** attempt
                        )
                        await asyncio.sleep(wait + random.uniform(0.5, 1.5))
                        continue
                    resp.raise_for_status()
                    self._capture_cookie_jar(resp)
                    text = self._response_text(resp, self._request_charset(charset))
                    if self._is_blocked_page(text):
                        if attempt == 0:
                            headers = self._with_403_fallback(headers)
                            await asyncio.sleep(1.0 + random.uniform(0.5, 1.5))
                            continue
                        try:
                            browser_html = await self._get_with_web_js(
                                url,
                                request_headers=headers,
                                fallback_http=False,
                            )
                        except RuntimeError as exc:
                            if "anti-bot/captcha" in str(exc):
                                raise
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        except Exception as exc:
                            browser_html = None
                            logger.warning(
                                "Browser fallback failed for %s: %s",
                                url,
                                exc,
                            )
                        if browser_html:
                            return browser_html
                        raise RuntimeError(
                            "Site returned an anti-bot/captcha page (网站要求验证码/人机验证，"
                            "请在浏览器中访问该网站通过验证后，把 Cookie 导入书源再同步): "
                            + url
                        )
                    return text
                except httpx.ConnectError as exc:
                    # DNS pollution bypass: when the direct connect fails and
                    # the system resolver is poisoned (or the site is simply
                    # unreachable by name), resolve via DoH and retry with the
                    # real IP plus an explicit Host header. Only used without
                    # a proxy, since a proxy resolves the name itself.
                    if (
                        proxy is None
                        and doh_target is None
                        and isinstance(exc, httpx.ConnectError)
                    ):
                        hostname = urlparse(url).hostname or ""
                        needs_doh = bool(hostname) and (
                            self._looks_polluted(hostname)
                            or "Name or service not known" in str(exc)
                            or "getaddrinfo failed" in str(exc)
                            or "Temporary failure in name resolution" in str(exc)
                        )
                        if needs_doh:
                            ip = await self._resolve_via_doh(hostname)
                            rewritten = self._doh_rewrite(url) if ip else None
                            if rewritten:
                                doh_target = rewritten
                                logger.warning(
                                    "DNS pollution detected for %s; retrying via %s",
                                    hostname,
                                    rewritten[0],
                                )
                                continue
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

            if last_error is not None:
                raise last_error
            raise RuntimeError(f"Request failed after retries: {url}")

        proxies: list[str | None] = [None]
        if proxy_url:
            proxies.insert(0, proxy_url)

        last_error: httpx.HTTPError | None = None
        for proxy in proxies:
            try:
                return await _request(proxy)
            except httpx.RequestError as exc:
                last_error = exc
                if proxy is None:
                    raise
                logger.warning(
                    "Configured proxy %s unreachable (%s); retrying direct",
                    proxy_url,
                    exc,
                )
            except httpx.HTTPStatusError as exc:
                if proxy is None:
                    raise
                last_error = exc
                logger.warning(
                    "Configured proxy returned HTTP %s; retrying direct",
                    exc.response.status_code if exc.response is not None else "error",
                )

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Request failed after retries: {url}")

    async def fetch_cover(self, url: str) -> tuple[bytes, str] | None:
        """Fetch a cover image, applying coverDecodeJs when configured."""
        import asyncio
        import httpx

        if not url.startswith(("http://", "https://")):
            return None
        # A source may append a Legado ``,{...}`` URL-options suffix to the
        # cover URL too. Strip it before requesting so it is not sent as part
        # of the path (which turns into a 404 for otherwise valid images).
        clean_url, _ = self._split_options_suffix(url)
        url = clean_url or url
        await self._sleep_rate_limit()
        headers = self._build_headers({
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        })
        # Cover CDNs commonly reject a direct request without the book site's
        # Referer. A source-defined Referer wins over this default.
        headers.setdefault("Referer", self.base_url.rstrip("/") + "/")

        proxy_url = None
        try:
            from app.services.proxy_config import get_proxy_config
            cfg = get_proxy_config()
            if cfg.enabled:
                proxy_url = cfg.https_proxy or cfg.http_proxy
        except Exception:
            pass

        async def _request(proxy: str | None) -> tuple[bytes, str]:
            nonlocal headers
            last_error: httpx.HTTPError | None = None
            for attempt in range(3):
                try:
                    client = await self._get_http_client(proxy)
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 403 and attempt == 0:
                        headers = self._with_403_fallback(headers)
                        await asyncio.sleep(1.0 + random.uniform(0.5, 1.0))
                        continue
                    if resp.status_code in (429, 500, 502, 503, 504):
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.replace(".", "", 1).isdigit()
                            else 2 ** attempt
                        )
                        await asyncio.sleep(wait + random.uniform(0.5, 1.5))
                        continue
                    resp.raise_for_status()
                    self._capture_cookie_jar(resp)
                    return resp.content, resp.headers.get("content-type", "")
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
            if last_error is not None:
                raise last_error
            raise RuntimeError(f"Request failed after retries: {url}")

        proxies: list[str | None] = [None]
        if proxy_url:
            proxies.insert(0, proxy_url)

        last_error: Exception | None = None
        for proxy in proxies:
            try:
                data, content_type = await _request(proxy)
                if not data or len(data) < 128:
                    return None
                data = self._decode_inline_cover_rule(data)
                if self.engine:
                    decoded = self.engine.decode_cover(data)
                    if decoded:
                        data = decoded
                return data, content_type
            except httpx.RequestError as exc:
                last_error = exc
                if proxy is None:
                    break
                logger.warning(
                    "Configured proxy %s unreachable for cover (%s); retrying direct",
                    proxy_url,
                    exc,
                )
            except Exception as exc:
                last_error = exc
                break
        if last_error is not None:
            logger.warning("Failed to fetch cover {}: {}", url, last_error)
        return None

    def _decode_inline_cover_rule(self, data: bytes) -> bytes:
        """Support AES-CBC cover snippets exported by some YueDu sources."""
        rule = str((self.config.get("ruleBookInfo") or {}).get("coverUrl") or "")
        if "AES/CBC/PKCS5Padding" not in rule or "copyOfRange(raw, 0, 16)" not in rule:
            return data

        key_match = re.search(
            r"String\(\s*(['\"])(.*?)\1\s*\)\.getBytes",
            rule,
            re.DOTALL,
        )
        if not key_match or len(data) <= 16:
            return data
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding

            key = key_match.group(2).encode("utf-8")
            decryptor = Cipher(
                algorithms.AES(key), modes.CBC(data[:16])
            ).decryptor()
            padded = decryptor.update(data[16:]) + decryptor.finalize()
            unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
            return unpadder.update(padded) + unpadder.finalize()
        except Exception as exc:
            logger.debug("Could not decode inline cover rule: {}", exc)
            return data

    async def fetch_content_image(
        self,
        url: str,
        referer: str | None = None,
    ) -> tuple[bytes, str] | None:
        """Fetch one in-content image, applying imageDecode when configured."""
        import asyncio
        import httpx

        if not url.startswith(("http://", "https://")):
            return None
        clean_url, _ = self._split_options_suffix(url)
        url = clean_url or url
        await self._sleep_rate_limit()
        headers = self._build_headers({
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        })
        if referer:
            headers["Referer"] = referer

        proxy_url = None
        try:
            from app.services.proxy_config import get_proxy_config
            cfg = get_proxy_config()
            if cfg.enabled:
                proxy_url = cfg.https_proxy or cfg.http_proxy
        except Exception:
            pass

        async def _request(proxy: str | None) -> tuple[bytes, str]:
            nonlocal headers
            last_error: httpx.HTTPError | None = None
            for attempt in range(3):
                try:
                    client = await self._get_http_client(proxy)
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 403 and attempt == 0:
                        headers = self._with_403_fallback(headers)
                        await asyncio.sleep(1.0 + random.uniform(0.5, 1.0))
                        continue
                    if resp.status_code in (429, 500, 502, 503, 504):
                        retry_after = resp.headers.get("Retry-After", "")
                        wait = (
                            float(retry_after)
                            if retry_after and retry_after.replace(".", "", 1).isdigit()
                            else 2 ** attempt
                        )
                        await asyncio.sleep(wait + random.uniform(0.5, 1.5))
                        continue
                    resp.raise_for_status()
                    self._capture_cookie_jar(resp)
                    return resp.content, resp.headers.get("content-type", "")
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
            if last_error is not None:
                raise last_error
            raise RuntimeError(f"Request failed after retries: {url}")

        proxies: list[str | None] = [None]
        if proxy_url:
            proxies.insert(0, proxy_url)

        last_error: Exception | None = None
        for proxy in proxies:
            try:
                data, content_type = await _request(proxy)
                if not data or len(data) < 128:
                    return None
                if self.engine:
                    decoded = self.engine.decode_content_image(data)
                    if decoded:
                        data = decoded
                return data, content_type
            except httpx.RequestError as exc:
                last_error = exc
                if proxy is None:
                    break
                logger.warning(
                    "Configured proxy %s unreachable for content image (%s); retrying direct",
                    proxy_url,
                    exc,
                )
            except Exception as exc:
                last_error = exc
                break
        if last_error is not None:
            logger.warning("Failed to fetch content image {}: {}", url, last_error)
        return None

    def get_search_check_keyword(self, default: str = "\u6211\u7684") -> str:
        """Get the check keyword for search validation.

        Ported from BookSource.getCheckKeyword.
        """
        search_rules = self.config.get("ruleSearch", {})
        ck = search_rules.get("checkKeyWord", "")
        if ck and ck.strip():
            return ck.strip()
        return default

    @staticmethod
    def _resolve_toc_entries(
        entries: list[dict[str, Any]],
        base_url: str,
    ) -> list[dict[str, Any]]:
        """Resolve TOC chapter URLs against the page they were parsed from."""
        for entry in entries:
            ch_url = str(entry.get("chapterUrl") or "").strip()
            if ch_url and not ch_url.startswith(("http://", "https://")):
                entry["chapterUrl"] = urljoin(base_url, ch_url)
        return entries

    @staticmethod
    def _attach_next_urls(chapters: list[RemoteChapter]) -> list[RemoteChapter]:
        return [
            RemoteChapter(
                source_chapter_id=chapter.source_chapter_id,
                title=chapter.title,
                url=chapter.url,
                chapter_number=chapter.chapter_number,
                next_url=(
                    chapters[index + 1].url
                    if index + 1 < len(chapters)
                    else None
                ),
            )
            for index, chapter in enumerate(chapters)
        ]

    @staticmethod
    def _book_id_from_url(url: str) -> str:
        """Return a stable source book id from a book URL.

        Legado uses the full book URL as the book key. Keep the full
        normalized URL so build_book_url() can reconstruct it directly
        without depending on the bookUrlPattern.
        """
        return YueduPlugin._strip_url_options_suffix(url).rstrip("/") or url

    @staticmethod
    def _make_absolute(href: str, base: str) -> str:
        """Convert relative URL to absolute."""
        return urljoin(base, href)
