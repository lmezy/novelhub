"""YueDu book source plugin -- interprets YueDu (Legado) source JSON to crawl novels.

Each instance is configured with a single YueDu book source JSON. The plugin
translates YueDu's rule-based definitions (JSONPath, CSS selectors, URL templates)
into NovelHub's NovelSourcePlugin protocol.

Usage:
    plugin = YueduPlugin(yuedu_source_json)
    book = await plugin.fetch_book(url)
    content = await plugin.fetch_chapter_content(chapter)
"""

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook
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

    # ---- Required: fetch_book ----

    async def fetch_book(self, url: str) -> RemoteBook:
        """Fetch book info + chapter list from a book page URL."""
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")

        html = await self._get(url)
        info = self.engine.parse_book_info(html)
        toc = self.engine.parse_toc(html)

        chapters: list[RemoteChapter] = []
        for i, ch in enumerate(toc):
            title = ch.get("chapterName", f"Chapter {i + 1}")
            ch_url = ch.get("chapterUrl", "")
            if ch_url and not ch_url.startswith("http"):
                ch_url = self._make_absolute(ch_url, url)
            chapters.append(RemoteChapter(
                source_chapter_id=str(i + 1),
                title=title,
                url=ch_url,
                chapter_number=i + 1,
            ))

        book_title = info.get("name", "Unknown")
        author = info.get("author", "Unknown")
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

    # ---- Required: fetch_chapter_content ----

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        """Fetch and return the full text of a single chapter."""
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")

        html = await self._get(chapter.url)
        content = self.engine.parse_content(html)

        if content and ("<" in content or ">" in content):
            try:
                soup = BeautifulSoup(content, "lxml")
                content = soup.get_text("\n", strip=True)
            except Exception:
                pass

        return content or html

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

        logger.warning(f"No bookshelf found for source {self.display_name}")
        return []

    # ---- Generic bookshelf HTML parser ----

    def _parse_bookshelf_html(self, html: str) -> list[RemoteShelfBook]:
        """Parse a bookshelf page using common CSS patterns."""
        soup = BeautifulSoup(html, "lxml")
        items: list[Tag] = []

        # Try each selector pattern
        for selector in SHELF_ITEM_SELECTORS:
            candidates = soup.select(selector)
            if candidates:
                items = candidates
                break

        if not items:
            return []

        books: list[RemoteShelfBook] = []
        for item in items:
            link_el = None
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

            full_url = self._make_absolute(href, self.base_url)
            book_id = full_url.split("/")[-1] if "/" in full_url else full_url

            books.append(RemoteShelfBook(
                source_book_id=book_id,
                title=title,
                author=author,
                url=full_url,
                latest_chapter_title=latest,
            ))

        return books

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

    # ---- Optional: update_book ----

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

        Supports JS-based API login (parses java.post/get/ajax calls).
        For form-based login, returns None (manual cookie needed).
        """
        from app.crawler.plugins.yuedu.login import YueduLoginParser
        parser = YueduLoginParser(self.config)

        if parser.can_auto_login():
            return await parser.execute_login(username, password)

        login_page = parser.get_login_page_url()
        if login_page:
            logger.info(f"Manual login page: {login_page}")

        logger.warning(f"No parseable login mechanism for {self.display_name}")
        return None

    # ---- Helpers ----

    async def _get(self, url: str) -> str:
        """HTTP GET with cookie and headers from config."""
        import httpx

        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 9) Mobile Safari/537.36"}
        if self._cookie:
            headers["Cookie"] = self._cookie

        header_rule = self.config.get("header", "")
        if header_rule:
            try:
                import json
                if "JSON.stringify" in header_rule:
                    m = re.search(r'JSON\.stringify\((\{.+?\})\)', header_rule, re.DOTALL)
                    if m:
                        custom_headers = json.loads(m.group(1))
                        headers.update(custom_headers)
            except Exception:
                pass

        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    @staticmethod
    def _make_absolute(href: str, base: str) -> str:
        """Convert relative URL to absolute."""
        return urljoin(base, href)

