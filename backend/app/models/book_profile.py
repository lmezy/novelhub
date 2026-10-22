"""Book profile rows for cross-book semantic discovery.

The ``embedding`` column is a pgvector type; it is deliberately **not** mapped
here.  The application only ever writes it through raw SQL (``CAST(:embedding AS
vector)``, see ``app/services/book_profiles.py``) and reads it back as text, so
the backend does not need the ``pgvector`` Python driver in the image -- and an
install whose database has the table but not the extension still starts.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from .base import Base


class BookProfile(Base):
    __tablename__ = "book_profiles"

    book_id = Column(String, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True)
    segment = Column(Integer, primary_key=True, default=0)
    excerpt = Column(Text)
    model = Column(String(120), default="")
    updated_at = Column(DateTime, server_default=func.now())
