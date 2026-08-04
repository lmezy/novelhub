"""AI assistant API endpoints -- multi-provider support."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, User
from app.services.auth import get_current_user
from app.services.visibility import ensure_book_visible
from app.services.ai import AIService


router = APIRouter(prefix="/ai", tags=["ai"])


async def _require_visible_book(db, user: User, book_id: str) -> Book:
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    return book


class ChatRequest(BaseModel):
    book_id: str = Field(..., description="Book to ask questions about")
    message: str = Field(..., description="User's question")
    context_chapters: int = Field(default=5, ge=1, le=20,
                                   description="Number of nearby chapters for context")


class ChatResponse(BaseModel):
    answer: str
    model: str
    tokens_used: int = 0


class SummaryRequest(BaseModel):
    book_id: str = Field(..., description="Book to summarize")
    chapter_start: int | None = Field(default=None, ge=1)
    chapter_end: int | None = Field(default=None, ge=1)


class SummaryResponse(BaseModel):
    summary: str
    chapters_covered: int
    model: str


class CharacterRequest(BaseModel):
    book_id: str = Field(..., description="Book to analyze characters for")


class CharacterResponse(BaseModel):
    characters: list[dict]
    model: str


class TimelineRequest(BaseModel):
    book_id: str = Field(..., description="Book to extract timeline from")


class TimelineResponse(BaseModel):
    events: list[dict]
    model: str


@router.post("/chat", response_model=ChatResponse)
async def ai_chat(payload: ChatRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Ask a question about a book with context-aware AI."""
    await _require_visible_book(db, user, payload.book_id)
    try:
        result = await AIService(db).chat(
            book_id=payload.book_id,
            message=payload.message,
            context_chapters=payload.context_chapters,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/summary", response_model=SummaryResponse)
async def ai_summary(payload: SummaryRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Generate a summary for a book or range of chapters."""
    await _require_visible_book(db, user, payload.book_id)
    try:
        result = await AIService(db).summarize(
            book_id=payload.book_id,
            chapter_start=payload.chapter_start,
            chapter_end=payload.chapter_end,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/person", response_model=CharacterResponse)
async def ai_characters(payload: CharacterRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Analyze and list characters in a book."""
    await _require_visible_book(db, user, payload.book_id)
    try:
        result = await AIService(db).analyze_characters(book_id=payload.book_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/timeline", response_model=TimelineResponse)
async def ai_timeline(payload: TimelineRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Extract a timeline of events from a book."""
    await _require_visible_book(db, user, payload.book_id)
    try:
        result = await AIService(db).extract_timeline(book_id=payload.book_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
