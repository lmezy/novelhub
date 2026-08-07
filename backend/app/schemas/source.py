from pydantic import BaseModel


class SourceCreate(BaseModel):
    id: str
    name: str
    url: str | None = None
    plugin_name: str
    enabled: bool = True
    is_r18: bool = False
    config: dict | None = None
    scope: str = "personal"


class SourceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    plugin_name: str | None = None
    enabled: bool | None = None
    is_r18: bool | None = None
    config: dict | None = None


class SourceOut(BaseModel):
    id: str
    name: str
    url: str | None = None
    plugin_name: str | None = None
    enabled: bool
    is_r18: bool = False
    config: dict | None = None
    owner_id: str | None = None
    submitter_id: str | None = None
    submitter_username: str | None = None
    show_contributor: bool = True

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
