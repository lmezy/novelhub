"""AliceSW incremental updater -- detects and fetches only new chapters."""

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.crawler.base import RemoteBook, RemoteChapter
from app.crawler.plugins.alicesw.config import AliceSWConfig
from app.crawler.plugins.alicesw.crawler import AliceSWCrawler
from app.crawler.plugins.alicesw.parser import AliceSWParser


@dataclass
class UpdateResult:
    """Result of an incremental update check."""
    book_id: str
    total_remote: int
    new_chapters: int
    updated_chapters: int
    unchanged: int
    chapters: list[RemoteChapter]


class AliceSWUpdater:
    """Handles incremental updates for AliceSW books.

    Compares remote chapter listings against known local state to avoid
    re-downloading unchanged chapters.
    """

    def __init__(
        self,
        config: Optional[AliceSWConfig] = None,
        crawler: Optional[AliceSWCrawler] = None,
        parser: Optional[AliceSWParser] = None,
    ):
        self.config = config or AliceSWConfig()
        self.crawler = crawler or AliceSWCrawler(config=self.config)
        self.parser = parser or AliceSWParser(config=self.config)

    async def check_for_updates(
        self,
        url: str,
        known_chapter_ids: set[str],
    ) -> UpdateResult:
        """Fetch remote book info and compare against known chapter IDs.

        Args:
            url: The book detail page URL.
            known_chapter_ids: Set of chapter IDs already stored locally.

        Returns:
            UpdateResult with counts and the list of new/updated chapters.
        """
        logger.info("Checking updates for book at {}", url)
        html = await self.crawler.get(url)
        remote_book = self.parser.parse_book(html, url)

        new_chapters: list[RemoteChapter] = []
        updated_count = 0
        unchanged = 0

        for ch in remote_book.chapters:
            if ch.source_chapter_id not in known_chapter_ids:
                new_chapters.append(ch)
            else:
                unchanged += 1

        logger.info(
            "Update check: {} remote chapters, {} new, {} unchanged",
            len(remote_book.chapters),
            len(new_chapters),
            unchanged,
        )

        return UpdateResult(
            book_id=remote_book.source_book_id,
            total_remote=len(remote_book.chapters),
            new_chapters=len(new_chapters),
            updated_chapters=updated_count,
            unchanged=unchanged,
            chapters=new_chapters,
        )

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        """Fetch the full text content of a single chapter."""
        html = await self.crawler.get(chapter.url)
        _, content = self.parser.parse_chapter_content(html)
        return content

    async def fetch_new_chapters(
        self,
        url: str,
        known_chapter_ids: set[str],
    ) -> UpdateResult:
        """Convenience: check for updates and return only new chapters.

        This is the primary entry point for incremental sync: it fetches
        the remote book info, identifies chapters not yet stored locally,
        and returns the list ready for download.
        """
        return await self.check_for_updates(url, known_chapter_ids)

    def set_cookie(self, cookie: str) -> None:
        """Propagate cookie to the underlying crawler."""
        self.crawler.set_cookie(cookie)

    async def close(self) -> None:
        """Clean up HTTP client resources."""
        await self.crawler.close()
