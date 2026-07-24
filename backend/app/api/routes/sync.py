from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.sync import SyncRequest, SyncResult
from app.services.sync import SyncService


router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/book", response_model=SyncResult)
async def sync_book(payload: SyncRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await SyncService(db).sync_book(payload.source_id, payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
