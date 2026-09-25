from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.services.jwt import decode_token


bearer_scheme = HTTPBearer(auto_error=False)

# ``<img src="/api/chapters/…">`` cannot send an Authorization header, so a
# chapter's in-content images would answer 401 for every reader even though
# the HTML around them is authenticated.  The login endpoints therefore also
# drop the same JWT into an HttpOnly cookie scoped to the chapter routes, and
# those routes accept either credential.
MEDIA_COOKIE_NAME = "novelhub_media"
MEDIA_COOKIE_PATH = "/api/chapters"


async def _user_from_jwt(token: str, db: AsyncSession) -> User | None:
    """Resolve a JWT (bearer or cookie) to its user, or None when invalid."""
    if not token:
        return None
    try:
        payload = decode_token(token)
    except JWTError:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return await db.get(User, user_id)


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


async def get_current_user_media(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate a media request via bearer token or the media cookie.

    Chapter images are embedded as plain ``<img>`` tags, which browsers fetch
    without the ``Authorization`` header the rest of the API uses.  Accepting
    the login cookie for these read-only routes keeps the images behind the
    same visibility rules as the chapter text itself.
    """
    token = credentials.credentials if credentials is not None else ""
    if not token:
        token = request.cookies.get(MEDIA_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization")

    user = await _user_from_jwt(token, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid authorization")
    return user


async def get_current_user_or_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate via API token OR JWT bearer.

    Used by the **read-only** library surface only (book list/detail, chapter
    list/body, search) so that a token pasted into a third-party client can
    read the library as its owner.  Everything that writes, everything that
    touches one user's own rows (bookmarks, progress, bookshelf, cookies) and
    every admin route stays on :func:`get_current_user` / :func:`require_admin`:
    a leaked token must not be able to change or delete anything.
    """
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
    if user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_super_admin(
    user: User = Depends(get_current_user),
) -> User:
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return user
