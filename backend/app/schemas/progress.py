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
    # ``reading_progress.chapter_id`` is ``ON DELETE SET NULL``: re-syncing a book
    # deletes its stale chapters and clears the pointer, so this really can be
    # NULL.  Declaring it ``str`` made every ``GET /progress`` answer a 500
    # ("Input should be a valid string") for every user holding such a row.
    chapter_id: str | None = None
    position: int
    updated_at: datetime

    class Config:
        from_attributes = True
