from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Source, User
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


class LocalImportRequest(BaseModel):
    path: str


@router.post("/local", response_model=SyncResult)
async def import_local_book(
    payload: LocalImportRequest,
    db: AsyncSession = Depends(get_db),
):
    source = await db.get(Source, "local")
    source_id = "local"
    if source is None or source.plugin_name != "local_markdown":
        source = Source(
            id="local_markdown",
            name="本地 Markdown",
            plugin_name="local_markdown",
            enabled=True,
        )
        db.add(source)
        await db.commit()
        source_id = source.id

    path = payload.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="Local path is required")
    if not path.startswith("file://"):
        path = f"file://{path}"
    try:
        return await SyncService(db).sync_book(source_id, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
