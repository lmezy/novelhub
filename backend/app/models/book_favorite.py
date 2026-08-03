from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.sql import func

from .base import Base


class BookFavorite(Base):
    __tablename__ = "book_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "book_id", name="uq_book_favorites_user_book"),
    )

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    book_id = Column(String, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
