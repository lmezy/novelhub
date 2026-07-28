"""NovelSourcePlugin template -- extend this to add a new novel website.

Copy this file to crawler/plugins/your_site/ and implement each method.
See alicesw/ for a complete example.
"""

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook


class MySitePlugin:
    """Template plugin -- replace class name and fill in methods."""

    name = "my_site"

    def __init__(self):
        self.base_url = "https://example.com"
        self._cookie = ""

    # ---- Required ----

    async def fetch_book(self, url: str) -> RemoteBook:
        """Fetch book info + chapter list from a book page URL.
        
        1. HTTP GET the URL
        2. Parse HTML to extract: title, author, description, status
        3. Parse chapter list: each with source_chapter_id, title, url, chapter_number
        4. Return a RemoteBook
        """
        raise NotImplementedError

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        """Fetch and return the full text of a single chapter."""
        raise NotImplementedError

    async def fetch_bookshelf(self, cookie: str) -> list[RemoteShelfBook]:
        """Fetch the user bookshelf page, return list of books."""
        raise NotImplementedError

    async def update_book(self, url: str) -> RemoteBook | None:
        """Re-fetch a book to check for new chapters."""
        return await self.fetch_book(url)

    def set_cookie(self, cookie: str) -> None:
        """Set the cookie string for authenticated requests."""
        self._cookie = cookie

    # ---- Helpers (implement or override) ----

    async def _get(self, url: str) -> str:
        """HTTP GET with cookie support. Override for custom headers."""
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

    @staticmethod
    def _make_absolute(href: str, base: str) -> str:
        """Convert relative URL to absolute."""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return base + href
        return base + "/" + href
