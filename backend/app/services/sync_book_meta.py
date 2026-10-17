"""Book metadata DB helpers for the sync service (mixin).

Split out of ``sync.py``: title/author/tag/category lookups and the global
R18-conflict bookkeeping that ``SyncService`` uses while syncing.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import selectinload

from loguru import logger

from app.core.clock import naive_now
from app.models import (
    Author,
    Book,
    BookCategory,
    BookCustomTag,
    BookTag,
    Category,
    CustomTag,
    SourceChange,
    Tag,
)
from app.services.book_kind import normalize_kind
from app.services.book_title import normalized_title_sql
from app.services.search import search_service


class SyncBookMetaMixin:
    async def _find_same_title_books(self, book: Book) -> list[Book]:
        """Find other source books with the same normalized title.

        The title comparison runs in the database: this helper is called at the
        end of *every* global book sync, and doing the normalisation in Python
        meant loading the whole ``books`` table (with its eager tag/category
        loads) each time -- ~10 s per call on a 24k-book library, which also
        showed up as the crawler pinning a CPU core.
        """
        normalized = self._normalize_title_for_match(book.title)
        if not normalized:
            return []
        rows = await self.db.scalars(
            select(Book)
            .options(selectinload(Book.tags))
            .where(
                Book.id != book.id,
                Book.source_id.is_not(None),
                normalized_title_sql(Book.title) == normalized,
            )
        )
        return [
            b
            for b in rows.all()
            if self._normalize_title_for_match(b.title) == normalized
        ]

    async def _find_same_title_books_by_id(self, book_id: str) -> list[Book]:
        """Same-title lookup that survives an expired ``Book`` instance."""
        trigger_title = await self._book_column(book_id, Book.title) or ""
        normalized = self._normalize_title_for_match(trigger_title)
        if not normalized:
            return []
        rows = await self.db.scalars(
            select(Book)
            .options(selectinload(Book.tags))
            .where(
                Book.id != book_id,
                Book.source_id.is_not(None),
                normalized_title_sql(Book.title) == normalized,
            )
        )
        return [
            b
            for b in rows.all()
            if self._normalize_title_for_match(b.title) == normalized
        ]

    async def _book_column(self, book_id: str, column):
        """Read one ``books`` column without touching an ORM instance."""
        return await self.db.scalar(
            select(column).where(Book.id == book_id)
        )

    async def _handle_global_r18_conflicts(self, book_id: str) -> None:
        """Mark same-title global books as R18 and queue admin confirmation.

        Takes the book id rather than the instance: every caller reaches here
        after chapter ``rollback()`` calls that expired the ``Book`` object,
        and even reading ``book.title`` synchronously could raise
        ``MissingGreenlet``.
        """
        trigger_title = await self._book_column(book_id, Book.title) or ""
        trigger_source_id_value = await self._book_column(book_id, Book.source_id)
        same = await self._find_same_title_books_by_id(book_id)
        ids = {b.id for b in same}
        ids.add(book_id)
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
            if data.get("title") == trigger_title:
                return
            if set(data.get("book_ids") or []).intersection(ids):
                return

        submitter_id = None
        trigger_source_id = trigger_source_id_value
        if trigger_source_id:
            source = await self.db.get(Source, trigger_source_id)
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
            source_id=trigger_source_id,
            source_data={
                "kind": "book_r18_conflict",
                "title": trigger_title,
                "book_ids": [b.id for b in books],
                "original_r18": original_r18,
            },
            status="pending",
        ))
        await self.db.commit()

        for b in books:
            try:
                await asyncio.to_thread(search_service.index_book, {
                    "id": b.id,
                    "title": await self._book_column(b.id, Book.title) or "",
                    "author": await self._book_author_name(b.author_id),
                    "description": await self._book_column(b.id, Book.description) or "",
                    "status": await self._book_column(b.id, Book.status) or "",
                    "source_id": await self._book_column(b.id, Book.source_id) or "",
                    "author_id": b.author_id or "",
                    "is_r18": b.is_r18,
                    "owner_id": None,
                    "is_public": bool(b.is_public),
                    "tags": await self._book_tag_names(b.id),
                    "category_names": await self._book_category_names(b.id),
                    "kind": normalize_kind(await self._book_column(b.id, Book.kind)),
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
        result = rows.all()
        if callable(getattr(result, "__await__", None)):
            # ``AsyncMock``-based tests stub ``execute`` with a coroutine for
            # ``all()``; real sessions return the list directly.
            result = await result
        return [name for (name,) in result]

    async def _book_category_names(self, book_id: str) -> list[str]:
        """Load category names without touching ORM relationships."""
        book_is_r18 = await self._book_column(book_id, Book.is_r18)
        query = (
            select(Category.name)
            .join(BookCategory, BookCategory.category_id == Category.id)
            .where(BookCategory.book_id == book_id)
        )
        if not book_is_r18:
            query = query.where(Category.is_r18.is_(False))
        rows = await self.db.execute(query)
        result = rows.all()
        if callable(getattr(result, "__await__", None)):
            result = await result
        return [name for (name,) in result]

    async def _book_author_name(self, author_id: str | None) -> str:
        """Load an author name without touching ``Book.author``."""
        if not author_id:
            return "Unknown"
        try:
            name = await self.db.scalar(
                select(Author.name).where(Author.id == author_id)
            )
        except MissingGreenlet:
            # The session was rolled back concurrently; the id is still valid,
            # but this read cannot proceed on the expired session.
            return "Unknown"
        return str(name or "").strip() or "Unknown"

    @staticmethod
    async def _book_custom_tag_names(db, book_id: str) -> list[str]:
        """Load user custom tag names without touching ORM relationships.

        ``Book.custom_tags`` is ``lazy="selectin"`` and is not loaded on every
        path that reaches here (resync, existing-book lookups without the
        loader option).  Reading it synchronously raised ``MissingGreenlet``
        once a ``rollback()`` expired the instance, so query the names
        explicitly like ``_book_tag_names`` does.
        """
        rows = await db.execute(
            select(CustomTag.name)
            .join(
                BookCustomTag,
                BookCustomTag.custom_tag_id == CustomTag.id,
            )
            .where(BookCustomTag.book_id == book_id)
        )
        result = rows.all()
        if callable(getattr(result, "__await__", None)):
            result = await result
        return [name for (name,) in result]

