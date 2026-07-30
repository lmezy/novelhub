from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.category import Category
from app.models.book_category import BookCategory
from uuid import uuid4


class CategoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[Category]:
        result = await self.db.scalars(select(Category).order_by(Category.name))
        return list(result)

    async def get(self, category_id: str) -> Category | None:
        return await self.db.get(Category, category_id)

    async def get_by_name(self, name: str) -> Category | None:
        return await self.db.scalar(select(Category).where(Category.name == name))

    async def create(self, name: str, description: str | None = None, color: str | None = None) -> Category:
        cat = Category(id=str(uuid4()), name=name, description=description, color=color)
        self.db.add(cat)
        await self.db.commit()
        await self.db.refresh(cat)
        return cat

    async def delete(self, category_id: str) -> bool:
        cat = await self.get(category_id)
        if cat is None:
            return False
        await self.db.delete(cat)
        await self.db.commit()
        return True

    async def get_book_categories(self, book_id: str) -> list[Category]:
        result = await self.db.scalars(
            select(Category)
            .join(BookCategory, BookCategory.category_id == Category.id)
            .where(BookCategory.book_id == book_id)
            .order_by(Category.name)
        )
        return list(result)

    async def set_book_categories(self, book_id: str, category_ids: list[str]) -> None:
        await self.db.execute(delete(BookCategory).where(BookCategory.book_id == book_id))
        for cid in category_ids:
            bc = BookCategory(book_id=book_id, category_id=cid)
            self.db.add(bc)
        await self.db.commit()
