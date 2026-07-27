from datetime import datetime
from pydantic import BaseModel


class CrawlTaskOut(BaseModel):
    id: str
    source: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    created_at: datetime | None = None

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
