from sqlalchemy import Column, String, Boolean, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB

from .base import Base


class Source(Base):

    __tablename__ = "sources"

    id = Column(
        String,
        primary_key=True
    )

    name = Column(
        String(100),
        nullable=False
    )

    url = Column(
        String(255)
    )

    plugin_name = Column(
        String(100)
    )

    enabled = Column(
        Boolean,
        default=True
    )

    is_r18 = Column(
        Boolean,
        default=False,
        nullable=False
    )

    config = Column(
        JSONB,
        nullable=True
    )

    owner_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True
    )

    __table_args__ = (
        Index("ix_sources_owner_id", "owner_id"),
    )
