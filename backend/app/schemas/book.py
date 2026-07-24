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


class BookOut(BaseModel):
    id: str
    title: str
    author_id: str | None = None
    source_id: str | None = None
    source_book_id: str | None = None
    cover: str | None = None
    description: str | None = None
    status: str | None = None

    class Config:
        from_attributes = True
