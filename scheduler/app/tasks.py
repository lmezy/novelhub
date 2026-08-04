"""Celery tasks for automated novel syncing.

daily_sync_all      -- beat-scheduled: sync every enabled source
sync_single_source  -- sync one source by ID
resync_all_books    -- resync every book already in the library
"""

import asyncio
from uuid import uuid4
from datetime import datetime, timezone

from celery_app import app
from loguru import logger


def _naive_utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@app.task(name="tasks.daily_sync_all")
def daily_sync_all() -> dict:
    """Daily beat task: sync all enabled sources."""
    return asyncio.get_event_loop().run_until_complete(_daily_sync_all_async())


@app.task(name="tasks.sync_single_source")
def sync_single_source(source_id: str) -> dict:
    """Sync a single source by its ID."""
    return asyncio.get_event_loop().run_until_complete(
        _sync_single_source_async(source_id)
    )


@app.task(name="tasks.resync_all_books")
def resync_all_books() -> dict:
    """Resync every book in the library (checks for new chapters)."""
    return asyncio.get_event_loop().run_until_complete(_resync_all_books_async())


@app.task(name="tasks.check_cookie_health")
def check_cookie_health() -> dict:
    """Periodic task: validate all cookies and auto-refresh expired ones."""
    return asyncio.get_event_loop().run_until_complete(_check_cookie_health_async())


@app.task(name="tasks.crawl_all_source")
def crawl_all_source(source_id: str, max_pages: int = 200, task_id: str | None = None) -> dict:
    """Crawl every discoverable book from a source in the background."""
    return asyncio.get_event_loop().run_until_complete(
        _crawl_all_source_async(source_id, max_pages, task_id)
    )


async def _check_cookie_health_async() -> dict:
    from app.services.cookie_health import CookieHealthService
    return await CookieHealthService.check_all_cookies()


async def _crawl_all_source_async(source_id: str, max_pages: int, task_id: str | None) -> dict:
    from app.core.database import SessionLocal
    from app.models import CrawlTask
    from app.services.crawl_runner import run_crawl_task_async

    if task_id is None:
        async with SessionLocal() as db:
            task = CrawlTask(
                id=str(uuid4()),
                source=source_id,
                mode="discover_all",
                max_pages=max_pages,
                status="pending",
            )
            db.add(task)
            await db.commit()
            task_id = task.id
    return await run_crawl_task_async(task_id)


async def _daily_sync_all_async() -> dict:
    from app.core.database import SessionLocal
    from app.models import Book, CrawlLog, CrawlTask, Source
    from app.services.sync import SyncService
    from sqlalchemy import select

    async with SessionLocal() as db:
        sources = await db.scalars(
            select(Source).where(Source.enabled == True)
        )
        source_list = list(sources)
        task_obj = CrawlTask(
            id=str(uuid4()),
            source="*",
            status="running",
            started_at=_naive_utcnow(),
        )
        db.add(task_obj)
        await db.commit()
        results = {
            "task_id": task_obj.id,
            "total_sources": len(source_list),
            "synced": 0,
            "failed": 0,
            "details": [],
        }
        service = SyncService(db)
        for src in source_list:
            try:
                try:
                    shelf_result = await service.sync_bookshelf(src.id)
                    results["details"].append({
                        "source_id": src.id,
                        "method": "bookshelf",
                        "status": "ok",
                        "books_on_shelf": shelf_result.get("total", 0),
                    })
                except ValueError:
                    books = await db.scalars(
                        select(Book).where(Book.source_id == src.id)
                    )
                    book_list = list(books)
                    if not book_list:
                        results["details"].append({
                            "source_id": src.id,
                            "method": "skip",
                            "status": "ok",
                            "reason": "no books and no cookie",
                        })
                        results["synced"] += 1
                        continue
                    for book in book_list:
                        try:
                            r = await service.resync_book(book.id)
                            results["details"].append({
                                "book_id": book.id,
                                "method": "resync",
                                "status": "ok",
                                "chapters": r.get("created_chapters", 0),
                            })
                        except Exception as exc:
                            results["details"].append({
                                "book_id": book.id,
                                "method": "resync",
                                "status": "failed",
                                "error": str(exc),
                            })
                results["synced"] += 1
            except Exception as exc:
                logger.opt(exception=exc).error(
                    "Sync failed for source {}", src.id
                )
                results["failed"] += 1
                results["details"].append({
                    "source_id": src.id,
                    "status": "failed",
                    "error": str(exc),
                })
        task_obj.status = "completed" if results["failed"] == 0 else "completed_with_errors"
        task_obj.finished_at = _naive_utcnow()
        await db.commit()
        logger.info("Daily sync: {} synced, {} failed", results["synced"], results["failed"])
        return results


async def _sync_single_source_async(source_id: str) -> dict:
    from app.core.database import SessionLocal
    from app.models import Book, Source
    from app.services.sync import SyncService
    from sqlalchemy import select

    async with SessionLocal() as db:
        source = await db.get(Source, source_id)
        if source is None or not source.enabled:
            return {"status": "skipped", "reason": "source not found or disabled"}
        service = SyncService(db)
        try:
            return await service.sync_bookshelf(source_id)
        except ValueError:
            books = await db.scalars(
                select(Book).where(Book.source_id == source_id)
            )
            results = []
            for book in books:
                try:
                    r = await service.resync_book(book.id)
                    results.append({"book_id": book.id, **r})
                except Exception as exc:
                    results.append({"book_id": book.id, "error": str(exc)})
            return {"status": "partial", "results": results}


async def _resync_all_books_async() -> dict:
    from app.core.database import SessionLocal
    from app.models import Book
    from app.services.sync import SyncService
    from sqlalchemy import select

    async with SessionLocal() as db:
        books = await db.scalars(select(Book))
        book_list = list(books)
        results = {"total_books": len(book_list), "ok": 0, "failed": 0, "details": []}
        service = SyncService(db)
        for book in book_list:
            try:
                r = await service.resync_book(book.id)
                results["ok"] += 1
                results["details"].append({
                    "book_id": book.id,
                    "chapters": r.get("created_chapters", 0),
                })
            except Exception as exc:
                results["failed"] += 1
                results["details"].append({
                    "book_id": book.id,
                    "error": str(exc),
                })
        return results
