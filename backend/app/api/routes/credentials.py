"""Source credential management for auto-login fallback."""

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models import Source, Cookie, User
from app.models.source_credential import SourceCredential
from app.services.cookie_crypto import encrypt_cookie, decrypt_cookie
from app.services.auth import get_current_user

router = APIRouter(
    prefix="/credentials",
    tags=["credentials"],
)


class CredentialCreate(BaseModel):
    source: str = Field(..., description="Source ID")
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class CredentialOut(BaseModel):
    id: str
    source: str
    username: str
    enabled: bool
    created_at: datetime | None = None

    class Config:
        from_attributes = True


def _can_access_source(user, source: Source) -> bool:
    if user.role in ("admin", "super_admin"):
        return True
    return source.owner_id is not None and source.owner_id == user.id


@router.get("", response_model=list[CredentialOut])
async def list_credentials(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(SourceCredential)
    if user.role not in ("admin", "super_admin"):
        query = (
            select(SourceCredential)
            .join(Source, Source.id == SourceCredential.source)
            .where(Source.owner_id == user.id)
        )
    result = await db.scalars(query)
    return list(result)


@router.post("", response_model=CredentialOut, status_code=201)
async def create_credential(
    payload: CredentialCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    source = await db.get(Source, payload.source)
    if source is None or not _can_access_source(user, source):
        raise HTTPException(status_code=404, detail="Source not found")
    existing = await db.scalar(
        select(SourceCredential).where(SourceCredential.source == payload.source)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Credential for this source already exists")

    cred = SourceCredential(
        id=str(uuid4()),
        source=payload.source,
        username=payload.username,
        password_encrypted=encrypt_cookie(payload.password),
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return cred


@router.delete("/{cred_id}", status_code=204)
async def delete_credential(
    cred_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cred = await db.get(SourceCredential, cred_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    source = await db.get(Source, cred.source)
    if source is None or not _can_access_source(user, source):
        raise HTTPException(status_code=404, detail="Credential not found")
    await db.delete(cred)
    await db.commit()


@router.post("/{cred_id}/auto-login")
async def trigger_auto_login(
    cred_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Attempt auto-login using stored credentials and auto-save the resulting cookie."""
    cred = await db.get(SourceCredential, cred_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    from app.crawler.registry import get_plugin

    # Get the source to look up its plugin name
    source = await db.get(Source, cred.source)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source not found: {cred.source}")
    if not _can_access_source(user, source):
        raise HTTPException(status_code=404, detail="Credential not found")
    config = source.config if source.plugin_name == "yuedu" and source.config else None

    try:
        plugin = get_plugin(source.plugin_name, config=config)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown plugin: {source.plugin_name}")

    password = decrypt_cookie(cred.password_encrypted)
    cookie_str = await plugin.auto_login(cred.username, password)

    if cookie_str is None:
        raise HTTPException(status_code=400, detail="Auto-login failed. The source may require manual browser login.")

    # Auto-save the cookie to the Cookie table
    existing_cookie = await db.scalar(
        select(Cookie).where(Cookie.source == cred.source)
    )
    if existing_cookie:
        existing_cookie.cookie_data = encrypt_cookie(cookie_str)
    else:
        new_cookie = Cookie(
            id=str(uuid4()),
            source=cred.source,
            cookie_data=encrypt_cookie(cookie_str),
        )
        db.add(new_cookie)

    await db.commit()

    return {
        "status": "ok",
        "source": cred.source,
        "cookie_saved": True,
        "message": "Auto-login succeeded. Cookie has been saved automatically.",
    }
