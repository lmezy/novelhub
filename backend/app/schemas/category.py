from datetime import datetime
from pydantic import BaseModel


class CategoryCreate(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None


class CategoryOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    color: str | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class BookCategoryAssign(BaseModel):
    book_id: str
    category_ids: list[str]
