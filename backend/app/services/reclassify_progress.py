"""Progress plumbing for the novel/comic re-classification.

Same shape as the search-index rebuild: the state lives in a JSON file inside
the storage volume rather than in a module global, because the endpoint that
answers the progress poll may be served by a *different* uvicorn worker (the
NAS runs ``--workers 2``) and a global would answer "not running" half the time.

Re-classification reads several chapters of every book, so on a 46 000-book
library it is minutes of work -- long enough to outlive a gateway timeout and
long enough that "did it finish?" is a real question.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from app.core.config import settings
from app.core.clock import naive_now

STATE_FILE = ".reclassify-progress.json"
#: A run publishes at most this often (phase changes ignore the throttle).
PUBLISH_THROTTLE_SECONDS = 0.5
#: A "running" state whose heartbeat is older than this means the process died.
STALE_SECONDS = 600


def state_path() -> Path:
    return Path(settings.STORAGE_PATH) / STATE_FILE


def empty_progress() -> dict[str, Any]:
    return {
        "running": False,
        "phase": "idle",
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
        "books_total": 0,
        "books_done": 0,
        "updated": 0,
        "scanned": 0,
        "percent": 0.0,
        "message": "",
        "error": None,
        "result": None,
        "cancel_requested": False,
    }


def read_progress() -> dict[str, Any]:
    """The last published state (never raises)."""
    progress = empty_progress()
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:
        return progress
    if not isinstance(raw, dict):
        return progress
    for key in progress:
        if key in raw:
            progress[key] = raw[key]
    if progress.get("running"):
        try:
            heartbeat = float(raw.get("heartbeat") or 0)
        except (TypeError, ValueError):
            heartbeat = 0.0
        if time.time() - heartbeat > STALE_SECONDS:
            progress["running"] = False
            progress["phase"] = "stale"
            progress["error"] = "上一次识别没有正常结束（进程/容器重启过）。可以直接重新点「重新识别」。"
    return progress


def write_progress(update: dict[str, Any]) -> dict[str, Any]:
    """Merge ``update`` into the stored state and publish it atomically."""
    current = read_progress()
    current.update({k: v for k, v in update.items() if k != "heartbeat"})
    current["heartbeat"] = time.time()
    current["updated_at"] = naive_now().isoformat(timespec="seconds")
    total = int(current.get("books_total") or 0)
    done = int(current.get("books_done") or 0)
    current["percent"] = (
        round(min(100.0, 100.0 * done / total), 1) if total else 0.0
    )

    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # progress must never break the run
        logger.debug("Could not write reclassify progress: {}", exc)
    return current


def progress_callback() -> Callable[[dict[str, Any]], None]:
    """A throttled writer for ``reclassify_books(progress=...)``."""
    last_write = {"at": 0.0}

    def publish(update: dict[str, Any]) -> None:
        forced = bool(update.pop("force", False))
        now = time.time()
        if not forced and now - last_write["at"] < PUBLISH_THROTTLE_SECONDS:
            return
        last_write["at"] = now
        write_progress(update)

    return publish


def should_stop() -> bool:
    return bool(read_progress().get("cancel_requested"))


#: Strong references to in-flight runs: ``asyncio`` keeps only weak ones.
_TASKS: set[asyncio.Task] = set()


def start(*, scan_content: bool, source_id: str | None, force: bool) -> dict[str, Any]:
    """Kick off a background run and return the initial progress.

    Returns the progress of the *already running* run when one is in flight, so
    two clicks cannot start two runs over the same books.
    """
    current = read_progress()
    if current.get("running"):
        return current

    write_progress({
        "running": True,
        "phase": "starting",
        "started_at": naive_now().isoformat(timespec="seconds"),
        "finished_at": None,
        "books_done": 0,
        "books_total": 0,
        "updated": 0,
        "scanned": 0,
        "percent": 0.0,
        "error": None,
        "result": None,
        "cancel_requested": False,
        "message": "已开始，正在统计书目…",
    })
    task = asyncio.create_task(_run(scan_content, source_id, force))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return read_progress()


def cancel() -> dict[str, Any]:
    """Ask the running re-classification to stop at its next chunk boundary."""
    current = read_progress()
    if not current.get("running"):
        return current
    return write_progress({
        "cancel_requested": True,
        "message": "已请求取消，等当前批次写完就停…",
    })


async def run_reclassify(
    db,
    *,
    scan_content: bool,
    source_id: str | None,
    force: bool,
    progress=None,
    should_stop=None,
) -> dict[str, Any]:
    """Reclassify, then refresh just the search documents whose kind moved.

    Shared by the blocking endpoint and the background runner so both report
    the same numbers.
    """
    from app.services.book_kind import reclassify_books
    from app.services.search import search_service

    result = await reclassify_books(
        db,
        scan_content=scan_content,
        source_id=source_id,
        force=force,
        progress=progress,
        should_stop=should_stop,
    )
    changed = result.pop("changed_book_ids", [])
    try:
        # Only the books whose kind actually moved need touching in the index,
        # otherwise a re-classified comic keeps showing up in novel results.
        result["index"] = await search_service.sync_book_kinds(book_ids=changed)
    except Exception as exc:
        # The DB side already succeeded; report the index problem instead of
        # failing the whole request.
        result["index"] = {"error": str(exc)}
    return result


async def _run(scan_content: bool, source_id: str | None, force: bool) -> None:
    """Run to completion in the background, publishing progress and failures."""
    from app.core.database import SessionLocal

    publish = progress_callback()
    try:
        async with SessionLocal() as db:
            result = await run_reclassify(
                db,
                scan_content=scan_content,
                source_id=source_id,
                force=force,
                progress=publish,
                should_stop=should_stop,
            )
    except Exception as exc:
        logger.exception("Book kind re-classification failed")
        write_progress({
            "running": False,
            "phase": "failed",
            "finished_at": naive_now().isoformat(timespec="seconds"),
            "error": str(exc) or type(exc).__name__,
            "message": f"识别失败：{exc}",
            "force": True,
        })
        return

    if result.get("cancelled"):
        write_progress({
            "running": False,
            "phase": "cancelled",
            "finished_at": naive_now().isoformat(timespec="seconds"),
            "updated": result.get("updated", 0),
            "message": f"识别已取消（已改的 {result.get('updated', 0)} 本保留）",
            "force": True,
        })
        return

    write_progress({
        "running": False,
        "phase": "done",
        "finished_at": naive_now().isoformat(timespec="seconds"),
        "books_done": result.get("books", 0),
        "updated": result.get("updated", 0),
        "scanned": result.get("scanned", 0),
        "percent": 100.0,
        "result": result,
        "message": (
            f"完成：共 {result.get('books', 0)} 本，"
            f"小说 {result.get('novels', 0)} 本 / 漫画 {result.get('comics', 0)} 本，"
            f"本次修正 {result.get('updated', 0)} 本"
        ),
        "force": True,
    })
    logger.info(
        "Book kind re-classification finished: {} books, {} changed",
        result.get("books", 0),
        result.get("updated", 0),
    )
