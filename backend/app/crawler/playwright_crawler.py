"""Playwright-based crawler for JS-rendered novel sites.

Usage in plugins:
    crawler = PlaywrightCrawler()
    await crawler.start()
    html = await crawler.get("https://example.com/book/123")
    await crawler.stop()
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from loguru import logger


class PlaywrightCrawler:
    """Headless browser crawler with cookie and rate-limit support."""

    def __init__(
        self,
        headless: bool = True,
        rate_limit: float = 1.0,
        timeout: float = 30_000,
        user_agent: str | None = None,
    ):
        self._headless = headless
        self._rate_limit = rate_limit
        self._timeout = timeout
        self._user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
        self._browser = None
        self._context = None
        self._cookie_str: str = ""
        self._last_request: float = 0

    async def start(self) -> None:
        """Launch the browser. Call once before any requests."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "playwright is required. Install with: pip install playwright && playwright install chromium"
            )

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = await self._browser.new_context(
            user_agent=self._user_agent,
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
        )
        if self._cookie_str:
            await self._apply_cookies()
        logger.info("Playwright browser launched (headless={})", self._headless)

    async def stop(self) -> None:
        """Shut down the browser."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_pw"):
            await self._pw.stop()
        logger.info("Playwright browser stopped")

    async def get(self, url: str, wait_selector: str | None = None) -> str:
        """Fetch a page and return its HTML content."""
        if self._context is None:
            raise RuntimeError("Call start() before making requests")

        await self._rate_limit_wait()

        page = await self._context.new_page()
        try:
            logger.debug("Playwright GET {}", url)
            await page.goto(url, timeout=self._timeout, wait_until="domcontentloaded")
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=self._timeout)
            content = await page.content()
            return content
        finally:
            await page.close()

    async def get_with_retry(
        self, url: str, wait_selector: str | None = None, max_retries: int = 3
    ) -> str:
        """Fetch with automatic retry on failure."""
        last_exc = None
        for attempt in range(max_retries):
            try:
                return await self.get(url, wait_selector=wait_selector)
            except Exception as exc:
                last_exc = exc
                wait_s = 2 ** attempt
                logger.warning(
                    "Playwright GET {} failed (attempt {}/{}), retrying in {}s",
                    url, attempt + 1, max_retries, wait_s,
                )
                await asyncio.sleep(wait_s)
        raise last_exc

    def set_cookie(self, cookie_str: str) -> None:
        """Set cookie string (same format as HTTP Cookie header)."""
        self._cookie_str = cookie_str

    async def _apply_cookies(self) -> None:
        """Parse cookie string and set on browser context."""
        if not self._cookie_str or self._context is None:
            return
        cookies = []
        for item in self._cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                name, _, value = item.partition("=")
                cookies.append({"name": name.strip(), "value": value.strip(), "domain": "", "path": "/"})
        if cookies:
            await self._context.add_cookies(cookies)
            logger.debug("Applied {} cookies to browser context", len(cookies))

    async def _rate_limit_wait(self) -> None:
        """Ensure minimum interval between requests."""
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)
        self._last_request = time.monotonic()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.stop()