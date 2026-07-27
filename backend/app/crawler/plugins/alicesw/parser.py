"""AliceSW HTML parsers using BeautifulSoup."""

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook
from app.crawler.plugins.alicesw.config import AliceSWConfig


class AliceSWParser:
    """HTML parser for AliceSW pages."""

    def __init__(self, config: Optional[AliceSWConfig] = None):
        self.config = config or AliceSWConfig()
        self.sel = self.config.selectors

    def parse_bookshelf(self, html: str) -> list[RemoteShelfBook]:
        """Parse the bookshelf page into a list of shelf book entries."""
        soup = BeautifulSoup(html, "lxml")
        items = soup.select(self.sel["bookshelf_item"])
        books: list[RemoteShelfBook] = []

        for item in items:
            title_el = item.select_one(self.sel["bookshelf_title"])
            author_el = item.select_one(self.sel["bookshelf_author"])
            link_el = item.select_one(self.sel["bookshelf_link"])
            latest_el = item.select_one(self.sel["bookshelf_latest"])

            if not link_el:
                continue

            href = link_el.get("href", "")
            book_id = self._extract_book_id(href)

            books.append(RemoteShelfBook(
                source_book_id=book_id,
                title=title_el.get_text(strip=True) if title_el else "Unknown",
                author=author_el.get_text(strip=True) if author_el else "Unknown",
                url=self._make_absolute(href),
                latest_chapter_title=latest_el.get_text(strip=True) if latest_el else None,
            ))

        return books

    def parse_book(self, html: str, url: str) -> RemoteBook:
        """Parse a book detail page into a RemoteBook."""
        soup = BeautifulSoup(html, "lxml")
        book_id = self._extract_book_id(url)

        title = self._get_text(soup, "book_title") or "Unknown"
        author = self._get_text(soup, "book_author") or "Unknown"
        description = self._get_text(soup, "book_description")
        status = self._get_text(soup, "book_status") or "unknown"

        # Parse chapter list
        chapter_items = soup.select(self.sel["chapter_item"])
        chapters: list[RemoteChapter] = []

        for index, item in enumerate(chapter_items, start=1):
            link = item.select_one(self.sel["chapter_link"])
            if not link:
                continue

            href = link.get("href", "")
            chapter_id = self._extract_chapter_id(href)
            chapter_title = self._get_text_from(item, "chapter_title") or f"Chapter {index}"

            chapters.append(RemoteChapter(
                source_chapter_id=chapter_id,
                title=chapter_title,
                url=self._make_absolute(href),
                chapter_number=index,
            ))

        return RemoteBook(
            source_book_id=book_id,
            title=title,
            author=author,
            description=description,
            status=status,
            chapters=chapters,
        )

    def parse_chapter_content(self, html: str) -> tuple[str, str]:
        """Parse chapter page. Returns (title, content)."""
        soup = BeautifulSoup(html, "lxml")

        title_el = soup.select_one(self.sel["chapter_title_sel"])
        title = title_el.get_text(strip=True) if title_el else "Untitled"

        content_el = soup.select_one(self.sel["chapter_content"])
        if content_el is None:
            return title, ""

        # Remove script and style tags
        for tag in content_el.find_all(["script", "style", "ins", "div.ad", "div.ads"]):
            tag.decompose()

        # Get text with newlines preserved
        paragraphs = []
        for p in content_el.find_all(["p", "br"]):
            text = p.get_text(strip=True)
            if text:
                paragraphs.append(text)

        if not paragraphs:
            text = content_el.get_text("\n", strip=True)
            paragraphs = [p for p in text.split("\n") if p.strip()]

        return title, "\n\n".join(paragraphs)

    def _get_text(self, soup: BeautifulSoup, key: str) -> Optional[str]:
        el = soup.select_one(self.sel[key])
        return el.get_text(strip=True) if el else None

    def _get_text_from(self, el: Tag, key: str) -> Optional[str]:
        child = el.select_one(self.sel[key])
        return child.get_text(strip=True) if child else el.get_text(strip=True)

    def _extract_book_id(self, url: str) -> str:
        """Extract book ID from URL like /book/12345 or /book/12345/..."""
        match = re.search(r"/book/([^/]+)", url)
        return match.group(1) if match else url.split("/")[-1]

    def _extract_chapter_id(self, url: str) -> str:
        """Extract chapter ID from URL."""
        match = re.search(r"/chapter/([^/?#]+)", url)
        if match:
            return match.group(1)
        # Fallback: use the filename without extension
        name = url.rstrip("/").split("/")[-1]
        return name.rsplit(".", 1)[0] or name

    def _make_absolute(self, href: str) -> str:
        """Convert relative URL to absolute."""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return self.config.base_url + href
        return self.config.base_url + "/" + href
