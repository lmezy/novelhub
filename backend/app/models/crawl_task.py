from sqlalchemy import (
    Column,
    String,
    DateTime,
    Integer,
    Text
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
