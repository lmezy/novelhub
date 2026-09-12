"""DB-backed full-site crawl queue worker.

Manual crawl tasks are stored in ``crawl_tasks`` and picked up by a single
worker in priority order. This lets users pause/cancel queued tasks and move
a specific source to the front without waiting for every earlier task.
"""

import asyncio
import os
from typing import Any
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select, update

from app.core.config import settings, sync_thread_count
from app.core.database import SessionLocal
from app.models import CrawlTask


def _naive_utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Failures that are worth retrying on their own: the proxy/upstream was
# momentarily unreachable, not the source rules or a captcha gate.  A short
# outage used to mark the whole task failed (and the UI then showed a
# misleading "书源未返回可同步的书籍").
_TRANSIENT_TASK_MARKERS = (
    "timeout",
    "timed out",
    "connecttimeout",
    "readtimeout",
    "pooltimeout",
    "connecterror",
    "readerror",
    "writeerror",
    "remoteprotocolerror",
    "proxyerror",
    "connection reset",
    "connection closed",
    "connection refused",
    "connection aborted",
    "temporarily unavailable",
    "temporary failure",
    "network is unreachable",
    "name or service not known",
    "getaddrinfo failed",
    "upstream server returned",
    "server returned a transient",
    "error code 5",
    "empty content",
    "网络",
    "暂时无法访问",
)

# These need a Cookie / a different exit node, so retrying immediately only
# hammers the site.
_NON_TRANSIENT_TASK_MARKERS = (
    "anti-bot",
    "captcha",
    "验证码",
    "人机验证",
    "身份验证",
    "反爬",
    "cookie",
    "legado js",
    "书源规则",
)


