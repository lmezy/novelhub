from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from .base import Base


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("book_id", "source_chapter_id", name="uq_chapters_book_external_id"),
        # Chapter lists / EPUB export filter by book_id and order by
        # chapter_number; the unique index above cannot serve the sort.
        Index("ix_chapters_book_id_number", "book_id", "chapter_number"),
    )

    id = Column(String, primary_key=True)
    book_id = Column(String, ForeignKey("books.id"))
    chapter_number = Column(Integer)
    source_chapter_id = Column(String(500))
    title = Column(String(255))
    content_path = Column(String(500))
    hash = Column(String(128))
    created_at = Column(DateTime, server_default=func.now())
