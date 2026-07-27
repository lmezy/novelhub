from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Cookie, User
from app.repositories.cookie import CookieRepository
from app.schemas.cookie import CookieCreate, CookieOut, CookieUpdate
from app.services.auth import require_admin

router = APIRouter(
    prefix="/cookies",
    tags=["cookies"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=list[CookieOut])
async def list_cookies(db: AsyncSession = Depends(get_db)):
    repo = CookieRepository(db)
    return await repo.list()


@router.post("", response_model=CookieOut, status_code=201)
async def create_cookie(payload: CookieCreate, db: AsyncSession = Depends(get_db)):
    repo = CookieRepository(db)
    existing = await repo.get_by_source(payload.source)
    if existing:
        raise HTTPException(status_code=409, detail="Cookie for this source already exists")
    cookie = Cookie(
        id=str(uuid4()),
        source=payload.source,
        cookie_data=payload.cookie_data,
        expired_at=payload.expired_at,
    )
    return await repo.add(cookie)


@router.get("/{cookie_id}", response_model=CookieOut)
async def get_cookie(cookie_id: str, db: AsyncSession = Depends(get_db)):
    repo = CookieRepository(db)
    cookie = await repo.get(cookie_id)
    if cookie is None:
        raise HTTPException(status_code=404, detail="Cookie not found")
    return cookie


@router.put("/{cookie_id}", response_model=CookieOut)
async def update_cookie(cookie_id: str, payload: CookieUpdate, db: AsyncSession = Depends(get_db)):
    repo = CookieRepository(db)
    cookie = await repo.get(cookie_id)
    if cookie is None:
        raise HTTPException(status_code=404, detail="Cookie not found")
    if payload.cookie_data is not None:
        cookie.cookie_data = payload.cookie_data
    if payload.expired_at is not None:
        cookie.expired_at = payload.expired_at
    await db.flush()
    return cookie


@router.delete("/{cookie_id}", status_code=204)
async def delete_cookie(cookie_id: str, db: AsyncSession = Depends(get_db)):
    repo = CookieRepository(db)
    cookie = await repo.get(cookie_id)
    if cookie is None:
        raise HTTPException(status_code=404, detail="Cookie not found")
    await repo.delete(cookie)
