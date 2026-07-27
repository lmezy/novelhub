from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source import Source
from app.repositories.base import BaseRepository


class SourceRepository(BaseRepository[Source]):
    model = Source

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def list_enabled(self) -> list[Source]:
        result = await self.db.scalars(
            select(Source).where(Source.enabled == True).order_by(Source.name.asc())
        )
        return list(result)
