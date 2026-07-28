from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import ReadingProgress
from app.schemas.progress import ReadingProgressOut, ReadingProgressUpsert


router = APIRouter(prefix="/progress", tags=["progress"])


@router.put("", response_model=ReadingProgressOut)
async def upsert_progress(payload: ReadingProgressUpsert, db: AsyncSession = Depends(get_db)):
    progress = await db.scalar(
        select(ReadingProgress).where(
            ReadingProgress.user_id == payload.user_id,
            ReadingProgress.book_id == payload.book_id,
        )
    )
    if progress is None:
        progress = ReadingProgress(id=str(uuid4()), **payload.model_dump())
        db.add(progress)
    else:
        progress.chapter_id = payload.chapter_id
        progress.position = payload.position
    await db.commit()
    await db.refresh(progress)
    return progress
@router.get("", response_model=list[ReadingProgressOut])
async def list_progress(
    user_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(
        select(ReadingProgress)
        .where(ReadingProgress.user_id == user_id)
        .order_by(ReadingProgress.updated_at.desc())
        .limit(20)
    )
    return list(result)

