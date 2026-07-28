"""AliceSW plugin -- ties crawler, login, and parser into a NovelSourcePlugin."""

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook
from app.crawler.plugins.alicesw.config import AliceSWConfig
from app.crawler.plugins.alicesw.crawler import AliceSWCrawler
from app.crawler.plugins.alicesw.login import AliceSWLogin
from app.crawler.plugins.alicesw.parser import AliceSWParser
from app.crawler.plugins.alicesw.updater import AliceSWUpdater


class AliceSWPlugin:
    """Plugin for the AliceSW novel source site."""

    name = "alicesw"

    def __init__(self, config: AliceSWConfig | None = None):
        self.config = config or AliceSWConfig()
        self.login = AliceSWLogin(source_id=self.name)
        self.crawler = AliceSWCrawler(config=self.config)
        self.parser = AliceSWParser(config=self.config)
        self.updater = AliceSWUpdater(config=self.config, crawler=self.crawler, parser=self.parser)

    async def fetch_book(self, url: str) -> RemoteBook:
        html = await self.crawler.get(url)
        return self.parser.parse_book(html, url)

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        html = await self.crawler.get(chapter.url)
        title, content = self.parser.parse_chapter_content(html)
        return content

    async def fetch_bookshelf(self, cookie: str) -> list[RemoteShelfBook]:
        self.set_cookie(cookie)
        url = self.config.base_url + self.config.bookshelf_url
        html = await self.crawler.get(url)
        return self.parser.parse_bookshelf(html)

    async def update_book(self, url: str) -> RemoteBook | None:
        result = await self.updater.check_for_updates(url, set())
        return await self.fetch_book(url)

    def set_cookie(self, cookie: str) -> None:
        self.login.set_cookie(cookie)
        self.crawler.set_cookie(cookie)
        self.updater.set_cookie(cookie)
