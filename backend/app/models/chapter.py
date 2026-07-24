from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from .base import Base


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("book_id", "source_chapter_id", name="uq_chapters_book_external_id"),
    )

    id = Column(String, primary_key=True)
    book_id = Column(String, ForeignKey("books.id"))
    chapter_number = Column(Integer)
    source_chapter_id = Column(String(255))
    title = Column(String(255))
    content_path = Column(String(500))
    hash = Column(String(128))
    created_at = Column(DateTime, server_default=func.now())
