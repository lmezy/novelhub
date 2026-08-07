import asyncio
import re
from uuid import uuid4
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from loguru import logger

from app.crawler.registry import get_plugin
from app.core.config import settings, sync_thread_count
from app.core.database import SessionLocal
from app.core.events import emit, EventType
from app.models import (
    Author,
    Book,
    BookFavorite,
    BookTag,
    Chapter,
    Cookie,
    Source,
    SourceChange,
    Tag,
    User,
)
from app.repositories.tag import TagRepository
from app.services.storage import BookStorage
from app.services.search import search_service
from app.services.cookie_crypto import safe_decrypt_cookie
from app.services.r18 import detect_r18


class SyncPaused(Exception):
    """Raised by a task checkpoint when a crawl task has been paused."""


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
    async def _fetch_chapter_with_retry(
        plugin,
        remote_chapter,
        attempts: int = 3,
    ) -> str:
        """Fetch a chapter, retrying transient network errors."""
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return await plugin.fetch_chapter_content(remote_chapter)
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep((2 ** attempt) + 0.5)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Chapter fetch failed")

    @staticmethod
    def _chapter_concurrency(config: dict | None) -> int:
        """Pick chapter fetch concurrency, mirroring Legado's thread model."""
        default = min(
            max(1, int(getattr(settings, "SYNC_CHAPTER_CONCURRENCY", 9))),
            sync_thread_count(),
        )
        if getattr(settings, "SYNC_IGNORE_RATE_LIMIT", False):
            return default
        if not config:
            return default
        rate = str(config.get("concurrentRate", "") or "").strip()
        if "/" in rate:
            try:
                count = int(rate.split("/", 1)[0].strip())
            except ValueError:
                count = 0
            if count > 0:
                return min(count, 16)
        elif rate and rate != "0":
            # Legado treats a plain interval as one request per interval.
            return 1
        return default

    @staticmethod
    def _is_http_url(url: str) -> bool:
        return url.startswith(("http://", "https://"))

    @staticmethod
    def _normalize_title_for_match(title: str | None) -> str:
        return re.sub(
            r"[\s《》「」『』〈〉（）【】\[\]\"'“”‘’]+",
            "",
            title or "",
        ).lower()

    @staticmethod
    def _clean_sync_tags(
        tags: list[str] | set[str],
        title: str,
        author: str,
    ) -> list[str]:
        """Drop title/author fragments that old imports stored as tags."""
        def _normalize(value: str) -> str:
            return re.sub(
                r"[\s《》「」『』〈〉（）【】\[\]\"'“”‘’]+",
                "",
                value or "",
            ).lower()

        title_norm = _normalize(title) if title and title.lower() != "unknown" else ""
        author_norm = (
            _normalize(author) if author and author.lower() != "unknown" else ""
        )
        cleaned: list[str] = []
        for tag in tags:
            tag = str(tag or "").strip().lower()
            normalized = _normalize(tag)
            if not normalized:
                continue
            if title_norm and (
                normalized == title_norm
                or normalized in title_norm
                or title_norm in normalized
            ):
                continue
            if author_norm and (
                normalized == author_norm
                or normalized in author_norm
                or author_norm in normalized
            ):
                continue
            cleaned.append(tag)
        return cleaned

    async def _find_same_title_books(self, book: Book) -> list[Book]:
        """Find other source books with the same normalized title."""
        normalized = self._normalize_title_for_match(book.title)
        rows = await self.db.scalars(
            select(Book)
            .options(selectinload(Book.tags))
            .where(
                Book.id != book.id,
                Book.source_id.is_not(None),
            )
        )
        return [
            b
            for b in rows.all()
            if self._normalize_title_for_match(b.title) == normalized
        ]

    async def _handle_global_r18_conflicts(self, book: Book) -> None:
        """Mark same-title global books as R18 and queue admin confirmation."""
        same = await self._find_same_title_books(book)
        ids = {b.id for b in same}
        ids.add(book.id)
        books = [
            b
            for b in (
            (await self.db.scalars(
                select(Book)
                .options(selectinload(Book.tags), selectinload(Book.categories))
                .where(Book.id.in_(ids))
            )).all()
            )
            if b.owner_id is None
        ]
        has_r18 = any(b.is_r18 for b in books)
        has_non_r18 = any(not b.is_r18 for b in books)
        if not (has_r18 and has_non_r18):
            return

        original_r18 = {b.id: b.is_r18 for b in books}
        changed = False
        for b in books:
            if not b.is_r18:
                b.is_r18 = True
                changed = True
        if changed:
            await self.db.commit()

        pending = list(
            (
                await self.db.scalars(
                    select(SourceChange).where(
                        SourceChange.action == "confirm_r18",
                    )
                )
            ).all()
        )
        for change in pending:
            data = change.source_data or {}
            if data.get("title") == book.title:
                return
            if set(data.get("book_ids") or []).intersection(ids):
                return

        submitter_id = None
        if hasattr(book, "source_id"):
            source = await self.db.get(Source, book.source_id)
            if source is not None:
                submitter_id = source.submitter_id
        if not submitter_id:
            submitter_id = await self.db.scalar(
                select(User.id)
                .where(User.role.in_(("admin", "super_admin")))
                .limit(1)
            )
        if not submitter_id:
            return

        self.db.add(SourceChange(
            id=str(uuid4()),
            user_id=submitter_id,
            action="confirm_r18",
            source_id=book.source_id,
            source_data={
                "kind": "book_r18_conflict",
                "title": book.title,
                "book_ids": [b.id for b in books],
                "original_r18": original_r18,
            },
            status="pending",
        ))
        await self.db.commit()

        for b in books:
            try:
                search_service.index_book({
                    "id": b.id,
                    "title": b.title,
                    "author": b.author_name or "",
                    "description": b.description or "",
                    "status": b.status or "",
                    "source_id": b.source_id or "",
                    "author_id": b.author_id or "",
                    "is_r18": b.is_r18,
                    "tags": list(b.tag_names),
                    "category_names": list(b.category_names),
                })
            except Exception:
                continue

    async def _book_tag_names(self, book_id: str) -> list[str]:
        """Load tag names without triggering a sync lazy-load on ORM objects."""
        rows = await self.db.execute(
            select(Tag.name)
            .join(BookTag, BookTag.tag_id == Tag.id)
            .where(BookTag.book_id == book_id)
        )
        return [name for (name,) in rows.all()]

    @staticmethod
    def _book_custom_tag_names(book) -> list[str]:
        """Load user custom tag names for search indexing."""
        try:
            return [
                bct.custom_tag.name
                for bct in book.custom_tags
                if bct.custom_tag
            ]
        except Exception:
            return []

    @staticmethod
    def _is_book_r18(source: Source, remote_book) -> bool:
        return detect_r18(
            source_is_r18=getattr(source, "is_r18", False),
            title=remote_book.title,
            author=remote_book.author,
            description=remote_book.description,
            tags=getattr(remote_book, "tags", []) or [],
        )

    async def sync_book(
        self,
        source_id: str,
        url: str,
        progress_cb: Callable[[dict], Awaitable[None]] | None = None,
        checkpoint_cb: Callable[[], Awaitable[None]] | None = None,
    ) -> dict:
        source = await self.db.get(Source, source_id)
        if source is None or not source.enabled:
            raise ValueError("Source not found or disabled")
        if source.plugin_name != "local_markdown" and not self._is_http_url(url):
            raise ValueError(f"Unsupported book URL: {url}")

        emit(EventType.SYNC_STARTED, source_id=source_id, url=url)
        logger.info("Starting sync for source={} url={}", source_id, url)

        config = source.config if source.plugin_name == 'yuedu' else None
        plugin = get_plugin(source.plugin_name, config=config)
        cookie_record = await self.db.scalar(
            select(Cookie).where(Cookie.source == source_id)
        )
        if cookie_record:
            plugin.set_cookie(safe_decrypt_cookie(cookie_record.cookie_data))
        remote_book = await plugin.fetch_book(url)
        if (
            not remote_book.chapters
            or str(remote_book.title or "").strip() in ("", "Unknown")
        ):
            raise ValueError(
                f"Book page returned no usable metadata/chapters: {url} "
                f"(title={remote_book.title!r}, chapters={len(remote_book.chapters)})"
            )

        async def _checkpoint() -> None:
            if checkpoint_cb is not None:
                await checkpoint_cb()

        await _checkpoint()

        book_title = self._safe_title(remote_book)
        author_name = self._safe_author(remote_book.author)
        is_r18 = self._is_book_r18(source, remote_book)

        author = await self._get_or_create_author(author_name)
        book, is_new = await self._get_or_create_book(
            source.id,
            author.id,
            remote_book,
            is_r18=is_r18,
            owner_id=source.owner_id,
        )
        remote_cover_url = await self._persist_cover(book, plugin, remote_book)

        # Keep tags scoped to this source. Same-title books from other sources
        # may legitimately have different tags, so syncing one source must not
        # rewrite their stored tags.
        classification_tag = "r18" if is_r18 else "all-ages"
        source_tags: set[str] = set()
        for tag in await self._book_tag_names(book.id):
            tag = tag.strip().lower()
            if tag and tag not in ("all-ages", "r18"):
                source_tags.add(tag)
        for tag in remote_book.tags:
            tag = str(tag).strip().lower()
            if tag:
                source_tags.add(tag)
        source_tags = self._clean_sync_tags(source_tags, book_title, author_name)
        await self._save_tags(
            book.id,
            sorted([*source_tags, classification_tag]),
        )
        # Persist the book before chapter downloads so a later chapter failure
        # cannot leave chapters pointing at an uncommitted book row.
        await self.db.commit()

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
                "tags": remote_book.tags,
                "cover": book.cover,
                "cover_url": remote_cover_url,
            },
        )

        book_tags = await self._book_tag_names(book.id)
        book_custom_tags = self._book_custom_tag_names(book)
        index_tags = list(dict.fromkeys([*book_tags, *book_custom_tags]))
        try:
            from app.services.auto_categorize import AutoCategorizationService
            book_category_names = await AutoCategorizationService.categorize_book(
                self.db,
                book.id,
            )
        except Exception:
            await self.db.rollback()
            book_category_names = []
        search_service.index_book({
            "id": book.id,
            "title": book.title,
            "author": author_name,
            "description": book.description or "",
            "status": book.status or "",
            "source_id": book.source_id or "",
            "author_id": book.author_id or "",
            "is_r18": book.is_r18,
            "tags": index_tags,
            "category_names": book_category_names,
        })

        if is_new:
            emit(EventType.BOOK_CREATED, book_id=book.id, title=book.title)
        else:
            emit(EventType.BOOK_UPDATED, book_id=book.id, title=book.title)

        book_id = book.id
        book_title = book.title
        book_is_r18 = book.is_r18
        book_author = author_name
        book_description = (book.description or "")[:2000]
        book_values = {
            "source_id": book.source_id,
            "author_id": book.author_id,
            "source_book_id": book.source_book_id,
            "title": book_title,
            "cover": book.cover,
            "description": book.description,
            "status": book.status,
            "is_r18": book_is_r18,
            "owner_id": source.owner_id,
        }
        created = 0
        skipped = 0
        total = len(remote_book.chapters)
        failed_chapters: list[dict] = []

        async def _report_progress(remote_chapter) -> None:
            if progress_cb is not None:
                await progress_cb({
                    "book_id": book_id,
                    "book_title": book_title,
                    "chapter_number": remote_chapter.chapter_number,
                    "chapter_title": remote_chapter.title,
                    "created_chapters": created,
                    "skipped_chapters": skipped,
                    "failed_chapters": len(failed_chapters),
                    "total_chapters": total,
                })

        existing_source_ids = await self._reconcile_chapter_ids(
            book_id,
            remote_book.chapters,
        )

        missing_chapters = [
            remote_chapter
            for remote_chapter in remote_book.chapters
            if remote_chapter.source_chapter_id not in existing_source_ids
        ]
        for remote_chapter in remote_book.chapters:
            if remote_chapter.source_chapter_id in existing_source_ids:
                skipped += 1
                await _report_progress(remote_chapter)

        await _checkpoint()

        concurrency = self._chapter_concurrency(config)
        semaphore = asyncio.Semaphore(concurrency)
        results_queue = asyncio.Queue(maxsize=concurrency * 2)

        async def _producer(remote_chapter) -> None:
            async with semaphore:
                try:
                    content = await self._fetch_chapter_with_retry(
                        plugin,
                        remote_chapter,
                    )
                    result = (remote_chapter, content, None)
                except Exception as exc:
                    result = (remote_chapter, None, exc)
            await results_queue.put(result)

        producers = [
            asyncio.create_task(_producer(remote_chapter))
            for remote_chapter in missing_chapters
        ]
        remaining = len(producers)
        # Make sure the book row really exists before chapter inserts begin.
        book_row_verified = await self._ensure_book_row(book_id, book_values)
        try:
            while remaining > 0:
                remote_chapter, content, error = await results_queue.get()
                remaining -= 1
                await _checkpoint()
                if error is not None:
                    failed_chapters.append({
                        "chapter_number": remote_chapter.chapter_number,
                        "title": remote_chapter.title,
                        "url": remote_chapter.url,
                        "error": str(error)[:300],
                    })
                    logger.warning(
                        "Failed to sync chapter {} ({}): {}",
                        remote_chapter.title,
                        remote_chapter.url,
                        error,
                    )
                    await _report_progress(remote_chapter)
                    continue

                try:
                    content_path, content_hash = self.storage.write_chapter(
                        author_name,
                        book_title,
                        remote_chapter.chapter_number,
                        remote_chapter.title,
                        content,
                    )
                    chapter = Chapter(
                        id=str(uuid4()),
                        book_id=book_id,
                        chapter_number=remote_chapter.chapter_number,
                        source_chapter_id=remote_chapter.source_chapter_id,
                        title=remote_chapter.title,
                        content_path=content_path,
                        hash=content_hash,
                    )
                    if not book_row_verified:
                        if not await self._ensure_book_row(book_id, book_values):
                            raise SQLAlchemyError(
                                "Book row is missing and could not be restored"
                            )
                        book_row_verified = True
                    self.db.add(chapter)
                    await self.db.flush()
                    # Commit per chapter so a later failure cannot lose earlier work.
                    await self.db.commit()

                    search_service.buffer_chapter({
                        "id": chapter.id,
                        "book_id": book_id,
                        "title": chapter.title or "",
                        "chapter_number": chapter.chapter_number,
                        "content": content[: search_service.CONTENT_INDEX_LIMIT],
                        "book_title": book_title,
                        "book_author": book_author,
                        "book_description": book_description,
                        "tags": index_tags,
                        "category_names": book_category_names,
                        "is_r18": book_is_r18,
                    })

                    emit(
                        EventType.CHAPTER_CREATED,
                        chapter_id=chapter.id,
                        book_id=book_id,
                    )
                    created += 1
                except SQLAlchemyError as exc:
                    await self.db.rollback()
                    book_row_verified = False
                    failed_chapters.append({
                        "chapter_number": remote_chapter.chapter_number,
                        "title": remote_chapter.title,
                        "url": remote_chapter.url,
                        "error": str(exc)[:300],
                    })
                    logger.warning(
                        "Failed to sync chapter {} ({}): {}",
                        remote_chapter.title,
                        remote_chapter.url,
                        exc,
                    )
                    await _report_progress(remote_chapter)
                    continue
                except Exception as exc:
                    await self.db.rollback()
                    failed_chapters.append({
                        "chapter_number": remote_chapter.chapter_number,
                        "title": remote_chapter.title,
                        "url": remote_chapter.url,
                        "error": str(exc)[:300],
                    })
                    logger.warning(
                        "Failed to sync chapter {} ({}): {}",
                        remote_chapter.title,
                        remote_chapter.url,
                        exc,
                    )
                await _report_progress(remote_chapter)
        finally:
            for producer in producers:
                producer.cancel()
            await asyncio.gather(*producers, return_exceptions=True)
            search_service.flush_chapters()

        emit(
            EventType.SYNC_COMPLETED,
            book_id=book_id,
            created=created,
            skipped=skipped,
            failed=len(failed_chapters),
        )
        if source.owner_id is None:
            synced_book = await self.db.get(Book, book_id)
            if synced_book is not None:
                await self._handle_global_r18_conflicts(synced_book)
        logger.info(
            "Sync complete book={} created={} skipped={} failed={}",
            book_id,
            created,
            skipped,
            len(failed_chapters),
        )

        return {
            "book_id": book_id,
            "created_chapters": created,
            "skipped_chapters": skipped,
            "failed_chapters": failed_chapters,
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

    async def _persist_cover(self, book, plugin, remote_book) -> str | None:
        """Download a remote cover, save it locally, and update book.cover."""
        cover_url = str(getattr(remote_book, "cover_url", "") or "").strip()
        if not cover_url or not self._is_http_url(cover_url):
            return None
        if len(cover_url) > 255 or "\n" in cover_url or " " in cover_url:
            # A source rule may return several img@src values joined together.
            # That is not a usable cover and cannot fit the books.cover column.
            return None

        fetch = getattr(plugin, "fetch_cover", None)
        if not callable(fetch):
            book.cover = cover_url
            return cover_url

        try:
            result = await fetch(cover_url)
            data = result[0] if isinstance(result, tuple) else result
        except Exception as exc:
            logger.warning("Failed to fetch cover {}: {}", cover_url, exc)
            book.cover = cover_url
            return cover_url

        if not data:
            book.cover = cover_url
            return cover_url

        try:
            local_path = self.storage.save_cover(book.id, data)
        except Exception as exc:
            logger.warning("Failed to save cover for {}: {}", book.id, exc)
            book.cover = cover_url
            return cover_url

        book.cover = local_path
        return cover_url

    async def _get_or_create_book(
        self,
        source_id: str,
        author_id: str,
        remote_book,
        is_r18: bool = False,
        owner_id: str | None = None,
    ) -> tuple[Book, bool]:
        book = await self.db.scalar(
            select(Book)
            .options(selectinload(Book.tags))
            .where(
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
            book.owner_id = owner_id
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
            owner_id=owner_id,
        )
        self.db.add(book)
        await self.db.flush()
        return book, True

    async def _reconcile_chapter_ids(
        self,
        book_id: str,
        remote_chapters: list,
    ) -> set[str]:
        """Return existing source chapter ids, upgrading legacy numeric ids to URLs.

        Legado identifies chapters by their URL, while older NovelHub syncs
        stored the positional chapter number. Upgrading in place prevents a
        full re-download when the TOC order is stable.
        """
        rows = await self.db.scalars(
            select(Chapter).where(Chapter.book_id == book_id)
        )
        chapters = list(rows.all())
        by_id = {
            str(ch.source_chapter_id): ch
            for ch in chapters
            if ch.source_chapter_id is not None
        }
        by_number = {
            ch.chapter_number: ch
            for ch in chapters
            if ch.chapter_number is not None
        }

        for remote_chapter in remote_chapters:
            source_id = str(remote_chapter.source_chapter_id or "")
            if not source_id or source_id in by_id:
                continue
            legacy = by_number.get(remote_chapter.chapter_number)
            if legacy is None:
                continue
            legacy_id = str(legacy.source_chapter_id or "")
            if legacy_id.startswith(("http://", "https://")):
                continue
            if legacy_id == source_id:
                continue
            by_id.pop(legacy_id, None)
            legacy.source_chapter_id = source_id
            by_id[source_id] = legacy

        await self.db.flush()
        return set(by_id)

    async def _ensure_book_row(self, book_id: str, book_values: dict) -> bool:
        """Restore a missing book row before chapter inserts."""
        try:
            existing = await self.db.scalar(
                select(Book.id).where(Book.id == book_id)
            )
            if existing is not None:
                return True
            self.db.add(Book(id=book_id, **book_values))
            await self.db.commit()
            return True
        except SQLAlchemyError:
            await self.db.rollback()
            return False

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
                    "failed_chapters": [],
                })
                continue
            try:
                result = await self.sync_book(source_id, shelf_book.url)
                if source.owner_id and result.get("book_id"):
                    existing_favorite = await self.db.scalar(
                        select(BookFavorite).where(
                            BookFavorite.user_id == source.owner_id,
                            BookFavorite.book_id == result["book_id"],
                        )
                    )
                    if existing_favorite is None:
                        self.db.add(BookFavorite(
                            id=str(uuid4()),
                            user_id=source.owner_id,
                            book_id=result["book_id"],
                        ))
                        await self.db.commit()
                results.append({
                    "book_id": result["book_id"],
                    "status": "ok",
                    "created_chapters": result.get("created_chapters", 0),
                    "skipped_chapters": result.get("skipped_chapters", 0),
                    "failed_chapters": result.get("failed_chapters", []),
                })
            except Exception as exc:
                logger.opt(exception=exc).warning(
                    "Failed to sync shelf book {}", shelf_book.url
                )
                await self.db.rollback()
                results.append({
                    "url": shelf_book.url,
                    "status": "failed",
                    "error": str(exc),
                    "created_chapters": 0,
                    "skipped_chapters": 0,
                    "failed_chapters": [],
                })

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
            tag = await tag_repo.get_or_create(name)
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
        source, plugin = await self._book_plugin(book)
        url = self._book_url(book, source, plugin)
        return await self.sync_book(book.source_id, url)

    async def resync_chapter(self, chapter_id: str) -> dict:
        """Re-fetch and overwrite a single chapter from its book source."""
        chapter = await self.db.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("Chapter not found")
        book = await self.db.get(Book, chapter.book_id)
        if book is None:
            raise ValueError("Book not found")
        if not book.source_id or not book.source_book_id:
            raise ValueError("Book has no source reference")

        source, plugin = await self._book_plugin(book)
        url = self._book_url(book, source, plugin)
        remote_book = await plugin.fetch_book(url)
        remote_chapter = self._match_remote_chapter(chapter, remote_book.chapters)
        if remote_chapter is None:
            raise ValueError("Chapter not found in source TOC")

        content = await self._fetch_chapter_with_retry(plugin, remote_chapter)
        book_id = book.id
        book_is_r18 = book.is_r18
        book_author = book.author_name or "Unknown"
        book_description = (book.description or "")[:2000]
        book_tags = list(book.tag_names)
        book_custom_tags = self._book_custom_tag_names(book)
        index_tags = list(dict.fromkeys([*book_tags, *book_custom_tags]))
        book_category_names = list(book.category_names)
        author_name = book.author_name or "Unknown"
        content_path, content_hash = self.storage.write_chapter(
            author_name,
            book.title,
            remote_chapter.chapter_number,
            remote_chapter.title,
            content,
        )

        chapter.title = remote_chapter.title
        chapter.chapter_number = remote_chapter.chapter_number
        chapter.source_chapter_id = remote_chapter.source_chapter_id
        chapter.content_path = content_path
        chapter.hash = content_hash
        await self.db.commit()

        search_service.index_chapter({
            "id": chapter.id,
            "book_id": book_id,
            "title": chapter.title or "",
            "chapter_number": chapter.chapter_number,
            "content": content[: search_service.CONTENT_INDEX_LIMIT],
            "book_title": book.title,
            "book_author": book_author,
            "book_description": book_description,
            "tags": index_tags,
            "category_names": book_category_names,
            "is_r18": book_is_r18,
        })
        emit(
            EventType.CHAPTER_UPDATED,
            chapter_id=chapter.id,
            book_id=book_id,
        )

        return {
            "chapter_id": chapter.id,
            "book_id": book_id,
            "updated": True,
            "title": chapter.title,
            "chapter_number": chapter.chapter_number,
            "content_path": content_path,
            "content_length": len(content),
        }

    async def _book_plugin(self, book: Book) -> tuple[Source, object]:
        """Load a book's source and plugin instance for re-sync operations."""
        source = await self.db.get(Source, book.source_id)
        if source is None:
            raise ValueError("Source not found")
        if not source.enabled:
            raise ValueError("Source disabled")
        config = source.config if source.plugin_name == "yuedu" else None
        plugin = get_plugin(source.plugin_name, config=config)
        cookie_record = await self.db.scalar(
            select(Cookie).where(Cookie.source == book.source_id)
        )
        if cookie_record:
            plugin.set_cookie(safe_decrypt_cookie(cookie_record.cookie_data))
        return source, plugin

    @staticmethod
    def _book_url(book: Book, source: Source, plugin) -> str:
        """Construct the remote book URL used by re-sync operations."""
        if hasattr(plugin, "build_book_url"):
            return plugin.build_book_url(book.source_book_id)
        cfg = plugin.config if hasattr(plugin, "config") else None
        if cfg and hasattr(cfg, "book_url"):
            return cfg.base_url + cfg.book_url.format(book_id=book.source_book_id)
        url = source.url or ""
        if not url:
            raise ValueError("Cannot determine book URL for re-sync")
        return urljoin(url.rstrip("/") + "/", book.source_book_id)

    @staticmethod
    def _match_remote_chapter(chapter: Chapter, remote_chapters: list):
        """Find the remote chapter by URL, position, or normalized title."""
        for remote_chapter in remote_chapters:
            if (
                chapter.source_chapter_id
                and remote_chapter.source_chapter_id == chapter.source_chapter_id
            ):
                return remote_chapter
        for remote_chapter in remote_chapters:
            if (
                chapter.chapter_number is not None
                and remote_chapter.chapter_number == chapter.chapter_number
            ):
                return remote_chapter
        normalized = SyncService._normalize_title_for_match(chapter.title)
        for remote_chapter in remote_chapters:
            if (
                normalized
                and SyncService._normalize_title_for_match(remote_chapter.title) == normalized
            ):
                return remote_chapter
        return None

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
        chapter_progress_cb: Callable[[dict], Awaitable[None]] | None = None,
        before_step: Callable[[], Awaitable[None]] | None = None,
        checkpoint_cb: Callable[[], Awaitable[None]] | None = None,
        start_page: int = 1,
        page_batch_size: int = 0,
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
                "chapters_failed": 0,
                "details": [],
            }

        seen: set[str] = set()
        details: list[dict] = []
        books_found = 0
        books_synced = 0
        books_failed = 0
        chapters_created = 0
        chapters_skipped = 0
        chapters_failed = 0
        pages_checked = 0
        start_page = max(1, int(start_page or 1))
        max_pages = max_pages or 0
        page_batch_size = max(0, int(page_batch_size or 0))
        pages_in_run = 0
        done = max_pages > 0 and start_page > max_pages

        page = start_page
        while max_pages <= 0 or page <= max_pages:
            pages_in_run += 1
            if before_step is not None:
                await before_step()
            page_books = await plugin.discover_books(url=url, page=page)
            if not page_books:
                done = True
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
                done = True
                break

            book_concurrency = min(
                max(1, int(getattr(settings, "SYNC_BOOK_CONCURRENCY", 3))),
                sync_thread_count(),
            )
            continuous_books = bool(
                getattr(settings, "SYNC_BOOK_CONTINUOUS", False)
            )
            chapter_progress_lock = asyncio.Lock()

            async def _guarded_chapter_progress(info: dict) -> None:
                if chapter_progress_cb is None:
                    return
                async with chapter_progress_lock:
                    await chapter_progress_cb(info)

            async def _sync_one(sb):
                async with SessionLocal() as session:
                    return await SyncService(session).sync_book(
                        source_id,
                        sb.url,
                        progress_cb=_guarded_chapter_progress,
                        checkpoint_cb=checkpoint_cb,
                    )

            async def _record_outcome(sb, outcome) -> None:
                nonlocal books_synced, books_failed
                nonlocal chapters_created, chapters_skipped, chapters_failed
                if isinstance(outcome, SyncPaused):
                    raise outcome
                if isinstance(outcome, BaseException):
                    await self.db.rollback()
                    books_failed += 1
                    logger.warning(
                        "Failed to sync book {} ({}): {}",
                        sb.title,
                        sb.url,
                        outcome,
                    )
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": False,
                        "error": str(outcome),
                        "failed_chapters": [],
                    })
                else:
                    details.append({
                        "title": sb.title,
                        "author": sb.author,
                        "url": sb.url,
                        "synced": True,
                        "book_id": outcome.get("book_id"),
                        "created_chapters": outcome.get("created_chapters", 0),
                        "skipped_chapters": outcome.get("skipped_chapters", 0),
                        "failed_chapters": outcome.get("failed_chapters", []),
                    })
                    books_synced += 1
                    chapters_created += outcome.get("created_chapters", 0)
                    chapters_skipped += outcome.get("skipped_chapters", 0)
                    chapters_failed += len(outcome.get("failed_chapters", []))

                if progress_cb is not None:
                    await progress_cb(pages_checked, books_found, books_synced, books_failed)

            active_tasks: dict[asyncio.Task, object] = {}

            async def _record_finished(finished_tasks) -> None:
                for finished in finished_tasks:
                    sb_done = active_tasks.pop(finished)
                    if finished.cancelled():
                        continue
                    exc = finished.exception()
                    outcome = exc if exc is not None else finished.result()
                    await _record_outcome(sb_done, outcome)

            try:
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
                    if book_concurrency <= 1:
                        try:
                            result = await self.sync_book(
                                source_id,
                                sb.url,
                                progress_cb=chapter_progress_cb,
                                checkpoint_cb=checkpoint_cb,
                            )
                        except SyncPaused:
                            raise
                        except Exception as exc:
                            await _record_outcome(sb, exc)
                        else:
                            await _record_outcome(sb, result)
                        continue

                    task = asyncio.create_task(_sync_one(sb))
                    active_tasks[task] = sb
                    if not continuous_books and len(active_tasks) >= book_concurrency:
                        finished_tasks, _ = await asyncio.wait(
                            list(active_tasks.keys()),
                            return_when=asyncio.ALL_COMPLETED,
                        )
                        await _record_finished(finished_tasks)
                        if before_step is not None:
                            await before_step()
                    elif continuous_books:
                        while len(active_tasks) >= book_concurrency:
                            finished_tasks, _ = await asyncio.wait(
                                list(active_tasks.keys()),
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            await _record_finished(finished_tasks)
                            if before_step is not None:
                                await before_step()

                while active_tasks:
                    finished_tasks, _ = await asyncio.wait(
                        list(active_tasks.keys()),
                        return_when=asyncio.ALL_COMPLETED,
                    )
                    await _record_finished(finished_tasks)
                    if before_step is not None:
                        await before_step()
            except BaseException:
                for task in active_tasks:
                    task.cancel()
                if active_tasks:
                    await asyncio.gather(*active_tasks, return_exceptions=True)
                raise
            if max_pages > 0 and page >= max_pages:
                done = True
                break
            if page_batch_size > 0 and pages_in_run >= page_batch_size:
                break
            page += 1

        if before_step is not None:
            await before_step()

        return {
            "source_id": source_id,
            "pages_checked": pages_checked,
            "books_found": books_found,
            "books_synced": books_synced,
            "books_failed": books_failed,
            "chapters_created": chapters_created,
            "chapters_skipped": chapters_skipped,
            "chapters_failed": chapters_failed,
            "details": details,
            "next_page": pages_checked + 1 if pages_checked else start_page,
            "done": done,
        }
