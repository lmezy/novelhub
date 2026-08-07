from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.sql import func

from .base import Base


class Invite(Base):
    __tablename__ = "invites"
    __table_args__ = (
        Index("ix_invites_code", "code", unique=True),
        Index("ix_invites_created_by", "created_by"),
    )

    id = Column(String, primary_key=True)
    code = Column(String(32), nullable=False)
    created_by = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    expires_at = Column(DateTime, nullable=False)
    used_by = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
