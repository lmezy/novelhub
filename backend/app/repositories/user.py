from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    def __init__(self, db: AsyncSession):
        super().__init__(db)

    async def get_by_username(self, username: str) -> User | None:
        return await self.db.scalar(select(User).where(User.username == username))
