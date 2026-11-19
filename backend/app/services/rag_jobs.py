"""Background RAG indexing: the admin batch run and the reader's auto queue.

Indexing a book means reading every chapter file, calling the embedding API in
batches of 16 and writing one JSONB row per chunk.  A single 1 000-chapter book
is minutes of work; the whole library can take hundreds of thousands of API
calls and tens of gigabytes:

* an administrator can set the number of books and chunks or select all;
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
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from loguru import logger
from sqlalchemy import func, select

from app.core.clock import naive_now
from app.core.config import settings

STATE_FILE = ".rag-jobs.json"
LOCK_FILE = ".rag-jobs.lock"

DEFAULT_BATCH_CHUNKS = 400
DEFAULT_AUTO_CHUNKS = 300
#: The reader-triggered queue never grows past this many books.
MAX_AUTO_QUEUE = 20
#: Books "打开即建" may index in one calendar day.
AUTO_DAILY_LIMIT = 20
#: Stop the run after this many failures in a row (rate limit, bad key, ...).
MAX_CONSECUTIVE_FAILURES = 3
#: Fall back to the file's age when its owner cannot be determined.
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
        "discarded": 0,
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


def _lock_predates_process(path: Path, pid: int) -> bool:
    """Detect a stale lock whose PID has been reused on Linux."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        start_ticks = int(stat.rpartition(")")[2].split()[19])
        uptime = time.clock_gettime(time.CLOCK_BOOTTIME)
        process_started_at = time.time() - uptime + start_ticks / os.sysconf("SC_CLK_TCK")
        return process_started_at > path.stat().st_mtime + 1
    except (OSError, IndexError, ValueError, AttributeError):
        return False


def _lock_owner_alive(path: Path) -> bool | None:
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
    except (OSError, ValueError):
        return None
    if pid <= 0:
        return None
    if pid == os.getpid():
        return not _lock_predates_process(path, pid)
    if os.name == "nt":  # Windows os.kill(pid, 0) terminates the process.
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return None
    return not _lock_predates_process(path, pid)


def _file_lock_active(path: Path) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    except OSError:
        age = 0.0
    owner_alive = _lock_owner_alive(path)
    return owner_alive is True or (owner_alive is None and age < LOCK_STALE_SECONDS)


def acquire_file_lock(path: Path) -> bool:
    """Take a single-runner lock file, or report that another process owns it."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _file_lock_active(path):
                return False
            try:  # the previous worker died without cleaning up
                path.unlink()
            except FileNotFoundError:
                pass
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


def release_file_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _acquire_lock() -> bool:
    return acquire_file_lock(_lock_path())


def _release_lock() -> None:
    release_file_lock(_lock_path())


@contextmanager
def _state_lock():
    """Serialize short queue updates across web workers."""
    path = state_path().with_suffix(".state.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        yield


# -- queue -------------------------------------------------------------------


TERMINAL_PHASES = {"done", "failed", "cancelled", "interrupted"}


def _processed_count(state: Mapping[str, Any]) -> int:
    return state["done"] + state["skipped"] + state["failed"]


def _pending_count(state: Mapping[str, Any]) -> int:
    return len(state["queue"]) + int(bool(state["current"]))


def _clear_pending(state: dict[str, Any]) -> None:
    state["discarded"] = max(state["discarded"], _pending_count(state))
    state.update(total=_processed_count(state), queue=[], current=None)


def _recover_state(state: dict[str, Any]) -> dict[str, Any]:
    """Normalize older terminal state, or a run whose worker stopped."""
    if state["running"]:
        if _file_lock_active(_lock_path()):
            state["total"] = _processed_count(state) + _pending_count(state)
        else:
            _finish_state(state, "interrupted")
    elif state["phase"] in TERMINAL_PHASES:
        _clear_pending(state)
    return state


def _enqueue_state() -> dict[str, Any]:
    state = _recover_state(read_state())
    if state["phase"] in TERMINAL_PHASES:
        return {**empty_state(), "auto_date": state["auto_date"], "auto_count": state["auto_count"]}
    return state


def _append(state: dict[str, Any], items: Iterable[dict[str, Any]]) -> int:
    """Add books to the queue, skipping duplicates of queued/running ones."""
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
            "max_chunks": int(item.get("max_chunks", DEFAULT_BATCH_CHUNKS)),
        })
        added += 1
    state["queue"] = queue
    if added:
        state["total"] = int(state.get("total") or 0) + added
    return added


def ensure_worker() -> bool:
    """Start the queue runner in this process unless another one owns it."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    with _state_lock():
        state = _recover_state(read_state())
        if not state["queue"] or not _acquire_lock():
            return False
        state.update(
            running=True,
            phase="running",
            started_at=naive_now().isoformat(timespec="seconds"),
            message=f"队列中还有 {len(state['queue'])} 本",
        )
        _write(state)
        task = loop.create_task(_run())
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return True


def cancel() -> dict[str, Any]:
    with _state_lock():
        state = _recover_state(read_state())
        if not state["running"]:
            return state
        state["cancel_requested"] = True
        state["message"] = "已请求取消，当前这本写完后停止…"
        return _write(state)


