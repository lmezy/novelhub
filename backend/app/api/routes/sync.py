from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.schemas.sync import (
    BookshelfSyncRequest,
    BookshelfSyncResult,
    DiscoverRequest,
    DiscoverResult,
    SyncRequest,
    SyncResult,
)
from app.services.auth import require_admin
from app.services.sync import SyncService

router = APIRouter(prefix="/sync", tags=["sync"], dependencies=[Depends(require_admin)])


@router.post("/book", response_model=SyncResult)
async def sync_book(payload: SyncRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).sync_book(payload.source_id, payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bookshelf", response_model=BookshelfSyncResult)
async def sync_bookshelf(payload: BookshelfSyncRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).sync_bookshelf(payload.source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/discover", response_model=DiscoverResult)
async def discover_and_sync(payload: DiscoverRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).discover_and_sync(
            source_id=payload.source_id,
            url=payload.url,
            page=payload.page,
            sync=payload.sync,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
