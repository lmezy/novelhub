"""Bookshelf (favourites) reading for the YueDu plugin.

Split out of the ``YueduPlugin`` god class.  Used by the Cookie health
check to tell a still-valid session from an expired one, so it must
fail loudly-but-cheaply rather than hang on a blocked source.
"""

from app.crawler.base import RemoteShelfBook
from app.crawler.plugins.yuedu.common import logger
from app.crawler.plugins.yuedu.js_runtime import try_eval_js_pattern
from app.crawler.plugins.yuedu.selectors import BOOKSHELF_LINK_PATTERNS
from app.crawler.plugins.yuedu.selectors import BOOKSHELF_PATH_CANDIDATES
from app.crawler.plugins.yuedu.selectors import SHELF_AUTHOR_SELECTORS
from app.crawler.plugins.yuedu.selectors import SHELF_ITEM_SELECTORS
from app.crawler.plugins.yuedu.selectors import SHELF_LINK_SELECTORS
from bs4 import BeautifulSoup
from bs4 import Tag
from urllib.parse import urljoin
from urllib.parse import urlparse


class BookshelfMixin:
    """Methods extracted from ``YueduPlugin``."""

    async def fetch_bookshelf(self, cookie: str) -> list[RemoteShelfBook]:
        """Fetch user bookshelf by trying the configured URL or auto-detecting it.

        Strategy:
        1. Use bookshelf_url from config if available
        2. Otherwise try common bookshelf path patterns
        3. Parse the first successful response
        """
        self._cookie = cookie
        self._configured_cookie = cookie
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
