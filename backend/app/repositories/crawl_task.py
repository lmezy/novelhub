from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crawl_task import CrawlTask
from app.repositories.base import BaseRepository


class CrawlTaskRepository(BaseRepository[CrawlTask]):
    model = CrawlTask

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def list_recent(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[CrawlTask]:
        query = select(CrawlTask)
        if user_id is not None:
            query = query.where(CrawlTask.user_id == user_id)
        result = await self.db.scalars(
            query.order_by(CrawlTask.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result)

    async def list_by_status(self, status: str) -> list[CrawlTask]:
        result = await self.db.scalars(
            select(CrawlTask).where(CrawlTask.status == status)
        )
        return list(result)
