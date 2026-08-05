from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class BookshelfGroup(Base):
    __tablename__ = "bookshelf_groups"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_bookshelf_groups_user_name"),
        Index("ix_bookshelf_groups_user_id", "user_id"),
    )

    id = Column(String, primary_key=True)
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(50), nullable=False)
    order = Column(Integer, default=0, nullable=False)
    show = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", lazy="joined")


class BookFavoriteGroup(Base):
    __tablename__ = "book_favorite_groups"
    __table_args__ = (
        Index("ix_book_favorite_groups_favorite_id", "favorite_id"),
        Index("ix_book_favorite_groups_group_id", "group_id"),
    )

    favorite_id = Column(
        String,
        ForeignKey("book_favorites.id", ondelete="CASCADE"),
        primary_key=True,
    )
    group_id = Column(
        String,
        ForeignKey("bookshelf_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at = Column(DateTime, server_default=func.now())
