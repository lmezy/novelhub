from uuid import uuid4
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.crawler.registry import get_plugin
from app.core.events import emit, EventType
from app.models import Author, Book, BookTag, Chapter, Cookie, Source, Tag
from app.repositories.tag import TagRepository
from app.services.storage import BookStorage
from app.services.search import search_service
from app.services.cookie_crypto import safe_decrypt_cookie
from app.services.r18 import detect_r18


class SyncService:
    def __init__(self, db: AsyncSession, storage: BookStorage | None = None):
        self.db = db
        self.storage = storage or BookStorage()

    @staticmethod
    def _safe_text(value, fallback: str) -> str:
        text = str(value).strip() if value else ""
        return text or fallback

    def _safe_title(self, remote_book) -> str:
        return self._safe_text(remote_book.title, remote_book.source_book_id or "Unknown")

    @staticmethod
    def _safe_author(name: str | None) -> str:
        return SyncService._safe_text(name, "Unknown")

    @staticmethod
    def _is_http_url(url: str) -> bool:
        return url.startswith(("http://", "https://"))

    @staticmethod
    def _is_book_r18(source: Source, remote_book) -> bool:
        return detect_r18(
            source_is_r18=getattr(source, "is_r18", False),
            title=remote_book.title,
            author=remote_book.author,
            description=remote_book.description,
            tags=getattr(remote_book, "tags", []) or [],
        )

    async def sync_book(self, source_id: str, url: str) -> dict:
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")
        if source.plugin_name != "local_markdown" and not self._is_http_url(url):
            raise ValueError(f"Unsupported book URL: {url}")

        emit(EventType.SYNC_STARTED, source_id=source_id, url=url)
        logger.info("Starting sync for source={} url={}", source_id, url)

        config = source.config if source.plugin_name == 'yuedu' else None
        plugin = get_plugin(source.plugin_name, config=config)
        remote_book = await plugin.fetch_book(url)
        if (
            not remote_book.chapters
            or str(remote_book.title or "").strip() in ("", "Unknown")
        ):
            raise ValueError(
                f"Book page returned no usable metadata/chapters: {url} "
                f"(title={remote_book.title!r}, chapters={len(remote_book.chapters)})"
            )

        book_title = self._safe_title(remote_book)
        author_name = self._safe_author(remote_book.author)
        is_r18 = self._is_book_r18(source, remote_book)

        author = await self._get_or_create_author(author_name)
        book, is_new = await self._get_or_create_book(
            source.id, author.id, remote_book, is_r18=is_r18
        )

        # Save remote tags plus the admin-only classification tag.
        classification_tag = "r18" if is_r18 else "all-ages"
        await self._save_tags(book.id, [*remote_book.tags, classification_tag])

        self.storage.write_metadata(
            author_name,
            book_title,
            {
                "source_id": source.id,
                "source_book_id": remote_book.source_book_id,
                "title": book_title,
                "author": author_name,
                "description": remote_book.description,
                "status": remote_book.status,
                "is_r18": is_r18,
            },
        )

        search_service.index_book({
            "id": book.id,
            "title": book.title,
            "description": book.description or "",
            "status": book.status or "",
            "source_id": book.source_id or "",
            "author_id": book.author_id or "",
            "is_r18": book.is_r18,
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
                author_name,
                book_title,
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
                "is_r18": book.is_r18,
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
            await self.db.rollback()

        return {
            "book_id": book.id,
            "created_chapters": created,
            "skipped_chapters": skipped,
        }

    async def _get_or_create_author(self, name: str) -> Author:
        # Fallback for empty/missing author names from book sources
        safe_name = name.strip() if name and name.strip() else "Unknown"
        author = await self.db.scalar(select(Author).where(Author.name == safe_name))
        if author:
            return author
        author = Author(id=str(uuid4()), name=safe_name)
        self.db.add(author)
        await self.db.flush()
        return author

    async def _get_or_create_book(
        self,
        source_id: str,
        author_id: str,
        remote_book,
        is_r18: bool = False,
    ) -> tuple[Book, bool]:
        book = await self.db.scalar(
            select(Book).where(
                Book.source_id == source_id,
                Book.source_book_id == remote_book.source_book_id,
            )
        )
        if book:
            book.title = self._safe_title(remote_book)
            book.author_id = author_id
            book.description = remote_book.description
            book.status = remote_book.status
            book.is_r18 = is_r18
            await self.db.flush()
            return book, False

        book = Book(
            id=str(uuid4()),
            source_id=source_id,
            author_id=author_id,
            source_book_id=remote_book.source_book_id,
            title=self._safe_title(remote_book),
            description=remote_book.description,
            status=remote_book.status,
            is_r18=is_r18,
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
        cookie_data = safe_decrypt_cookie(cookie_record.cookie_data)
        plugin.set_cookie(cookie_data)

        shelf_books = await plugin.fetch_bookshelf(cookie_data)
        logger.info("Found {} books on bookshelf for source={}", len(shelf_books), source_id)

        results = []
        for shelf_book in shelf_books:
            if not self._is_http_url(shelf_book.url):
                results.append({
                    "url": shelf_book.url,
                    "status": "skipped",
                    "error": "Unsupported URL",
                    "created_chapters": 0,
                    "skipped_chapters": 0,
                })
                continue
            try:
                result = await self.sync_book(source_id, shelf_book.url)
                results.append({"book_id": result["book_id"], "status": "ok", "created_chapters": result.get("created_chapters", 0), "skipped_chapters": result.get("skipped_chapters", 0)})
            except Exception as exc:
                logger.opt(exception=exc).warning(
                    "Failed to sync shelf book {}", shelf_book.url
                )
                await self.db.rollback()
                results.append({"url": shelf_book.url, "status": "failed", "error": str(exc), "created_chapters": 0, "skipped_chapters": 0})

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
        if hasattr(plugin, "build_book_url"):
            url = plugin.build_book_url(book.source_book_id)
        elif cfg and hasattr(cfg, 'book_url'):
            url = cfg.base_url + cfg.book_url.format(book_id=book.source_book_id)
        else:
            url = source.url or ""
            if not url:
                raise ValueError("Cannot determine book URL for re-sync")
            url = urljoin(url.rstrip("/") + "/", book.source_book_id)
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

        cookie_record = await self.db.scalar(
            select(Cookie).where(Cookie.source == source_id)
        )
        if cookie_record:
            plugin.set_cookie(safe_decrypt_cookie(cookie_record.cookie_data))

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
                if not self._is_http_url(sb.url):
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": False,
                        "error": "Unsupported URL",
                    })
                    continue
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
                    await self.db.rollback()
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

    async def discover_and_sync_all(
        self,
        source_id: str,
        url: str | None = None,
        max_pages: int = 200,
        sync: bool = True,
        progress_cb: Callable[[int, int, int, int], Awaitable[None]] | None = None,
        before_step: Callable[[], Awaitable[None]] | None = None,
        start_page: int = 1,
    ) -> dict:
        """Discover every book across catalog pages and optionally sync them."""
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")

        config = source.config if source.plugin_name == "yuedu" else None
        plugin = get_plugin(source.plugin_name, config=config)

        cookie_record = await self.db.scalar(
            select(Cookie).where(Cookie.source == source_id)
        )
        if cookie_record:
            plugin.set_cookie(safe_decrypt_cookie(cookie_record.cookie_data))

        if not hasattr(plugin, "discover_books"):
            return {
                "source_id": source_id,
                "pages_checked": 0,
                "books_found": 0,
                "books_synced": 0,
                "books_failed": 0,
                "chapters_created": 0,
                "chapters_skipped": 0,
                "details": [],
            }

        seen: set[str] = set()
        details: list[dict] = []
        books_found = 0
        books_synced = 0
        books_failed = 0
        chapters_created = 0
        chapters_skipped = 0
        pages_checked = 0
        start_page = max(1, int(start_page or 1))

        for page in range(start_page, max_pages + 1):
            if before_step is not None:
                await before_step()
            page_books = await plugin.discover_books(url=url, page=page)
            if not page_books:
                break
            pages_checked = page

            new_books = []
            for sb in page_books:
                key = sb.url.split("#", 1)[0].rstrip("/")
                if not key or key in seen or not self._is_http_url(key):
                    continue
                seen.add(key)
                new_books.append(sb)

            if not new_books:
                break

            for sb in new_books:
                if before_step is not None:
                    await before_step()
                books_found += 1
                if not sync:
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": False,
                    })
                    if progress_cb is not None:
                        await progress_cb(pages_checked, books_found, books_synced, books_failed)
                    continue
                try:
                    result = await self.sync_book(source_id, sb.url)
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": True,
                        "book_id": result.get("book_id"),
                        "created_chapters": result.get("created_chapters", 0),
                        "skipped_chapters": result.get("skipped_chapters", 0),
                    })
                    books_synced += 1
                    chapters_created += result.get("created_chapters", 0)
                    chapters_skipped += result.get("skipped_chapters", 0)
                except Exception as exc:
                    await self.db.rollback()
                    books_failed += 1
                    logger.warning(
                        "Failed to sync book {} ({}): {}",
                        sb.title,
                        sb.url,
                        exc,
                    )
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": False,
                        "error": str(exc),
                    })

                if progress_cb is not None:
                    await progress_cb(pages_checked, books_found, books_synced, books_failed)

        return {
            "source_id": source_id,
            "pages_checked": pages_checked,
            "books_found": books_found,
            "books_synced": books_synced,
            "books_failed": books_failed,
            "chapters_created": chapters_created,
            "chapters_skipped": chapters_skipped,
            "details": details,
            "next_page": min(max_pages, pages_checked) + 1 if pages_checked else start_page,
        }
