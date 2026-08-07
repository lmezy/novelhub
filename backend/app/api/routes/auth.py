from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Invite, User
from app.schemas.auth import RegisterResult, TokenOut
from app.schemas.user import (
    ChangePasswordRequest,
    UserCreate,
    UserLogin,
    UserOut,
    UpdateEmailRequest,
    UpdateNicknameRequest,
    UserSelfVisibilityUpdate,
    UserSettingsUpdate,
)
from app.services.auth import get_current_user
from app.services.jwt import create_token
from app.services.security import hash_password, verify_password
from app.services.settings import get_registration_approval_enabled
from app.services.account import email_available, username_available
from app.services.invite import generate_invite_code, generate_nickname
from app.services.validation import password_error


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResult, status_code=201)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    if not payload.invite_code:
        raise HTTPException(status_code=400, detail="Invite code is required")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    invite = await db.scalar(
        select(Invite).where(Invite.code == payload.invite_code)
    )
    if invite is None:
        raise HTTPException(status_code=400, detail="Invalid invite code")
    if invite.used_by is not None or invite.expires_at <= now:
        raise HTTPException(status_code=400, detail="Invite code is invalid or expired")
    inviter = await db.get(User, invite.created_by)
    if inviter is None:
        raise HTTPException(status_code=400, detail="Invalid invite code")
    inviter_id = inviter.id
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
        invite_code=generate_invite_code(),
        nickname=generate_nickname(),
        invited_by_id=inviter_id,
        invite_tag=inviter.username,
        r18_enabled=False,
        non_r18_enabled=True,
        can_manage_visibility=False,
    )
    db.add(user)
    if invite is not None:
        invite.used_by = user.id
        invite.used_at = now
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


@router.put("/me/settings", response_model=UserOut)
async def update_my_settings(
    payload: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = dict(current_user.settings or {})
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is not None:
            settings[key] = value
    current_user.settings = settings
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.put("/me/password")
async def change_my_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    error = password_error(payload.new_password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    current_user.password_hash = hash_password(payload.new_password)
    await db.commit()
    return {"status": "ok"}


@router.put("/me/email", response_model=UserOut)
async def update_my_email(
    payload: UpdateEmailRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.email and current_user.email.lower() == payload.email.lower():
        return current_user
    error = await email_available(db, payload.email)
    if error:
        raise HTTPException(status_code=409, detail=error)
    current_user.email = payload.email
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.put("/me/nickname", response_model=UserOut)
async def update_my_nickname(
    payload: UpdateNicknameRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    nickname = (payload.nickname or "").strip()
    if not nickname or len(nickname) > 48:
        raise HTTPException(
            status_code=400,
            detail="Nickname must be 1-48 characters",
        )
    if nickname.lower() in ("null", "none", "undefined"):
        raise HTTPException(
            status_code=400,
            detail="Nickname cannot be null, none, or undefined",
        )
    current_user.nickname = nickname
    await db.commit()
    await db.refresh(current_user)
    return current_user
