from datetime import datetime

from pydantic import BaseModel


class BookmarkCreate(BaseModel):
    book_id: str
    chapter_id: str
    position: int = 0
    note: str | None = None


class BookmarkUpdate(BaseModel):
    note: str | None = None
    position: int | None = None


class BookmarkOut(BaseModel):
    id: str
    user_id: str
    book_id: str
    chapter_id: str
    position: int
    note: str | None
    created_at: datetime
    chapter_title: str | None = None
    chapter_number: int | None = None
    book_title: str | None = None

    class Config:
        from_attributes = True
