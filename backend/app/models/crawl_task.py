from sqlalchemy import (
    Column,
    String,
    DateTime,
    Text
)

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


    error = Column(
        Text
    )


    created_at = Column(
        DateTime,
        server_default=func.now()
    )
