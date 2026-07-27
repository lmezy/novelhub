from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.crawler.registry import get_plugin
from app.core.events import emit, EventType
from app.models import Author, Book, Chapter, Source
from app.services.storage import BookStorage
from app.services.search import search_service


class SyncService:
    def __init__(self, db: AsyncSession, storage: BookStorage | None = None):
        self.db = db
        self.storage = storage or BookStorage()

    async def sync_book(self, source_id: str, url: str) -> dict:
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")

        emit(EventType.SYNC_STARTED, source_id=source_id, url=url)
        logger.info("Starting sync for source={} url={}", source_id, url)

        plugin = get_plugin(source.plugin_name)
        remote_book = await plugin.fetch_book(url)

        author = await self._get_or_create_author(remote_book.author)
        book, is_new = await self._get_or_create_book(source.id, author.id, remote_book)

        self.storage.write_metadata(
            remote_book.author,
            remote_book.title,
            {
                "source_id": source.id,
                "source_book_id": remote_book.source_book_id,
                "title": remote_book.title,
                "author": remote_book.author,
                "description": remote_book.description,
                "status": remote_book.status,
            },
        )

        search_service.index_book({
            "id": book.id,
            "title": book.title,
            "author": remote_book.author,
            "description": book.description or "",
            "status": book.status or "",
            "source_id": book.source_id or "",
            "author_id": book.author_id or "",
        })

        if is_new:
            emit(EventType.BOOK_CREATED, book_id=book.id, title=book.title)
        else:
            emit(EventType.BOOK_UPDATED, book_id=book.id, title=book.title)

        created = 0
        skipped = 0
        for remote_chapter in remote_book.chapters:
            existing = await self.db.scalar(
                select(Chapter).where(
                    Chapter.book_id == book.id,
                    Chapter.source_chapter_id == remote_chapter.source_chapter_id,
                )
            )
            if existing:
                skipped += 1
                continue

            content = await plugin.fetch_chapter_content(remote_chapter)
            content_path, content_hash = self.storage.write_chapter(
                remote_book.author,
                remote_book.title,
                remote_chapter.chapter_number,
                remote_chapter.title,
                content,
            )
            chapter = Chapter(
                id=str(uuid4()),
                book_id=book.id,
                chapter_number=remote_chapter.chapter_number,
                source_chapter_id=remote_chapter.source_chapter_id,
                title=remote_chapter.title,
                content_path=content_path,
                hash=content_hash,
            )
            self.db.add(chapter)
            await self.db.flush()

            search_service.index_chapter({
                "id": chapter.id,
                "book_id": book.id,
                "title": chapter.title or "",
                "chapter_number": chapter.chapter_number,
                "content": content[:5000],
            })

            emit(EventType.CHAPTER_CREATED, chapter_id=chapter.id, book_id=book.id)
            created += 1

        await self.db.commit()
        emit(EventType.SYNC_COMPLETED, book_id=book.id, created=created, skipped=skipped)
        logger.info("Sync complete book={} created={} skipped={}", book.id, created, skipped)
        return {
            "book_id": book.id,
            "created_chapters": created,
            "skipped_chapters": skipped,
        }

    async def _get_or_create_author(self, name: str) -> Author:
        author = await self.db.scalar(select(Author).where(Author.name == name))
        if author:
            return author
        author = Author(id=str(uuid4()), name=name)
        self.db.add(author)
        await self.db.flush()
        return author

    async def _get_or_create_book(self, source_id: str, author_id: str, remote_book) -> tuple[Book, bool]:
        book = await self.db.scalar(
            select(Book).where(
                Book.source_id == source_id,
                Book.source_book_id == remote_book.source_book_id,
            )
        )
        if book:
            book.title = remote_book.title
            book.author_id = author_id
            book.description = remote_book.description
            book.status = remote_book.status
            await self.db.flush()
            return book, False

        book = Book(
            id=str(uuid4()),
            source_id=source_id,
            author_id=author_id,
            source_book_id=remote_book.source_book_id,
            title=remote_book.title,
            description=remote_book.description,
            status=remote_book.status,
        )
        self.db.add(book)
        await self.db.flush()
        return book, True
