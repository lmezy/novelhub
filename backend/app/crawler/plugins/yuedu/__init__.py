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
            chapter_num += 1
            chapters.append(RemoteChapter(
                source_chapter_id=str(chapter_num),
                title=title,
                url=ch_url,
                chapter_number=chapter_num,
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
        content = self.engine.parse_content(html)
        parts = [content] if content else [html]

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
        """
        if not self.engine:
            raise RuntimeError("YueduPlugin not configured")
        if url:
            explore_url = self._make_absolute(url, self.base_url)
            explore_url = self.engine._substitute(explore_url, page=str(page))
        else:
            explore_url = self.engine.build_explore_url(page=page)
        if not explore_url:
            logger.warning(f"No explore URL for {self.display_name}")
            return []
        html = await self._get(explore_url)
        explore_rules = self.config.get("ruleExplore", {})
        if explore_rules.get("bookList", ""):
            return self.engine.parse_explore_results(html)
        return self.engine.parse_search_results(html)
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

    async def _get(self, url: str) -> str:
        """HTTP GET with cookie, headers from config, rate limiting, and cookie jar."""
        import asyncio
        import json
        import httpx

        # Concurrent rate limiting
        rate = self.config.get("concurrentRate", "")
        if rate:
            try:
                delay_ms = int(rate.strip()) if rate.strip().isdigit() else 0
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)
            except (ValueError, TypeError):
                pass

        headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) Mobile Safari/537.36"}

        # Add stored cookies
        if self._cookie:
            headers["Cookie"] = self._cookie

        # Parse header rule from config (may be JS or JSON)
        header_rule = self.config.get("header", "")
        header_rule = self.config.get("header", "")
        if header_rule:
            try:
                if "JSON.stringify" in header_rule:
                    m = re.search(r'JSON\\.stringify\\((\\{.+?\\})\\)', header_rule, re.DOTALL)
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
                    m = re.search(r'JSON\\.stringify\\((\\{.+?\\})\\)', js, re.DOTALL)
                    if not m:
                        m = re.search(r'"(?:User-Agent|Content-Type|Cookie|Referer|Accept)[^}]*}', js, re.IGNORECASE)
                    if m:
                        try:
                            hdr_str = m.group(0)
                            if not hdr_str.startswith("{"):
                                hdr_str = "{" + hdr_str + "}"
                            hdr_str = re.sub(r'(\\w+):', r'"\\1":', hdr_str)
                            custom_headers = json.loads(hdr_str)
                            headers.update(custom_headers)
                        except (json.JSONDecodeError, ValueError):
                            pass
            except Exception:
                pass


        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            # Cookie jar: collect Set-Cookie headers
            if self.config.get("enabledCookieJar", False):
                set_cookies = resp.headers.get_all("set-cookie")
                if set_cookies:
                    new_parts = []
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

            return resp.text
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
