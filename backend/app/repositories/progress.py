from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reading_progress import ReadingProgress
from app.repositories.base import BaseRepository


class ReadingProgressRepository(BaseRepository[ReadingProgress]):
    model = ReadingProgress

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def get_by_user_book(self, user_id: str, book_id: str) -> ReadingProgress | None:
        return await self.db.scalar(
            select(ReadingProgress).where(
                ReadingProgress.user_id == user_id,
                ReadingProgress.book_id == book_id,
            )
        )
