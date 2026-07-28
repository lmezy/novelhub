"""Source credentials for auto-login fallback when cookies expire."""

from sqlalchemy import Column, String, Text, DateTime, Boolean
from sqlalchemy.sql import func
from .base import Base


class SourceCredential(Base):
    __tablename__ = "source_credentials"

    id = Column(String, primary_key=True)
    source = Column(String(100), nullable=False, unique=True)
    username = Column(String(255), nullable=False)
    password_encrypted = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())