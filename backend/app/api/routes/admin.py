from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User, Source, SourceChange
from app.schemas.user import UserOut
from app.schemas.admin import (
    UserContentVisibilityUpdate,
    UserRoleUpdate,
    UserR18Update,
)
from app.services.auth import get_current_user, require_admin, require_super_admin
from app.services.proxy_config import get_proxy_config, ProxyConfig, set_proxy_config

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserOut])
async def list_users(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(select(User).order_by(User.created_at.desc()))
    return list(result)


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

