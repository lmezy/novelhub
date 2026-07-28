from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.services.jwt import decode_token


bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        payload = decode_token(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def get_current_user_or_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate via API token OR JWT bearer."""
    if x_api_token:
        from app.services.token_service import TokenService
        api_token = await TokenService(db).verify_token(x_api_token)
        if api_token:
            user = await db.get(User, api_token.user_id)
            if user:
                return user
        raise HTTPException(status_code=401, detail="Invalid API token")

    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing authentication")

    try:
        payload = decode_token(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
