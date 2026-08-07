from datetime import datetime
from pydantic import BaseModel


class SourceChangeCreate(BaseModel):
    action: str  # 'create' or 'delete'
    source_id: str | None = None  # for delete
    source_data: dict | None = None  # {id, name, url, plugin_name, config} for create


class SourceChangeOut(BaseModel):
    id: str
    user_id: str
    submitter_username: str | None = None
    action: str
    source_id: str | None = None
    source_data: dict | None = None
    status: str
    reviewer_id: str | None = None
    review_note: str | None = None
    created_at: datetime | None = None
    reviewed_at: datetime | None = None

    class Config:
        from_attributes = True


class SourceChangeReview(BaseModel):
    action: str  # 'approve' or 'reject'
    note: str | None = None
