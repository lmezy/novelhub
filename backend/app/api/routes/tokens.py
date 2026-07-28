"""API Token management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.services.auth import get_current_user
from app.services.token_service import TokenService

router = APIRouter(prefix="/tokens", tags=["tokens"])


class TokenCreateRequest(BaseModel):
    name: str = Field(..., description="Label for this token")
    expires_days: int | None = Field(default=None, ge=1, le=365)


class TokenCreateResponse(BaseModel):
    id: str
    token: str
    prefix: str
    message: str = "Save this token now -- it will not be shown again."


class TokenOut(BaseModel):
    id: str
    name: str
    prefix: str
    is_active: bool
    last_used_at: str | None
    created_at: str
    expires_at: str | None


@router.post("", response_model=TokenCreateResponse, status_code=201)
async def create_token(payload: TokenCreateRequest,
                        user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    svc = TokenService(db)
    token_id, raw = await svc.create_token(user.id, payload.name, payload.expires_days)
    token = await db.get(ApiToken, token_id)
    return {"id": token_id, "token": raw, "prefix": token.token_prefix}  # type: ignore


@router.get("", response_model=list[TokenOut])
async def list_tokens(user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    svc = TokenService(db)
    return await svc.list_tokens(user.id)


@router.delete("/{token_id}", status_code=204)
async def revoke_token(token_id: str,
                        user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    svc = TokenService(db)
    if not await svc.revoke_token(token_id):
        raise HTTPException(status_code=404, detail="Token not found")
