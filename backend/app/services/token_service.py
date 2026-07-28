"""API Token management -- generate, verify, revoke."""

import hashlib
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.api_token import ApiToken


class TokenService:
    TOKEN_PREFIX = "nh_"

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_token(self, user_id: str, name: str,
                            expires_days: int | None = None) -> tuple[str, str]:
        """Generate a new API token. Returns (token_id, plaintext_token)."""
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token_prefix = self.TOKEN_PREFIX + raw[:8]

        expires_at = None
        if expires_days:
            expires_at = datetime.now(timezone.utc).replace(
                hour=23, minute=59, second=59
            )
            from datetime import timedelta
            expires_at += timedelta(days=expires_days)

        token = ApiToken(
            id=str(uuid4()),
            user_id=user_id,
            name=name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            expires_at=expires_at,
        )
        self.db.add(token)
        await self.db.commit()
        logger.info("Created API token {} for user {}", token_prefix, user_id)
        return token.id, raw

    async def verify_token(self, raw_token: str) -> ApiToken | None:
        """Verify a raw token string. Returns the ApiToken if valid."""
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token = await self.db.scalar(
            select(ApiToken).where(ApiToken.token_hash == token_hash)
        )
        if token is None or not token.is_active:
            return None
        if token.expires_at and token.expires_at < datetime.now(timezone.utc):
            return None
        token.last_used_at = datetime.now(timezone.utc)
        self.db.add(token)
        await self.db.commit()
        return token

    async def list_tokens(self, user_id: str) -> list[ApiToken]:
        result = await self.db.scalars(
            select(ApiToken).where(ApiToken.user_id == user_id)
        )
        return list(result)

    async def revoke_token(self, token_id: str) -> bool:
        token = await self.db.get(ApiToken, token_id)
        if token is None:
            return False
        token.is_active = False
        await self.db.commit()
        return True
