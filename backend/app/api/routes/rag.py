"""RAG (Retrieval-Augmented Generation) API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.services.auth import require_admin
from app.services.rag import RAGService


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


@router.post("/index/{book_id}", dependencies=[Depends(require_admin)])
async def index_book(book_id: str, db: AsyncSession = Depends(get_db)):
    """Index all chapters of a book for semantic search."""
    try:
        result = await RAGService(db).index_book(book_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/search", response_model=list[RAGSearchResult])
async def search_chunks(payload: RAGSearchRequest, db: AsyncSession = Depends(get_db)):
    """Semantic search within a book's indexed content."""
    try:
        results = await RAGService(db).search(
            book_id=payload.book_id,
            query=payload.query,
            top_k=payload.top_k,
        )
        return results
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/index/{book_id}", dependencies=[Depends(require_admin)])
async def delete_index(book_id: str, db: AsyncSession = Depends(get_db)):
    """Remove RAG index for a book."""
    await RAGService(db).delete_book_index(book_id)
    return {"status": "deleted"}
