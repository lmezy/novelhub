from pydantic import BaseModel


class SyncRequest(BaseModel):
    source_id: str
    url: str


class SyncResult(BaseModel):
    book_id: str
    created_chapters: int
    skipped_chapters: int


class BookshelfSyncRequest(BaseModel):
    source_id: str


class BookshelfSyncResult(BaseModel):
    source_id: str
    total: int
    results: list[dict]
