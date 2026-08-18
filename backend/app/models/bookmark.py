from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from .base import Base


class Bookmark(Base):
    """A user bookmark pinned to a chapter + reading position of a book.

    Mirrors the Legado (YueDu) bookmark concept: a marker created while
    reading, keeping the chapter and the position inside it so the user can
    jump back later. `position` is a 0-100 percent of the chapter, matching
    the existing ReadingProgress semantics used by the web reader.
    """

    __tablename__ = "bookmarks"
    __table_args__ = (
        Index("ix_bookmarks_user_book", "user_id", "book_id"),
        Index("ix_bookmarks_chapter", "chapter_id"),
    )

    id = Column(String, primary_key=True)
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    book_id = Column(
        String,
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id = Column(
        String,
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    position = Column(Integer, default=0, nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
