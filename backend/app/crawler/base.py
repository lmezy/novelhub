from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class RemoteChapter:
    source_chapter_id: str
    title: str
    url: str
    chapter_number: int
    next_url: str | None = None


@dataclass(frozen=True)
class RemoteBook:
    source_book_id: str
    title: str
    author: str
    description: str | None
    status: str | None
    chapters: list[RemoteChapter]
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RemoteShelfBook:
    source_book_id: str
    title: str
    author: str
    url: str
    latest_chapter_title: str | None = None


class NovelSourcePlugin(Protocol):
    name: str

    async def fetch_book(self, url: str) -> RemoteBook:
        ...

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        ...

    async def fetch_bookshelf(self, cookie: str) -> list[RemoteShelfBook]:
        ...

    async def update_book(self, url: str) -> RemoteBook | None:
        ...

    def set_cookie(self, cookie: str) -> None:
        ...

    async def auto_login(self, username: str, password: str) -> str | None:
        """Attempt auto-login with credentials. Returns cookie string on success, None on failure."""
        ...

    async def discover_books(self, url: str | None = None, page: int = 1) -> list[RemoteShelfBook]:
        """Discover books from a catalog/explore/ranking page.

        Each plugin implements site-specific parsing. The yuedu plugin
        uses exploreUrl rules from the source JSON.
        """
        ...
