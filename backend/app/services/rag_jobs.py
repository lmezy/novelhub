"""Background RAG indexing: the admin batch run and the reader's auto queue.

Indexing a book means reading every chapter file, calling the embedding API in
batches of 16 and writing one JSONB row per chunk.  A single 1 000-chapter book
is minutes of work; the whole library would be hundreds of thousands of API
calls and tens of gigabytes, so *nothing here is unbounded*:

* a batch run is capped at :data:`MAX_BATCH_BOOKS` books and
  :data:`MAX_CHUNKS_PER_BOOK` chunks per book (the per-book API cap);
* "打开即建" puts at most one book at a time into the queue, at most
  :data:`AUTO_DAILY_LIMIT` books a day, and never indexes a book that already
  has vectors, a comic, or a book with no readable chapters;
* a run stops after :data:`MAX_CONSECUTIVE_FAILURES` failures in a row (a bad
  key or a rate limit is not going to heal by itself).

The job state lives in a JSON file inside the storage volume, like the index
rebuild's: the progress endpoint may be served by the *other* uvicorn worker,
so a module global would answer "not running" half the time.  A lock file makes
sure only one worker actually runs the queue.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from loguru import logger
from sqlalchemy import func, select

from app.core.clock import naive_now
from app.core.config import settings

STATE_FILE = ".rag-jobs.json"
LOCK_FILE = ".rag-jobs.lock"

#: Hard ceilings.  The embedding API bills per token and every chunk becomes a
#: JSONB row (a 1 024-dim vector as JSON is ~20 KB), so these are limits rather
#: than defaults: a whole-library run is not a supported operation.
MAX_BATCH_BOOKS = 200
MAX_CHUNKS_PER_BOOK = 2000
DEFAULT_BATCH_CHUNKS = 400
DEFAULT_AUTO_CHUNKS = 300
#: The reader-triggered queue never grows past this many books.
MAX_AUTO_QUEUE = 20
#: Books "打开即建" may index in one calendar day.
AUTO_DAILY_LIMIT = 20
#: Stop the run after this many failures in a row (rate limit, bad key, ...).
MAX_CONSECUTIVE_FAILURES = 3
#: A lock whose owner died is ignored after this long.
LOCK_STALE_SECONDS = 3600
#: Rough on-disk cost of one stored chunk, used for the UI estimate.
CHUNK_BYTES_ESTIMATE = 20 * 1024
#: Ids kept in ``recent_errors`` so the admin can see what went wrong.
RECENT_ERROR_LIMIT = 5

_TASKS: set[asyncio.Task] = set()


# -- state -------------------------------------------------------------------


def state_path() -> Path:
    return Path(settings.STORAGE_PATH) / STATE_FILE


def _lock_path() -> Path:
    return Path(settings.STORAGE_PATH) / LOCK_FILE


def empty_state() -> dict[str, Any]:
    return {
        "running": False,
        "phase": "idle",
        "queue": [],
        "current": None,
        "total": 0,
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "chunks": 0,
        "max_chunks": DEFAULT_BATCH_CHUNKS,
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
        "message": "",
        "error": None,
        "cancel_requested": False,
        "auto_date": "",
        "auto_count": 0,
        "recent_errors": [],
    }


def read_state() -> dict[str, Any]:
    """The last published job state (never raises)."""
    state = empty_state()
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:
        return state
    if not isinstance(raw, dict):
        return state
    for key in state:
        if key in raw:
            state[key] = raw[key]
    if not isinstance(state.get("queue"), list):
        state["queue"] = []
    if not isinstance(state.get("recent_errors"), list):
        state["recent_errors"] = []
    return state


def _write(state: Mapping[str, Any]) -> dict[str, Any]:
    payload = {**empty_state(), **dict(state)}
    payload["updated_at"] = naive_now().isoformat(timespec="seconds")
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # progress must never break an index run
        logger.debug("Could not write RAG job state: {}", exc)
    return payload


# -- worker lock -------------------------------------------------------------


def _lock_owner_alive(path: Path) -> bool:
    """Whether the pid stored in the lock file still exists.

    The lock lives on a volume shared with the other uvicorn worker; both are
    pids in the same container, so ``os.kill(pid, 0)`` answers both "another
    worker is running it" and "the container restarted, clear this".  Falling
    back to the age check keeps a lock written by something we cannot inspect
    from blocking forever.
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
        pid = int(raw.split()[0]) if raw else 0
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _acquire_lock() -> bool:
    """Take the single-runner lock, or report that another worker owns it."""
    path = _lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                age = 0.0
            if age < LOCK_STALE_SECONDS and _lock_owner_alive(path):
                return False
            try:  # the previous worker died without cleaning up
                path.unlink()
            except OSError:
                return False
            continue
        except OSError:
            return False
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        return True
    return False


def _release_lock() -> None:
    try:
        _lock_path().unlink()
    except OSError:
        pass


# -- queue -------------------------------------------------------------------


