from typing import Generic, Optional, Sequence, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, id: str) -> Optional[ModelT]:
        return await self.db.get(self.model, id)

    async def list(self, *, offset: int = 0, limit: int = 50) -> Sequence[ModelT]:
        result = await self.db.scalars(
            select(self.model).offset(offset).limit(limit)
        )
        return list(result)

    async def count(self) -> int:
        result = await self.db.scalar(select(func.count()).select_from(self.model))
        return result or 0

    async def add(self, instance: ModelT) -> ModelT:
        self.db.add(instance)
        await self.db.flush()
        return instance

    async def delete(self, instance: ModelT) -> None:
        await self.db.delete(instance)
        await self.db.flush()
