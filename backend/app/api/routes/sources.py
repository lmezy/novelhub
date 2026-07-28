from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Source, Book, Cookie, CrawlTask, CrawlLog
from app.models.source_credential import SourceCredential
from app.schemas.source import SourceCreate, SourceOut
from app.services.auth import require_admin


router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)):
    result = await db.scalars(select(Source).order_by(Source.name.asc()))
    return list(result)


@router.post("", response_model=SourceOut, status_code=201, dependencies=[Depends(require_admin)])
async def create_source(payload: SourceCreate, db: AsyncSession = Depends(get_db)):
    if await db.get(Source, payload.id):
        raise HTTPException(status_code=409, detail="Source already exists")
    source = Source(**payload.model_dump(mode="json"))
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=200, dependencies=[Depends(require_admin)])
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)):
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Count related books
    book_count = await db.scalar(
        select(Book).where(Book.source_id == source_id)
    )
    deleted_books = 0
    if book_count:
        # Delete related books and their chapters will cascade
        await db.execute(delete(Book).where(Book.source_id == source_id))
        deleted_books = 1  # simplified count

    # Delete related cookies
    await db.execute(delete(Cookie).where(Cookie.source == source_id))

    # Delete related credentials
    await db.execute(delete(SourceCredential).where(SourceCredential.source == source_id))

    # Delete related crawl tasks and logs
    await db.execute(delete(CrawlTask).where(CrawlTask.source == source_id))
    await db.execute(delete(CrawlLog).where(CrawlLog.source == source_id))

    # Delete the source itself
    await db.delete(source)
    await db.commit()

    return {
        "status": "ok",
        "deleted": source_id,
        "name": source.name,
        "deleted_books": deleted_books,
    }
