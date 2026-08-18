from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Book, ReadingProgress, User
from app.services.auth import get_current_user
from app.services.visibility import (
    can_view_all_ages,
    can_view_r18,
    ensure_book_visible,
)
from app.schemas.progress import ReadingProgressOut, ReadingProgressUpsert


router = APIRouter(prefix="/progress", tags=["progress"])


@router.put("", response_model=ReadingProgressOut)
async def upsert_progress(payload: ReadingProgressUpsert, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    book = await db.get(Book, payload.book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    # Always scope progress to the authenticated user; ignore any client-supplied user_id.
    progress = await db.scalar(
        select(ReadingProgress).where(
            ReadingProgress.user_id == user.id,
            ReadingProgress.book_id == payload.book_id,
        )
    )
    if progress is None:
        progress = ReadingProgress(
            id=str(uuid4()),
            user_id=user.id,
            book_id=payload.book_id,
            chapter_id=payload.chapter_id,
            position=max(0, min(100, int(payload.position))),
        )
        db.add(progress)
    else:
        progress.chapter_id = payload.chapter_id
        progress.position = max(0, min(100, int(payload.position)))
    await db.commit()
    await db.refresh(progress)
    return progress


@router.get("/{book_id}", response_model=ReadingProgressOut | None)
async def get_progress(
    book_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated user's reading progress for a single book."""
    book = await db.get(Book, book_id)
    if not ensure_book_visible(user, book):
        raise HTTPException(status_code=404, detail="Book not found")
    return await db.scalar(
        select(ReadingProgress).where(
            ReadingProgress.user_id == user.id,
            ReadingProgress.book_id == book_id,
        )
    )


@router.get("", response_model=list[ReadingProgressOut])
async def list_progress(
    user_id: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in ("admin", "super_admin") and user_id != user.id:
        return []
    query = (
        select(ReadingProgress)
        .join(Book, Book.id == ReadingProgress.book_id)
        .where(ReadingProgress.user_id == user_id)
        .order_by(ReadingProgress.updated_at.desc())
        .limit(20)
    )
    if user.role not in ("admin", "super_admin"):
        query = query.where(or_(
            Book.owner_id.is_(None),
            Book.owner_id == user.id,
            Book.is_public == True,
        ))
    conditions = []
    if can_view_all_ages(user):
        conditions.append(Book.is_r18 == False)
    if can_view_r18(user):
        conditions.append(Book.is_r18 == True)
    query = query.where(or_(*conditions)) if conditions else query.where(Book.id == "__none__")
    result = await db.scalars(query)
    return list(result)
