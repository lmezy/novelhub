from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime
)

from sqlalchemy.sql import func

from .base import Base



class ReadingProgress(Base):


    __tablename__="reading_progress"



    id=Column(
        String,
        primary_key=True
    )


    user_id=Column(
        String
    )


    book_id=Column(
        String
    )


    chapter_id=Column(
        String
    )


    position=Column(
        Integer,
        default=0
    )


    updated_at=Column(
        DateTime,
        server_default=func.now()
    )
