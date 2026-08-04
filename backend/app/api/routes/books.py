import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, BookFavorite, Chapter, Source, User
from app.services.auth import get_current_user, require_admin
from app.services.book_cleanup import delete_books
from app.services.epub import EpubService
from app.services.sync import SyncService
from app.services.visibility import (
    can_view_r18,
    can_view_all_ages,
    ensure_book_visible,
    visible_tags,
)
from app.schemas.book import (
    BookCreate,
    BookOut,
    BookSourceAlternate,
    BookSourceAlternatesOut,
    ManualBookCreate,
)
from app.services.manual_import import ManualImportService


router = APIRouter(prefix="/books", tags=["books"])


class BatchDeleteRequest(BaseModel):
    ids: list[str]


def _normalize_book_title(title: str) -> str:
    return re.sub(
        r"[\s《》「」『』〈〉（）【】\[\]\"'“”‘’]+",
        "",
        title or "",
    ).lower()


def _serialize_book(book: Book, user: User, is_favorite: bool = False) -> BookOut:
    is_admin = user.role in ("admin", "super_admin")
    return BookOut(
        id=book.id,
        title=book.title,
        author_id=book.author_id,
        source_id=book.source_id,
        source_book_id=book.source_book_id,
        cover=book.cover,
        description=book.description,
        status=book.status,
        is_r18=book.is_r18 if is_admin else False,
        is_favorite=is_favorite,
        created_at=book.created_at,
        updated_at=book.updated_at,
        tag_names=visible_tags(user, book.tag_names),
        author_name=book.author_name,
    )


@router.get("", response_model=list[BookOut])
async def list_books(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = select(Book).options(selectinload(Book.tags)).order_by(Book.updated_at.desc())
    if user.role not in ("admin", "super_admin"):
        conditions = []
        if can_view_all_ages(user):
            conditions.append(Book.is_r18 == False)
        if can_view_r18(user):
            conditions.append(Book.is_r18 == True)
        query = query.where(or_(*conditions)) if conditions else query.where(Book.id == "__none__")
    result = await db.scalars(query)
    books = list(result)
    favorite_ids = set(
        await db.scalars(
            select(BookFavorite.book_id).where(BookFavorite.user_id == user.id)
        )
    )
    for book in books:
        book.is_favorite = book.id in favorite_ids
    return [_serialize_book(book, user, book.is_favorite) for book in books]


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
    query = (
        select(Book)
        .join(BookFavorite, BookFavorite.book_id == Book.id)
        .where(BookFavorite.user_id == user.id)
        .options(selectinload(Book.tags))
        .order_by(BookFavorite.created_at.desc())
    )
    if user.role not in ("admin", "super_admin"):
        conditions = []
        if can_view_all_ages(user):
            conditions.append(Book.is_r18 == False)
        if can_view_r18(user):
            conditions.append(Book.is_r18 == True)
        query = query.where(or_(*conditions)) if conditions else query.where(Book.id == "__none__")
    result = await db.scalars(query)
    books = list(result)
    return [_serialize_book(book, user, True) for book in books]


@router.post("/{book_id}/favorite")
async def favorite_book(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
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
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
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
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    favorite = await db.scalar(
        select(BookFavorite).where(
            BookFavorite.user_id == user.id,
            BookFavorite.book_id == book_id,
        )
    )
    book.is_favorite = favorite is not None
    return _serialize_book(book, user, book.is_favorite)


@router.get("/{book_id}/sources", response_model=BookSourceAlternatesOut)
async def list_book_sources(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List other library books with the same title across sources."""
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")

    query = select(Book).options(selectinload(Book.author)).where(Book.id != book_id)
    if user.role not in ("admin", "super_admin"):
        conditions = []
        if can_view_all_ages(user):
            conditions.append(Book.is_r18 == False)
        if can_view_r18(user):
            conditions.append(Book.is_r18 == True)
        query = query.where(or_(*conditions)) if conditions else query.where(Book.id == "__none__")

    normalized = _normalize_book_title(book.title)
    candidates = [
        b
        for b in (await db.scalars(query)).all()
        if _normalize_book_title(b.title) == normalized
    ]
    candidates.append(book)

    if not candidates:
        return BookSourceAlternatesOut(book_id=book_id, sources=[])

    chapter_counts = dict(
        (
            await db.execute(
                select(Chapter.book_id, func.count(Chapter.id))
                .where(Chapter.book_id.in_([b.id for b in candidates]))
                .group_by(Chapter.book_id)
            )
        ).all()
    )

    source_ids = {b.source_id for b in candidates if b.source_id}
    source_names: dict[str, str] = {}
    if source_ids:
        source_rows = await db.execute(
            select(Source.id, Source.name).where(Source.id.in_(source_ids))
        )
        source_names = dict(source_rows.all())

    sources = [
        BookSourceAlternate(
            id=b.id,
            source_id=b.source_id,
            source_name=source_names.get(b.source_id),
            source_book_id=b.source_book_id,
            title=b.title,
            author_name=b.author_name,
            status=b.status,
            chapter_count=chapter_counts.get(b.id, 0),
            updated_at=b.updated_at,
            is_current=b.id == book.id,
        )
        for b in candidates
    ]
    sources.sort(
        key=lambda s: (
            not s.is_current,
            s.author_name != book.author_name,
            s.source_name or "",
        )
    )
    return BookSourceAlternatesOut(book_id=book_id, sources=sources)


@router.delete("/{book_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_book(book_id: str, db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    await delete_books(db, [book.id])
@router.get("/{book_id}/epub")
async def download_epub(book_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
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


