from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from .base import Base


class AIBookInsight(Base):
    """One saved AI analysis of one book (question + answer).

    Shared by every reader of that book: when a similar question is asked again
    the saved answer is fed back into the prompt instead of starting from zero.
    ``stale`` marks an analysis whose chapter was re-fetched, so the model knows
    to re-check it against the new text.
    """

    __tablename__ = "ai_book_insights"
    __table_args__ = (
        Index("ix_ai_book_insights_book_updated", "book_id", "updated_at"),
        Index("ix_ai_book_insights_chapter", "chapter_id"),
    )

    id = Column(String, primary_key=True)
    book_id = Column(
        String, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id = Column(
        String, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    user_id = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    #: Which model produced the answer (informational; a model switch does not
    #: invalidate the analysis, it is re-checked against the text anyway).
    answered_by = Column(String(128), nullable=False, default="")
    hit_count = Column(Integer, nullable=False, default=0)
    stale = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())
