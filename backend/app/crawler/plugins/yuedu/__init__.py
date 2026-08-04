"""YueDu book source plugin -- interprets YueDu (Legado) source JSON to crawl novels.

Each instance is configured with a single YueDu book source JSON. The plugin
translates YueDu's rule-based definitions (JSONPath, CSS selectors, URL templates)
into NovelHub's NovelSourcePlugin protocol.

Usage:
    plugin = YueduPlugin(yuedu_source_json)
    book = await plugin.fetch_book(url)
    content = await plugin.fetch_chapter_content(chapter)
"""

import base64
import json
import logging
import random
import re
from typing import Any
from urllib.parse import urljoin, urlparse

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
    "#list a",
    ".listmain a",
    ".chapterlist a",
    "ul.chapter-list a",
    ".chapter-list a",
    "div.listmain a",
    "li.chapter-item a",
    "dd.chapter a",
    ".book-catalog a",
    "#catalog a",
]


class YueduPlugin:
    """A NovelSourcePlugin implementation driven by a YueDu book source JSON."""

    name = "yuedu"

    def __init__(self, source_config: dict[str, Any] | None = None):
        self.config: dict[str, Any] = source_config or {}
        self.engine: YueduRuleEngine | None = None
        if self.config:
            self.engine = YueduRuleEngine(self.config)
        self.base_url: str = self.config.get("bookSourceUrl", "")
        self._cookie: str = ""

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
        self.config = config
        self.engine = YueduRuleEngine(config)
        self.base_url = config.get("bookSourceUrl", "")

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

        # Use webJs-enabled fetch if the source has webJs configured
        web_js = self.engine.get_web_js()
        if web_js:
            html = await self._get_with_web_js(url, web_js)
        else:
            html = await self._get(url)
        info = self.engine.parse_book_info(html)

        # Run preUpdateJs before parsing TOC
        toc_data = {"bookUrl": url, "baseUrl": self.base_url}
        self.engine.run_pre_update_js(toc_data)

        toc = self.engine.parse_toc(html)

        # Follow nextTocUrl for paginated tables of contents
        max_toc_pages = 20
        current_toc_html = html
        for _ in range(max_toc_pages):
            next_toc_url = self.engine.get_next_toc_url(current_toc_html)
            if not next_toc_url:
                break
            current_toc_html = await self._get(next_toc_url)
            more_toc = self.engine.parse_toc(current_toc_html)
            if more_toc:
                toc.extend(more_toc)

        generic = self._parse_book_generic(html, url)
        if not str(info.get("name") or "").strip():
            info["name"] = generic["title"]
        if not str(info.get("author") or "").strip():
            info["author"] = generic["author"]
        if not info.get("intro"):
            info["intro"] = generic["description"]
        if not info.get("status"):
            info["status"] = generic["status"]

        chapters: list[RemoteChapter] = []
        chapter_num = 0
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
                ch_url = self._make_absolute(ch_url, url)
            if not self._is_chapter_url(ch_url, url):
                continue
            title = str(title or "").strip() or f"Chapter {chapter_num + 1}"
            book_name = str(info.get("name") or "").strip()
            if (
                title in ("目录", "简介", "上一章", "下一章", "返回目录", "首页", "开始阅读")
                or (book_name and title == book_name)
            ):
                continue
            chapter_num += 1
            chapters.append(RemoteChapter(
                source_chapter_id=str(chapter_num),
                title=title,
                url=ch_url,
                chapter_number=chapter_num,
            ))

        if not chapters:
            chapters = generic["chapters"]

        book_title = str(info.get("name") or "").strip() or "Unknown"
        author = str(info.get("author") or "").strip() or "Unknown"
        description = info.get("intro", "")
        status = info.get("status", "")

        return RemoteBook(
            source_book_id=url.split("/")[-1] if "/" in url else url,
            title=book_title,
            author=author,
            description=description if description else None,
            status=status if status else None,
            chapters=chapters,
            tags=info.get("kind", "").split(",") if info.get("kind") else [],
        )

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
        title = re.sub(r"^《(.+)》$", r"\1", title).strip()

        author = ""
        for selector in GENERIC_BOOK_AUTHOR_SELECTORS:
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if value:
                author = str(value).strip()
                break

        description = ""
        for selector in GENERIC_BOOK_DESC_SELECTORS:
            el = soup.select_one(selector)
            if el is None:
                continue
            value = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
            if value:
                description = str(value).strip()
                break

        status = ""
        page_text = soup.get_text(" ", strip=True)
        for marker in ("已完结", "完结", "连载中", "连载"):
            if marker in page_text:
                status = "completed" if marker in ("已完结", "完结") else "ongoing"
                break

        chapter_links: list[Tag] = []
        for selector in GENERIC_CHAPTER_SELECTORS:
            links = soup.select(selector)
            if len(links) >= 2:
                chapter_links = links
                break

        if not chapter_links:
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").strip()
                if not href or href in ("#", "javascript:;", "javascript:void(0)"):
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
            if not href or href in ("#", "javascript:;", "javascript:void(0)"):
                continue
            abs_url = self._make_absolute(href, url)
            if abs_url in seen_urls:
                continue
            if not self._is_chapter_url(abs_url, url):
                continue
            seen_urls.add(abs_url)
            chapter_number += 1
            chapters.append(RemoteChapter(
                source_chapter_id=urlparse(abs_url).path,
                title=text,
                url=abs_url,
                chapter_number=chapter_number,
            ))

        return {
            "title": title,
            "author": author,
            "description": description,
            "status": status,
            "chapters": chapters,
        }

    # ---- Required: fetch_chapter_content ----

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        """Fetch and return the full text of a single chapter.

        Supports multi-page chapters via nextContentUrl rule.
        Uses webJs for JS-rendered pages when configured.
        """
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")

        web_js = self.engine.get_web_js()
        if web_js:
            html = await self._get_with_web_js(chapter.url, web_js)
        else:
            html = await self._get(chapter.url)
        try:
            content = self.engine.parse_content(html)
        except Exception:
            content = ""
        if not content:
            content = self._parse_chapter_content_generic(html)
        parts = [content] if content else []

        # Follow nextContentUrl for multi-page chapters
        max_pages = 20  # safety limit
        current_html = html
        for _ in range(max_pages):
            next_url = self.engine.get_next_content_url(current_html)
            if not next_url:
                break
            current_html = await self._get(next_url)
            next_part = self.engine.parse_content(current_html)
            if next_part and next_part != current_html:
                parts.append(next_part)

        content = "\n".join(parts)

        if content and ("<" in content or ">" in content):
            try:
                soup = BeautifulSoup(content, "lxml")
                content = soup.get_text("\n", strip=True)
            except Exception:
                pass

        if not content:
            content = self._parse_chapter_content_generic(html)
        content = content.strip()
        replace_rules = (self.config.get("ruleContent") or {}).get("replaceRegex", [])
        if replace_rules:
            content = self.engine._apply_replace_regex(content, replace_rules).strip()
        return content or ""

    def _parse_chapter_content_generic(self, html: str) -> str:
        """Extract readable text when the configured content rule misses."""
        soup = BeautifulSoup(html, "lxml")
        content_selectors = (
            "#content",
            "#chapter-content",
            "article",
            "div.content",
            ".chapter-content",
            ".read-content",
            ".reader-content",
            ".article",
        )
        for selector in content_selectors:
            el = soup.select_one(selector)
            if el is None:
                continue
            for tag in el.find_all(["script", "style", "ins", "nav", "header", "footer"]):
                tag.decompose()
            paragraphs = [
                p.get_text(strip=True)
                for p in el.find_all(["p", "br"])
                if p.get_text(strip=True)
            ]
            if paragraphs:
                return "\n\n".join(paragraphs)
            text = el.get_text("\n", strip=True)
            if text:
                return text
        return ""

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
                    author = author_el.get_text(strip=True)
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

            book_id = full_url.split("/")[-1] if "/" in full_url else full_url
            path = urlparse(full_url).path.lower()
            # Skip empty IDs or bare numeric category IDs, but keep numeric
            # book IDs under /novel/ or /book/ paths.
            if not book_id or (
                book_id.isdigit()
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
            if not href or href in ("#", "javascript:;", "javascript:void(0)"):
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

            book_id = full_url.split("/")[-1]
            if not book_id or (
                book_id.isdigit()
                and not any(seg in path for seg in ("/novel/", "/book/", "/read/", "/detail/"))
            ):
                continue

            author = "Unknown"
            if a_tag.parent is not None:
                for sel in SHELF_AUTHOR_SELECTORS:
                    author_el = a_tag.parent.select_one(sel)
                    if author_el:
                        author = author_el.get_text(" ", strip=True) or "Unknown"
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
        pattern = self.config.get("bookUrlPattern", "")
        if pattern and pattern.strip():
            try:
                if re.search(pattern, url):
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
        segments = [seg for seg in path.split("/") if seg]
        for prefix in ("novel", "book", "read", "detail", "xiaoshuo"):
            if prefix not in segments:
                continue
            tail = segments[segments.index(prefix) + 1:]
            if len(tail) == 1 and tail[0]:
                return True
        return False

    def _is_chapter_url(self, url: str, book_url: str) -> bool:
        """Filter out book-page, category, and navigation links from a TOC."""
        abs_url = self._make_absolute(url, book_url or self.base_url)
        abs_book = self._make_absolute(book_url, self.base_url)
        if abs_url.rstrip("/") == abs_book.rstrip("/"):
            return False

        path = urlparse(abs_url).path.lower()
        skip_paths = (
            "/lists/", "/list", "/category/", "/categories/", "/tag/", "/tags/",
            "/author/", "/search", "/bookcase/", "/bookshelf/", "/user/",
            "/login", "/register", "/signup", "/about", "/help", "/faq",
            "/contact", "/rank", "/top", "/sort", "/finish", "/wanben",
            "/quanben", "/allvisit", "/lastupdate", "/history", "/index",
        )
        if any(seg in path for seg in skip_paths):
            return False
        if self._is_book_url(abs_url, require_pattern=True):
            return False
        return True

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
        BookSourceExtensions.exploreKinds().
        """
        explore_url = self.config.get("exploreUrl", "")
        if not explore_url or not explore_url.strip():
            return []

        rule_str = explore_url.strip()

        # If it starts with <js> or @js:, it's JS that returns JSON
        if rule_str.startswith("<js>") or rule_str.startswith("@js:"):
            return self._parse_explore_json(rule_str)

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
                kinds.append({"title": line, "url": line})
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
                        {"title": item.get("title", ""), "url": item.get("url", item.get("title", ""))}
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
        """
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")
        if url:
            explore_url = self._make_absolute(url, self.base_url)
            explore_url = self.engine._substitute(explore_url, page=str(page))
            return await self._fetch_explore_url(explore_url)

        kinds = self.get_explore_kinds()
        if kinds:
            results = []
            for kind in kinds:
                kind_url = kind.get("url", "").strip()
                if not kind_url:
                    continue
                kind_url = self.engine._substitute(kind_url, page=str(page))
                if not kind_url.startswith(("http://", "https://")):
                    kind_url = self._make_absolute(kind_url, self.base_url)
                if not kind_url.startswith(("http://", "https://")):
                    continue
                try:
                    results.extend(await self._fetch_explore_url(kind_url))
                except Exception as exc:
                    logger.warning(
                        f"Explore kind failed: {kind.get('title', kind_url)} ({exc})"
                    )
            return results

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
        return await self._fetch_explore_url(explore_url)

    async def _fetch_explore_url(self, explore_url: str) -> list[dict[str, Any]]:
        html = await self._get(explore_url)
        return self._explore_items_from_html(html, explore_url)

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
            return [
                item for item in normalized_items
                if self._is_book_url(
                    self._explore_item_url(item),
                    require_pattern=True,
                )
            ]

        # Generic fallback for list/category pages whose configured rules no
        # longer match the live site.
        shelf_books = self._parse_bookshelf_html(html, base_url=page_url)
        return [
            {
                "bookUrl": book.url,
                "name": book.title,
                "author": book.author,
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
                source_book_id=full_url.rstrip("/").split("/")[-1] or full_url,
                title=str(item.get("name") or item.get("title") or book_url).strip() or "Unknown",
                author=str(item.get("author") or "").strip() or "Unknown",
                url=full_url,
                latest_chapter_title=item.get("latestChapterTitle"),
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
        options = self._parse_url_options(search_url)
        request_url = options["url"] if options else search_url

        if options and str(options.get("method", "GET")).upper() == "POST":
            html = await self._post(
                request_url,
                body=options.get("body"),
                headers=options.get("headers") or {},
            )
        else:
            web_js = (options or {}).get("web_js") or self.engine.get_web_js()
            if web_js or (options or {}).get("web_view"):
                html = await self._get_with_web_js(request_url, web_js)
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
                "author": str(item.get("author") or "").strip() or "Unknown",
                "bookUrl": full_url,
                "coverUrl": str(item.get("coverUrl") or "").strip() or None,
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
                "author": book.author,
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
            "web_view": bool(option.get("webView")),
            "web_js": option.get("webJs") or "",
        }

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

    async def _get_with_web_js(self, url: str, web_js: str) -> str:
        """Fetch a page that requires JavaScript rendering (webJs).

        Uses Playwright to load the page in a headless browser,
        execute the webJs script, and return the resulting HTML.
        
        Falls back to plain HTTP GET if Playwright is unavailable
        or if the webJs execution fails.
        """
        import asyncio

        web_js = web_js.strip()
        if not web_js:
            return await self._get(url)

        # Try Playwright first
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning(
                "playwright not installed; falling back to plain HTTP for webJs"
            )
            return await self._get(url)

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox"],
                )
                try:
                    context = await browser.new_context(
                        viewport={"width": 1280, "height": 720},
                        locale="zh-CN",
                    )
                    page = await context.new_page()

                    # Apply cookies if set
                    if self._cookie:
                        await context.add_cookies(
                            self._parse_cookies_for_playwright()
                        )

                    await page.goto(url, wait_until="networkidle", timeout=30000)

                    # Execute the webJs and get the page content
                    # webJs typically modifies the DOM; we capture innerHTML after execution
                    try:
                        await page.evaluate(
                            f"(function(){{ var result=document.documentElement.outerHTML; {web_js}; }})()"
                        )
                    except Exception as e:
                        logger.warning(f"webJs execution error: {e}")

                    # Get the full page HTML after JS execution
                    html = await page.content()

                    await context.close()
                    return html
                finally:
                    await browser.close()
        except Exception as e:
            logger.warning(
                f"Playwright webJs fetch failed for {url}: {e}; falling back to HTTP"
            )

        # Fallback: try evaluating webJs on plain HTTP response
        html = await self._get(url)
        if self.engine:
            result = self.engine.eval_web_js(web_js, html)
            if result and result != html:
                return result
        return html

    def _parse_cookies_for_playwright(self) -> list[dict[str, Any]]:
        """Parse cookie string into Playwright cookie format."""
        cookies = []
        if not self._cookie:
            return cookies
        domain = urlparse(self.base_url).netloc or "localhost"
        for part in self._cookie.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": domain,
                    "path": "/",
                })
        return cookies

    def _build_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build request headers from source config, cookies, and per-request extras."""
        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) Mobile Safari/537.36"}
        if self._cookie:
            headers["Cookie"] = self._cookie

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
        return headers

    async def _sleep_rate_limit(self) -> None:
        """Polite rate limiting: source rule wins, otherwise use a safe default."""
        import asyncio

        rate = self.config.get("concurrentRate", "")
        delay_ms = 0
        if rate:
            try:
                delay_ms = int(str(rate).strip()) if str(rate).strip().isdigit() else 0
            except (ValueError, TypeError):
                delay_ms = 0
        if delay_ms <= 0:
            from app.core.config import settings
            delay_ms = settings.CRAWL_DELAY_MS
        if delay_ms > 0:
            await asyncio.sleep((delay_ms + random.uniform(200, 600)) / 1000.0)

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

    async def _post(
        self,
        url: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        """HTTP POST with the same retry/proxy behavior as _get."""
        import asyncio
        import httpx

        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Unsupported URL: {url}")
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
            last_error: httpx.HTTPError | None = None
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(
                        headers=headers,
                        timeout=30,
                        follow_redirects=True,
                        proxy=proxy,
                        trust_env=False,
                    ) as client:
                        if isinstance(body, str):
                            content_type = headers.get("Content-Type", "").lower()
                            if "json" in content_type:
                                resp = await client.post(url, content=body)
                            else:
                                resp = await client.post(url, data=body)
                        elif body is None:
                            resp = await client.post(url)
                        else:
                            resp = await client.post(url, json=body)

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
                        return resp.text
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

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Request failed after retries: {url}")

    async def _get(self, url: str) -> str:
        """HTTP GET with cookie, headers from config, rate limiting, and cookie jar."""
        import asyncio
        import httpx

        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Unsupported URL: {url}")
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
            last_error: httpx.HTTPError | None = None
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(
                        headers=headers,
                        timeout=30,
                        follow_redirects=True,
                        proxy=proxy,
                        trust_env=False,
                    ) as client:
                        resp = await client.get(url)
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
                        return resp.text
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

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Request failed after retries: {url}")
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
    def _make_absolute(href: str, base: str) -> str:
        """Convert relative URL to absolute."""
        return urljoin(base, href)
