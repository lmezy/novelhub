"""AliceSW HTTP crawler with rate limiting, retries, and cookie support."""

import asyncio
import time
from typing import Optional

import httpx
from loguru import logger

from app.crawler.plugins.alicesw.config import AliceSWConfig


class AliceSWCrawler:
    """HTTP client for AliceSW with built-in rate limiting."""

    def __init__(self, config: Optional[AliceSWConfig] = None):
        self.config = config or AliceSWConfig()
        self._last_request: float = 0
        self._cookie_header: str = ""
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Referer": self.config.base_url + "/",
            }
            if self._cookie_header:
                headers["Cookie"] = self._cookie_header
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    def set_cookie(self, cookie: str) -> None:
        self._cookie_header = cookie.strip().replace("\n", "")
        self._client = None

    async def _rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self.config.request_interval:
            await asyncio.sleep(self.config.request_interval - elapsed)
        self._last_request = time.monotonic()

    async def get(self, url: str) -> str:
        client = await self._get_client()
        last_error: Optional[Exception] = None
        for attempt in range(self.config.max_retries):
            try:
                await self._rate_limit()
                logger.debug("GET {} (attempt {})", url, attempt + 1)
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "charset=" in content_type:
                    encoding = content_type.split("charset=")[-1].strip()
                    return resp.content.decode(encoding, errors="replace")
                raw = resp.content
                try:
                    return raw.decode(self.config.page_encoding)
                except UnicodeDecodeError:
                    return raw.decode("gbk", errors="replace")
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 503):
                    wait = 5 * (attempt + 1)
                    logger.warning("Rate limited on {}, waiting {}s", url, wait)
                    await asyncio.sleep(wait)
                    last_error = e
                    continue
                raise
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("Request failed for {}: {}, retrying in {}s", url, e, wait)
                    await asyncio.sleep(wait)
                    continue
                raise
        raise last_error or RuntimeError(f"Failed to fetch {url}")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
