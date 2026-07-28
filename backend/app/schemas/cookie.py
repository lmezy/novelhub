from datetime import datetime
from pydantic import BaseModel


class CookieCreate(BaseModel):
    source: str
    cookie_data: str
    expired_at: datetime | None = None


class CookieUpdate(BaseModel):
    cookie_data: str | None = None
    expired_at: datetime | None = None


class CookieOut(BaseModel):
    id: str
    source: str
    cookie_data: str
    expired_at: datetime | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class CookieMaskedOut(CookieOut):
    cookie_data: str = "***encrypted***"
