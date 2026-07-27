from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.author import Author
from app.repositories.base import BaseRepository


class AuthorRepository(BaseRepository[Author]):
    model = Author

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def get_by_name(self, name: str) -> Author | None:
        return await self.db.scalar(select(Author).where(Author.name == name))
