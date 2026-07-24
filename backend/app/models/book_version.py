from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.sql import func

from .base import Base


class BookVersion(Base):
    __tablename__ = "book_versions"

    id = Column(String, primary_key=True)
    chapter_id = Column(String, ForeignKey("chapters.id"))
    content_hash = Column(String(128))
    change_type = Column(String(32))
    content_path = Column(String(500))
    created_at = Column(DateTime, server_default=func.now())
