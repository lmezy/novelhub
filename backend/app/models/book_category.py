from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.orm import relationship
from .base import Base


class BookCategory(Base):
    __tablename__ = "book_categories"

    book_id = Column(String, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    category_id = Column(String, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    category = relationship("Category", lazy="joined")
