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
    cover_url: str | None = None
    is_r18: bool = False


@dataclass(frozen=True)
class RemoteShelfBook:
    source_book_id: str
    title: str
    author: str
    url: str
    latest_chapter_title: str | None = None
    # Catalog sources can supply a category before the detail page is fetched.
    # Keep it so sync can retain that classification when detail rules are sparse.
    tags: list[str] = field(default_factory=list)


class EmptyTocError(RuntimeError):
    """The source's own TOC rule produced no chapters for a book.

    Raised when ``ruleBookInfo.tocUrl`` resolves to a dedicated catalogue page
    and neither ``ruleToc`` nor a generic scan of that page returns a chapter
    list.  The book detail page is *not* a fallback table of contents: on
    御宅屋 (yswhub.cc) its only ``/read/*.html`` links are 相关推荐 /
    作者其他作品, so scanning it invented chapters that were really other
    books -- every one of them then failed with "Chapter returned empty
    content" (601 warnings in 15 hours on the live crawler).

    Callers treat this as "skip this book", not as a failure: the source rule is
    stale and retrying cannot fix it.
    """


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
