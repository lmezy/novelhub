from pydantic import BaseModel


class ChapterOut(BaseModel):
    id: str
    title: str | None = None
    chapter_number: int
    source_chapter_id: str | None = None
    content_path: str

    class Config:
        from_attributes = True


class ChapterContentOut(ChapterOut):
    content: str
