"""API Token model for programmatic access."""

from sqlalchemy import Column, DateTime, ForeignKey, String, Boolean
from sqlalchemy.sql import func

from .base import Base


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100))
    token_hash = Column(String(128))
    token_prefix = Column(String(8))  # e.g. "nh_abc123" for display
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)
