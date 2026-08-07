from pydantic import BaseModel


class SyncRequest(BaseModel):
    source_id: str
    url: str


class SyncResult(BaseModel):
    book_id: str
    created_chapters: int
    skipped_chapters: int
    failed_chapters: list[dict] = []


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


class LocalScanRequest(BaseModel):
    path: str
    max_depth: int = 3


class LocalBookCandidate(BaseModel):
    path: str
    title: str
    author: str
    format: str = "markdown"
    description: str | None = None
    status: str | None = None
    tags: list[str] = []
    is_r18: bool = False
    categories: list[str] = []
    chapter_count: int
    has_metadata: bool = False


class LocalScanResult(BaseModel):
    root: str
    books: list[LocalBookCandidate]


class LocalImportRequest(BaseModel):
    path: str
    book_paths: list[str] | None = None


class LocalChapterRef(BaseModel):
    title: str
    path: str
    chapter_number: int


class LocalBookDetail(BaseModel):
    path: str
    title: str
    author: str
    format: str = "markdown"
    description: str | None = None
    status: str | None = None
    tags: list[str] = []
    is_r18: bool = False
    categories: list[str] = []
    chapter_count: int
    has_metadata: bool
    chapters: list[LocalChapterRef]


class LocalDirectResult(BaseModel):
    books: list[LocalBookDetail]


class LocalImportResultItem(BaseModel):
    path: str
    status: str
    book_id: str | None = None
    created_chapters: int = 0
    skipped_chapters: int = 0
    error: str | None = None


class LocalImportResult(BaseModel):
    results: list[LocalImportResultItem]


class LocalContentRequest(BaseModel):
    path: str


class LocalContentResult(BaseModel):
    path: str
    title: str
    content: str
