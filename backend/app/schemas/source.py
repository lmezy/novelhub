from pydantic import BaseModel, Field

from app.services.source_interval import MAX_SYNC_INTERVAL_SECONDS


class SourceCreate(BaseModel):
    id: str
    name: str
    url: str | None = None
    plugin_name: str
    enabled: bool = True
    is_r18: bool = False
    config: dict | None = None
    scope: str = "personal"
    #: One upstream request per N seconds; ``None`` keeps the source's own
    #: ``concurrentRate``, ``0`` disables throttling for this source.
    sync_interval_seconds: int | None = Field(
        default=None,
        ge=0,
        le=MAX_SYNC_INTERVAL_SECONDS,
    )


class SourceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    plugin_name: str | None = None
    enabled: bool | None = None
    is_r18: bool | None = None
    config: dict | None = None
    scope: str | None = None
    sync_interval_seconds: int | None = Field(
        default=None,
        ge=0,
        le=MAX_SYNC_INTERVAL_SECONDS,
    )


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
    sync_interval_seconds: int | None = None

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
