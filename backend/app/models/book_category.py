from sqlalchemy import Column, ForeignKey, Index, String
from sqlalchemy.orm import relationship
from .base import Base


class BookCategory(Base):
    __tablename__ = "book_categories"
    # The composite primary key is ``(book_id, category_id)``, so a lookup *by
    # category* (the library page's "per category" sections, and browsing one
    # category) had no index to use: Postgres fell back to a sequential scan for
    # every category.  This is the reverse order, which makes those lookups index
    # scans.
    __table_args__ = (
        Index("ix_book_categories_category_id", "category_id", "book_id"),
    )

    book_id = Column(String, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    category_id = Column(String, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    category = relationship("Category", lazy="joined")
