"""Fanqie Novel plugin skeleton."""

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook


class FanqiePlugin:
    """Plugin for fanqie.com / fanqienovel.com."""

    name = "fanqie"

    def __init__(self):
        self.base_url = "https://fanqienovel.com"
        self._cookie = ""

    async def fetch_book(self, url: str) -> RemoteBook:
        html = await self._get(url)
        raise NotImplementedError("Fanqie plugin: fetch_book not yet implemented")

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        html = await self._get(chapter.url)
        raise NotImplementedError("Fanqie plugin: fetch_chapter_content not yet implemented")

    async def fetch_bookshelf(self, cookie: str) -> list[RemoteShelfBook]:
        self.set_cookie(cookie)
        raise NotImplementedError("Fanqie plugin: fetch_bookshelf not yet implemented")

    async def update_book(self, url: str) -> RemoteBook | None:
        return await self.fetch_book(url)

    def set_cookie(self, cookie: str) -> None:
        self._cookie = cookie

    async def _get(self, url: str) -> str:
        import httpx
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
