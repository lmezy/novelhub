from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import naive_now
from app.models.cookie import Cookie
from app.repositories.base import BaseRepository


class CookieRepository(BaseRepository[Cookie]):
    model = Cookie

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def get_by_source(self, source: str) -> Cookie | None:
        return await self.db.scalar(
            select(Cookie).where(Cookie.source == source)
        )

    async def list_active(self) -> list[Cookie]:
        # ``cookies.expired_at`` is a naive ``DateTime`` holding *local* wall
        # clock time, so the bound parameter has to be naive local too.
        now = naive_now()
        result = await self.db.scalars(
            select(Cookie).where(
                Cookie.expired_at.is_(None) | (Cookie.expired_at > now)
            )
        )
        return list(result)
