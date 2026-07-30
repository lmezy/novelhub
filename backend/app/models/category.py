from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.sql import func
from .base import Base


class Category(Base):
    __tablename__ = "categories"

    id = Column(String, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text)
    color = Column(String(7))
    created_at = Column(DateTime, server_default=func.now())
