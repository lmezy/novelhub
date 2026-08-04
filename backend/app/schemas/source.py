from pydantic import BaseModel


class SourceCreate(BaseModel):
    id: str
    name: str
    url: str | None = None
    plugin_name: str
    enabled: bool = True
    config: dict | None = None


class SourceOut(BaseModel):
    id: str
    name: str
    url: str | None = None
    plugin_name: str | None = None
    enabled: bool
    config: dict | None = None

    class Config:
        from_attributes = True


class RemoteBookSearchResult(BaseModel):
    source_id: str
    source_name: str
    name: str
    author: str = "Unknown"
    url: str
    cover_url: str | None = None
    intro: str | None = None
    kind: str | None = None
    latest_chapter: str | None = None
    word_count: str | None = None
    in_library: bool = False
    book_id: str | None = None


class RemoteBookSearchOut(BaseModel):
    source_id: str
    source_name: str
    query: str
    page: int
    total: int
    results: list[RemoteBookSearchResult]
