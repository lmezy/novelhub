"""Qidian plugin -- Playwright-based crawler for Qidian.com."""

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook
from app.crawler.plugins.qidian.parser import QidianParser
from app.crawler.plugins.qidian.crawler import QidianCrawler
from app.crawler.plugins.qidian.config import QidianConfig


class QidianPlugin:
    """Plugin for Qidian.com with Playwright JS rendering support.

    Handles Qidian's dynamic pages, cookie-based login for VIP content.
    Font encryption is noted but requires additional custom font mapping
    for full text extraction on protected chapters.
    """

    name = "qidian"

    def __init__(self):
        self.config = QidianConfig()
        self.parser = QidianParser()
        self.crawler: QidianCrawler | None = None
        self._cookie: str = ""

    async def _ensure_crawler(self) -> QidianCrawler:
        if self.crawler is None:
            self.crawler = QidianCrawler(config=self.config)
            if self._cookie:
                self.crawler.set_cookie(self._cookie)
        return self.crawler

    async def fetch_book(self, url: str) -> RemoteBook:
        """Fetch and parse a Qidian book page."""
        crawler = await self._ensure_crawler()
        import re
        bid = re.search(r"/book/(\d+)", url)
        if not bid:
            raise ValueError(f"Could not extract book ID from URL: {url}")
        book_id = bid.group(1)
        html = await crawler.get_book_page(book_id)
        return self.parser.parse_book(html, url)

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        """Fetch and parse chapter content."""
        crawler = await self._ensure_crawler()
        html = await crawler.get_chapter_content(chapter.url)
        title, content = self.parser.parse_chapter_content(html)
        return content

    async def fetch_bookshelf(self, cookie: str) -> list[RemoteShelfBook]:
        """Fetch Qidian bookshelf (requires login cookie)."""
        self.set_cookie(cookie)
        crawler = await self._ensure_crawler()
        url = f"{self.config.base_url}/shelf"
        html = await crawler.get_with_retry(url)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        books = []
        for item in soup.select(".book-img-text li, .shelf-book-item"):
            link = item.select_one("a")
            if not link:
                continue
            title_el = item.select_one("h4, .book-title, a[data-bid]")
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            href = link.get("href", "")
            books.append(RemoteShelfBook(
                source_book_id=href.rstrip("/").split("/")[-1],
                title=title,
                author="",
                url=href if href.startswith("http") else f"{self.config.base_url}{href}",
            ))
        return books

    async def update_book(self, url: str) -> RemoteBook | None:
        return await self.fetch_book(url)

    def set_cookie(self, cookie: str) -> None:
        self._cookie = cookie
        if self.crawler:
            self.crawler.set_cookie(cookie)

    async def close(self) -> None:
        if self.crawler:
            await self.crawler.close()
            self.crawler = None

    async def discover_books(self, url: str | None = None, page: int = 1) -> list[RemoteShelfBook]:
        """Not implemented for Qidian. Use yuedu plugin for catalog discovery."""
        return []

    async def auto_login(self, username: str, password: str) -> str | None:
        """Attempt Playwright-based login to Qidian."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return None
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
                page = await browser.new_page()
                await page.goto(f"{self.config.base_url}/login", wait_until="networkidle", timeout=30000)
                await page.fill('input[name="username"], input[type="text"]', username)
                await page.fill('input[name="password"], input[type="password"]', password)
                await page.click('button[type="submit"], input[type="submit"]')
                await page.wait_for_timeout(3000)
                cookies = await page.context.cookies()
                await browser.close()
                if cookies:
                    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                    self.set_cookie(cookie_str)
                    return cookie_str
        except Exception:
            pass
        return None
