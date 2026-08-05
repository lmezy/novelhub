from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class CustomTag(Base):
    __tablename__ = "custom_tags"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_custom_tags_owner_name"),
        Index("ix_custom_tags_name", "name"),
    )

    id = Column(String, primary_key=True)
    owner_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(50), nullable=False)
    is_public = Column(Boolean, default=False, nullable=False)
    show_user = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    owner = relationship("User", lazy="joined")


class BookCustomTag(Base):
    __tablename__ = "book_custom_tags"
    __table_args__ = (
        Index("ix_book_custom_tags_book_id", "book_id"),
        Index("ix_book_custom_tags_custom_tag_id", "custom_tag_id"),
    )

    book_id = Column(
        String,
        ForeignKey("books.id", ondelete="CASCADE"),
        primary_key=True,
    )
    custom_tag_id = Column(
        String,
        ForeignKey("custom_tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at = Column(DateTime, server_default=func.now())

    custom_tag = relationship("CustomTag", lazy="joined")
