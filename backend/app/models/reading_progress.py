from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from .base import Base


class ReadingProgress(Base):
    __tablename__ = "reading_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "book_id", name="uq_reading_progress_user_book"),
    )

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"))
    book_id = Column(String, ForeignKey("books.id"))
    chapter_id = Column(String, ForeignKey("chapters.id"))
    position = Column(Integer, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
