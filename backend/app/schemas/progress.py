from datetime import datetime

from pydantic import BaseModel


class ReadingProgressUpsert(BaseModel):
    user_id: str
    book_id: str
    chapter_id: str
    position: int = 0


class ReadingProgressOut(BaseModel):
    id: str
    user_id: str
    book_id: str
    chapter_id: str
    position: int
    updated_at: datetime

    class Config:
        from_attributes = True
