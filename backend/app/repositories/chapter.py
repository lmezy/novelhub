from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chapter import Chapter
from app.repositories.base import BaseRepository


class ChapterRepository(BaseRepository[Chapter]):
    model = Chapter

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def list_by_book(self, book_id: str) -> list[Chapter]:
        result = await self.db.scalars(
            select(Chapter)
            .where(Chapter.book_id == book_id)
            .order_by(Chapter.chapter_number.asc())
        )
        return list(result)

    async def get_by_source(self, book_id: str, source_chapter_id: str) -> Chapter | None:
        return await self.db.scalar(
            select(Chapter).where(
                Chapter.book_id == book_id,
                Chapter.source_chapter_id == source_chapter_id,
            )
        )
