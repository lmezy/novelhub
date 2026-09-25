"""RAG (Retrieval-Augmented Generation) API endpoints."""

from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, User
from app.services.ai_client import AIError
from app.services.auth import get_current_user, require_admin
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
