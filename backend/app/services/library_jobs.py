"""Whole-library book-profile build ("书库画像"), used by cross-book discovery.

Discovery needs one profile per *book*, not per chapter, so the build walks the
library once: four excerpts per book, one embedding call per four books (16
texts), a row per segment.  The whole library is ~185 000 rows (~1.3 GB).

Like the RAG queue this runs in the background behind a lock file (the state
endpoint may be served by the other uvicorn worker), is cancellable between
batches, and stops after :data:`MAX_CONSECUTIVE_FAILURES` failed batches.  A
single run is capped at :data:`MAX_BOOKS_PER_RUN` books so a mistake costs
minutes, not a day.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from loguru import logger

from app.core.clock import naive_now
from app.core.config import settings
from app.services.rag_jobs import acquire_file_lock, release_file_lock

STATE_FILE = ".library-jobs.json"
LOCK_FILE = ".library-jobs.lock"

DEFAULT_BOOKS_PER_RUN = 2000
#: The whole library is ~46k books (about 11.6k embedding requests, 1-2 hours).
#: The cap is a guard against a typo, not a recommendation to run it all at once.
MAX_BOOKS_PER_RUN = 50000
#: Books per embedding request: four segments each fills one 16-text batch.
BOOKS_PER_REQUEST = 4
MAX_CONSECUTIVE_FAILURES = 3

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
        "limit": DEFAULT_BOOKS_PER_RUN,
        "force": False,
        "total": 0,
        "done": 0,
        "skipped": 0,
        "failed": 0,
        "vectors": 0,
        "current": None,
        "model": "",
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
        "message": "",
        "error": None,
        "cancel_requested": False,
    }


def read_state() -> dict[str, Any]:
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
    except Exception as exc:  # progress must never break the build
        logger.debug("Could not write library job state: {}", exc)
    return payload


def status() -> dict[str, Any]:
    state = read_state()
    state["limits"] = {
        "max_books_per_run": MAX_BOOKS_PER_RUN,
        "default_books_per_run": DEFAULT_BOOKS_PER_RUN,
        "books_per_request": BOOKS_PER_REQUEST,
    }
    return state


def cancel() -> dict[str, Any]:
    state = read_state()
    if not state.get("running"):
        return state
    state["cancel_requested"] = True
    state["message"] = "已请求取消，当前这批写完后停止…"
    return _write(state)


# -- entry point -------------------------------------------------------------


def start(*, limit: int = DEFAULT_BOOKS_PER_RUN, force: bool = False) -> dict[str, Any]:
    """Queue a whole-library profile build (admin-triggered)."""
    from app.services import rag_jobs

    if read_state().get("running"):
        return status()
    if rag_jobs.read_state().get("running"):
        # Both jobs hammer the embedding API and the same disk; keep them serial.
        raise ValueError("批量建索引正在运行，等它结束再开始书库画像")
    limit = max(1, min(int(limit or DEFAULT_BOOKS_PER_RUN), MAX_BOOKS_PER_RUN))
    _write({
        **empty_state(),
        "running": True,
        "phase": "running",
        "limit": limit,
        "force": bool(force),
        "started_at": naive_now().isoformat(timespec="seconds"),
        "message": "已开始，正在挑选书籍…",
    })
    if not ensure_worker():
        state = read_state()
        state["running"] = False
        state["phase"] = "failed"
        state["error"] = "另一个进程正在执行书库画像"
        return _write(state)
    return status()


def ensure_worker() -> bool:
    if not acquire_file_lock(_lock_path()):
        return False
    try:
        task = asyncio.get_running_loop().create_task(_run())
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return True
    except RuntimeError:
        release_file_lock(_lock_path())
        return False


# -- the worker --------------------------------------------------------------


async def _run() -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.database import SessionLocal
    from app.models import Book, Chapter
    from app.services.ai_client import AIError, LLMClient
    from app.services.ai_config import get_ai_config
    from app.services.book_profiles import (
        EMBEDDING_DIMS,
        candidate_query,
        insert_profiles,
        segment_texts,
    )
    from app.services.storage import BookStorage

    storage = BookStorage()
    consecutive_failures = 0
    cancelled = False
    error: str | None = None
    try:
        async with SessionLocal() as db:
            state = read_state()
            cfg = await get_ai_config(db)
            if not cfg.embeddings_supported:
                raise AIError("向量服务未配置（设置 → AI → 语义检索）")
            client = LLMClient(cfg)
            state["model"] = cfg.effective_embedding_model
            _write(state)

            books = list(
                (
                    await db.scalars(
                        candidate_query(
                            force=bool(state.get("force")),
                            limit=int(state.get("limit") or DEFAULT_BOOKS_PER_RUN),
                        ).options(selectinload(Book.tags), selectinload(Book.categories))
                    )
                ).unique().all()
            )
            state["total"] = len(books)
            state["message"] = f"共 {len(books)} 本待建画像"
            _write(state)

            for start_index in range(0, len(books), BOOKS_PER_REQUEST):
                if read_state().get("cancel_requested"):
                    cancelled = True
                    break
                batch = books[start_index:start_index + BOOKS_PER_REQUEST]
                try:
                    await _process_batch(db, batch, client, storage, segment_texts, insert_profiles)
                except AIError:
                    raise
                except Exception as exc:  # noqa: BLE001 - one batch must not kill the run
                    consecutive_failures += 1
                    state = read_state()
                    state["failed"] = int(state.get("failed") or 0) + len(batch)
                    state["current"] = None
                    _write(state)
                    logger.warning("Book profile batch failed: {}", exc)
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        error = f"连续 {consecutive_failures} 批失败，已停止：{str(exc)[:200]}"
                        break
                    continue

                consecutive_failures = 0
                state = read_state()
                state["done"] = int(state.get("done") or 0)
                state["current"] = None
                state["message"] = (
                    f"已生成 {state.get('done', 0)} 本 / {state.get('total', 0)} 本，"
                    f"{state.get('vectors', 0)} 条向量"
                )
                _write(state)
    except Exception as exc:  # noqa: BLE001 - surface the reason to the admin
        logger.exception("Library profile build failed")
        error = str(exc)[:400] or type(exc).__name__
    finally:
        state = read_state()
        state["running"] = False
        state["current"] = None
        state["finished_at"] = naive_now().isoformat(timespec="seconds")
        state["cancel_requested"] = False
        if error:
            state["phase"] = "failed"
            state["error"] = error
            state["message"] = "书库画像构建已停止"
        elif cancelled:
            state["phase"] = "cancelled"
            state["message"] = "已取消（已写入的画像保留）"
        else:
            state["phase"] = "done"
            state["message"] = (
                f"完成：新画像 {state.get('done', 0)} 本、跳过 {state.get('skipped', 0)} 本、"
                f"失败 {state.get('failed', 0)} 本"
            )
        _write(state)
        release_file_lock(_lock_path())


async def _process_batch(db, batch, client, storage, segment_texts, insert_profiles) -> None:
    """Build and store profiles for up to ``BOOKS_PER_REQUEST`` books."""
    from sqlalchemy import select

    from app.models import Chapter
    from app.services.book_profiles import EMBEDDING_DIMS

    ids = [book.id for book in batch]
    rows = (
        await db.execute(
            select(Chapter.book_id, Chapter.chapter_number, Chapter.content_path)
            .where(Chapter.book_id.in_(ids))
            .order_by(Chapter.book_id, Chapter.chapter_number)
        )
    ).all()
    by_book: dict[str, list[Any]] = {}
    for book_id, number, path in rows:
        by_book.setdefault(book_id, []).append(
            SimpleNamespace(chapter_number=number, content_path=path)
        )

    texts_by_book: dict[str, list[str]] = {}
    for book in batch:
        chapters = by_book.get(book.id) or []
        if not chapters:
            continue
        texts = await asyncio.to_thread(
            segment_texts, book, chapters, storage.read_chapter
        )
        if texts:
            texts_by_book[book.id] = texts

    state = read_state()
    empty = [book for book in batch if book.id not in texts_by_book]
    if empty:
        state["skipped"] = int(state.get("skipped") or 0) + len(empty)
    if not texts_by_book:
        _write(state)
        return

    flat = [text for texts in texts_by_book.values() for text in texts]
    vectors = await client.embed(flat, batch_size=16)
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMS:
            raise AIError(
                f"向量模型返回了 {len(vector)} 维，书库画像要求 {EMBEDDING_DIMS} 维；"
                "请改用 1024 维模型（bge-m3 / text-embedding-v3 / embedding-3）。"
            )

    offset = 0
    model = client.cfg.effective_embedding_model
    for book in batch:
        texts = texts_by_book.get(book.id)
        if not texts:
            continue
        slice_ = vectors[offset:offset + len(texts)]
        offset += len(texts)
        await insert_profiles(db, book.id, texts, slice_, model=model)
        state["done"] = int(state.get("done") or 0) + 1
        state["vectors"] = int(state.get("vectors") or 0) + len(texts)
        state["current"] = {"book_id": book.id, "title": getattr(book, "title", "")}
    await db.commit()
    _write(state)


__all__ = [
    "DEFAULT_BOOKS_PER_RUN",
    "MAX_BOOKS_PER_RUN",
    "cancel",
    "read_state",
    "start",
    "status",
]
