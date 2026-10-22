"""RAG (Retrieval-Augmented Generation) API endpoints."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, ChapterEmbedding, User
from app.services import rag_jobs
from app.services.ai_client import AIError
from app.services.ai_config import get_ai_config
from app.services.auth import get_current_user, require_admin
from app.services.book_kind import KIND_COMIC
from app.services.rag import RAGService
from app.services.visibility import ensure_book_visible


router = APIRouter(prefix="/rag", tags=["rag"])


class RAGSearchRequest(BaseModel):
    book_id: str = Field(..., description="Book to search within")
    query: str = Field(..., description="Semantic search query")
    top_k: int = Field(default=5, ge=1, le=20)


class RAGSearchResult(BaseModel):
    chapter_id: str
    chapter_number: int
    chapter_title: str
    chunk_index: int
    content: str
    similarity: float


class RAGIndexResult(BaseModel):
    book_id: str
    chapters: int = 0
    indexed_chapters: int = 0
    chunks: int = 0
    truncated: bool = False
    skipped: bool = False
    reason: str = ""
    model: str = ""
    title: str = ""


class RAGIndexStatus(BaseModel):
    book_id: str
    indexed: bool
    chunks: int
    indexed_chapters: int
    total_chapters: int
    ready: bool
    embeddings_supported: bool
    embedding_model: str = ""


class RAGIndexedBook(BaseModel):
    book_id: str
    title: str = ""
    chunks: int = 0
    chapters: int = 0
    indexed_at: str | None = None


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AIError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/status/{book_id}", response_model=RAGIndexStatus)
async def index_status(book_id: str, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Whether this book is ready for semantic search."""
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    return await RAGService(db).index_status(book_id)


@router.get("/index", response_model=list[RAGIndexedBook],
            dependencies=[Depends(require_admin)])
async def list_indexed(limit: int = Query(50, ge=1, le=200),
                       db: AsyncSession = Depends(get_db)):
    """Books that currently have RAG vectors (admin view)."""
    return await RAGService(db).list_indexed_books(limit=limit)


@router.post("/index/{book_id}", response_model=RAGIndexResult,
             dependencies=[Depends(require_admin)])
