from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.crawler.registry import get_plugin
from app.core.events import emit, EventType
from app.models import Author, Book, BookTag, Chapter, Cookie, Source, Tag
from app.repositories.tag import TagRepository
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

        config = source.config if source.plugin_name == 'yuedu' else None
        plugin = get_plugin(source.plugin_name, config=config)
        remote_book = await plugin.fetch_book(url)

        author = await self._get_or_create_author(remote_book.author)
        book, is_new = await self._get_or_create_book(source.id, author.id, remote_book)

        # Save tags from remote book
        if remote_book.tags:
            await self._save_tags(book.id, remote_book.tags)

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
        total = len(remote_book.chapters)
        batch = 0
        for remote_chapter in remote_book.chapters:
            batch += 1
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

            # Commit every 20 chapters so partial progress is saved on failure
            if batch % 20 == 0:
                await self.db.commit()
                logger.debug("Checkpoint: {}/{} chapters synced for book {}", created, total, book.id)

        await self.db.commit()
        emit(EventType.SYNC_COMPLETED, book_id=book.id, created=created, skipped=skipped)
        logger.info("Sync complete book={} created={} skipped={}", book.id, created, skipped)

        # Auto-categorize after sync (if new book or new tags)
        try:
            from app.services.auto_categorize import AutoCategorizationService
            await AutoCategorizationService.categorize_book(self.db, book.id)
        except Exception:
            pass

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

    async def sync_bookshelf(self, source_id: str) -> dict:
        """Sync all books from a user's bookshelf."""
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")

        cookie_record = await self.db.scalar(
            select(Cookie).where(Cookie.source == source_id)
        )
        if cookie_record is None:
            raise ValueError(f"No cookie found for source '{source_id}'")

        config = source.config if source.plugin_name == 'yuedu' else None
        plugin = get_plugin(source.plugin_name, config=config)
        plugin.set_cookie(cookie_record.cookie_data)

        shelf_books = await plugin.fetch_bookshelf(cookie_record.cookie_data)
        logger.info("Found {} books on bookshelf for source={}", len(shelf_books), source_id)

        results = []
        for shelf_book in shelf_books:
            try:
                result = await self.sync_book(source_id, shelf_book.url)
                results.append({"book_id": result["book_id"], "status": "ok"})
            except Exception as exc:
                logger.opt(exception=exc).warning(
                    "Failed to sync shelf book {}", shelf_book.url
                )
                results.append({"url": shelf_book.url, "status": "failed", "error": str(exc)})

        return {"source_id": source_id, "total": len(shelf_books), "results": results}

    async def _save_tags(self, book_id: str, tag_names: list[str]) -> None:
        """Create or get tags and associate them with the book."""
        tag_repo = TagRepository(self.db)
        # Remove old tag associations
        old_tags = await self.db.scalars(
            select(BookTag).where(BookTag.book_id == book_id)
        )
        for bt in old_tags:
            await self.db.delete(bt)

        for name in tag_names:
            name = name.strip().lower()
            if not name:
                continue
            tag = await tag_repo.get_by_name(name)
            if tag is None:
                tag = Tag(id=str(uuid4()), name=name)
                self.db.add(tag)
                await self.db.flush()
            bt = BookTag(book_id=book_id, tag_id=tag.id)
            self.db.add(bt)
        await self.db.flush()
    async def resync_book(self, book_id: str) -> dict:
        """Re-sync a book already in the library from its source."""
        book = await self.db.get(Book, book_id)
        if book is None:
            raise ValueError("Book not found")
        if not book.source_id or not book.source_book_id:
            raise ValueError("Book has no source reference")
        source = await self.db.get(Source, book.source_id)
        if source is None:
            raise ValueError("Source not found")
        config = source.config if source.plugin_name == 'yuedu' else None
        plugin = get_plugin(source.plugin_name, config=config)
        # Construct URL from config
        cfg = plugin.config if hasattr(plugin, 'config') else None
        if cfg and hasattr(cfg, 'book_url'):
            url = cfg.base_url + cfg.book_url.format(book_id=book.source_book_id)
        else:
            url = source.url or ""
            if not url:
                raise ValueError("Cannot determine book URL for re-sync")
        return await self.sync_book(book.source_id, url)

    async def discover_and_sync(
        self,
        source_id: str,
        url: str | None = None,
        page: int = 1,
        sync: bool = True,
    ) -> dict:
        """Discover books from a source's explore/catalog page and optionally sync them.

        Uses the plugin's discover_books method if available. Falls back
        to returning an empty list for plugins that don't implement it.
        """
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")

        config = source.config if source.plugin_name == "yuedu" else None
        plugin = get_plugin(source.plugin_name, config=config)

        if not hasattr(plugin, "discover_books"):
            return {
                "source_id": source_id,
                "books_found": 0,
                "books_synced": 0,
                "details": [],
            }

        shelf_books = await plugin.discover_books(url=url, page=page)
        books_found = len(shelf_books)
        books_synced = 0
        details = []

        if sync:
            for sb in shelf_books:
                try:
                    result = await self.sync_book(source_id, sb.url)
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": True,
                        "book_id": result.get("book_id"),
                    })
                    books_synced += 1
                except Exception as exc:
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": False,
                        "error": str(exc),
                    })
        else:
            for sb in shelf_books:
                details.append({
                    "title": sb.title,
                    "author": sb.author,
                    "url": sb.url,
                    "synced": False,
                })

        return {
            "source_id": source_id,
            "books_found": books_found,
            "books_synced": books_synced,
            "details": details,
        }
