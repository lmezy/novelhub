"""Index lifecycle, writes and maintenance for the search service.

Split out of ``search.py`` (which keeps the query engine): everything that
creates/configures indexes, writes documents, or rebuilds/backfills them
lives here as a mixin, so the 3000-line god file becomes two navigable ones.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import meilisearch
from loguru import logger

from app.core.clock import naive_now
from app.core.config import settings
from app.services.book_kind import KIND_NOVEL, normalize_kind
from app.services.search_ngrams import (
    GRAM_FIELD_FOR_BOOK,
    GRAM_FIELD_FOR_CHAPTER,
    book_gram_fields,
    chapter_gram_fields,
    gram_field_for,
    gram_filter,
)


# -- rebuild progress --------------------------------------------------------
#
# A rebuild reads every book, every chapter file and pushes ~760 000 documents
# into the engine; on a NAS that is hours of work, and doing it inside the HTTP
# request left the admin staring at a spinner that a 30 s gateway timeout turns
# into ``499`` while the work kept running (codex-handoff 59/60).
#
# The state therefore lives in a small JSON file inside the storage volume, not
# in memory: the endpoint that answers the progress poll may be served by a
# *different* uvicorn worker (the NAS runs ``--workers 2``), so a module global
# would answer "not running" half the time.

REBUILD_STATE_FILE = ".search-rebuild.json"
#: A rebuild publishes at most this often (phase changes ignore the throttle).
REBUILD_PROGRESS_THROTTLE_SECONDS = 1.0
#: A "running" state whose heartbeat is older than this means the process died
#: (container restart) -- the UI must stop polling instead of forever.
# Must stay well above ``INDEX_BACKLOG_MAX_WAIT_SECONDS``: the rebuild publishes
# progress and then waits for the engine, and a window shorter than that wait
# turns a healthy rebuild into a "stale" one (2026-10-20: 300 s of waiting vs a
# 180 s window killed a live rebuild through its own monitor).
REBUILD_STALE_SECONDS = 900


class RebuildCancelled(RuntimeError):
    """Raised inside a rebuild when the admin asked it to stop."""


def rebuild_state_path() -> Path:
    return Path(settings.STORAGE_PATH) / REBUILD_STATE_FILE


def empty_rebuild_progress() -> dict[str, Any]:
    return {
        "running": False,
        "phase": "idle",
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
        "books_total": 0,
        "books_done": 0,
        "chapters_total": 0,
        "chapters_done": 0,
        "documents_done": 0,
        "documents_total": 0,
        "percent": 0.0,
        "message": "",
        "error": None,
        "result": None,
        "cancel_requested": False,
    }


def read_rebuild_progress() -> dict[str, Any]:
    """The last published rebuild state (never raises)."""
    progress = empty_rebuild_progress()
    try:
        raw = json.loads(rebuild_state_path().read_text(encoding="utf-8"))
    except Exception:
        return progress
    if not isinstance(raw, dict):
        return progress
    for key in progress:
        if key in raw:
            progress[key] = raw[key]
    if progress.get("running"):
        heartbeat = 0.0
        try:
            heartbeat = float(raw.get("heartbeat") or 0)
        except (TypeError, ValueError):
            heartbeat = 0.0
        if time.time() - heartbeat > REBUILD_STALE_SECONDS:
            progress["running"] = False
            progress["phase"] = "stale"
            progress["error"] = (
                "上一次重建没有正常结束（进程/容器重启过）。可以直接重新点「重建索引」。"
            )
    return progress


def write_rebuild_progress(update: dict[str, Any]) -> dict[str, Any]:
    """Merge ``update`` into the stored state and publish it atomically."""
    current = read_rebuild_progress()
    current.update({k: v for k, v in update.items() if k != "heartbeat"})
    current["heartbeat"] = time.time()
    current["updated_at"] = naive_now().isoformat(timespec="seconds")
    total = int(current.get("documents_total") or 0)
    done = int(current.get("documents_done") or 0)
    current["percent"] = round(100.0 * done / total, 1) if total else 0.0

    path = rebuild_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(current, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception as exc:  # progress must never break the rebuild
        logger.debug("Could not write rebuild progress: {}", exc)
    return current


#: Strong references to in-flight rebuild tasks: ``asyncio`` keeps only weak ones,
#: and a garbage-collected task would abort a multi-hour rebuild silently.
_REBUILD_TASKS: set[asyncio.Task] = set()


class SearchIndexMixin:
    """Index lifecycle, writes and maintenance for the search service."""

    def max_total_hits(self) -> int:
        """The engine's ``pagination.maxTotalHits`` for this deployment.

        ``MAX_TOTAL_HITS`` is the default the class ships with; a library that
        grows past it can raise the ceiling from configuration instead of
        needing a code change, because the value is applied to the index
        settings on the next ``_ensure_index``.
        """
        try:
            configured = int(getattr(settings, "MEILI_MAX_TOTAL_HITS", 0) or 0)
        except (TypeError, ValueError):
            configured = 0
        return configured if configured > 0 else self.MAX_TOTAL_HITS

    def content_index_limit(self) -> int:
        """Characters of chapter body kept in one index document.

        Not a cosmetic knob: Meilisearch re-tokenises the whole document on
        every write, and CJK text is expensive.  Measured on the live library,
        a 100 000-character chapter costs ~26 s of engine CPU, and 11 627 stored
        chapters are that big (the largest is a whole TXT imported as a single
        chapter, 1.38 MB) -- at the old 100k cap a full rebuild was a
        multi-month job.  The full text always stays in storage; this only
        limits the index copy.
        """
        try:
            configured = int(getattr(settings, "MEILI_CONTENT_INDEX_LIMIT", 0) or 0)
        except (TypeError, ValueError):
            configured = 0
        return configured if configured > 0 else self.CONTENT_INDEX_LIMIT

    @classmethod
    def gram_fields(cls, name: str) -> tuple[str, ...]:
        """The derived gram attributes ``name`` declares filterable and writes."""
        return GRAM_FIELD_FOR_BOOK if name == cls.INDEX_BOOKS else GRAM_FIELD_FOR_CHAPTER

    @classmethod
    def scan_budget(cls, name: str) -> int:
        """How far a gram-narrowed walk on ``name`` may go, in candidates.

        Never above the engine's own ceiling: asking the engine for more than
        ``maxTotalHits`` rows is not an error, it just silently stops there, and a
        window larger than the ceiling would report a total the engine cannot
        serve pages for.
        """
        budget = (
            cls.SCAN_BUDGET_BOOKS if name == cls.INDEX_BOOKS
            else cls.SCAN_BUDGET_CHAPTERS
        )
        return max(1, min(budget, cls.MAX_TOTAL_HITS))

    @classmethod
    def filterable_attributes(cls, name: str) -> list[str]:
        """Every filterable attribute of one index, base fields plus grams."""
        return [*cls.FILTERABLE_ATTRIBUTES, *cls.gram_fields(name)]

    @staticmethod
    def _primary_key_of(index: Any) -> str | None:
        """The index's primary key, whichever shape the client returned."""
        value = (
            index.get("primaryKey") if isinstance(index, dict)
            else getattr(index, "primary_key", None)
        )
        return str(value) if value else None

    def wait_for_task(self, task: Any, timeout_seconds: float = 30.0) -> None:
        """Wait for one of the engine's tasks -- they are all asynchronous.

        ``delete_index`` only *enqueues* the deletion.  Without waiting, a
        rebuild's ``_ensure_index`` still saw the old index and skipped the
        create; the deletion then landed, the first document batch auto-created
        the index **without a primary key**, and every following batch failed
        with ``primary_key_multiple_candidates_found`` -- the whole rebuild
        produced an empty index (2026-10-20).  Never raises: a slow engine must
        not break the caller.
        """
        if isinstance(task, dict):
            uid = task.get("taskUid") or task.get("uid")
        else:
            uid = getattr(task, "task_uid", None) or getattr(task, "uid", None)
        if uid is None:
            return
        waiter = getattr(self.client, "wait_for_task", None)
        if waiter is None:
            return
        try:
            waiter(
                uid,
                timeout_in_ms=int(max(1.0, timeout_seconds) * 1000),
                interval_in_ms=200,
            )
        except Exception as exc:
            logger.debug("Task {} did not confirm: {}", uid, exc)

    def _ensure_index(self, name: str, primary_key: str = "id") -> None:
        if name in self._ensured:
            return
        try:
            existing = self.client.get_index(name)
        except meilisearch.errors.MeilisearchApiError:
            self.client.create_index(name, {"primaryKey": primary_key})
        else:
            # An index that exists *without* a primary key cannot take our
            # documents: a chapter carries ``id`` **and** ``book_id``, so the
            # engine's inference refuses the batch.  An empty index accepts the
            # key, so repair it here (after a wipe, or after losing the
            # delete/create race described in ``wait_for_task``).
            if not self._primary_key_of(existing):
                try:
                    self.client.update_index(name, {"primaryKey": primary_key})
                    logger.warning(
                        "Index {} had no primary key; set it to {!r}",
                        name, primary_key,
                    )
                except Exception as exc:
                    logger.debug(
                        "Could not set the primary key of {}: {}", name, exc
                    )
        index = self.client.index(name)
        index.update_filterable_attributes(self.filterable_attributes(name))
        if name == self.INDEX_BOOKS:
            index.update_searchable_attributes(self.SEARCHABLE_BOOKS)
        else:
            index.update_searchable_attributes(self.SEARCHABLE_CHAPTERS)
        try:
            index.update_pagination_settings({"maxTotalHits": self.max_total_hits()})
        except Exception:
            pass
        self._ensured.add(name)

    def ensure_indexes(self) -> None:
        """Create the books/chapters indexes if they do not exist yet."""
        self._ensure_index(self.INDEX_BOOKS)
        self._ensure_index(self.INDEX_CHAPTERS)

    @staticmethod
    def _normalize_visibility(doc: dict) -> None:
        """Make every indexed document carry explicit visibility fields.

        ``owner_id`` must be present (null for global books) so the engine's
        owner filter can rely on it; ``is_public`` is always a real bool.
        """
        doc["owner_id"] = doc.get("owner_id") or None
        doc["is_public"] = bool(doc.get("is_public", False))

    def index_book(self, book: dict) -> None:
        self._ensure_index(self.INDEX_BOOKS)
        doc = dict(book)
        self._normalize_visibility(doc)
        # Every book document must carry ``kind``: it is what keeps a search
        # started on /novels or /comics inside its own half of the library, and
        # a document without the field matches neither side of the filter.
        doc["kind"] = normalize_kind(doc.get("kind"))
        # The derived gram fields are what let a metadata search reach every
        # match instead of the engine's first ``maxTotalHits``; they are computed
        # here, in the one funnel every write path goes through, so a book cannot
        # be indexed without them (``sync_book_grams`` backfills older ones).
        doc.update(book_gram_fields(doc))
        self.client.index(self.INDEX_BOOKS).add_documents([doc])
        logger.debug("Indexed book {}", doc.get("id"))

    def index_chapter(self, chapter: dict) -> None:
        self._ensure_index(self.INDEX_CHAPTERS)
        doc = dict(chapter)
        self._normalize_visibility(doc)
        doc.update(chapter_gram_fields(doc))
        self.client.index(self.INDEX_CHAPTERS).add_documents([doc])
        logger.debug("Indexed chapter {}", doc.get("id"))

    def update_book_tags(self, book_id: str, tags: list[str]) -> None:
        """Merge new tags into an indexed book.

        ``update_documents`` (HTTP PUT) merges the given fields and leaves the
        rest of the document alone.  ``add_documents`` (HTTP POST) *replaces*
        the document, so this one-field write used to reduce the whole book to
        ``{id, tags}`` -- dropping ``kind`` (making it unfindable from /novels
        and /comics) and every derived gram field with it.  Only a later
        ``sync_book_kinds`` backfill repaired the damage.
        """
        self._ensure_index(self.INDEX_BOOKS)
        self.client.index(self.INDEX_BOOKS).update_documents([{"id": book_id, "tags": tags}])

    def update_chapter_tags(self, chapter_id: str, tags: list[str]) -> None:
        """Merge new tags into an indexed chapter (see ``update_book_tags``)."""
        self._ensure_index(self.INDEX_CHAPTERS)
        self.client.index(self.INDEX_CHAPTERS).update_documents([
            {"id": chapter_id, "tags": tags}
        ])

    def buffer_chapter(self, chapter: dict) -> None:
        """Queue a chapter for batched indexing, flushing in chunks."""
        self._ensure_index(self.INDEX_CHAPTERS)
        doc = dict(chapter)
        self._normalize_visibility(doc)
        # ``flush_chapters`` sends these with ``add_documents``, which replaces
        # the stored document, so the gram fields have to be on the buffered copy
        # rather than merged in later.
        doc.update(chapter_gram_fields(doc))
        self._chapter_buffer.append(doc)
        if len(self._chapter_buffer) >= self.CHAPTER_BUFFER_SIZE:
            self.flush_chapters()

    def flush_chapters(self) -> None:
        """Send queued chapter documents to Meilisearch in one batch."""
        if not self._chapter_buffer:
            return
        docs = self._chapter_buffer
        self._chapter_buffer = []
        try:
            self.client.index(self.INDEX_CHAPTERS).add_documents(docs)
            logger.debug("Indexed {} chapters", len(docs))
        except Exception as exc:
            logger.warning("Failed to index {} chapters: {}", len(docs), exc)

    def index_missing_grams(self, index_name: str) -> bool:
        """Whether ``index_name`` still holds documents without the gram fields.

        Same shape as ``index_missing_kind``, opposite direction: "missing" means
        *any* document lacks *any* gram field, and the caller must then keep its
        windowed path.  A partly backfilled index is the dangerous state -- the
        gram filter would silently drop the documents that are not backfilled
        yet, and there is no per-document way to tell at query time which those
        are.  ``NOT EXISTS`` is a filter, so it answers in one cheap request.

        Every gram field is probed, not just the first: an index that has
        ``title_grams`` but not ``book_title_grams`` would pass a single-field
        probe and then hide every chapter from a chapter-scoped title search.
        """
        self._ensure_index(index_name)
        missing = self.gram_missing_filter(index_name)
        try:
            result = self.client.index(index_name).search("", {
                "limit": 1,
                "filter": missing,
                "attributesToRetrieve": ["id"],
            })
        except Exception as exc:
            logger.warning("Could not probe {} for gram fields: {}", index_name, exc)
            # An unknown index state must not read as "safe to narrow": on an
            # index whose filterable attributes do not carry the probe (or an
            # engine that rejects the filter) the gram path would answer every
            # search with nothing, which is worse than a truncated answer.
            return True
        return bool(result.get("hits"))

    def gram_index_ready(self, index_name: str) -> bool:
        """Whether metadata searches on ``index_name`` may use the gram filter.

        The answer is only true once a full backfill has **verified** that every
        document carries every gram field (``sync_book_grams``, or
        ``rebuild_index`` which writes them itself), and it is then re-verified
        once per ``GRAM_READY_TTL_SECONDS`` rather than on every search.

        The re-verification exists because readiness can be *revoked* from the
        outside: ``backend/`` code is shared by three containers and only some of
        them may have been rebuilt, and a writer on the old code indexes
        documents without the gram fields.  A stale "ready" would then hide
        exactly those documents from every metadata search -- silently, and
        looking like "the book is not in the library" rather than like a bug.
        One probe per TTL turns that into a bounded, self-healing fallback to the
        windowed path.
        """
        deadline = self._gram_ready.get(index_name)
        if deadline is None:
            return False
        if deadline < time.monotonic():
            if self.index_missing_grams(index_name):
                self._gram_ready.pop(index_name, None)
                logger.warning(
                    "{} no longer holds complete gram fields; metadata search "
                    "falls back to its candidate window",
                    index_name,
                )
                return False
        self._mark_gram_ready(index_name)
        return True

    def _mark_gram_ready(self, index_name: str) -> None:
        """Record that ``index_name`` is verified complete, until the TTL."""
        self._gram_ready[index_name] = time.monotonic() + self.GRAM_READY_TTL_SECONDS

    def delete_book(self, book_id: str) -> None:
        try:
            self.client.index(self.INDEX_BOOKS).delete_document(book_id)
            logger.debug("Deleted book {} from search index", book_id)
        except meilisearch.errors.MeilisearchApiError:
            pass

    def delete_chapter(self, chapter_id: str) -> None:
        try:
            self.client.index(self.INDEX_CHAPTERS).delete_document(chapter_id)
            logger.debug("Deleted chapter {} from search index", chapter_id)
        except meilisearch.errors.MeilisearchApiError:
            pass

    def delete_chapter_from_index(self, chapter_id: str) -> None:
        try:
            self.client.index(self.INDEX_CHAPTERS).delete_document(chapter_id)
            logger.debug("Deleted chapter {} from search index", chapter_id)
        except Exception:
            pass

    def delete_chapters_from_index(self, chapter_ids: list[str]) -> None:
        """Delete chapter documents in batches.

        Deleting a whole library one document at a time makes the API request
        block for thousands of HTTP round trips. Meilisearch accepts a list,
        which turns a library cleanup into a handful of requests.
        """
        ids = [str(i) for i in chapter_ids if i]
        if not ids:
            return
        index = self.client.index(self.INDEX_CHAPTERS)
        for start in range(0, len(ids), 1000):
            try:
                index.delete_documents(ids[start : start + 1000])
            except Exception as exc:
                logger.warning(
                    "Failed to delete {} chapters from search index: {}",
                    len(ids[start : start + 1000]),
                    exc,
                )
                return

    def delete_books_from_index(self, book_ids: list[str]) -> None:
        """Delete book documents in batches."""
        ids = [str(i) for i in book_ids if i]
        if not ids:
            return
        index = self.client.index(self.INDEX_BOOKS)
        for start in range(0, len(ids), 1000):
            try:
                index.delete_documents(ids[start : start + 1000])
            except Exception as exc:
                logger.warning(
                    "Failed to delete {} books from search index: {}",
                    len(ids[start : start + 1000]),
                    exc,
                )
                return

    def get_index_stats(self) -> dict:
        """Return document counts for both indexes."""
        try:
            self.ensure_indexes()
        except Exception as exc:
            logger.warning("Failed to ensure search indexes before stats: {}", exc)
        result = {}
        for idx_name in [self.INDEX_BOOKS, self.INDEX_CHAPTERS]:
            try:
                idx = self.client.get_index(idx_name)
                stats = idx.get_stats()
                result[idx_name] = {
                    "documents": stats.number_of_documents,
                    "is_indexing": stats.is_indexing,
                    "last_update": getattr(stats, "updated_at", None),
                }
            except Exception:
                result[idx_name] = {"documents": 0, "is_indexing": False, "last_update": None}
        return result

    def engine_backlog(self) -> int | None:
        """How many tasks the engine still has queued, or ``None`` if unknown.

        Meilisearch coalesces consecutive small tasks into **one batch**, and a
        batch is a single transaction: on 2026-10-19 the live engine ended up
        with 3 787 chapter tasks (~380 000 documents) in one batch and spent 15
        hours reading the index at 60-76 MB/s with a 12 GB container, without
        committing a single document (books index fine, chapters frozen at
        339 600).  A writer that waits for the engine to catch up never builds
        that batch.
        """
        try:
            tasks = self.client.get_tasks({"statuses": "enqueued,processing", "limit": 1})
        except Exception as exc:  # a probe must never break indexing
            logger.debug("Could not read the engine backlog: {}", exc)
            return None
        # The client returns a ``TaskResults`` object (which ``.get`` does not
        # exist on -- that bug made this probe return ``None`` and the rebuild
        # never waited, 2026-10-19), while older/newer clients may hand back the
        # raw mapping.  Accept both.
        total = tasks.get("total") if isinstance(tasks, dict) else getattr(tasks, "total", None)
        try:
            return int(total) if total is not None else None
        except (TypeError, ValueError):
            return None

    async def wait_for_engine(self) -> None:
        """Block while the engine's queue is longer than ``INDEX_BACKLOG_LIMIT``.

        Bounded: if the engine is hopelessly behind (or its task endpoint is
        unavailable) the rebuild continues anyway after
        ``INDEX_BACKLOG_MAX_WAIT_SECONDS`` -- a stuck index is bad, but an
        unbounded wait that never writes anything is worse.
        """
        limit = max(1, int(getattr(settings, "MEILI_BACKLOG_LIMIT", 0) or 0) or self.INDEX_BACKLOG_LIMIT)
        deadline = time.monotonic() + self.INDEX_BACKLOG_MAX_WAIT_SECONDS
        warned = False
        while True:
            backlog = await asyncio.to_thread(self.engine_backlog)
            if backlog is None or backlog <= limit:
                return
            if time.monotonic() >= deadline:
                if not warned:
                    logger.warning(
                        "Engine backlog is still {} (limit {}); continuing after {}s",
                        backlog, limit, self.INDEX_BACKLOG_MAX_WAIT_SECONDS,
                    )
                return
            if not warned:
                logger.info(
                    "Engine backlog {} > {}; waiting for it to catch up",
                    backlog, limit,
                )
                warned = True
            # Keep the progress heartbeat warm: this wait lasts minutes, and a
            # heartbeat older than REBUILD_STALE_SECONDS makes everyone watching
            # (the admin UI, an operator's monitor) declare the rebuild dead --
            # which is exactly how a live rebuild got killed on 2026-10-20: the
            # monitor saw ``stale`` and exited, taking the rebuild with it.
            write_rebuild_progress({
                "message": f"等待搜索引擎消化（队列 {backlog} 条）…",
            })
            await asyncio.sleep(self.INDEX_BACKLOG_POLL_SECONDS)

    async def rebuild_index(
        self,
        progress: Callable[[dict[str, Any]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict:
        """Rebuild both indexes from database and storage records.

        Books are submitted in batches, like chapters.  The old per-book
        ``index_book`` loop enqueued **one Meilisearch task per book** -- 46 176
        single-document tasks for the live library -- and each of those is its
        own LMDB write transaction; with the daily backup reading the same disk
        the engine needed hours to drain them (see codex-handoff 59).

        ``progress`` is called with partial counters while the rebuild runs (the
        admin UI shows them as a progress bar), ``should_stop`` is polled between
        batches so a running rebuild can be cancelled.
        """
        result = {"books": 0, "chapters": 0}

        def publish(force: bool = False, **values: Any) -> None:
            if progress is None:
                return
            progress({**values, "force": force})

        def check_stop() -> None:
            if should_stop is not None and should_stop():
                raise RebuildCancelled("重建已取消")

        async def _rebuild():
            from app.core.database import SessionLocal
            from app.models import Book, BookCustomTag, Chapter, CustomTag
            from app.services.storage import BookStorage
            from sqlalchemy import func, select

            storage = BookStorage()
            publish(force=True, phase="starting", message="正在删除旧索引…",
                    documents_done=0, documents_total=0,
                    started_at=naive_now().isoformat(timespec="seconds"))
            async with SessionLocal() as db:
                for idx_name in [self.INDEX_BOOKS, self.INDEX_CHAPTERS]:
                    try:
                        # The deletion is asynchronous: wait for it, otherwise
                        # ``_ensure_index`` below still sees the old index and
                        # the re-created one would have no primary key.
                        await asyncio.to_thread(
                            self.wait_for_task, self.client.delete_index(idx_name)
                        )
                    except Exception:
                        pass
                self._ensured.clear()
                # The gram fields are written by the rebuild itself, but the
                # probe's cached answer belongs to the index that was just
                # deleted.
                self._gram_ready.clear()
                self._ensure_index(self.INDEX_BOOKS)
                self._ensure_index(self.INDEX_CHAPTERS)

                # Totals first: the progress bar needs a denominator before the
                # first batch, and counting is cheap compared to reading files.
                books_total = int(await db.scalar(select(func.count(Book.id))) or 0)
                chapters_total = int(
                    await db.scalar(select(func.count(Chapter.id))) or 0
                )
                publish(
                    force=True, phase="books",
                    books_total=books_total, books_done=0,
                    chapters_total=chapters_total, chapters_done=0,
                    documents_total=books_total + chapters_total,
                    documents_done=0,
                    message=f"正在写入书籍 0 / {books_total}",
                )

                books = (await db.scalars(select(Book))).unique().all()
                custom_rows = await db.execute(
                    select(BookCustomTag.book_id, CustomTag.name)
                    .join(CustomTag, BookCustomTag.custom_tag_id == CustomTag.id)
                )
                custom_tag_map: dict[str, list[str]] = {}
                for book_id, name in custom_rows.all():
                    custom_tag_map.setdefault(str(book_id), []).append(name)
                book_meta: dict[str, dict] = {}
                book_batch: list[dict] = []
                for book in books:
                    check_stop()
                    tags = list(
                        dict.fromkeys(
                            [*book.tag_names, *custom_tag_map.get(book.id, [])]
                        )
                    )
                    doc = {
                        "id": book.id,
                        "title": book.title,
                        "author": book.author_name or "",
                        "description": book.description or "",
                        "status": book.status or "",
                        "source_id": book.source_id or "",
                        "author_id": book.author_id or "",
                        "is_r18": book.is_r18,
                        "owner_id": book.owner_id or None,
                        "is_public": bool(book.is_public),
                        "tags": tags,
                        "category_names": book.category_names,
                        "kind": normalize_kind(book.kind),
                    }
                    book_meta[book.id] = doc
                    book_batch.append(doc)
                    if len(book_batch) >= self.BOOK_BUFFER_SIZE:
                        self.client.index(self.INDEX_BOOKS).add_documents(book_batch)
                        result["books"] += len(book_batch)
                        book_batch = []
                        await self.wait_for_engine()
                        publish(
                            books_done=result["books"],
                            documents_done=result["books"],
                            message=f"正在写入书籍 {result['books']} / {books_total}",
                        )
                if book_batch:
                    self.client.index(self.INDEX_BOOKS).add_documents(book_batch)
                    result["books"] += len(book_batch)
                    publish(
                        force=True, books_done=result["books"],
                        documents_done=result["books"],
                        message=f"书籍完成（{result['books']}），开始写章节…",
                    )

                publish(
                    force=True, phase="chapters",
                    books_done=result["books"],
                    documents_done=result["books"],
                    message=f"正在写入章节 0 / {chapters_total}",
                )
                chapters = (await db.scalars(select(Chapter))).unique().all()
                batch: list[dict] = []
                for chapter in chapters:
                    if len(batch) == 0:
                        # Checked once per batch, not per document: reading the
                        # cancel flag on every chapter would be 718 000 file
                        # reads.
                        check_stop()
                    meta = book_meta.get(chapter.book_id) or {}
                    content = ""
                    if chapter.content_path:
                        try:
                            content = storage.read_chapter(chapter.content_path)
                        except Exception:
                            content = ""
                    batch.append({
                        "id": chapter.id,
                        "book_id": chapter.book_id,
                        "title": chapter.title or "",
                        "chapter_number": chapter.chapter_number or 0,
                        "book_title": meta.get("title", ""),
                        "book_author": meta.get("author", ""),
                        "book_description": (meta.get("description") or "")[
                            : self.CHAPTER_BOOK_DESCRIPTION_LIMIT
                        ],
                        "content": content[: self.content_index_limit()],
                        "is_r18": bool(meta.get("is_r18", False)),
                        "owner_id": meta.get("owner_id"),
                        "is_public": bool(meta.get("is_public", False)),
                        "tags": meta.get("tags") or [],
                        "category_names": meta.get("category_names") or [],
                        "kind": meta.get("kind") or KIND_NOVEL,
                        **chapter_gram_fields({
                            "title": chapter.title or "",
                            "book_title": meta.get("title", ""),
                            "book_author": meta.get("author", ""),
                        }),
                    })
                    if len(batch) >= self.CHAPTER_BUFFER_SIZE:
                        self.client.index(self.INDEX_CHAPTERS).add_documents(batch)
                        result["chapters"] += len(batch)
                        batch = []
                        await self.wait_for_engine()
                        publish(
                            chapters_done=result["chapters"],
                            documents_done=result["books"] + result["chapters"],
                            message=(
                                f"正在写入章节 {result['chapters']} / {chapters_total}"
                            ),
                        )
                if batch:
                    self.client.index(self.INDEX_CHAPTERS).add_documents(batch)
                    result["chapters"] += len(batch)
                    publish(
                        force=True, chapters_done=result["chapters"],
                        documents_done=result["books"] + result["chapters"],
                        message="章节写入完成，正在收尾…",
                    )

                # The rebuild just wrote every document, gram fields included, so
                # the metadata search may take the gram path immediately rather
                # than waiting for the next startup backfill.
                self._mark_gram_ready(self.INDEX_BOOKS)
                self._mark_gram_ready(self.INDEX_CHAPTERS)

        await _rebuild()
        return result

    def rebuild_progress_callback(self) -> Callable[[dict[str, Any]], None]:
        """A throttled writer for ``rebuild_index(progress=...)``."""
        last_write = {"at": 0.0}

        def publish(update: dict[str, Any]) -> None:
            forced = bool(update.get("force"))
            update = {k: v for k, v in update.items() if k != "force"}
            now = time.time()
            if not forced and now - last_write["at"] < REBUILD_PROGRESS_THROTTLE_SECONDS:
                return
            last_write["at"] = now
            write_rebuild_progress(update)

        return publish

    def start_rebuild(self) -> dict[str, Any]:
        """Kick off a background rebuild and return the initial progress.

        Returns the progress of the *already running* rebuild when one is in
        flight, so two clicks cannot run two rebuilds (they would delete each
        other's index).
        """
        current = read_rebuild_progress()
        if current.get("running"):
            return current

        write_rebuild_progress({
            "running": True,
            "phase": "starting",
            "started_at": naive_now().isoformat(timespec="seconds"),
            "finished_at": None,
            "books_done": 0,
            "chapters_done": 0,
            "documents_done": 0,
            "documents_total": 0,
            "percent": 0.0,
            "error": None,
            "result": None,
            "cancel_requested": False,
            "message": "已开始，正在准备…",
        })
        task = asyncio.create_task(self._run_rebuild_background())
        _REBUILD_TASKS.add(task)
        task.add_done_callback(_REBUILD_TASKS.discard)
        return read_rebuild_progress()

    def cancel_rebuild(self) -> dict[str, Any]:
        """Ask the running rebuild to stop at its next batch boundary."""
        current = read_rebuild_progress()
        if not current.get("running"):
            return current
        return write_rebuild_progress({
            "cancel_requested": True,
            "message": "已请求取消，等当前批次写完就停…",
        })

    async def _run_rebuild_background(self) -> None:
        """Run a rebuild to completion, publishing progress and failures."""
        publish = self.rebuild_progress_callback()
        try:
            result = await self.rebuild_index(
                progress=publish,
                should_stop=lambda: bool(read_rebuild_progress().get("cancel_requested")),
            )
        except RebuildCancelled:
            logger.info("Search index rebuild cancelled by the admin")
            write_rebuild_progress({
                "running": False,
                "phase": "cancelled",
                "finished_at": naive_now().isoformat(timespec="seconds"),
                "message": "重建已取消（已写入的部分保留）",
            })
            return
        except Exception as exc:
            # A background task that raises would only be logged as unhandled;
            # the UI has to be able to say *why* it stopped.
            logger.exception("Search index rebuild failed")
            write_rebuild_progress({
                "running": False,
                "phase": "failed",
                "finished_at": naive_now().isoformat(timespec="seconds"),
                "error": str(exc) or type(exc).__name__,
                "message": "重建失败",
            })
            return

        write_rebuild_progress({
            "running": False,
            "phase": "done",
            "finished_at": naive_now().isoformat(timespec="seconds"),
            "books_done": result.get("books", 0),
            "chapters_done": result.get("chapters", 0),
            "documents_done": int(result.get("books", 0)) + int(result.get("chapters", 0)),
            "percent": 100.0,
            "result": result,
            "message": (
                f"完成：书籍 {result.get('books', 0)} 本、章节 "
                f"{result.get('chapters', 0)} 章已提交，等待搜索服务消化"
            ),
        })

    #: Characters of the *book* description copied into every chapter document.
    #: The book's own index holds the full text; duplicating 2 000 characters
    #: across 718 221 chapter documents cost more index than the chapter bodies
    #: themselves (the live index reached 63 GB) and made every merge slow.  300
    #: keeps "search by summary" working while shrinking each document.
    CHAPTER_BOOK_DESCRIPTION_LIMIT = 300
    # Chapters are submitted in this many documents per Meilisearch task (see
    # ``CHAPTER_BUFFER_SIZE``); a book document is ~20x smaller, so one task can
    # carry many more of them.
    BOOK_BUFFER_SIZE = 500
    #: Tasks the engine may still have queued before a rebuild waits for it.
    #: Keep it far below what one batch can hold: Meilisearch merges consecutive
    #: tasks, and a merged batch of thousands of chapter documents is a single
    #: transaction that the live 12 GB container could not finish (2026-10-19).
    INDEX_BACKLOG_LIMIT = 200
    INDEX_BACKLOG_POLL_SECONDS = 2.0
    #: Never wait longer than this for the engine, so a wedged engine cannot hang
    #: a rebuild forever.
    INDEX_BACKLOG_MAX_WAIT_SECONDS = 300.0
    KIND_BACKFILL_BATCH = 1000
    # Ids one ``NOT EXISTS`` sweep collects per request, and the ceiling on one
    # sweep.  Meilisearch caps ``offset + limit`` at ``maxTotalHits``, so a sweep
    # cannot page past the engine's own ceiling in a single run; a set larger than
    # that converges over successive runs because each one fixes the ids it did
    # see.
    MISSING_SCAN_CHUNK = 1000
    MISSING_SCAN_MAX = 200_000

    def missing_document_ids(self, index_name: str, missing_filter: str) -> list[str]:
        """Ids of documents in ``index_name`` matching ``missing_filter``.

        ``missing_filter`` is a Meilisearch filter expression such as
        ``kind NOT EXISTS`` or the OR of every gram field (see
        ``gram_missing_filter``).

        ``index_missing_kind`` answers the same question well enough to *decide*
        whether a backfill is needed, but acting on that decision used to mean
        rewriting the whole library -- 718k chapters on the production index,
        which is a near-full re-index because every chapter update re-tokenises
        the chapter body.  Collecting the ids instead turns it into an update of
        exactly the documents that need one, and it is also what makes the stale
        documents visible: an id here that has no row in Postgres is an orphan
        that no backfill can ever satisfy, so the probe would otherwise report
        "missing" on every single startup, forever.
        """
        self._ensure_index(index_name)
        ids: list[str] = []
        offset = 0
        index = self.client.index(index_name)
        while offset < self.MISSING_SCAN_MAX:
            result = index.search("", {
                "limit": self.MISSING_SCAN_CHUNK,
                "offset": offset,
                "filter": missing_filter,
                "attributesToRetrieve": ["id"],
            })
            hits = result.get("hits") or []
            for hit in hits:
                doc_id = hit.get("id")
                if doc_id is not None:
                    ids.append(str(doc_id))
            if len(hits) < self.MISSING_SCAN_CHUNK:
                break
            offset += len(hits)
        return ids

    async def _ids_present_in_db(self, db, index_name: str, ids: list[str]) -> set[str]:
        """Which of ``ids`` still have a source row (the rest are stale documents)."""
        from sqlalchemy import select

        from app.models import Book, Chapter

        model = Book if index_name == self.INDEX_BOOKS else Chapter
        present: set[str] = set()
        for start in range(0, len(ids), self.KIND_BACKFILL_BATCH):
            chunk = ids[start : start + self.KIND_BACKFILL_BATCH]
            rows = (await db.execute(select(model.id).where(model.id.in_(chunk)))).all()
            present.update(str(row[0]) for row in rows)
        return present

    async def purge_orphan_documents(
        self, db, index_name: str, missing_filter: str
    ) -> int:
        """Drop index documents that have no source row any more.

        An orphan can never be given the field -- both backfills read Postgres --
        so ``missing_filter`` keeps matching it forever.  That is what made the
        completeness probes report a stale index on every startup (measured on
        the production index: 3 400 chapter documents whose rows were deleted,
        which queued 718k chapter rewrites per restart that could not change
        anything) and it would equally block the gram gate, since an orphan can
        never receive grams.  It is also simply wrong for search: an orphan is a
        hit the reader cannot open.
        """
        ids = self.missing_document_ids(index_name, missing_filter)
        if not ids:
            return 0
        present = await self._ids_present_in_db(db, index_name, ids)
        orphans = [doc_id for doc_id in ids if doc_id not in present]
        if not orphans:
            return 0
        if index_name == self.INDEX_BOOKS:
            self.delete_books_from_index(orphans)
        else:
            self.delete_chapters_from_index(orphans)
        logger.warning(
            "{}: dropped {} stale documents that no longer have a source row",
            index_name,
            len(orphans),
        )
        return len(orphans)

    @classmethod
    def gram_missing_filter(cls, index_name: str) -> str:
        """The Meilisearch filter matching documents short of any gram field."""
        return " OR ".join(
            f"{field} NOT EXISTS" for field in cls.gram_fields(index_name)
        )

    def index_missing_kind(self, index_name: str) -> bool:
        """Whether an index still holds documents without the ``kind`` field.

        The field was added after both indexes already held the whole library,
        and a document without it matches neither ``kind = "novel"`` nor
        ``kind = "comic"`` -- so ``/novels`` and ``/comics`` search would return
        nothing until it is backfilled.

        Only used to *decide*; the repair itself goes through
        ``missing_document_ids`` so it touches those documents and not the
        library.
        """
        self._ensure_index(index_name)
        try:
            result = self.client.index(index_name).search("", {
                "limit": 1,
                "filter": "kind NOT EXISTS",
                "attributesToRetrieve": ["id"],
            })
        except Exception as exc:
            logger.warning("Could not probe {} for kind: {}", index_name, exc)
            # Do not trigger a full backfill on an unknown index state.
            return False
        return bool(result.get("hits"))

    async def sync_book_kinds(
        self,
        *,
        force: bool = False,
        book_ids: list[str] | None = None,
    ) -> dict:
        """Copy each book's ``kind`` onto its documents in both search indexes.

        Uses ``update_documents`` so only ``kind`` is merged into the existing
        documents.  Three callers, three shapes:

        * ``book_ids`` given (after ``/books/reclassify``) -- refresh only the
          books whose kind actually changed, plus their chapters;
        * ``force=True`` -- rewrite the whole library;
        * default (the startup path) -- collect the documents the engine reports
          as missing the field and repair **only those**, deleting the ones that
          no longer exist in Postgres.

        That last shape used to be "probe; if anything is missing, rewrite the
        whole library".  On the production index the probe is *always* true --
        the chapters index holds ~3 400 documents whose rows were deleted from
        Postgres and which no backfill can ever satisfy -- so every restart
        enqueued 718k chapter rewrites (a near-full re-index) that could not
        change anything, and it ran twice per start.  Repairing by id is both
        correct and orders of magnitude smaller, and dropping the orphans is what
        lets the probe finally report an index as complete.

        Chapters need the field too: a ``content`` search runs against the
        chapters index, so without it a search started on /novels or /comics
        would silently return nothing.
        """
        self._ensure_index(self.INDEX_BOOKS)
        self._ensure_index(self.INDEX_CHAPTERS)
        targets = None
        missing: dict[str, list[str]] = {}
        if book_ids is not None:
            targets = [str(book_id) for book_id in book_ids if book_id]
            if not targets:
                return {"books": 0, "chapters": 0, "skipped": True}
            need_books = need_chapters = True
        elif force:
            need_books = need_chapters = True
        else:
            missing = {
                self.INDEX_BOOKS: self.missing_document_ids(
                    self.INDEX_BOOKS, "kind NOT EXISTS"
                ),
                self.INDEX_CHAPTERS: self.missing_document_ids(
                    self.INDEX_CHAPTERS, "kind NOT EXISTS"
                ),
            }
            need_books = bool(missing[self.INDEX_BOOKS])
            need_chapters = bool(missing[self.INDEX_CHAPTERS])
        result = {
            "books": 0,
            "chapters": 0,
            "orphans": 0,
            "skipped": not (need_books or need_chapters),
        }
        if result["skipped"]:
            return result

        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models import Book, Chapter

        batches = (
            [targets[i : i + self.KIND_BACKFILL_BATCH] for i in range(0, len(targets), self.KIND_BACKFILL_BATCH)]
            if targets is not None
            else None
        )

        async with SessionLocal() as db:
            books_index = self.client.index(self.INDEX_BOOKS)
            chapters_index = self.client.index(self.INDEX_CHAPTERS)

            async def _book_docs(book_query) -> list[dict]:
                rows = (await db.execute(book_query)).all()
                return [
                    {"id": str(book_id), "kind": normalize_kind(kind)}
                    for book_id, kind in rows
                ]

            async def _chapter_docs(chapter_query) -> list[dict]:
                rows = (await db.execute(chapter_query)).all()
                return [
                    {"id": str(chapter_id), "kind": normalize_kind(kind)}
                    for chapter_id, kind in rows
                ]

            if missing:
                # Startup repair: touch the documents the engine reported, and
                # nothing else.  An id with no row is an index document whose
                # source is gone -- it can never be repaired, and leaving it in
                # place is what made the probe report "missing" forever.
                for index_name, ids in missing.items():
                    if not ids:
                        continue
                    is_books = index_name == self.INDEX_BOOKS
                    present = await self._ids_present_in_db(db, index_name, ids)
                    orphans = [doc_id for doc_id in ids if doc_id not in present]
                    for start in range(0, len(present), self.KIND_BACKFILL_BATCH):
                        chunk = sorted(present)[start : start + self.KIND_BACKFILL_BATCH]
                        if is_books:
                            docs = await _book_docs(
                                select(Book.id, Book.kind).where(Book.id.in_(chunk))
                            )
                        else:
                            # ``outerjoin``: a chapter whose book row is missing
                            # still gets a kind, otherwise it stays "missing"
                            # forever just like an orphan.
                            docs = await _chapter_docs(
                                select(Chapter.id, Book.kind)
                                .outerjoin(Book, Book.id == Chapter.book_id)
                                .where(Chapter.id.in_(chunk))
                            )
                        if docs:
                            (books_index if is_books else chapters_index).update_documents(docs)
                            result["books" if is_books else "chapters"] += len(docs)
                    if orphans:
                        if is_books:
                            self.delete_books_from_index(orphans)
                        else:
                            self.delete_chapters_from_index(orphans)
                        result["orphans"] += len(orphans)
                        logger.warning(
                            "{}: dropped {} index documents whose row no longer "
                            "exists (they were unfixable by any backfill and kept "
                            "the completeness probe failing)",
                            index_name,
                            len(orphans),
                        )
            elif batches is not None:
                # Targeted refresh: one round trip per batch for books and for
                # their chapters.
                for batch in batches:
                    if need_books:
                        docs = await _book_docs(
                            select(Book.id, Book.kind).where(Book.id.in_(batch))
                        )
                        if docs:
                            books_index.update_documents(docs)
                            result["books"] += len(docs)
                    if need_chapters:
                        docs = await _chapter_docs(
                            select(Chapter.id, Book.kind)
                            .outerjoin(Book, Book.id == Chapter.book_id)
                            .where(Chapter.book_id.in_(batch))
                        )
                        if docs:
                            chapters_index.update_documents(docs)
                            result["chapters"] += len(docs)
            else:
                if need_books:
                    last_id = ""
                    while True:
                        docs = await _book_docs(
                            select(Book.id, Book.kind)
                            .where(Book.id > last_id)
                            .order_by(Book.id)
                            .limit(self.KIND_BACKFILL_BATCH)
                        )
                        if not docs:
                            break
                        last_id = docs[-1]["id"]
                        books_index.update_documents(docs)
                        result["books"] += len(docs)
                if need_chapters:
                    last_id = ""
                    while True:
                        docs = await _chapter_docs(
                            select(Chapter.id, Book.kind)
                            .join(Book, Book.id == Chapter.book_id)
                            .where(Chapter.id > last_id)
                            .order_by(Chapter.id)
                            .limit(self.KIND_BACKFILL_BATCH)
                        )
                        if not docs:
                            break
                        last_id = docs[-1]["id"]
                        chapters_index.update_documents(docs)
                        result["chapters"] += len(docs)

        logger.info(
            "Backfilled search kind: {} books, {} chapters",
            result["books"],
            result["chapters"],
        )
        return result

    def delete_book_from_index(self, book_id: str) -> None:
        try:
            self.client.index(self.INDEX_BOOKS).delete_document(book_id)
            logger.debug("Deleted book {} from search index", book_id)
        except Exception:
            pass

    async def sync_book_grams(
        self,
        *,
        force: bool = False,
    ) -> dict:
        """Merge the derived gram fields into every existing document.

        The fields were introduced after both indexes already held the whole
        library, and a search cannot use the gram filter until *every* document
        carries them -- a partly migrated index would answer with the backfilled
        books only, silently dropping the rest.  So this walks the library once
        (``update_documents`` merges, so nothing else in the document is touched
        and no chapter body has to be re-read) and the startup caller is a no-op
        on every later restart.

        Books are read from Postgres; a chapter's ``book_title``/``book_author``
        come from its book row, which ``Chapter`` can be joined to, so no storage
        read is involved either.

        **Chapters are not backfilled by default.**  Meilisearch re-tokenises the
        whole document on every update, and a chapter document carries up to
        ``content_index_limit()`` characters of body (20 000 by default) -- so
        gramming 732k chapters is a near-full re-index of a 94 GB store, measured on production
        as a single batch still running after 19 minutes with 11 of the 12 GB
        container's memory in use.  Books carry no body, so their backfill is
        cheap and is what the search box mostly needs.  Set
        ``MEILI_GRAM_BACKFILL_CHAPTERS=true`` to pay for the chapters too; until
        it completes they simply keep their windowed candidate path.
        """
        self._ensure_index(self.INDEX_BOOKS)
        self._ensure_index(self.INDEX_CHAPTERS)
        wants = {
            self.INDEX_BOOKS: True,
            self.INDEX_CHAPTERS: bool(
                getattr(settings, "MEILI_GRAM_BACKFILL_CHAPTERS", False)
            ),
        }
        # A disabled index must never be reported ready: searches would then take
        # the gram filter on documents that were never grammed and answer with
        # nothing at all.
        for index_name, enabled in wants.items():
            if not enabled:
                self._gram_ready.pop(index_name, None)
        need_books = wants[self.INDEX_BOOKS] and (
            force or self.index_missing_grams(self.INDEX_BOOKS)
        )
        need_chapters = wants[self.INDEX_CHAPTERS] and (
            force or self.index_missing_grams(self.INDEX_CHAPTERS)
        )
        # The probes above have just answered for the enabled indexes, so one with
        # nothing missing is ready *right now* -- this is also how a process that
        # starts after an earlier backfill (or after ``rebuild_index``) learns it
        # may take the gram path without paying a per-search probe.
        if wants[self.INDEX_BOOKS] and not need_books:
            self._mark_gram_ready(self.INDEX_BOOKS)
        if wants[self.INDEX_CHAPTERS] and not need_chapters:
            self._mark_gram_ready(self.INDEX_CHAPTERS)
        result = {
            "books": 0,
            "chapters": 0,
            "orphans": 0,
            "skipped": not (need_books or need_chapters),
            "enabled": [name for name, on in wants.items() if on],
        }
        if result["skipped"]:
            return result

        from sqlalchemy import select, text

        from app.core.database import SessionLocal
        from app.models import Author, Book, Chapter

        async with SessionLocal() as db:
            # The backend runs two uvicorn workers that each fire this startup
            # backfill; without a lock both would re-tokenise the whole index
            # twice.  ``pg_try_advisory_lock`` is session-scoped, so it stays held
            # for as long as this session's connection is open (the whole
            # backfill) and is released automatically on close -- even on a crash,
            # restart or the process being killed.  The key is a constant integer,
            # so it is inlined rather than bound, which keeps the statement
            # executable by every session shim (see the backfill test).
            acquired = (
                await db.execute(
                    text(
                        "SELECT pg_try_advisory_lock("
                        f"{self.GRAM_BACKFILL_ADVISORY_LOCK_KEY})"
                    )
                )
            ).scalar()
            if not acquired:
                result["skipped"] = True
                result["locked"] = True
                logger.info(
                    "Gram backfill is already running in another worker; skipping"
                )
                return result

            books_index = self.client.index(self.INDEX_BOOKS)
            chapters_index = self.client.index(self.INDEX_CHAPTERS)
            if need_books:
                last_id = ""
                while True:
                    rows = (
                        await db.execute(
                            # ``Book.author_name`` is a Python property over the
                            # ``author`` relationship, not a mapped column, so it
                            # cannot appear in a ``select()`` -- naming it there
                            # raised "Column expression ... expected" and killed
                            # the whole backfill on its first query in production
                            # (2026-10-14).  Join the table instead.
                            select(Book.id, Book.title, Author.name)
                            .outerjoin(Author, Author.id == Book.author_id)
                            .where(Book.id > last_id)
                            .order_by(Book.id)
                            .limit(self.GRAM_BACKFILL_BATCH)
                        )
                    ).all()
                    if not rows:
                        break
                    docs = []
                    for book_id, title, author in rows:
                        doc = {
                            "id": str(book_id),
                            "title": title or "",
                            "author": author or "",
                        }
                        last_id = str(book_id)
                        docs.append({**doc, **book_gram_fields(doc)})
                    books_index.update_documents(docs)
                    result["books"] += len(docs)
            if need_chapters:
                last_id = ""
                while True:
                    rows = (
                        await db.execute(
                            # ``outerjoin``, not ``join``: a chapter whose book row
                            # is missing must still receive its own ``title_grams``,
                            # otherwise it stays "missing" for the probe forever and
                            # the gram path can never switch on.  Such a chapter
                            # simply gets empty book-title grams.
                            select(
                                Chapter.id, Chapter.title,
                                Book.title, Author.name,
                            )
                            .outerjoin(Book, Book.id == Chapter.book_id)
                            .outerjoin(Author, Author.id == Book.author_id)
                            .where(Chapter.id > last_id)
                            .order_by(Chapter.id)
                            .limit(self.GRAM_BACKFILL_BATCH)
                        )
                    ).all()
                    if not rows:
                        break
                    docs = []
                    for chapter_id, title, book_title, book_author in rows:
                        doc = {
                            "id": str(chapter_id),
                            "title": title or "",
                            "book_title": book_title or "",
                            "book_author": book_author or "",
                        }
                        last_id = str(chapter_id)
                        docs.append({**doc, **chapter_gram_fields(doc)})
                    chapters_index.update_documents(docs)
                    result["chapters"] += len(docs)

        # Verify rather than assume.  Only an index that now reports nothing
        # missing may take the gram path: a backfill interrupted halfway would
        # otherwise hide exactly the documents it never reached, and a search
        # that silently loses books is worse than one that is merely truncated.
        #
        # Any document still missing the fields is either not yet written or an
        # orphan with no source row.  An orphan can never be grammed by a backfill
        # that reads Postgres, so leaving it would fail this check on every
        # startup forever -- which is exactly what the kind probe did for a day.
        # Drop the orphans, then judge the index.
        async with SessionLocal() as db:
            for index_name, enabled in wants.items():
                if not enabled:
                    continue
                result["orphans"] += await self.purge_orphan_documents(
                    db, index_name, self.gram_missing_filter(index_name)
                )
                if self.index_missing_grams(index_name):
                    self._gram_ready.pop(index_name, None)
                else:
                    self._mark_gram_ready(index_name)
        logger.info(
            "Backfilled search grams: {} books, {} chapters ({} stale documents "
            "dropped); enabled indexes: {}",
            result["books"],
            result["chapters"],
            result["orphans"],
            result["enabled"],
        )
        return result

