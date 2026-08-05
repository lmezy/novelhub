from datetime import datetime

from pydantic import BaseModel


class AuthorOut(BaseModel):
    id: str
    name: str
    description: str | None = None

    class Config:
        from_attributes = True


class BookCreate(BaseModel):
    title: str
    author_id: str | None = None
    source_id: str | None = None
    source_book_id: str | None = None
    description: str | None = None
    status: str | None = "unknown"


class ManualChapterIn(BaseModel):
    title: str = ""
    content: str = ""


class ManualBookCreate(BaseModel):
    title: str
    author: str = "未知作者"
    description: str | None = None
    status: str = "ongoing"
    tags: list[str] = []
    chapters: list[ManualChapterIn]


class CustomTagUserOut(BaseModel):
    id: str
    username: str


class CustomTagOnBookOut(BaseModel):
    id: str
    name: str
    is_public: bool = False
    show_user: bool = True
    count: int = 1
    applied_by_me: bool = False
    users: list[CustomTagUserOut] = []


class BookOut(BaseModel):
    id: str
    title: str
    author_id: str | None = None
    source_id: str | None = None
    source_book_id: str | None = None
    cover: str | None = None
    description: str | None = None
    status: str | None = None
    is_r18: bool = False
    is_favorite: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True
    tag_names: list[str] = []
    author_name: str | None = None
    custom_tags: list[CustomTagOnBookOut] = []
    shelf_group_ids: list[str] = []


class BookSourceAlternate(BaseModel):
    id: str
    source_id: str | None = None
    source_name: str | None = None
    source_book_id: str | None = None
    title: str
    author_name: str | None = None
    status: str | None = None
    chapter_count: int = 0
    updated_at: datetime | None = None
    is_current: bool = False


class BookSourceAlternatesOut(BaseModel):
    book_id: str
    sources: list[BookSourceAlternate]
