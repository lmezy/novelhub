"""LocalMarkdownPlugin -- import locally stored Markdown book directories.

Reads books already stored in the NovelHub file layout:
  {root}/{author}/{title}/metadata.json
  {root}/{author}/{title}/000001.md
"""

import json
import re
from pathlib import Path

from app.crawler.base import RemoteBook, RemoteChapter, RemoteShelfBook
from app.services.local_file_parser import parse_local_file


class LocalMarkdownPlugin:
    """Plugin for importing locally stored markdown books.

    URL is a local directory path pointing to a book folder
    that contains metadata.json and numbered .md chapter files.
    """

    name = "local_markdown"

    def __init__(self) -> None:
        self._file_cache: dict[
            str,
            tuple[float, int, dict, list[tuple[str, str]]],
        ] = {}

    async def fetch_book(self, url: str) -> RemoteBook:
        book_dir = Path(str(url).replace("file://", ""))
        if book_dir.is_file():
            return self._fetch_book_file(book_dir)
        if not book_dir.is_dir():
            raise ValueError(f"Not a directory: {url}")

        meta_path = book_dir / "metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {}

        author = meta.get("author", book_dir.parent.name)
        title = meta.get("title", book_dir.name)

        chapters: list[RemoteChapter] = []
        md_files = sorted(book_dir.glob("[0-9]*.md"))
        if not md_files:
            md_files = sorted(book_dir.glob("*.md"))
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

    def _load_file_cache(self, path: Path):
        stat = path.stat()
        key = str(path)
        cached = self._file_cache.get(key)
        if (
            cached is not None
            and cached[0] == stat.st_mtime
            and cached[1] == stat.st_size
        ):
            return cached[2], cached[3]
        meta, chapters = parse_local_file(path)
        self._file_cache[key] = (
            stat.st_mtime,
            stat.st_size,
            meta,
            chapters,
        )
        return meta, chapters

    def _fetch_book_file(self, path: Path) -> RemoteBook:
        meta, chapters = self._load_file_cache(path)
        remote_chapters = []
        for index, (title, _content) in enumerate(chapters, start=1):
            remote_chapters.append(RemoteChapter(
                source_chapter_id=f"local:{path}:{index}",
                title=title or f"Chapter {index}",
                url=str(path),
                chapter_number=index,
            ))
        return RemoteBook(
            source_book_id=f"local:{path}",
            title=str(meta.get("title") or path.stem),
            author=str(meta.get("author") or "Unknown"),
            description=None,
            status=None,
            chapters=remote_chapters,
            tags=[],
        )

    async def fetch_chapter_content(self, chapter: RemoteChapter) -> str:
        if chapter.source_chapter_id.startswith("local:"):
            path = Path(chapter.url)
            meta, chapters = self._load_file_cache(path)
            index = int(chapter.source_chapter_id.rsplit(":", 1)[1])
            return chapters[index - 1][1]
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
