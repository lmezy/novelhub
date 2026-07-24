from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from .base import Base


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("source_id", "source_book_id", name="uq_books_source_external_id"),
    )

    id = Column(String, primary_key=True)
    source_id = Column(String, ForeignKey("sources.id"))
    author_id = Column(String, ForeignKey("authors.id"))
    source_book_id = Column(String(255))
    title = Column(String(255), nullable=False)
    cover = Column(String(255))
    description = Column(Text)
    status = Column(String(32))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
