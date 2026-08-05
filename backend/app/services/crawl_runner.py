"""DB-backed full-site crawl queue worker.

Manual crawl tasks are stored in ``crawl_tasks`` and picked up by a single
worker in priority order. This lets users pause/cancel queued tasks and move
a specific source to the front without waiting for every earlier task.
"""

import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, update

from app.core.database import SessionLocal
from app.models import CrawlTask


def _naive_utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _next_pending_task_id() -> str | None:
    async with SessionLocal() as db:
        return await db.scalar(
            select(CrawlTask.id)
            .where(CrawlTask.status == "pending")
            .order_by(CrawlTask.priority.desc(), CrawlTask.created_at.asc())
            .limit(1)
        )


async def _reset_stale_running_tasks() -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(CrawlTask)
            .where(CrawlTask.status == "running")
            .values(status="pending")
        )
        await db.commit()


async def run_crawl_task_async(task_id: str) -> dict:
    """Execute one pending crawl task with pause/cancel/progress support."""
    from app.services.sync import SyncService

    class TaskCancelled(Exception):
        pass

    class TaskPaused(Exception):
        pass

    async with SessionLocal() as db:
        task_obj = await db.get(CrawlTask, task_id)
        if task_obj is None:
            return {"status": "not_found"}
        if task_obj.status != "pending":
            return {"status": "skipped", "reason": task_obj.status}

        task_obj.status = "running"
        task_obj.started_at = _naive_utcnow()
        task_obj.error = None
        await db.commit()
        await db.refresh(task_obj)
        if task_obj.status != "running":
            return {"status": "skipped", "reason": task_obj.status}

        start_page = int((task_obj.progress or {}).get("next_page") or 1)
        chapter_progress_updates = 0

        async def _wait_if_paused() -> None:
            await db.refresh(task_obj)
            if task_obj.status == "cancelled":
                raise TaskCancelled("Task cancelled")
            if task_obj.status == "paused":
                raise TaskPaused("Task paused")

        async def _update_progress(
            page: int,
            found: int,
            synced: int,
            failed: int,
        ) -> None:
            task_obj.progress = {
                **(task_obj.progress or {}),
                "pages_checked": page,
                "books_found": found,
                "books_synced": synced,
                "books_failed": failed,
                "next_page": page,
            }
            await db.commit()

        async def _update_chapter_progress(info: dict) -> None:
            nonlocal chapter_progress_updates
            task_obj.progress = {
                **(task_obj.progress or {}),
                "current_book": info.get("book_title") or "",
                "current_chapter": info.get("chapter_title") or "",
                "current_chapters_created": info.get("created_chapters", 0),
                "current_chapters_skipped": info.get("skipped_chapters", 0),
                "current_chapters_failed": info.get("failed_chapters", 0),
                "current_chapters_total": info.get("total_chapters", 0),
            }
            chapter_progress_updates += 1
            if chapter_progress_updates % 10 == 0:
                await db.commit()

        try:
            result = await SyncService(db).discover_and_sync_all(
                task_obj.source,
                max_pages=task_obj.max_pages or 200,
                progress_cb=_update_progress,
                chapter_progress_cb=_update_chapter_progress,
                before_step=_wait_if_paused,
                start_page=start_page,
            )
            task_obj.status = "completed"
            task_obj.result = result
            task_obj.progress = {
                "pages_checked": result.get("pages_checked", 0),
                "books_found": result.get("books_found", 0),
                "books_synced": result.get("books_synced", 0),
                "books_failed": result.get("books_failed", 0),
                "chapters_created": result.get("chapters_created", 0),
                "chapters_skipped": result.get("chapters_skipped", 0),
                "chapters_failed": result.get("chapters_failed", 0),
                "done": True,
            }
            task_obj.finished_at = _naive_utcnow()
            await db.commit()
            return result
        except TaskPaused:
            task_obj.status = "paused"
            await db.commit()
            return {"status": "paused", "task_id": task_id}
        except TaskCancelled as exc:
            task_obj.status = "cancelled"
            task_obj.error = str(exc)
            task_obj.finished_at = _naive_utcnow()
            await db.commit()
            raise
        except Exception as exc:
            task_obj.status = "failed"
            task_obj.error = str(exc)
            task_obj.finished_at = _naive_utcnow()
            await db.commit()
            logger.opt(exception=exc).error("Crawl task {} failed", task_id)
            raise


async def _worker_loop() -> None:
    while True:
        task_id = await _next_pending_task_id()
        if task_id is None:
            await asyncio.sleep(2)
            continue
        try:
            await run_crawl_task_async(task_id)
        except Exception as exc:
            logger.error("Crawl task {} stopped: {}", task_id, exc)
        await asyncio.sleep(0.5)


async def main_async() -> None:
    await _reset_stale_running_tasks()
    await _worker_loop()


def main() -> None:
    asyncio.run(main_async())
