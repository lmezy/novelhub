"""LocalMarkdownPlugin -- import locally stored Markdown book directories.

Reads books already stored in the NovelHub file layout:
  {root}/{author}/{title}/metadata.json
  {root}/{author}/{title}/000001.md
"""

import json
import re
from pathlib import Path

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook


class LocalMarkdownPlugin:
    """Plugin for importing locally stored markdown books.

    URL is a local directory path pointing to a book folder
    that contains metadata.json and numbered .md chapter files.
    """

    name = "local_markdown"

    async def fetch_book(self, url: str) -> RemoteBook:
        book_dir = Path(url)
        if not book_dir.is_dir():
            raise ValueError(f"Not a directory: {url}")

        meta_path = book_dir / "metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {}

        author = book_dir.parent.name
        title = meta.get("title", book_dir.name)

        chapters: list[RemoteChapter] = []
        md_files = sorted(book_dir.glob("[0-9]*.md"))
        for md_file in md_files:
            match = re.match(r"^(\d+)", md_file.stem)
            num = int(match.group(1)) if match else len(chapters) + 1
            first_line = md_file.read_text(encoding="utf-8").split("\n")[0]
            ch_title = first_line.lstrip("#").strip() or f"Chapter {num}"
            chapters.append(RemoteChapter(
                source_chapter_id=str(num),
                title=ch_title,
                url=str(md_file),
                chapter_number=num,
            ))

        return RemoteBook(
            source_book_id=book_dir.name,
            title=title,
            author=author,
            description=meta.get("description"),
            status=meta.get("status"),
            chapters=chapters,
            tags=meta.get("tags", []),
        )

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        path = Path(chapter.url)
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n", 1)
        return lines[1] if len(lines) > 1 else ""

    async def fetch_bookshelf(self, cookie: str) -> list[RemoteShelfBook]:
        return []

    async def update_book(self, url: str) -> RemoteBook | None:
        return await self.fetch_book(url)

    def set_cookie(self, cookie: str) -> None:
        pass

    async def auto_login(self, username: str, password: str) -> str | None:
        return None

    async def discover_books(self, url: str | None = None, page: int = 1) -> list[RemoteShelfBook]:
        """Not applicable for local markdown imports."""
        return []
