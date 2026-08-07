from datetime import datetime
from pydantic import BaseModel
from typing import Any


class CrawlTaskOut(BaseModel):
    id: str
    source: str
    mode: str = "bookshelf"
    max_pages: int = 200
    priority: int = 0
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    resume_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    progress: dict[str, Any] | None = None
    created_at: datetime | None = None
    user_id: str | None = None

    class Config:
        from_attributes = True


class CrawlLogOut(BaseModel):
    id: str
    task_id: str | None = None
    level: str | None = None
    message: str | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True
