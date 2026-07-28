"""Fanqie plugin -- Playwright-based crawler for fanqienovel.com."""

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook
from app.crawler.plugins.fanqie.parser import FanqieParser
from app.crawler.plugins.fanqie.crawler import FanqieCrawler
from app.crawler.plugins.fanqie.config import FanqieConfig


class FanqiePlugin:
    """Plugin for fanqienovel.com with Playwright JS rendering."""

    name = "fanqie"

    def __init__(self):
        self.config = FanqieConfig()
        self.parser = FanqieParser()
        self.crawler: FanqieCrawler | None = None
        self._cookie: str = ""

    async def _ensure_crawler(self) -> FanqieCrawler:
        if self.crawler is None:
            self.crawler = FanqieCrawler(config=self.config)
            if self._cookie:
                self.crawler.set_cookie(self._cookie)
        return self.crawler

    async def fetch_book(self, url: str) -> RemoteBook:
        """Fetch and parse a Fanqie book page."""
        crawler = await self._ensure_crawler()
        import re
        bid = re.search(r"/page/(\d+)", url)
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
        """Fetch Fanqie bookshelf (requires login cookie)."""
        self.set_cookie(cookie)
        crawler = await self._ensure_crawler()
        url = f"{self.config.base_url}/shelf"
        html = await crawler.get_with_retry(url)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        books = []
        for item in soup.select(".shelf-item, .book-item, .book-card"):
            link = item.select_one("a")
            if not link:
                continue
            title_el = item.select_one(".title, .book-title, h3")
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