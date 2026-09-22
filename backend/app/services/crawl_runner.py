"""DB-backed full-site crawl queue worker.

Manual crawl tasks are stored in ``crawl_tasks`` and picked up by a single
worker in priority order. This lets users pause/cancel queued tasks and move
a specific source to the front without waiting for every earlier task.
"""

import asyncio
import os
import time
from typing import Any
from datetime import timedelta

from loguru import logger
from sqlalchemy import select, update

from app.core.clock import naive_now
from app.core.config import (
    db_pool_capacity,
    settings,
    sync_source_concurrency,
    task_stall_seconds,
    task_stop_grace_seconds,
    task_supervise_interval_seconds,
)
from app.core.database import SessionLocal
from app.core.heartbeat import mark_alive
from app.models import CrawlTask


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


async def _next_pending_tasks(
    limit: int = 1,
    exclude_sources: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Pending ``(task_id, source)`` pairs in priority order.

    ``exclude_sources`` keeps a source that is already syncing out of the
    result: every source gets its own worker, but one source must never run two
    tasks at once (that is what would break a site's ``concurrentRate``).
    """
    async with SessionLocal() as db:
        query = select(CrawlTask.id, CrawlTask.source).where(
            CrawlTask.status == "pending",
            (CrawlTask.resume_at.is_(None))
            | (CrawlTask.resume_at <= naive_now()),
        )
        if exclude_sources:
            query = query.where(CrawlTask.source.notin_(exclude_sources))
        rows = await db.execute(
            query.order_by(
                CrawlTask.priority.desc(), CrawlTask.created_at.asc()
            ).limit(limit)
        )
        return [(row[0], row[1]) for row in rows.all()]


async def _reset_stale_running_tasks() -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(CrawlTask)
            .where(CrawlTask.status == "running")
            .values(status="pending", resume_at=None)
        )
        await db.commit()


#: How often a chapter-level progress report is written to the task row while
#: the tenth update is still far away.  That row is what the stall watchdog
#: reads (``_progress_marker``), and an image album chapter is downloaded one
#: image at a time over hours, so a time bound is what keeps the row moving.
_CHAPTER_PROGRESS_COMMIT_SECONDS = 30.0


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
        task_obj.started_at = naive_now()
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
        chapter_progress_committed_at = time.monotonic()
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
            nonlocal chapter_progress_updates, chapter_progress_committed_at
            nonlocal progress_state
            async with db_lock:
                progress_state.update({
                    "current_book": info.get("book_title") or "",
                    "current_chapter": info.get("chapter_title") or "",
                    "current_chapters_created": info.get("created_chapters", 0),
                    "current_chapters_skipped": info.get("skipped_chapters", 0),
                    "current_chapters_failed": info.get("failed_chapters", 0),
                    "current_chapters_total": info.get("total_chapters", 0),
                    # Images of the chapter being downloaded right now.  A
                    # gallery chapter is fetched one image at a time, so an hour
                    # or more can pass between two chapter reports and this is
                    # the only counter that moves inside it (see
                    # ``_progress_marker``).
                    "current_images_done": int(info.get("images_done", 0) or 0),
                    "current_images_total": int(info.get("images_total", 0) or 0),
                })
                task_obj.progress = {
                    **progress_state,
                }
                chapter_progress_updates += 1
                now = time.monotonic()
                # Waiting for the tenth report left the row untouched for the
                # whole first album chapter: online on 2026-09-22 two gallery
                # tasks were killed as "no progress for 60 minutes" while their
                # logs showed images being fetched until minutes before.
                if (
                    chapter_progress_updates % 10 == 0
                    or now - chapter_progress_committed_at
                    >= _CHAPTER_PROGRESS_COMMIT_SECONDS
                ):
                    chapter_progress_committed_at = now
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
                    {"status": "cancelled", "finished_at": naive_now()},
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
                    "resume_at": naive_now() + timedelta(
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
                "finished_at": naive_now(),
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
                "finished_at": naive_now(),
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
                    "resume_at": naive_now() + timedelta(seconds=delay),
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
                "finished_at": naive_now(),
                "progress": dict(progress_state),
                "result": _carry_result_counters(previous_result, progress_state),
            })
            logger.opt(exception=exc).error("Crawl task {} failed", task_id)
            raise


#: In-flight background AI diagnoses of finished tasks.  Kept in a set so the
#: event loop cannot garbage-collect them mid-flight.
_diagnosis_tasks: set[asyncio.Task] = set()


def _spawn_auto_diagnosis(task_id: str) -> None:
    """Ask the AI to explain a freshly failed task, without blocking the queue.

    ``auto_diagnose_task`` re-checks everything itself (feature enabled, AI
    configured, task really failed, not analysed before) and never raises, so
    this hook cannot break a worker.
    """
    from app.services.ai_diagnosis import auto_diagnose_task

    async def _run() -> None:
        try:
            await auto_diagnose_task(task_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Auto diagnosis for {} skipped: {}", task_id, exc)

    try:
        task = asyncio.create_task(_run())
    except RuntimeError:  # pragma: no cover - no running loop
        logger.debug("Auto diagnosis for {} skipped: no event loop", task_id)
        return
    _diagnosis_tasks.add(task)
    task.add_done_callback(_diagnosis_tasks.discard)


# Connections held back from the task slots below.  A running task keeps one
# connection for its whole life, while the queue loop (``_next_pending_tasks``)
# and the failure writers (``_write_task_row``) need one of their own -- often
# exactly when every task is failing at once.  Reserving them keeps that
# simultaneous case from having to wait for a connection that no task will
# release before the pool times out.
_TASK_CONNECTION_RESERVE = 4


def _task_slot_budget() -> int:
    """How many crawl tasks this process's connection pool can actually serve."""
    return max(1, db_pool_capacity() - _TASK_CONNECTION_RESERVE)


def task_concurrency_limit() -> int:
    """Effective ceiling on concurrently running crawl tasks.

    ``SYNC_WORKER_CONCURRENCY`` is the operator's ceiling and ``0`` means "one
    worker per source".  The pool budget is a *hard* one: every running task
    holds a pooled connection for the entire full-site sync
    (``run_crawl_task_async`` opens its session once and keeps it), so a task
    beyond the pool cannot make progress at all -- it just waits
    ``pool_timeout`` and then fails with "QueuePool limit of size 10 overflow 20
    reached".  Whichever ceiling is lower wins.
    """
    configured = sync_source_concurrency()
    budget = _task_slot_budget()
    return budget if configured <= 0 else min(configured, budget)


#: Row statuses a running task may still legitimately hold.
_RUNNING_STATUSES = ("running", "pending")

#: Why the worker gave up on a coroutine.
_STALLED = "stalled"

_STALL_ERROR = (
    "任务已连续 {minutes} 分钟没有任何进度，为避免它一直占用同步队列，"
    "已被强制结束。请检查代理、网络或书源状态后重试。"
)


class _ActiveTask:
    """One crawl task this worker is running, plus its supervision state."""

    __slots__ = (
        "task_id",
        "source",
        "started_at",
        "stop_seen_at",
        "marker",
        "marker_at",
        "abandoned_at",
        "released",
    )

    def __init__(self, task_id: str, source: str, now: float) -> None:
        self.task_id = task_id
        self.source = source
        self.started_at = now
        #: When the row first said "this must not run any more", so the task
        #: keeps a grace period to stop at its own checkpoint.
        self.stop_seen_at: float | None = None
        #: Last progress heartbeat of this task, and when it last changed.
        self.marker: tuple | None = None
        self.marker_at = now
        #: When the supervisor cancelled this coroutine.
        self.abandoned_at: float | None = None
        #: True once its queue slot was handed back without the coroutine
        #: ending, so its own cleanup no longer owns the source claim.
        self.released = False


def _progress_marker(progress: Any) -> tuple:
    """Cheap heartbeat read out of a task's ``progress`` blob.

    The sync writes one of these fields at every checkpoint, so an unchanged
    marker over a long period is the worker's only evidence that a coroutine
    parked on a dead connection is not coming back.

    Image progress belongs here because an album chapter is downloaded one image
    at a time: without it a task that is fetching its 400th image looks exactly
    like one that stopped an hour ago.
    """
    if not isinstance(progress, dict):
        progress = {}
    return (
        progress.get("pages_checked"),
        progress.get("books_found"),
        progress.get("books_synced"),
        progress.get("current_book"),
        progress.get("current_chapter"),
        progress.get("current_chapters_created"),
        progress.get("current_images_done"),
        progress.get("current_images_total"),
    )


async def _read_active_task_states(task_ids: list[str]) -> dict[str, dict[str, Any]]:
    """``status``/``progress`` of the tasks this worker is running right now.

    One query for the whole batch, so supervising a full queue costs a single
    round trip per interval.  Pause, cancel and delete are written by the API in
    another process, so the row is the only place the worker can learn them.
    """
    if not task_ids:
        return {}
    async with SessionLocal() as db:
        rows = await db.execute(
            select(CrawlTask.id, CrawlTask.status, CrawlTask.progress).where(
                CrawlTask.id.in_(task_ids)
            )
        )
        return {
            row[0]: {"status": row[1], "progress": row[2]}
            for row in rows.all()
        }


def _abandoned_tasks(
    active: dict[asyncio.Task, _ActiveTask],
    states: dict[str, dict[str, Any]],
    *,
    now: float,
    stop_grace: float,
    stall_seconds: float,
) -> list[tuple[asyncio.Task, _ActiveTask, str]]:
    """Which running coroutines to give up on, and why.

    A task can hold a slot it no longer deserves in two ways:

    * its row says it must not run any more (paused / cancelled / already
      finished / deleted) but its coroutine never reached a checkpoint to
      notice.  After ``stop_grace`` the slot, the source and the pooled
      connection are taken back -- this is what used to wedge the whole queue:
      every slot was held by a task the user had already paused, so newly
      created tasks could never start;
    * it is still ``running`` but has reported no progress at all for
      ``stall_seconds``, the only way a coroutine parked forever on a dead
      proxy or browser driver is ever reclaimed.

    Pure, so the decision can be tested without a database or an event loop.
    """
    abandoned: list[tuple[asyncio.Task, _ActiveTask, str]] = []
    for task, entry in list(active.items()):
        state = states.get(entry.task_id) or {}
        status = state.get("status")
        if status in _RUNNING_STATUSES:
            entry.stop_seen_at = None
            if status == "pending":
                # Batch mode re-queues a task with ``resume_at`` set and returns,
                # so the row is briefly ``pending`` again while the coroutine is
                # still finishing.  A task that has not committed ``running``
                # yet looks the same.  Nothing to reclaim either way.
                continue
            marker = _progress_marker(state.get("progress"))
            if marker != entry.marker:
                entry.marker = marker
                entry.marker_at = now
            elif stall_seconds > 0 and now - entry.marker_at >= stall_seconds:
                abandoned.append((task, entry, _STALLED))
            continue
        entry.stop_seen_at = (
            now if entry.stop_seen_at is None else entry.stop_seen_at
        )
        if now - entry.stop_seen_at >= stop_grace:
            abandoned.append((task, entry, status or "deleted"))
    return abandoned


async def _fail_stalled_task(task_id: str, stalled_seconds: float) -> None:
    """Persist ``failed`` for a task the worker walked away from as hung.

    Guarded by ``status == 'running'``, so a task that paused, was cancelled or
    finished on its own between the decision and this write is never
    overwritten.
    """
    try:
        async with SessionLocal() as db:
            await db.execute(
                update(CrawlTask)
                .where(CrawlTask.id == task_id, CrawlTask.status == "running")
                .values(
                    status="failed",
                    error=_STALL_ERROR.format(
                        minutes=max(1, int(stalled_seconds // 60))
                    ),
                    finished_at=naive_now(),
                )
            )
            await db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not fail stalled crawl task {}: {}", task_id, exc)


async def _supervise_active(
    active: dict[asyncio.Task, _ActiveTask],
    *,
    now: float | None = None,
) -> None:
    """Cancel the coroutines of tasks that must not be running any more.

    Never raises: a supervisor that dies takes the whole queue with it.
    ``now`` is passed in by the loop so that the grace periods measured here and
    by ``_release_abandoned`` share one clock reading.
    """
    if not active:
        return
    if now is None:
        now = time.monotonic()
    stall_seconds = task_stall_seconds()
    try:
        states = await _read_active_task_states(
            [entry.task_id for entry in active.values()]
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Crawl task supervision skipped: {}", exc)
        return

    for task, entry, reason in _abandoned_tasks(
        active,
        states,
        now=now,
        stop_grace=task_stop_grace_seconds(),
        stall_seconds=stall_seconds,
    ):
        entry.abandoned_at = now
        # Cancel before writing, so the task stops doing work as soon as
        # possible; the write below only has to beat a cooperating coroutine.
        task.cancel()
        if reason == _STALLED:
            logger.warning(
                "Crawl task {} reported no progress for {}s; failing it and "
                "freeing its queue slot",
                entry.task_id,
                int(stall_seconds),
            )
            await _fail_stalled_task(entry.task_id, stall_seconds)
        else:
            logger.info(
                "Crawl task {} is {} but still running; cancelling its worker",
                entry.task_id,
                reason,
            )


def _release_abandoned(
    active: dict[asyncio.Task, _ActiveTask],
    running_sources: set[str],
    claimed: set[str],
    *,
    now: float,
    grace: float,
) -> list[str]:
    """Hand back the slots of coroutines that ignored their cancellation.

    A cancelled coroutine ends at its next ``await``.  One that does not -- a
    stuck browser driver, a long synchronous call inside a thread -- would
    otherwise keep its slot, its source and its connection forever, which is
    the very deadlock the supervision exists to prevent.  Its own cleanup no
    longer owns the source claim (``released``), so a fresh task for that source
    cannot be cancelled out by the zombie waking up later.
    """
    released: list[str] = []
    for task, entry in list(active.items()):
        if entry.abandoned_at is None or now - entry.abandoned_at < grace:
            continue
        active.pop(task)
        entry.released = True
        claimed.discard(entry.task_id)
        running_sources.discard(entry.source)
        released.append(entry.task_id)
        logger.error(
            "Crawl task {} ignored its cancellation for {}s; releasing its "
            "queue slot anyway",
            entry.task_id,
            int(now - entry.abandoned_at),
        )
    return released


#: How long the loop waits after an unexpected error before polling again.  The
#: queue is database-backed, so the error that matters in practice is the
#: database going away for a few seconds; a short fixed delay rides that out
#: without hammering a database that is still coming back.
_LOOP_ERROR_BACKOFF_SECONDS = 5.0


async def _worker_loop() -> None:
    """Run every book source in its own worker, in parallel.

    The previous loop used one global pool (``SYNC_WORKER_CONCURRENCY``,
    default 3), so a fourth source waited behind three multi-hour full-site
    tasks.  Legado behaves differently: each source carries its own
    ``concurrentRate`` limiter, so sources sync independently and the *site*
    still sees only the request rate it declared.

    ``SYNC_WORKER_CONCURRENCY <= 0`` (the default) therefore means "one worker
    per source", bounded by the connection budget rather than by a fixed batch
    size -- see ``task_concurrency_limit``.  A positive value keeps a global
    ceiling for small hosts, still bounded by that same budget.  A single source
    never runs two tasks at once.

    Every running task is supervised (see ``_supervise_active``): a task the row
    says must not run any more -- paused, cancelled, deleted -- is cancelled
    once its grace period is over, and so is one that has stopped reporting
    progress.  Without that, a slot could be held forever by a task whose sync
    never reached a checkpoint, and once every slot was held that way the queue
    stopped consuming new tasks for good (seen online on 2026-09-20: nine
    freshly created tasks sat ``pending`` for 12h while the worker stayed idle).

    The loop itself has to outlive its own mistakes as well: online on
    2026-09-22 the database restarted, the very next poll raised out of this
    coroutine, and -- because ``asyncio.run`` then hung in its own teardown --
    the container stayed alive with a queue that consumed nothing for as long as
    nobody restarted it.  Every iteration therefore logs and backs off instead
    of ending the loop, and reports its liveness through ``app.core.heartbeat``
    so the container supervisor can restart a worker that stops ticking.
    """
    limit = task_concurrency_limit()
    supervise_interval = task_supervise_interval_seconds()
    stop_grace = task_stop_grace_seconds()
    claimed: set[str] = set()
    active: dict[asyncio.Task, _ActiveTask] = {}
    running_sources: set[str] = set()
    next_supervise_at = 0.0

    async def _run_guarded(entry: _ActiveTask) -> None:
        try:
            await run_crawl_task_async(entry.task_id)
        except Exception as exc:
            logger.error("Crawl task {} stopped: {}", entry.task_id, exc)
        finally:
            if not entry.released:
                running_sources.discard(entry.source)
            # Explain what just failed, if the feature is on.  Fire-and-forget:
            # a slow model call must never hold up the queue for this source.
            _spawn_auto_diagnosis(entry.task_id)

    def _free_slots() -> int:
        # Bounded batch: enough to fill every free slot without loading an
        # unbounded pending backlog into memory.  Never more than the pool can
        # serve, so the loop can only start tasks that can get a connection.
        return max(1, limit - len(active))

    while True:
        try:
            # Liveness signal for the container's supervisor (see
            # ``app.core.heartbeat``).  This loop iterates at least every two
            # seconds, so a signal that stops moving is what tells a wedged
            # worker -- alive, holding the queue, consuming nothing -- apart from
            # a busy one.
            mark_alive()

            while len(active) < limit:
                candidates = await _next_pending_tasks(_free_slots(), running_sources)
                if not candidates:
                    break
                started_any = False
                for task_id, source in candidates:
                    if len(active) >= limit:
                        break
                    if task_id in claimed or source in running_sources:
                        continue
                    claimed.add(task_id)
                    running_sources.add(source)
                    entry = _ActiveTask(task_id, source, time.monotonic())
                    task = asyncio.create_task(_run_guarded(entry))
                    active[task] = entry
                    started_any = True
                if not started_any:
                    # All candidates are already claimed but not yet running.
                    break

            if not active:
                await asyncio.sleep(2)
                continue

            now = time.monotonic()
            if now >= next_supervise_at:
                next_supervise_at = now + supervise_interval
                await _supervise_active(active, now=now)
                _release_abandoned(
                    active, running_sources, claimed, now=now, grace=stop_grace
                )
                if not active:
                    # Supervision just handed back the last slot, so there is
                    # nothing left to wait on (``asyncio.wait`` refuses an empty
                    # set).
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
                entry = active.pop(finished)
                claimed.discard(entry.task_id)
                running_sources.discard(entry.source)
                if finished.cancelled():
                    logger.warning("Crawl task {} cancelled", entry.task_id)
                    continue
                exc = finished.exception()
                if exc is not None:
                    logger.error("Crawl task {} stopped: {}", entry.task_id, exc)
            await asyncio.sleep(0.2)
        except Exception as exc:
            # The queue has to outlive its own mistakes.  Online on 2026-09-22
            # the database restarted under a running worker: the poll that
            # followed raised out of this coroutine, and it never ran again --
            # every task created afterwards sat ``pending`` until the container
            # was restarted by hand.  One logged retry turns that permanent
            # outage into a hiccup.  ``CancelledError`` is a ``BaseException``,
            # so a real shutdown still stops the loop here.
            logger.error("Crawl queue loop error; retrying: {}", exc)
            await asyncio.sleep(_LOOP_ERROR_BACKOFF_SECONDS)


async def main_async() -> None:
    await _reset_stale_running_tasks()
    await _worker_loop()


def main() -> None:
    # The queue worker used to run with loguru's default handler only, so every
    # standard-library log line from the plugin layer (the "why did this source
    # return 0 books" diagnostics) was printed as bare text without a level.
    from app.core.logging import install_stdlib_logging_bridge

    install_stdlib_logging_bridge()
    asyncio.run(main_async())
