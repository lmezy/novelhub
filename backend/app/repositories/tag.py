from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag
from app.repositories.base import BaseRepository


class TagRepository(BaseRepository[Tag]):
    model = Tag

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def get_by_name(self, name: str) -> Tag | None:
        return await self.db.scalar(select(Tag).where(Tag.name == name))
