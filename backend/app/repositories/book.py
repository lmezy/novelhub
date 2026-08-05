from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import Book
from app.repositories.base import BaseRepository


class BookRepository(BaseRepository[Book]):
    model = Book

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def get_by_source(self, source_id: str, source_book_id: str) -> Book | None:
        return await self.db.scalar(
            select(Book).where(
                Book.source_id == source_id,
                Book.source_book_id == source_book_id,
            )
        )

    async def list_recent(self, *, offset: int = 0, limit: int = 20) -> list[Book]:
        return (
            await self.db.scalars(
                select(Book)
                .order_by(Book.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).unique().all()
