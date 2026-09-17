from dataclasses import replace
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User, Source, SourceChange
from app.schemas.user import AdminUserOut, UserOut
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
from app.services.ai_client import (
    AIError,
    LLMClient,
    diagnose,
    extract_supported_models,
    models_endpoint,
)
from app.services.ai_config import (
    MASKED_KEY,
    get_ai_config,
    set_ai_config,
)
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
from app.services.invite import generate_nickname

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(select(User).order_by(User.created_at.desc()))
    users = list(result)
    if user.role != "super_admin":
        for target in users:
            target.invite_tag = None
    return users


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
        nickname=generate_nickname(),
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
            payload.interval_hours,
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


# ---------------------------------------------------------------------------
# AI provider settings
#
# Stored in app_settings (see app.services.ai_config) so the model/key can be
# changed without rebuilding the container.  The API key is encrypted at rest
# and never returned; the UI only learns whether a key is stored and a masked
# hint.
# ---------------------------------------------------------------------------


class AIConfigUpdate(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None
    use_proxy: bool | None = None
    proxy_url: str | None = None
    context_chars: int | None = None
    rag_enabled: bool | None = None
    rag_top_k: int | None = None
    embedding_provider: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None


def _config_payload(payload: BaseModel) -> dict:
    values = payload.model_dump(exclude_unset=True)
    # ``None`` means "not sent"; the mask means "unchanged".
    for key in ("api_key", "embedding_api_key"):
        if values.get(key) == MASKED_KEY:
            values.pop(key)
    return values


@router.get("/ai")
async def admin_get_ai(db: AsyncSession = Depends(get_db),
                       current_user: User = Depends(require_admin)):
    """Current AI configuration (never includes the API key)."""
    cfg = await get_ai_config(db)
    payload = cfg.public_dict()
    proxy = get_proxy_config()
    payload["crawler_proxy"] = {
        "enabled": proxy.enabled,
        "url": proxy.https_proxy or proxy.http_proxy or "",
    }
    return payload


@router.put("/ai")
async def admin_update_ai(payload: AIConfigUpdate,
                          db: AsyncSession = Depends(get_db),
                          current_user: User = Depends(require_admin)):
    """Update AI settings; empty strings clear a field."""
    if payload.temperature is not None and not 0 <= payload.temperature <= 2:
        raise HTTPException(status_code=422, detail="temperature 必须在 0 到 2 之间")
    if payload.max_tokens is not None and not 1 <= payload.max_tokens <= 100000:
        raise HTTPException(status_code=422, detail="max_tokens 必须在 1 到 100000 之间")
    if payload.timeout is not None and not 5 <= payload.timeout <= 900:
        raise HTTPException(status_code=422, detail="timeout 必须在 5 到 900 秒之间")
    if payload.rag_top_k is not None and not 1 <= payload.rag_top_k <= 20:
        raise HTTPException(status_code=422, detail="rag_top_k 必须在 1 到 20 之间")
    if payload.context_chars is not None and not 2000 <= payload.context_chars <= 200000:
        raise HTTPException(status_code=422, detail="context_chars 必须在 2000 到 200000 之间")

    cfg = await set_ai_config(db, _config_payload(payload))
    return cfg.public_dict()


@router.post("/ai/test")
async def admin_test_ai(test_embeddings: bool = True,
                        db: AsyncSession = Depends(get_db),
                        current_user: User = Depends(require_admin)):
    """Round-trip a tiny completion (and an embedding) through the provider."""
    cfg = await get_ai_config(db)
    return await diagnose(cfg, test_embeddings=test_embeddings)


class AIModelsRequest(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    #: Empty keeps the stored key, so the admin can list models before saving.
    api_key: str | None = None


@router.post("/ai/models")
async def admin_ai_models(payload: AIModelsRequest | None = None,
                          db: AsyncSession = Depends(get_db),
                          current_user: User = Depends(require_admin)):
    """List the model ids the configured (or not-yet-saved) endpoint serves."""
    cfg = await get_ai_config(db)
    payload = payload or AIModelsRequest()
    overrides: dict = {}
    if payload.provider:
        overrides["provider"] = payload.provider
    if payload.base_url is not None and payload.base_url.strip():
        overrides["base_url"] = payload.base_url.strip()
    if payload.api_key:
        overrides["api_key"] = payload.api_key
    if overrides:
        cfg = replace(cfg, **overrides)

    client = LLMClient(cfg)
    try:
        models = await client.list_models()
    except AIError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "models": extract_supported_models(str(exc)),
            "base_url": cfg.effective_base_url,
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "error": str(exc) or type(exc).__name__, "models": []}

    return {
        "ok": True,
        "models": models,
        "base_url": cfg.effective_base_url,
        "endpoint": models_endpoint(cfg.effective_base_url, cfg.kind),
    }

