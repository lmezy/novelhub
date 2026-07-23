from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime
)

from sqlalchemy.sql import func

from .base import Base



class CrawlLog(Base):


    __tablename__="crawl_logs"



    id=Column(
        String,
        primary_key=True
    )


    task_id=Column(
        String
    )


    level=Column(
        String(20)
    )


    message=Column(
        Text
    )


    created_at=Column(
        DateTime,
        server_default=func.now()
    )
