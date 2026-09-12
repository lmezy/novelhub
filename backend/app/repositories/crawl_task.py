from sqlalchemy import select
from sqlalchemy import case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crawl_task import CrawlTask
from app.repositories.base import BaseRepository


class CrawlTaskRepository(BaseRepository[CrawlTask]):
    model = CrawlTask

    #: Order of the "recent tasks" list: what is running now, then what broke.
    #: Every other status keeps the plain newest-first order below.
    STATUS_RANK = {"running": 0, "failed": 1}

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
        rank = case(
            *[
                (CrawlTask.status == status, value)
                for status, value in self.STATUS_RANK.items()
            ],
            else_=len(self.STATUS_RANK),
        )
        result = await self.db.scalars(
            query.order_by(rank, CrawlTask.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result)

    async def list_by_status(self, status: str) -> list[CrawlTask]:
        result = await self.db.scalars(
            select(CrawlTask).where(CrawlTask.status == status)
        )
        return list(result)
