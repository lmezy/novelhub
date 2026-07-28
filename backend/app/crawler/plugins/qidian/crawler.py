"""Qidian crawler using Playwright for JS-rendered pages."""

from typing import Optional

from app.crawler.playwright_crawler import PlaywrightCrawler
from app.crawler.plugins.qidian.config import QidianConfig


class QidianCrawler:
    """Playwright-based crawler for Qidian.com."""

    def __init__(self, config: Optional[QidianConfig] = None):
        self.config = config or QidianConfig()
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
        url = f"{self.config.base_url}/book/{book_id}/"
        return await pw.get_with_retry(url, wait_selector=".book-info, .book-detail-wrap")

    async def get_chapter_content(self, chapter_url: str) -> str:
        """Fetch a chapter page."""
        pw = await self._ensure_browser()
        full_url = chapter_url if chapter_url.startswith("http") else f"{self.config.base_url}{chapter_url}"
        return await pw.get_with_retry(full_url, wait_selector=".read-content, .main-text")

    async def get_with_retry(self, url: str, wait_selector: str | None = None) -> str:
        """Generic page fetch with retry."""
        pw = await self._ensure_browser()
        return await pw.get_with_retry(url, wait_selector=wait_selector)

    def set_cookie(self, cookie: str) -> None:
        """Set cookie for authenticated requests."""
        self._cookie = cookie
        if self._pw:
            self._pw.set_cookie(cookie)

    async def close(self) -> None:
        if self._pw:
            await self._pw.stop()
            self._pw = None