async def index_book(
    book_id: str,
    force: bool = Query(False, description="Rebuild even if already indexed"),
    chapter_start: int | None = Query(None, ge=1),
    chapter_end: int | None = Query(None, ge=1),
    max_chunks: int | None = Query(None, ge=1, le=20000),
    db: AsyncSession = Depends(get_db),
):
    """Index all chapters of a book for semantic search."""
    try:
        return await RAGService(db).index_book(
            book_id, force=force,
            chapter_start=chapter_start, chapter_end=chapter_end,
            max_chunks=max_chunks,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/search", response_model=list[RAGSearchResult])
async def search_chunks(
    payload: RAGSearchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Semantic search within a book's indexed content."""
    book = await db.get(Book, payload.book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    try:
        return await RAGService(db).search(
            book_id=payload.book_id,
            query=payload.query,
            top_k=payload.top_k,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/index/{book_id}", dependencies=[Depends(require_admin)])
async def delete_index(book_id: str, db: AsyncSession = Depends(get_db)):
    """Remove RAG index for a book."""
    await RAGService(db).delete_book_index(book_id)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Background indexing: the reader's "打开即建" queue and the admin batch run.
#
# Both live behind the same runner (app.services.rag_jobs): one book at a time,
# capped books per run and capped chunks per book, so neither the embedding bill
# nor the Postgres JSONB table can grow without a human deciding to.
# ---------------------------------------------------------------------------


class RAGBatchRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=rag_jobs.MAX_BATCH_BOOKS)
    max_chunks: int = Field(
        default=rag_jobs.DEFAULT_BATCH_CHUNKS, ge=1, le=rag_jobs.MAX_CHUNKS_PER_BOOK
    )
    #: ``unindexed`` = newest novels that have no vectors yet; ``book_ids`` =
    #: exactly the ids given (still capped by ``limit``).
    scope: str = Field(default="unindexed", description="unindexed | book_ids")
    book_ids: list[str] = Field(default_factory=list)


@router.post("/auto/{book_id}")
async def auto_index_book(book_id: str, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """Queue this book for background indexing (no-op unless enabled).

    Called by the reader when a book is opened; it never blocks and every
    refusal is a reason string, so the panel can stay silent.
    """
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    return await rag_jobs.request_auto(db, book)


@router.post("/batch", dependencies=[Depends(require_admin)])
async def start_batch(payload: RAGBatchRequest, db: AsyncSession = Depends(get_db)):
    """Queue up to ``MAX_BATCH_BOOKS`` books for background indexing."""
    titles: dict[str, str] = {}
    if payload.scope == "book_ids":
        requested = list(dict.fromkeys(str(i) for i in payload.book_ids if i))[: payload.limit]
        if not requested:
            raise HTTPException(status_code=400, detail="没有提供书籍 ID")
        rows = (
            await db.execute(select(Book.id, Book.title).where(Book.id.in_(requested)))
        ).all()
        titles = {row[0]: row[1] for row in rows}
        ids = [book_id for book_id in requested if book_id in titles]
    else:
        rows = (
            await db.execute(
                select(Book.id, Book.title)
                .where(or_(Book.kind.is_(None), Book.kind != KIND_COMIC))
                .where(~exists().where(ChapterEmbedding.book_id == Book.id))
                .order_by(Book.updated_at.desc())
                .limit(payload.limit)
            )
        ).all()
        titles = {row[0]: row[1] for row in rows}
        ids = list(titles)

    if not ids:
        return {
            "queued": 0,
            "candidates": 0,
            "message": "没有符合条件的书（都建过索引，或没有可读章节）",
        }
    result = rag_jobs.start_batch(ids, max_chunks=payload.max_chunks, titles=titles)
    result["candidates"] = len(ids)
    return result


#: The panel polls the job status every few seconds; counting a multi-million
#: row table that often is wasteful, so the aggregate stats are cached briefly.
_STATS_TTL_SECONDS = 30.0
_stats_cache: dict[str, Any] = {"at": 0.0, "stats": None}


async def _index_stats(db: AsyncSession, cfg) -> dict[str, Any]:
    now = time.monotonic()
    cached = _stats_cache.get("stats")
    if cached is not None and now - float(_stats_cache.get("at") or 0.0) < _STATS_TTL_SECONDS:
        return cached
    indexed_books = int(await db.scalar(
        select(func.count(func.distinct(ChapterEmbedding.book_id)))
    ) or 0)
    chunks = int(await db.scalar(select(func.count()).select_from(ChapterEmbedding)) or 0)
    try:
        db_bytes = int(await db.scalar(
            text("select pg_total_relation_size('chapter_embeddings')")
        ) or 0)
    except Exception:  # pragma: no cover - the estimate is optional
        db_bytes = 0
    stats = {
        "indexed_books": indexed_books,
        "chunks": chunks,
        "db_bytes": db_bytes,
        "embeddings_supported": cfg.embeddings_supported,
        "embedding_model": cfg.effective_embedding_model,
        "auto_index_enabled": cfg.rag_auto_index,
        "rag_enabled": cfg.rag_enabled,
    }
    _stats_cache["at"] = now
    _stats_cache["stats"] = stats
    return stats


@router.get("/jobs/status", dependencies=[Depends(require_admin)])
async def jobs_status(db: AsyncSession = Depends(get_db)):
    """Progress of the background queue plus what is already indexed."""
    state = rag_jobs.status()
    cfg = await get_ai_config(db)
    state["stats"] = await _index_stats(db, cfg)
    return state


@router.post("/jobs/cancel", dependencies=[Depends(require_admin)])
async def cancel_jobs():
    """Stop after the book currently being indexed."""
    return rag_jobs.cancel()
