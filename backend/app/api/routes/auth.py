from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.schemas.auth import RegisterResult, TokenOut
from app.schemas.user import (
    UserCreate,
    UserLogin,
    UserOut,
    UserSelfVisibilityUpdate,
)
from app.services.auth import get_current_user
from app.services.jwt import create_token
from app.services.security import hash_password, verify_password
from app.services.settings import get_registration_approval_enabled
from app.services.account import email_available, username_available


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResult, status_code=201)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    username_error = await username_available(db, payload.username)
    if username_error:
        raise HTTPException(status_code=409, detail=username_error)
    email_error = await email_available(db, payload.email)
    if email_error:
        raise HTTPException(status_code=409, detail=email_error)

    approval_enabled = await get_registration_approval_enabled(db)
    user = User(
        id=str(uuid4()),
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role="user",
        approved=not approval_enabled,
        r18_enabled=False,
        non_r18_enabled=True,
        can_manage_visibility=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if approval_enabled:
        return RegisterResult(status="pending", user=user)
    return RegisterResult(
        status="approved",
        access_token=create_token(user.id),
        user=user,
    )


@router.post("/login", response_model=TokenOut)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    identifier = (payload.username or "").strip()
    user = await db.scalar(
        select(User).where(
            or_(
                User.username == identifier,
                func.lower(User.email) == identifier.lower(),
            )
        )
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.approved and user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Account pending approval")
    return TokenOut(access_token=create_token(user.id), user=user)


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me/visibility", response_model=UserOut)
async def update_my_visibility(
    payload: UserSelfVisibilityUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service visibility toggles for users granted permission by an admin."""
    if (
        current_user.role not in ("admin", "super_admin")
        and not current_user.can_manage_visibility
    ):
        raise HTTPException(
            status_code=403,
            detail="No permission to change content visibility",
        )
    if payload.r18_enabled is not None:
        current_user.r18_enabled = payload.r18_enabled
    if payload.non_r18_enabled is not None:
        current_user.non_r18_enabled = payload.non_r18_enabled
    await db.commit()
    await db.refresh(current_user)
    return current_user
