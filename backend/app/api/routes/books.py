from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import (
    Book,
    BookCategory,
    BookTag,
    BookVersion,
    Chapter,
    ChapterEmbedding,
    ReadingProgress,
    User,
)
from app.services.auth import get_current_user, require_admin
from app.services.epub import EpubService
from app.services.sync import SyncService
from app.schemas.book import BookCreate, BookOut


router = APIRouter(prefix="/books", tags=["books"])


class BatchDeleteRequest(BaseModel):
    ids: list[str]


@router.get("", response_model=list[BookOut])
async def list_books(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.scalars(
        select(Book).options(selectinload(Book.tags)).order_by(Book.updated_at.desc())
    )
    return list(result)


@router.post("/batch-delete", dependencies=[Depends(require_admin)])
async def batch_delete_books(
    payload: BatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No book ids provided")

    books = (
        await db.scalars(select(Book).where(Book.id.in_(payload.ids)))
    ).all()
    if not books:
        raise HTTPException(status_code=404, detail="No books found")

    book_ids = [book.id for book in books]
    await db.execute(delete(BookVersion).where(BookVersion.chapter_id.in_(
        select(Chapter.id).where(Chapter.book_id.in_(book_ids))
    )))
    await db.execute(delete(ChapterEmbedding).where(ChapterEmbedding.book_id.in_(book_ids)))
    await db.execute(delete(Chapter).where(Chapter.book_id.in_(book_ids)))
    await db.execute(delete(BookTag).where(BookTag.book_id.in_(book_ids)))
    await db.execute(delete(BookCategory).where(BookCategory.book_id.in_(book_ids)))
    await db.execute(delete(ReadingProgress).where(ReadingProgress.book_id.in_(book_ids)))

    for book in books:
        await db.delete(book)
    await db.commit()
    return {"deleted": len(book_ids)}


@router.post("", response_model=BookOut, status_code=201)
async def create_book(payload: BookCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = Book(id=str(uuid4()), **payload.model_dump())
    db.add(book)
    await db.commit()
    await db.refresh(book)
    return book


@router.get("/{book_id}", response_model=BookOut)
async def get_book(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book
@router.delete("/{book_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_book(book_id: str, db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    # Delete associated chapters
    chapters = await db.scalars(
        select(Chapter).where(Chapter.book_id == book_id)
    )
    for ch in chapters:
        await db.delete(ch)
    await db.delete(book)
    await db.commit()
@router.get("/{book_id}/epub")
async def download_epub(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    try:
        epub_bytes = await EpubService(db).generate(book_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_name = book.title.replace("/", "_").replace("\\", "_")[:60]
    return Response(
        content=epub_bytes,
        media_type="application/epub+zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.epub"'},
    )
@router.post("/{book_id}/sync", dependencies=[Depends(require_admin)])
async def resync_book(book_id: str, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).resync_book(book_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


