from fastapi import Depends, HTTPException

from app.models.user import User
from app.services.auth import get_current_user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
