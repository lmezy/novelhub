"""Qidian plugin -- skeleton for Qidian.com novel source."""

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook
from app.crawler.plugins.qidian.parser import QidianParser


class QidianPlugin:
    """Plugin for Qidian.com. Fill in with actual site selectors and logic."""

    name = "qidian"

    def __init__(self):
        self.base_url = "https://www.qidian.com"
        self._cookie = ""
        self.parser = QidianParser()

    async def fetch_book(self, url: str) -> RemoteBook:
        """Parse a Qidian book page."""
        html = await self._get(url)
        # TODO: Implement BeautifulSoup parsing for Qidian book pages
        raise NotImplementedError("Qidian plugin: fetch_book not yet implemented")

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        """Fetch a Qidian chapter content."""
        html = await self._get(chapter.url)
        # TODO: Parse chapter content div
        raise NotImplementedError("Qidian plugin: fetch_chapter_content not yet implemented")

    async def fetch_bookshelf(self, cookie: str) -> list[RemoteShelfBook]:
        """Fetch Qidian bookshelf."""
        self.set_cookie(cookie)
        # TODO: Implement bookshelf parsing
        raise NotImplementedError("Qidian plugin: fetch_bookshelf not yet implemented")

    async def update_book(self, url: str) -> RemoteBook | None:
        return await self.fetch_book(url)

    def set_cookie(self, cookie: str) -> None:
        self._cookie = cookie

    async def _get(self, url: str) -> str:
        import httpx
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": self.base_url + "/",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
