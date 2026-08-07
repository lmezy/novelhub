from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User, Source, SourceChange
from app.schemas.user import UserOut
from app.schemas.admin import (
    AdminUserCreate,
    AutoSyncSettingsUpdate,
    RegistrationApprovalUpdate,
    UserContentVisibilityUpdate,
    UserPasswordUpdate,
    UserRoleUpdate,
    UserR18Update,
)
from app.services.auth import get_current_user, require_admin, require_super_admin
from app.services.proxy_config import get_proxy_config, ProxyConfig, set_proxy_config
from app.services.security import hash_password
from app.services.settings import (
    get_auto_sync_settings,
    get_registration_approval_enabled,
    set_auto_sync_settings,
    set_registration_approval_enabled,
)
from app.services.account import (
    email_available,
    reserve_deleted_account,
    username_available,
)
from app.services.invite import generate_invite_code

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserOut])
async def list_users(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(select(User).order_by(User.created_at.desc()))
    return list(result)


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    payload: AdminUserCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if payload.role not in ("user", "admin", "super_admin"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if payload.role != "user" and current_user.role != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="Only super admin can create admin users",
        )
    username_error = await username_available(db, payload.username)
    if username_error:
        raise HTTPException(status_code=409, detail=username_error)
    email_error = await email_available(db, payload.email)
    if email_error:
        raise HTTPException(status_code=409, detail=email_error)

    user = User(
        id=str(uuid4()),
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        approved=True,
        invite_code=generate_invite_code(),
        r18_enabled=False,
        non_r18_enabled=True,
        can_manage_visibility=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/settings/registration-approval")
async def get_registration_approval(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return {
        "enabled": await get_registration_approval_enabled(db),
    }


@router.put("/settings/registration-approval")
async def update_registration_approval(
    payload: RegistrationApprovalUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return {
        "enabled": await set_registration_approval_enabled(
            db,
            payload.enabled,
        ),
    }


@router.get("/settings/auto-sync")
async def get_auto_sync(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await get_auto_sync_settings(db)


@router.put("/settings/auto-sync")
async def update_auto_sync(
    payload: AutoSyncSettingsUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await set_auto_sync_settings(
            db,
            payload.enabled,
            payload.time,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/{user_id}", status_code=200)
async def delete_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role in ("admin", "super_admin") and current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only super admin can delete admin users")
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    reserve_deleted_account(db, target.username, target.email)
    await db.delete(target)
    await db.commit()
    return {"status": "ok", "deleted": user_id}


@router.put("/users/{user_id}/role", response_model=UserOut)
async def update_user_role(
    user_id: str,
    payload: UserRoleUpdate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    if payload.role not in ("user", "admin", "super_admin"):
        raise HTTPException(status_code=400, detail="Invalid role")
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    target.role = payload.role
    await db.commit()
    await db.refresh(target)
    return target


@router.put("/users/{user_id}/approve", response_model=UserOut)
async def approve_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    target.approved = True
    await db.commit()
    await db.refresh(target)
    return target


@router.put("/users/{user_id}/password", response_model=UserOut)
async def update_user_password(
    user_id: str,
    payload: UserPasswordUpdate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    target.password_hash = hash_password(payload.password)
    await db.commit()
    await db.refresh(target)
    return target


@router.put("/users/{user_id}/r18", response_model=UserOut)
async def update_user_r18(
    user_id: str,
    payload: UserR18Update,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only switch that enables a user to see R18 books."""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    target.r18_enabled = payload.enabled
    await db.commit()
    await db.refresh(target)
    return target


@router.put("/users/{user_id}/visibility", response_model=UserOut)
async def update_user_visibility(
    user_id: str,
    payload: UserContentVisibilityUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only switches for R18 and non-R18 content visibility."""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.r18_enabled is not None:
        target.r18_enabled = payload.r18_enabled
    if payload.non_r18_enabled is not None:
        target.non_r18_enabled = payload.non_r18_enabled
    if payload.can_manage_visibility is not None:
        target.can_manage_visibility = payload.can_manage_visibility
    await db.commit()
    await db.refresh(target)
    return target


class ProxyConfigRequest(BaseModel):
    enabled: bool = False
    https_proxy: str = ""
    http_proxy: str = ""


@router.get("/proxy")
async def admin_get_proxy(current_user: User = Depends(require_admin)):
    cfg = get_proxy_config()
    return ProxyConfigRequest(
        enabled=cfg.enabled,
        https_proxy=cfg.https_proxy or "",
        http_proxy=cfg.http_proxy or "",
    )


@router.put("/proxy")
async def admin_update_proxy(
    payload: ProxyConfigRequest,
    current_user: User = Depends(require_admin),
):
    cfg = ProxyConfig(
        enabled=payload.enabled,
        https_proxy=payload.https_proxy,
        http_proxy=payload.http_proxy,
    )
    set_proxy_config(cfg)
    return payload

