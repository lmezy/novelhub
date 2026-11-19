from datetime import datetime
from typing import Annotated
from pydantic import BaseModel, StringConstraints


class CategoryCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
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