def status(*, include_queue: bool = True) -> dict[str, Any]:
    with _state_lock():
        state = _recover_state(read_state())
    state["queued"] = len(state.get("queue") or [])
    if not include_queue:
        state.pop("queue", None)
    state["limits"] = {
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

        with _state_lock():
            state = _enqueue_state()
            today = date.today().isoformat()
            if state["auto_date"] != today:
                state.update(auto_date=today, auto_count=0)
            if state["auto_count"] >= AUTO_DAILY_LIMIT:
                return {
                    "queued": False,
                    "reason": f"今天自动建索引已达上限（{AUTO_DAILY_LIMIT} 本），明天再自动继续",
                }
            if state["cancel_requested"] or len(state["queue"]) >= MAX_AUTO_QUEUE:
                return {"queued": False, "reason": "建索引队列暂不可用，稍后再打开这本书"}

            added = _append(state, [{
                "book_id": book.id,
                "title": getattr(book, "title", "") or "",
                "source": "auto",
                "max_chunks": DEFAULT_AUTO_CHUNKS,
            }])
            if not added:
                return {"queued": False, "reason": "已经在建索引队列里"}
            state["auto_count"] += 1
            position = len(state["queue"])
            _write(state)
        started = ensure_worker()
        return {"queued": True, "started": started, "position": position}
    except Exception as exc:  # noqa: BLE001 - auto indexing is best effort
        logger.warning("RAG auto index request failed for {}: {}", getattr(book, "id", "?"), exc)
        return {"queued": False, "reason": f"无法排队：{exc}"}


def start_batch(book_ids: Iterable[str], *, max_chunks: int = DEFAULT_BATCH_CHUNKS,
                titles: Mapping[str, str] | None = None,
                include_queue: bool = True) -> dict[str, Any]:
    """Queue books with the requested per-book limit. Zero means all chunks."""
    ids = [str(book_id) for book_id in book_ids if str(book_id or "").strip()]
    deduped = list(dict.fromkeys(ids))
    with _state_lock():
        state = _enqueue_state()
        if state["cancel_requested"]:
            raise ValueError("当前向量索引任务正在取消，请稍后再试")
        added = _append(state, (
            {"book_id": book_id, "title": (titles or {}).get(book_id, ""),
             "source": "batch", "max_chunks": max_chunks}
            for book_id in deduped
        ))
        if added:
            _write(state)
    started = ensure_worker()
    return {
        "queued": added,
        "duplicates": len(deduped) - added,
        "started": started,
        "max_chunks": max_chunks,
        "state": status(include_queue=include_queue),
    }


# -- the worker --------------------------------------------------------------


async def _index_one(item: Mapping[str, Any]) -> dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services.rag import RAGService

    async with SessionLocal() as db:
        service = RAGService(db)
        return await service.index_book(
            str(item["book_id"]),
            max_chunks=int(item.get("max_chunks", DEFAULT_BATCH_CHUNKS)),
        )


def _finish_state(state: dict[str, Any], phase: str, error: str | None = None) -> None:
    _clear_pending(state)
    if phase == "failed":
        message = "批量建索引已停止"
    elif phase == "cancelled":
        message = "已取消（已写入的向量保留）"
    elif phase == "interrupted":
        message = "向量索引任务已中断"
    else:
        message = f"完成：新索引 {state['done']} 本、跳过 {state['skipped']} 本、失败 {state['failed']} 本"
    finished_at = state["updated_at"] if phase == "interrupted" else None
    state.update(
        phase=phase, running=False, cancel_requested=False, error=error, message=message,
        finished_at=finished_at or naive_now().isoformat(timespec="seconds"),
    )


def _finish_run(state: dict[str, Any], phase: str, error: str | None = None) -> None:
    _finish_state(state, phase, error)
    _write(state)
    _release_lock()


async def _run() -> None:
    """Drain the queue, one book at a time."""
    consecutive_failures = 0
    try:
        while True:
            with _state_lock():
                state = read_state()
                if state["cancel_requested"]:
                    _finish_run(state, "cancelled")
                    return
                if not state["queue"]:
                    _finish_run(state, "done")
                    return

                item = state["queue"].pop(0)
                title = item.get("title") or item["book_id"]
                state.update(
                    current={"book_id": item["book_id"], "title": title},
                    phase="indexing",
                    message=f"正在索引：{title}",
                    max_chunks=int(item.get("max_chunks", DEFAULT_BATCH_CHUNKS)),
                )
                _write(state)

            try:
                result = await _index_one(item)
            except Exception as exc:  # noqa: BLE001 - one bad book must not stop the queue
                consecutive_failures += 1
                logger.warning("RAG indexing failed for {}: {}", item["book_id"], exc)
                with _state_lock():
                    state = read_state()
                    state["failed"] += 1
                    state["recent_errors"].append({
                        "book_id": item["book_id"], "title": title, "error": str(exc)[:300],
                    })
                    state["recent_errors"] = state["recent_errors"][-RECENT_ERROR_LIMIT:]
                    state["current"] = None
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES and not state["cancel_requested"]:
                        error = f"连续 {consecutive_failures} 本失败，已停止：{str(exc)[:200]}"
                        _finish_run(state, "failed", error)
                        return
                    _write(state)
                continue

            consecutive_failures = 0
            with _state_lock():
                state = read_state()
                if result.get("skipped"):
                    state["skipped"] += 1
                else:
                    state["done"] += 1
                    state["chunks"] += int(result.get("chunks") or 0)
                state["current"] = None
                state["message"] = f"已完成 {state['done']} 本，{state['chunks']} 个片段"
                _write(state)
    except asyncio.CancelledError:
        with _state_lock():
            _finish_run(read_state(), "interrupted")
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("RAG job runner crashed")
        with _state_lock():
            _finish_run(read_state(), "failed", str(exc) or type(exc).__name__)


__all__ = [
    "AUTO_DAILY_LIMIT",
    "DEFAULT_AUTO_CHUNKS",
    "DEFAULT_BATCH_CHUNKS",
    "cancel",
    "ensure_worker",
    "read_state",
    "request_auto",
    "start_batch",
    "status",
]
