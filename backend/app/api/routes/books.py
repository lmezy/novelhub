from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, BookFavorite, User
from app.services.auth import get_current_user, require_admin
from app.services.book_cleanup import delete_books
from app.services.epub import EpubService
from app.services.sync import SyncService
from app.schemas.book import BookCreate, BookOut, ManualBookCreate
from app.services.manual_import import ManualImportService


router = APIRouter(prefix="/books", tags=["books"])


class BatchDeleteRequest(BaseModel):
    ids: list[str]


@router.get("", response_model=list[BookOut])
async def list_books(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.scalars(
        select(Book).options(selectinload(Book.tags)).order_by(Book.updated_at.desc())
    )
    books = list(result)
    favorite_ids = set(
        await db.scalars(
            select(BookFavorite.book_id).where(BookFavorite.user_id == user.id)
        )
    )
    for book in books:
        book.is_favorite = book.id in favorite_ids
    return books


@router.post("/batch-delete", dependencies=[Depends(require_admin)])
async def batch_delete_books(
    payload: BatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No book ids provided")

    books = (
        await db.scalars(select(Book).where(Book.id.in_(payload.ids)))
    ).unique().all()
    if not books:
        raise HTTPException(status_code=404, detail="No books found")

    book_ids = [book.id for book in books]
    await delete_books(db, book_ids)
    return {"deleted": len(book_ids)}


@router.post("/manual", dependencies=[Depends(require_admin)])
async def create_manual_book(
    payload: ManualBookCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ManualImportService(db).import_book(
            title=payload.title,
            author=payload.author,
            description=payload.description,
            status=payload.status,
            tags=payload.tags,
            chapters=[chapter.model_dump() for chapter in payload.chapters],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=BookOut, status_code=201)
async def create_book(payload: BookCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = Book(id=str(uuid4()), **payload.model_dump())
    db.add(book)
    await db.commit()
    await db.refresh(book)
    return book


@router.get("/favorites", response_model=list[BookOut])
async def list_favorite_books(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(
        select(Book)
        .join(BookFavorite, BookFavorite.book_id == Book.id)
        .where(BookFavorite.user_id == user.id)
        .options(selectinload(Book.tags))
        .order_by(BookFavorite.created_at.desc())
    )
    books = list(result)
    for book in books:
        book.is_favorite = True
    return books


@router.post("/{book_id}/favorite")
async def favorite_book(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    existing = await db.scalar(
        select(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book_id,
        )
    )
    if existing is None:
        db.add(BookFavorite(id=str(uuid4()), user_id=user.id, book_id=book_id))
        await db.commit()
    return {"favorited": True}


@router.delete("/{book_id}/favorite")
async def unfavorite_book(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book_id,
        )
    )
    await db.commit()
    return {"favorited": False}


@router.get("/{book_id}", response_model=BookOut)
async def get_book(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    favorite = await db.scalar(
        select(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book_id,
        )
    )
    book.is_favorite = favorite is not None
    return book
@router.delete("/{book_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_book(book_id: str, db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    await delete_books(db, [book.id])
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


