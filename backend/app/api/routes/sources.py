from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Source, User
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