from pydantic import BaseModel


class ChapterOut(BaseModel):
    id: str
    title: str | None = None
    chapter_number: int
    source_chapter_id: str | None = None
    content_path: str
    # Content digest: the reader keys its client-side chunk cache on it, so a
    # re-synced (changed) chapter never serves a stale, shorter body.
    hash: str | None = None

    class Config:
        from_attributes = True


class ChapterContentOut(ChapterOut):
    content: str
