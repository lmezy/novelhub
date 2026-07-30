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


class DiscoverRequest(BaseModel):
    source_id: str
    url: str | None = None
    page: int = 1
    sync: bool = True


class DiscoverResult(BaseModel):
    source_id: str
    books_found: int
    books_synced: int
    details: list[dict]
