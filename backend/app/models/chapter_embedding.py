"""Chapter embedding chunks for RAG semantic search."""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from .base import Base


class ChapterEmbedding(Base):
    __tablename__ = "chapter_embeddings"
    __table_args__ = (
        Index("ix_chapter_embeddings_chapter", "chapter_id"),
        Index("ix_chapter_embeddings_book", "book_id"),
    )

    id = Column(String, primary_key=True)
    book_id = Column(String, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(String, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, default=0)
    content = Column(Text)
    embedding = Column(JSONB)  # float list stored as JSON
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
