"""Generate EPUB files from stored book chapters."""

import io
from uuid import uuid4

from ebooklib import epub
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
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
        chapter_list = list(chapters)

        epub_book = epub.EpubBook()
        epub_book.set_identifier(str(uuid4()))
        epub_book.set_title(book.title)
        epub_book.set_language("zh")
        if book.author_name:
            epub_book.add_author(book.author_name)

        epub_book.add_metadata("DC", "description", book.description or "")

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

        for ch in chapter_list:
            content = self.storage.read_chapter(ch.content_path)
            # Convert markdown-style to basic HTML
            html_content = self._markdown_to_html(content)

            c = epub.EpubHtml(
                title=ch.title or f"Chapter {ch.chapter_number}",
                file_name=f"chapter_{ch.chapter_number:04d}.xhtml",
                lang="zh",
            )
            c.content = f"""<h1>{ch.title or f'Chapter {ch.chapter_number}'}</h1>
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
        logger.info("Generated EPUB for book={} chapters={}", book_id, len(chapter_list))
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
