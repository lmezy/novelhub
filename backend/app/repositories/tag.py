from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag
from app.repositories.base import BaseRepository


class TagRepository(BaseRepository[Tag]):
    model = Tag

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def get_by_name(self, name: str) -> Tag | None:
        return await self.db.scalar(select(Tag).where(Tag.name == name))

    async def get_or_create(self, name: str) -> Tag:
        tag = await self.get_by_name(name)
        if tag is not None:
            return tag

        stmt = (
            pg_insert(Tag)
            .values(id=str(uuid4()), name=name)
            .on_conflict_do_nothing(index_elements=[Tag.name])
        )
        await self.db.execute(stmt)
        await self.db.flush()

        tag = await self.get_by_name(name)
        if tag is not None:
            return tag

        tag = Tag(id=str(uuid4()), name=name)
        self.db.add(tag)
        await self.db.flush()
        return tag
