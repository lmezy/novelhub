"""Fanqie crawler using Playwright for JS-rendered pages."""

from typing import Optional

from app.crawler.playwright_crawler import PlaywrightCrawler
from app.crawler.plugins.fanqie.config import FanqieConfig


class FanqieCrawler:
    """Playwright-based crawler for fanqienovel.com."""

    def __init__(self, config: Optional[FanqieConfig] = None):
        self.config = config or FanqieConfig()
        self._pw: Optional[PlaywrightCrawler] = None
        self._cookie: str = ""

    async def _ensure_browser(self) -> PlaywrightCrawler:
        if self._pw is None:
            self._pw = PlaywrightCrawler(
                headless=self.config.headless,
                rate_limit=self.config.rate_limit,
                timeout=self.config.timeout,
            )
            await self._pw.start()
            if self._cookie:
                self._pw.set_cookie(self._cookie)
        return self._pw

    async def get_book_page(self, book_id: str) -> str:
        """Fetch the book info page."""
        pw = await self._ensure_browser()
        url = f"{self.config.base_url}/page/{book_id}"
        return await pw.get_with_retry(url, wait_selector=".page-book, .book-info, .info")

    async def get_chapter_content(self, chapter_url: str) -> str:
        """Fetch a chapter page."""
        pw = await self._ensure_browser()
        full_url = chapter_url if chapter_url.startswith("http") else f"{self.config.base_url}{chapter_url}"
        return await pw.get_with_retry(full_url, wait_selector=".chapter-content, .article, .content")

    async def get_with_retry(self, url: str, wait_selector: str | None = None) -> str:
        """Generic page fetch with retry."""
        pw = await self._ensure_browser()
        return await pw.get_with_retry(url, wait_selector=wait_selector)

    def set_cookie(self, cookie: str) -> None:
        self._cookie = cookie
        if self._pw:
            self._pw.set_cookie(cookie)

    async def close(self) -> None:
        if self._pw:
            await self._pw.stop()
            self._pw = None