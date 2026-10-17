"""Generate EPUB files from stored book chapters."""

import io
from uuid import uuid4

from ebooklib import epub
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from loguru import logger

from app.models import Book, Chapter
from app.services.storage import BookStorage


class EpubService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = BookStorage()

    async def generate(self, book_id: str) -> bytes:
        book = await self.db.get(Book, book_id)
        if book is None:
            raise ValueError("Book not found")

        chapters = await self.db.scalars(
            select(Chapter)
            .where(Chapter.book_id == book_id)
            .order_by(Chapter.chapter_number.asc())
        )
        # Snapshot plain data NOW: the packing stage runs in a worker thread,
        # where touching ORM attributes on this session would be unsafe.
        meta = {
            "title": book.title,
            "author_name": book.author_name,
            "description": book.description or "",
        }
        chapter_data = [
            (ch.chapter_number, ch.title, ch.content_path) for ch in chapters
        ]

        # Reading hundreds of chapter files, converting them and packing the
        # EPUB are blocking CPU/disk work; doing it inline froze the event
        # loop for tens of seconds on thousand-chapter books.
        result = await run_in_threadpool(self._build_epub, meta, chapter_data)
        logger.info("Generated EPUB for book={} chapters={}", book_id, len(chapter_data))
        return result

    def _build_epub(self, meta: dict, chapter_data: list[tuple]) -> bytes:
        epub_book = epub.EpubBook()
        epub_book.set_identifier(str(uuid4()))
        epub_book.set_title(meta["title"])
        epub_book.set_language("zh")
        if meta["author_name"]:
            epub_book.add_author(meta["author_name"])

        epub_book.add_metadata("DC", "description", meta["description"])

        # Default CSS
        style = epub.EpubItem(
            uid="style",
            file_name="style/default.css",
            media_type="text/css",
            content="p { text-indent: 2em; margin: 0.5em 0; line-height: 1.8; }",
        )
        epub_book.add_item(style)

        spine = ["nav"]
        epub_chapters = []

        for number, title, content_path in chapter_data:
            content = self.storage.read_chapter(content_path)
            # Convert markdown-style to basic HTML
            html_content = self._markdown_to_html(content)

            heading = title or f"Chapter {number}"
            c = epub.EpubHtml(
                title=heading,
                file_name=f"chapter_{number:04d}.xhtml",
                lang="zh",
            )
            c.content = f"""<h1>{heading}</h1>
{html_content}""".encode("utf-8")
            c.add_item(style)
            epub_book.add_item(c)
            epub_chapters.append(c)
            spine.append(c)

        epub_book.toc = epub_chapters
        epub_book.add_item(epub.EpubNcx())
        epub_book.add_item(epub.EpubNav())
        epub_book.spine = spine

        buf = io.BytesIO()
        epub.write_epub(buf, epub_book)
        return buf.getvalue()

    @staticmethod
    def _markdown_to_html(content: str) -> str:
        """Simple markdown-to-HTML for novel content (paragraphs only)."""
        lines = content.strip().split("\n")
        html_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("# "):
                html_lines.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                html_lines.append(f"<h2>{line[3:]}</h2>")
            else:
                html_lines.append(f"<p>{line}</p>")
        return "\n".join(html_lines)
