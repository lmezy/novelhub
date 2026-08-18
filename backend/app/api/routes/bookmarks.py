from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, Bookmark, Chapter, User
from app.schemas.bookmark import BookmarkCreate, BookmarkOut, BookmarkUpdate
from app.services.auth import get_current_user
from app.services.visibility import ensure_book_visible


router = APIRouter(prefix="/bookmarks", tags=["bookmarks"])


async def _serialize(db: AsyncSession, bookmark: Bookmark) -> BookmarkOut:
    chapter = await db.get(Chapter, bookmark.chapter_id)
    book = await db.get(Book, bookmark.book_id)
    return BookmarkOut(
        id=bookmark.id,
        user_id=bookmark.user_id,
        book_id=bookmark.book_id,
        chapter_id=bookmark.chapter_id,
        position=bookmark.position,
        note=bookmark.note,
        created_at=bookmark.created_at,
        chapter_title=chapter.title if chapter else None,
        chapter_number=chapter.chapter_number if chapter else None,
        book_title=book.title if book else None,
    )


def _clamp_position(position: int) -> int:
    return max(0, min(100, int(position)))


@router.post("", response_model=BookmarkOut, status_code=201)
async def create_bookmark(
    payload: BookmarkCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    book = await db.get(Book, payload.book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    chapter = await db.get(Chapter, payload.chapter_id)
    if chapter is None or chapter.book_id != payload.book_id:
        raise HTTPException(status_code=404, detail="Chapter not found")
    bookmark = Bookmark(
        id=str(uuid4()),
        user_id=user.id,
        book_id=payload.book_id,
        chapter_id=payload.chapter_id,
        position=_clamp_position(payload.position),
        note=payload.note,
    )
    db.add(bookmark)
    await db.commit()
    await db.refresh(bookmark)
    return await _serialize(db, bookmark)


@router.get("", response_model=list[BookmarkOut])
async def list_bookmarks(
    book_id: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if book_id:
        book = await db.get(Book, book_id)
        if not ensure_book_visible(user, book):
            raise HTTPException(status_code=404, detail="Book not found")
    query = (
        select(Bookmark)
        .where(Bookmark.user_id == user.id)
        .order_by(Bookmark.created_at.desc())
    )
    if book_id:
        query = query.where(Bookmark.book_id == book_id)
    result = await db.scalars(query)
    bookmarks = list(result)
    return [await _serialize(db, b) for b in bookmarks]


@router.put("/{bookmark_id}", response_model=BookmarkOut)
async def update_bookmark(
    bookmark_id: str,
    payload: BookmarkUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    bookmark = await db.get(Bookmark, bookmark_id)
    if bookmark is None or bookmark.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    if payload.note is not None:
        bookmark.note = payload.note
    if payload.position is not None:
        bookmark.position = _clamp_position(payload.position)
    await db.commit()
    await db.refresh(bookmark)
    return await _serialize(db, bookmark)


@router.delete("/{bookmark_id}", status_code=204)
async def delete_bookmark(
    bookmark_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    bookmark = await db.get(Bookmark, bookmark_id)
    if bookmark is None or bookmark.user_id != user.id:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    await db.delete(bookmark)
    await db.commit()
    return None
