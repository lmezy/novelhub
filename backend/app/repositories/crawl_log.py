from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crawl_log import CrawlLog
from app.repositories.base import BaseRepository


class CrawlLogRepository(BaseRepository[CrawlLog]):
    model = CrawlLog

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def list_by_task(self, task_id: str, *, offset: int = 0, limit: int = 100) -> list[CrawlLog]:
        result = await self.db.scalars(
            select(CrawlLog)
            .where(CrawlLog.task_id == task_id)
            .order_by(CrawlLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result)
