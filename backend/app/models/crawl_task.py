from sqlalchemy import (
    Column,
    String,
    DateTime,
    Integer,
    Text,
    ForeignKey,
    Index
)
from sqlalchemy.dialects.postgresql import JSONB

from sqlalchemy.sql import func

from .base import Base


class CrawlTask(Base):

    __tablename__ = "crawl_tasks"


    id = Column(
        String,
        primary_key=True
    )


    source = Column(
        String(100),
        nullable=False
    )


    mode = Column(
        String(32),
        default="bookshelf"
    )


    max_pages = Column(
        Integer,
        default=200
    )


    priority = Column(
        Integer,
        default=0,
        nullable=False
    )


    status = Column(
        String(32),
        default="pending"
    )


    started_at = Column(
        DateTime
    )


    finished_at = Column(
        DateTime
    )


    resume_at = Column(
        DateTime
    )


    error = Column(
        Text
    )


    result = Column(
        JSONB
    )


    progress = Column(
        JSONB
    )


    created_at = Column(
        DateTime,
        server_default=func.now()
    )

    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    __table_args__ = (
        Index("ix_crawl_tasks_user_id", "user_id"),
    )
