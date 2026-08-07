from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("source_id", "source_book_id", name="uq_books_source_external_id"),
    )

    id = Column(String, primary_key=True)
    source_id = Column(String, ForeignKey("sources.id"))
    author_id = Column(String, ForeignKey("authors.id"))
    owner_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    source_book_id = Column(String(255))
    title = Column(String(255), nullable=False)
    is_r18 = Column(Boolean, default=False, nullable=False)
    is_public = Column(Boolean, default=False, nullable=False)
    all_ages_confirmed = Column(Boolean, default=False, nullable=False)
    cover = Column(String(255))
    display_cover = Column(String(255))
    description = Column(Text)
    status = Column(String(32))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    tags = relationship("BookTag", lazy="joined")
    categories = relationship("BookCategory", lazy="selectin")
    custom_tags = relationship("BookCustomTag", lazy="selectin")

    @property
    def tag_names(self) -> list[str]:
        return [bt.tag.name for bt in self.tags if bt.tag]

    @property
    def category_names(self) -> list[str]:
        return [
            bc.category.name
            for bc in self.categories
            if bc.category and (self.is_r18 or not bc.category.is_r18)
        ]

    @property
    def custom_tag_names(self) -> list[str]:
        return [bct.custom_tag.name for bct in self.custom_tags if bct.custom_tag]

    author = relationship("Author", lazy="joined")
    @property
    def author_name(self) -> str | None:
        return self.author.name if self.author else None
