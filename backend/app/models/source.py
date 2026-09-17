from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, Index
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

    #: Minimum gap between two upstream requests of this source, in seconds.
    #: ``None`` keeps the source's own ``concurrentRate`` (falling back to
    #: ``CRAWL_DELAY_MS``); ``0`` explicitly means "no throttle".  Sites like
    #: 搬山人 answer with a captcha when polled faster than their 拉取间隔, and
    #: the value a source ships with is often wrong, so it is overridable per
    #: source from the admin UI instead of being hardcoded per site.
    sync_interval_seconds = Column(
        Integer,
        nullable=True,
    )

    __table_args__ = (
        Index("ix_sources_owner_id", "owner_id"),
    )

    submitter = relationship("User", lazy="joined", foreign_keys=[submitter_id])

    @property
    def submitter_username(self) -> str | None:
        return self.submitter.username if self.submitter else None
