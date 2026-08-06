from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeletedAccount, User


RESERVATION_DAYS = 3


async def username_available(db: AsyncSession, username: str) -> str | None:
    active = await db.scalar(select(User.id).where(User.username == username))
    if active:
        return "Username already exists"
    cutoff = datetime.now(timezone.utc) - timedelta(days=RESERVATION_DAYS)
    deleted = await db.scalar(
        select(DeletedAccount.id).where(
            DeletedAccount.username == username,
            DeletedAccount.deleted_at >= cutoff,
        )
    )
    if deleted:
        return (
            "Username was recently used by a deleted account; "
            "try again after 3 days"
        )
    return None


async def email_available(db: AsyncSession, email: str | None) -> str | None:
    if not email:
        return None
    active = await db.scalar(
        select(User.id).where(func.lower(User.email) == email.lower())
    )
    if active:
        return "Email already exists"
    cutoff = datetime.now(timezone.utc) - timedelta(days=RESERVATION_DAYS)
    deleted = await db.scalar(
        select(DeletedAccount.id).where(
            func.lower(DeletedAccount.email) == email.lower(),
            DeletedAccount.deleted_at >= cutoff,
        )
    )
    if deleted:
        return (
            "Email was recently used by a deleted account; "
            "try again after 3 days"
        )
    return None


def reserve_deleted_account(
    db: AsyncSession,
    username: str,
    email: str | None,
) -> None:
    db.add(
        DeletedAccount(
            id=str(uuid4()),
            username=username,
            email=email,
        )
    )
