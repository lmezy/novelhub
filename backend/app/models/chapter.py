from sqlalchemy import (
Column,
String,
Integer,
Text,
ForeignKey,
DateTime
)

from sqlalchemy.sql import func

from .base import Base



class Chapter(Base):


    __tablename__="chapters"



    id=Column(
        String,
        primary_key=True
    )


    book_id=Column(
        String,
        ForeignKey("books.id")
    )


    chapter_number=Column(
        Integer
    )


    title=Column(
        String(255)
    )


    content_path=Column(
        String(500)
    )


    hash=Column(
        String(128)
    )


    created_at=Column(
        DateTime,
        server_default=func.now()
    )