def _append(items: Iterable[dict[str, Any]]) -> int:
    """Add books to the queue, skipping duplicates of queued/running ones."""
    state = read_state()
    queue = list(state.get("queue") or [])
    seen = {str(entry.get("book_id")) for entry in queue}
    current = state.get("current") or {}
    if current.get("book_id"):
        seen.add(str(current["book_id"]))
    added = 0
    for item in items:
        book_id = str(item.get("book_id") or "").strip()
        if not book_id or book_id in seen:
            continue
        seen.add(book_id)
        queue.append({
            "book_id": book_id,
            "title": str(item.get("title") or ""),
            "source": str(item.get("source") or "batch"),
            "max_chunks": int(item.get("max_chunks") or DEFAULT_BATCH_CHUNKS),
        })
        added += 1
    state["queue"] = queue
    if added:
        state["total"] = int(state.get("total") or 0) + added
    _write(state)
    return added


def ensure_worker() -> bool:
    """Start the queue runner in this process unless another one owns it."""
    if not _acquire_lock():
        return False
    try:
        state = read_state()
        if state.get("running"):
            # We hold the lock, so the "running" flag is a leftover from a dead
            # worker; reset it and continue with whatever is still queued.
            state["running"] = False
        state.update({
            "running": True,
            "phase": "running",
            "cancel_requested": False,
            "finished_at": None,
            "error": None,
        })
        if not state.get("started_at"):
            state["started_at"] = naive_now().isoformat(timespec="seconds")
        state["message"] = f"队列中还有 {len(state.get('queue') or [])} 本"
        _write(state)
        task = asyncio.get_running_loop().create_task(_run())
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return True
    except RuntimeError:  # no running loop (tests, sync callers)
        _release_lock()
        return False


def cancel() -> dict[str, Any]:
    state = read_state()
    if not state.get("running"):
        return state
    state["cancel_requested"] = True
    state["message"] = "已请求取消，当前这本写完后停止…"
    return _write(state)


def status() -> dict[str, Any]:
    state = read_state()
    state["queued"] = len(state.get("queue") or [])
    state["limits"] = {
        "max_batch_books": MAX_BATCH_BOOKS,
        "max_chunks_per_book": MAX_CHUNKS_PER_BOOK,
        "default_batch_chunks": DEFAULT_BATCH_CHUNKS,
        "default_auto_chunks": DEFAULT_AUTO_CHUNKS,
        "auto_daily_limit": AUTO_DAILY_LIMIT,
        "max_auto_queue": MAX_AUTO_QUEUE,
        "chunk_bytes_estimate": CHUNK_BYTES_ESTIMATE,
    }
    return state


# -- public entry points -----------------------------------------------------


async def request_auto(db: Any, book: Any) -> dict[str, Any]:
    """Ask for one book to be indexed in the background ("打开即建").

    Never raises into the reader's request path: every refusal is a reason the
    UI can show, and the reader's chat still works in window mode.
    """
    from app.models import Chapter, ChapterEmbedding
    from app.services.ai_config import get_ai_config
    from app.services.book_kind import KIND_COMIC, normalize_kind

    try:
        cfg = await get_ai_config(db)
        if not cfg.rag_auto_index:
            return {"queued": False, "reason": "未开启「打开书籍时自动建索引」"}
        if not cfg.embeddings_supported:
            return {"queued": False, "reason": "向量服务未配置（设置 → AI → 语义检索）"}
        if not cfg.rag_enabled:
            return {"queued": False, "reason": "「问答时使用语义检索」未开启，建了也不会用"}
        if normalize_kind(getattr(book, "kind", None)) == KIND_COMIC:
            return {"queued": False, "reason": "这是漫画，不需要语义索引"}
        chunks = int(await db.scalar(
            select(func.count()).select_from(ChapterEmbedding)
            .where(ChapterEmbedding.book_id == book.id)
        ) or 0)
        if chunks:
            return {"queued": False, "reason": "这本书已经有向量索引"}
        chapters = int(await db.scalar(
            select(func.count()).select_from(Chapter)
            .where(Chapter.book_id == book.id)
        ) or 0)
        if not chapters:
            return {"queued": False, "reason": "这本书还没有可读章节"}

        state = read_state()
        today = date.today().isoformat()
        if state.get("auto_date") != today:
            state["auto_date"] = today
            state["auto_count"] = 0
        if int(state.get("auto_count") or 0) >= AUTO_DAILY_LIMIT:
            return {
                "queued": False,
                "reason": f"今天自动建索引已达上限（{AUTO_DAILY_LIMIT} 本），明天再自动继续",
            }
        if len(state.get("queue") or []) >= MAX_AUTO_QUEUE:
            return {"queued": False, "reason": "建索引队列已满，稍后再打开这本书"}

        added = _append([{
            "book_id": book.id,
            "title": getattr(book, "title", "") or "",
            "source": "auto",
            "max_chunks": DEFAULT_AUTO_CHUNKS,
        }])
        if not added:
            return {"queued": False, "reason": "已经在建索引队列里"}
        # Count the book only once it is really queued: a repeated open must not
        # eat a slot of the daily budget.
        state = read_state()
        if state.get("auto_date") != today:
            state["auto_date"] = today
            state["auto_count"] = 0
        state["auto_count"] = int(state.get("auto_count") or 0) + 1
        _write(state)
        started = ensure_worker()
        return {"queued": True, "started": started,
                "position": len(read_state().get("queue") or [])}
    except Exception as exc:  # noqa: BLE001 - auto indexing is best effort
        logger.warning("RAG auto index request failed for {}: {}", getattr(book, "id", "?"), exc)
        return {"queued": False, "reason": f"无法排队：{exc}"}


