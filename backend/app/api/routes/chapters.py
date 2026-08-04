from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, Chapter, User
from app.schemas.chapter import ChapterContentOut, ChapterOut
from app.services.auth import get_current_user
from app.services.storage import BookStorage
from app.services.visibility import ensure_book_visible


router = APIRouter(tags=["chapters"])


@router.get("/books/{book_id}/chapters", response_model=list[ChapterOut])
async def list_chapters(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    result = await db.scalars(
        select(Chapter)
        .where(Chapter.book_id == book_id)
        .order_by(Chapter.chapter_number.asc())
    )
    return list(result)


@router.get("/chapters/{chapter_id}", response_model=ChapterContentOut)
async def get_chapter(chapter_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    book = await db.get(Book, chapter.book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Chapter not found")
    try:
        content = BookStorage().read_chapter(chapter.content_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Chapter content not found")
    except OSError:
        content = ""
    return ChapterContentOut(
        id=chapter.id,
        title=chapter.title,
        chapter_number=chapter.chapter_number,
        source_chapter_id=chapter.source_chapter_id,
        content_path=chapter.content_path,
        content=content or "",
    )
