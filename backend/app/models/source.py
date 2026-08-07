from sqlalchemy import Column, String, Boolean, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

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

    submitter_id = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    show_contributor = Column(
        Boolean,
        default=True,
        nullable=False,
    )

    __table_args__ = (
        Index("ix_sources_owner_id", "owner_id"),
    )

    submitter = relationship("User", lazy="joined", foreign_keys=[submitter_id])

    @property
    def submitter_username(self) -> str | None:
        return self.submitter.username if self.submitter else None
