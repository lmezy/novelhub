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

    async def discover_books(self, url: str | None = None, page: int = 1) -> list[RemoteShelfBook]:
        """Not implemented for AliceSW. Use yuedu plugin for catalog discovery."""
        return []

    async def auto_login(self, username: str, password: str) -> str | None:
        """Attempt form-based login to AliceSW and return cookie string on success."""
        import httpx
        from bs4 import BeautifulSoup
        from loguru import logger

        login_url = f"{self.config.base_url}/login"
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                trust_env=False,
            ) as client:
                resp = await client.get(login_url)
                soup = BeautifulSoup(resp.text, "lxml")
                csrf = soup.select_one('input[name="_token"], input[name="csrf_token"], meta[name="csrf-token"]')
                token = csrf.get("content") or csrf.get("value", "") if csrf else ""
                form_data = {"username": username, "password": password}
                if token:
                    form_data["_token"] = token
                login_resp = await client.post(login_url, data=form_data)
                cookies = login_resp.headers.get_all("set-cookie")
                if cookies:
                    cookie_str = "; ".join(c.split(";")[0] for c in cookies)
                    if len(cookie_str) > 20:
                        self.set_cookie(cookie_str)
                        logger.info("AliceSW auto-login successful for {}", username)
                        return cookie_str
        except Exception as exc:
            logger.warning("AliceSW auto-login failed: {}", exc)
        return None
