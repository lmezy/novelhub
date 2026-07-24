from pydantic import BaseModel


class SyncRequest(BaseModel):
    source_id: str
    url: str


class SyncResult(BaseModel):
    book_id: str
    created_chapters: int
    skipped_chapters: int