def _is_transient_task_error(exc: BaseException) -> bool:
    """Whether a failed crawl task should be retried automatically."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if any(marker in message for marker in _NON_TRANSIENT_TASK_MARKERS):
        return False
    if any(marker in message for marker in _TRANSIENT_TASK_MARKERS):
        return True
    # httpx.RequestError and asyncio.TimeoutError often stringify to "".
    return name in {
        "connecttimeout",
        "readtimeout",
        "writetimeout",
        "pooltimeout",
        "connecterror",
        "readerror",
        "writeerror",
        "proxyerror",
        "remoteprotocolerror",
        "timeouterror",
    }


def _task_retry_delay_seconds(retries: int) -> int:
    try:
        base = int(os.getenv("SYNC_TASK_RETRY_BASE_SECONDS", "60"))
    except (TypeError, ValueError):
        base = 60
    return max(5, base) * (2 ** max(0, retries))


def _describe_error(exc: BaseException | None) -> str:
    """Human-readable error text, even for exceptions with empty ``str()``."""
    if exc is None:
        return ""
    message = str(exc)
    return message if message.strip() else type(exc).__name__


# Counters a crawl task accumulates across the attempts of one task row.
_RESULT_COUNTER_KEYS = (
    "books_found",
    "books_synced",
    "books_failed",
    "books_filtered",
    "chapters_created",
    "chapters_skipped",
    "chapters_failed",
)


def _carry_result_counters(previous: dict | None, addition: dict | None) -> dict:
    """Merge one attempt's counters into a crawl-task ``result`` snapshot.

    Automatic retries re-run discovery, so a later attempt only knows its own
    numbers.  Carrying the earlier ones forward keeps the report truthful: a
    task that synced 68 books before a proxy outage used to lose those 68 when
    the retry reported (or failed with) zero.
    """
    merged = dict(previous or {})
    addition = addition or {}
    for key in _RESULT_COUNTER_KEYS:
        merged[key] = int(merged.get(key, 0) or 0) + int(addition.get(key, 0) or 0)
    merged["pages_checked"] = max(
        int(merged.get("pages_checked", 0) or 0),
        int(addition.get("pages_checked", 0) or 0),
    )
    return merged


async def _write_task_row(task_id: str, values: dict[str, Any]) -> bool:
    """Persist crawl-task fields through a dedicated short-lived session.

    The worker's session can already be unusable when a task fails: a
    rollback expires every ORM instance, and touching one of those expired
    attributes from non-async code raises ``MissingGreenlet``.  That used to
    leave the row stuck in ``running`` forever and hide the real error.  A
    separate session makes the terminal state write independent of whatever
    happened to the worker session.
    """
    async def _write() -> None:
        async with SessionLocal() as session:
            await session.execute(
                update(CrawlTask).where(CrawlTask.id == task_id).values(**values)
            )
            await session.commit()

    try:
        # Bounded: a row lock held by the broken worker session must not turn a
        # failed task into a hung worker.
        await asyncio.wait_for(_write(), timeout=20)
        return True
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error(
            "Failed to persist crawl task {} state ({}): {}",
            task_id,
            ", ".join(sorted(values)),
            exc,
        )
        return False


async def _apply_task_state(task_obj, db, task_id: str, values: dict[str, Any]) -> None:
    """Set crawl-task fields on the ORM object and make sure they are stored.

    Writing through the worker session keeps the in-process object coherent
    (tests and the resume path rely on it).  If that session is broken the
    write is retried with a fresh session so a task never stays ``running``.
    """
    for key, value in values.items():
        setattr(task_obj, key, value)
    try:
        await db.commit()
        return
    except Exception as exc:
        logger.warning(
            "Crawl task {} state commit failed ({}); retrying with a fresh session",
            task_id,
            exc,
        )
    try:
        await db.rollback()
    except Exception:
        pass
    await _write_task_row(task_id, values)


async def _next_pending_task_ids(limit: int = 1) -> list[str]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(CrawlTask.id)
            .where(
                CrawlTask.status == "pending",
                (CrawlTask.resume_at.is_(None))
                | (CrawlTask.resume_at <= _naive_utcnow()),
            )
            .order_by(CrawlTask.priority.desc(), CrawlTask.created_at.asc())
            .limit(limit)
        )
        return list(rows.all())


async def _reset_stale_running_tasks() -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(CrawlTask)
            .where(CrawlTask.status == "running")
            .values(status="pending", resume_at=None)
        )
        await db.commit()


async def run_crawl_task_async(task_id: str) -> dict:
    """Execute one pending crawl task with pause/cancel/progress support."""
    from app.services.sync import SyncPaused, SyncService

    class TaskCancelled(Exception):
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
        task_obj.resume_at = None
        await db.commit()
        await db.refresh(task_obj)
        if task_obj.status != "running":
            return {"status": "skipped", "reason": task_obj.status}

        progress_state = dict(task_obj.progress or {})
        # ``result`` holds what previous attempts already reported while
        # ``progress`` counters describe the attempt that is starting now.
        # Keeping the loaded counters here made every retry re-merge the same
        # numbers (68 synced books became 136 in the report).
        previous_result = (
            dict(task_obj.result) if isinstance(task_obj.result, dict) else {}
        )
        if not previous_result:
            # Tasks created before the counters moved into ``result`` only have
            # numbers in ``progress``; keep those instead of reporting zero.
            previous_result = {
                key: int(progress_state.get(key, 0) or 0)
                for key in _RESULT_COUNTER_KEYS
            }
            previous_result["pages_checked"] = int(
                progress_state.get("pages_checked", 0) or 0
            )
        for _key in _RESULT_COUNTER_KEYS:
            progress_state[_key] = 0
        progress_state["pages_checked"] = 0
        start_page = int(progress_state.get("next_page") or 1)
        chapter_progress_updates = 0
        db_lock = asyncio.Lock()

        async def _wait_if_paused() -> None:
            async with db_lock:
                await db.refresh(task_obj)
                if task_obj.status == "cancelled":
                    raise TaskCancelled("Task cancelled")
                if task_obj.status == "paused":
                    raise SyncPaused("Task paused")

        async def _update_progress(
            page: int,
            found: int,
            synced: int,
            failed: int,
        ) -> None:
            nonlocal progress_state
            async with db_lock:
                progress_state.update({
                    "pages_checked": page,
                    "books_found": found,
                    "books_synced": synced,
                    "books_failed": failed,
                    "next_page": page,
                })
                task_obj.progress = {
                    **progress_state,
                }
                await db.commit()

        async def _update_chapter_progress(info: dict) -> None:
            nonlocal chapter_progress_updates, progress_state
            async with db_lock:
                progress_state.update({
                    "current_book": info.get("book_title") or "",
                    "current_chapter": info.get("chapter_title") or "",
                    "current_chapters_created": info.get("created_chapters", 0),
                    "current_chapters_skipped": info.get("skipped_chapters", 0),
                    "current_chapters_failed": info.get("failed_chapters", 0),
                    "current_chapters_total": info.get("total_chapters", 0),
                })
                task_obj.progress = {
                    **progress_state,
                }
                chapter_progress_updates += 1
                if chapter_progress_updates % 10 == 0:
                    await db.commit()

        try:
            result = await SyncService(db).discover_and_sync_all(
                task_obj.source,
                max_pages=(
                    task_obj.max_pages
                    if task_obj.max_pages is not None
                    else 200
                ),
                progress_cb=_update_progress,
                chapter_progress_cb=_update_chapter_progress,
                before_step=_wait_if_paused,
                checkpoint_cb=_wait_if_paused,
                start_page=start_page,
                page_batch_size=int(getattr(settings, "SYNC_PAGE_BATCH_SIZE", 0) or 0),
                exclude_tags=list(getattr(task_obj, "exclude_tags", None) or []),
                exclude_categories=list(getattr(task_obj, "exclude_categories", None) or []),
            )
            await db.refresh(task_obj)
            if task_obj.status == "paused":
                await _apply_task_state(
                    task_obj, db, task_id,
                    {"status": "paused", "resume_at": None},
                )
                return {"status": "paused", "task_id": task_id}
            if task_obj.status == "cancelled":
                await _apply_task_state(
                    task_obj, db, task_id,
                    {"status": "cancelled", "finished_at": _naive_utcnow()},
                )
                raise TaskCancelled("Task cancelled")
            # Preserve every page result when a task is resumed in batches.
            previous = previous_result
            merged = dict(result)
            for key in ("books_found", "books_synced", "books_failed", "books_filtered", "chapters_created", "chapters_skipped", "chapters_failed"):
                merged[key] = int(previous.get(key, 0) or 0) + int(result.get(key, 0) or 0)
            merged["pages_checked"] = max(int(previous.get("pages_checked", 0) or 0), int(result.get("pages_checked", 0) or 0))
            merged["details"] = [*(previous.get("details") or []), *(result.get("details") or [])]
            merged["done"] = bool(result.get("done"))
            batch_size = int(getattr(settings, "SYNC_PAGE_BATCH_SIZE", 0) or 0)
            if batch_size > 0 and not result.get("done"):
                await _apply_task_state(task_obj, db, task_id, {
                    "status": "pending",
                    "result": merged,
                    "progress": {
                        "pages_checked": merged.get("pages_checked", 0),
                        "books_found": merged.get("books_found", 0),
                        "books_synced": merged.get("books_synced", 0),
                        "books_failed": merged.get("books_failed", 0),
                        "books_filtered": merged.get("books_filtered", 0),
                        "chapters_created": merged.get("chapters_created", 0),
                        "chapters_skipped": merged.get("chapters_skipped", 0),
                        "chapters_failed": merged.get("chapters_failed", 0),
                        "next_page": result.get("next_page", start_page),
                    },
                    "finished_at": None,
                    "resume_at": _naive_utcnow() + timedelta(
                        milliseconds=int(
                            getattr(settings, "SYNC_BATCH_INTERVAL_MS", 5000) or 0
                        )
                    ),
                })
                return {
                    "status": "queued",
                    "task_id": task_id,
                    "next_page": result.get("next_page", start_page),
                }
            await _apply_task_state(task_obj, db, task_id, {
                "status": (
                    "completed_with_errors"
                    if merged.get("books_failed", 0) or merged.get("chapters_failed", 0)
                    else "completed"
                ),
                "result": merged,
                "progress": {
                    "pages_checked": merged.get("pages_checked", 0),
                    "books_found": merged.get("books_found", 0),
                    "books_synced": merged.get("books_synced", 0),
                    "books_failed": merged.get("books_failed", 0),
                    "books_filtered": merged.get("books_filtered", 0),
                    "chapters_created": merged.get("chapters_created", 0),
                    "chapters_skipped": merged.get("chapters_skipped", 0),
                    "chapters_failed": merged.get("chapters_failed", 0),
                    "done": True,
                },
                "finished_at": _naive_utcnow(),
            })
            return result
        except SyncPaused:
            await _apply_task_state(
                task_obj, db, task_id, {"status": "paused", "resume_at": None}
            )
            return {"status": "paused", "task_id": task_id}
        except TaskCancelled as exc:
            await _apply_task_state(task_obj, db, task_id, {
                "status": "cancelled",
                "error": _describe_error(exc),
                "finished_at": _naive_utcnow(),
            })
            raise
        except Exception as exc:
            # Never read an ORM attribute here: the session may have been
            # rolled back by a failed book sync, which expires every instance
            # and turns a plain attribute read into MissingGreenlet.  The
            # in-memory ``progress_state`` snapshot is the source of truth.
            retries = int(progress_state.get("auto_retries") or 0)
            try:
                max_retries = int(os.getenv("SYNC_TASK_MAX_AUTO_RETRIES", "2"))
            except (TypeError, ValueError):
                max_retries = 2
            if _is_transient_task_error(exc) and retries < max(0, max_retries):
                delay = _task_retry_delay_seconds(retries)
                message = (
                    "网络/代理暂时不可用（"
                    f"{type(exc).__name__}: {_describe_error(exc)}），"
                    f"{delay} 秒后自动重试（第 {retries + 1}/{max_retries} 次）"
                )
                await _apply_task_state(task_obj, db, task_id, {
                    "status": "pending",
                    "finished_at": None,
                    "resume_at": _naive_utcnow() + timedelta(seconds=delay),
                    "progress": {**progress_state, "auto_retries": retries + 1},
                    "result": _carry_result_counters(previous_result, progress_state),
                    "error": message,
                })
                logger.warning(
                    "Crawl task {} hit a transient error, retrying in {}s "
                    "({}/{}): {}",
                    task_id,
                    delay,
                    retries + 1,
                    max_retries,
                    exc,
                )
                return {
                    "status": "retrying",
                    "task_id": task_id,
                    "retry_in_seconds": delay,
                }
            await _apply_task_state(task_obj, db, task_id, {
                "status": "failed",
                "error": _describe_error(exc),
                "finished_at": _naive_utcnow(),
                "progress": dict(progress_state),
                "result": _carry_result_counters(previous_result, progress_state),
            })
            logger.opt(exception=exc).error("Crawl task {} failed", task_id)
            raise


async def _worker_loop() -> None:
    concurrency = min(
        max(1, int(getattr(settings, "SYNC_WORKER_CONCURRENCY", 2))),
        sync_thread_count(),
    )
    claimed: set[str] = set()
    active: dict[asyncio.Task, str] = {}

    async def _run_guarded(task_id: str) -> None:
        try:
            await run_crawl_task_async(task_id)
        except Exception as exc:
            logger.error("Crawl task {} stopped: {}", task_id, exc)

    while True:
        while len(active) < concurrency:
            candidates = await _next_pending_task_ids(concurrency)
            if not candidates:
                break
            started_any = False
            for task_id in candidates:
                if len(active) >= concurrency:
                    break
                if task_id in claimed:
                    continue
                claimed.add(task_id)
                task = asyncio.create_task(_run_guarded(task_id))
                active[task] = task_id
                started_any = True
            if not started_any:
                # All candidates are already claimed but not yet running.
                await asyncio.sleep(0.1)
                break

        if not active:
            await asyncio.sleep(2)
            continue

        # Poll for newly created tasks even while existing tasks are running.
        # Without the timeout, a source created right after the initial fill
        # waits until an active task finishes before being picked up.
        done, _ = await asyncio.wait(
            active.keys(),
            return_when=asyncio.FIRST_COMPLETED,
            timeout=1.0,
        )
        for finished in done:
            task_id = active.pop(finished)
            claimed.discard(task_id)
            if finished.cancelled():
                logger.warning("Crawl task {} cancelled", task_id)
                continue
            exc = finished.exception()
            if exc is not None:
                logger.error("Crawl task {} stopped: {}", task_id, exc)
        await asyncio.sleep(0.2)


async def main_async() -> None:
    await _reset_stale_running_tasks()
    await _worker_loop()


def main() -> None:
    asyncio.run(main_async())
