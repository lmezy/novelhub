from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Invite, User
from app.services.auth import get_current_user
from app.services.invite import generate_invite_code

router = APIRouter(prefix="/invites", tags=["invites"], dependencies=[Depends(get_current_user)])


class InviteOut(BaseModel):
    id: str
    code: str
    expires_at: datetime | None = None
    used_at: datetime | None = None
    used_by: str | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _cleanup_expired(db: AsyncSession) -> None:
    await db.execute(
        delete(Invite).where(Invite.expires_at <= _utcnow())
    )
    await db.commit()


@router.post("", response_model=InviteOut, status_code=201)
async def create_invite(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _cleanup_expired(db)
    for _ in range(10):
        code = generate_invite_code()
        exists = await db.scalar(select(Invite.id).where(Invite.code == code))
        if not exists:
            break
    invite = Invite(
        id=str(uuid4()),
        code=code,
        created_by=user.id,
        expires_at=_utcnow() + timedelta(days=1),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


@router.get("", response_model=list[InviteOut])
async def list_invites(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _cleanup_expired(db)
    query = select(Invite).where(Invite.created_by == user.id)
    if user.role in ("admin", "super_admin"):
        query = select(Invite)
    rows = await db.scalars(query.order_by(Invite.created_at.desc()))
    return list(rows.all())


@router.delete("/{invite_id}", status_code=204)
async def delete_invite(
    invite_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    invite = await db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.created_by != user.id and user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=404, detail="Invite not found")
    await db.delete(invite)
    await db.commit()
