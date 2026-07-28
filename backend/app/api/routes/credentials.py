"""Source credential management for auto-login fallback."""

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models import Source, Cookie
from app.models.source_credential import SourceCredential
from app.services.cookie_crypto import encrypt_cookie, decrypt_cookie
from app.services.auth import require_admin

router = APIRouter(
    prefix="/credentials",
    tags=["credentials"],
    dependencies=[Depends(require_admin)],
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


@router.get("", response_model=list[CredentialOut])
async def list_credentials(db: AsyncSession = Depends(get_db)):
    result = await db.scalars(select(SourceCredential))
    return list(result)


@router.post("", response_model=CredentialOut, status_code=201)
async def create_credential(payload: CredentialCreate, db: AsyncSession = Depends(get_db)):
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
async def delete_credential(cred_id: str, db: AsyncSession = Depends(get_db)):
    cred = await db.get(SourceCredential, cred_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    await db.delete(cred)
    await db.commit()


@router.post("/{cred_id}/auto-login")
async def trigger_auto_login(cred_id: str, db: AsyncSession = Depends(get_db)):
    """Attempt auto-login using stored credentials and auto-save the resulting cookie."""
    cred = await db.get(SourceCredential, cred_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    from app.crawler.registry import get_plugin

    # Get the source to pass config for yuedu plugin
    source = await db.get(Source, cred.source)
    config = None
    if source and source.plugin_name == "yuedu" and source.config:
        config = source.config

    try:
        plugin = get_plugin(cred.source, config=config)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown source: {cred.source}")

    password = decrypt_cookie(cred.password_encrypted)
    cookie_str = await plugin.auto_login(cred.username, password)

    if cookie_str is None:
        raise HTTPException(status_code=400, detail="Auto-login failed. The source may require manual browser login.")

    # Auto-save the cookie to the Cookie table
    existing_cookie = await db.scalar(
        select(Cookie).where(Cookie.source == cred.source)
    )
    if existing_cookie:
        existing_cookie.cookie_data = cookie_str
    else:
        new_cookie = Cookie(
            id=str(uuid4()),
            source=cred.source,
            cookie_data=cookie_str,
        )
        db.add(new_cookie)

    await db.commit()

    return {
        "status": "ok",
        "source": cred.source,
        "cookie_saved": True,
        "message": "Auto-login succeeded. Cookie has been saved automatically.",
    }
