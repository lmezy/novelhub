from sqlalchemy import Column, ForeignKey, String

from .base import Base


class BookTag(Base):
    __tablename__ = "book_tags"

    book_id = Column(String, ForeignKey("books.id"), primary_key=True)
    tag_id = Column(String, ForeignKey("tags.id"), primary_key=True)
