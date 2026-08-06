from datetime import datetime
from pydantic import BaseModel


class CategoryCreate(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    is_r18: bool = False


class CategoryOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    color: str | None = None
    is_r18: bool = False
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class BookCategoryAssign(BaseModel):
    book_id: str
    category_ids: list[str]