def start_batch(book_ids: Iterable[str], *, max_chunks: int = DEFAULT_BATCH_CHUNKS,
                titles: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Queue an explicit list of books; the caller has already applied limits."""
    ids = [str(book_id) for book_id in book_ids if str(book_id or "").strip()]
    deduped = list(dict.fromkeys(ids))
    if len(deduped) > MAX_BATCH_BOOKS:
        raise ValueError(f"一次最多 {MAX_BATCH_BOOKS} 本，收到了 {len(deduped)} 本")
    chunks = max(1, min(int(max_chunks or DEFAULT_BATCH_CHUNKS), MAX_CHUNKS_PER_BOOK))
    added = _append([
        {"book_id": book_id, "title": (titles or {}).get(book_id, ""),
         "source": "batch", "max_chunks": chunks}
        for book_id in deduped
    ])
    started = ensure_worker()
    return {
        "queued": added,
        "duplicates": len(deduped) - added,
        "started": started,
        "max_chunks": chunks,
        "state": status(),
    }


# -- the worker --------------------------------------------------------------


async def _index_one(item: Mapping[str, Any]) -> dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services.rag import RAGService

    async with SessionLocal() as db:
        service = RAGService(db)
        return await service.index_book(
            str(item["book_id"]),
            max_chunks=int(item.get("max_chunks") or DEFAULT_BATCH_CHUNKS),
        )


async def _run() -> None:
    """Drain the queue, one book at a time."""
    consecutive_failures = 0
    cancelled = False
    error: str | None = None
    try:
        while True:
            state = read_state()
            if state.get("cancel_requested"):
                cancelled = True
                break
            queue = list(state.get("queue") or [])
            if not queue:
                # A request may have been appended between the read and the exit;
                # one short pause closes that window without a busy loop.
                await asyncio.sleep(0.5)
                if not read_state().get("queue"):
                    break
                continue

            item = queue.pop(0)
            state["queue"] = queue
            state["current"] = {
                "book_id": item["book_id"],
                "title": item.get("title") or item["book_id"],
            }
            state["phase"] = "indexing"
            state["message"] = f"正在索引：{state['current']['title']}"
            state["max_chunks"] = int(item.get("max_chunks") or DEFAULT_BATCH_CHUNKS)
            _write(state)

            try:
                result = await _index_one(item)
            except Exception as exc:  # noqa: BLE001 - one bad book must not stop the queue
                consecutive_failures += 1
                state = read_state()
                state["failed"] = int(state.get("failed") or 0) + 1
                recent = list(state.get("recent_errors") or [])
                recent.append({
                    "book_id": item["book_id"],
                    "title": state["current"]["title"],
                    "error": str(exc)[:300],
                })
                state["recent_errors"] = recent[-RECENT_ERROR_LIMIT:]
                state["current"] = None
                _write(state)
                logger.warning("RAG indexing failed for {}: {}", item["book_id"], exc)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    error = (
                        f"连续 {consecutive_failures} 本失败，已停止：{str(exc)[:200]}"
                    )
                    break
                continue

            consecutive_failures = 0
            state = read_state()
            if result.get("skipped"):
                state["skipped"] = int(state.get("skipped") or 0) + 1
            else:
                state["done"] = int(state.get("done") or 0) + 1
                state["chunks"] = int(state.get("chunks") or 0) + int(result.get("chunks") or 0)
            state["current"] = None
            state["message"] = (
                f"已完成 {int(state.get('done') or 0)} 本，"
                f"{int(state.get('chunks') or 0)} 个片段"
            )
            _write(state)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("RAG job runner crashed")
        error = str(exc) or type(exc).__name__
    finally:
        state = read_state()
        state["running"] = False
        state["current"] = None
        state["finished_at"] = naive_now().isoformat(timespec="seconds")
        state["cancel_requested"] = False
        if error:
            state["phase"] = "failed"
            state["error"] = error
            state["message"] = "批量建索引已停止"
        elif cancelled:
            state["phase"] = "cancelled"
            state["message"] = "已取消（已写入的向量保留）"
        else:
            state["phase"] = "done"
            state["message"] = (
                f"完成：新索引 {int(state.get('done') or 0)} 本、跳过 "
                f"{int(state.get('skipped') or 0)} 本、失败 {int(state.get('failed') or 0)} 本"
            )
        state["queue"] = []
        _write(state)
        _release_lock()


__all__ = [
    "AUTO_DAILY_LIMIT",
    "DEFAULT_AUTO_CHUNKS",
    "DEFAULT_BATCH_CHUNKS",
    "MAX_BATCH_BOOKS",
    "MAX_CHUNKS_PER_BOOK",
    "cancel",
    "ensure_worker",
    "read_state",
    "request_auto",
    "start_batch",
    "status",
]
