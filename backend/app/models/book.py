from sqlalchemy import (
Column,
String,
Text,
DateTime,
ForeignKey
)

from sqlalchemy.sql import func

from .base import Base



class Book(Base):


    __tablename__="books"



    id=Column(
        String,
        primary_key=True
    )


    source_id=Column(
        String,
        ForeignKey("sources.id")
    )


    author_id=Column(
        String,
        ForeignKey("authors.id")
    )


    title=Column(
        String(255),
        nullable=False
    )


    cover=Column(
        String(255)
    )


    description=Column(
        Text
    )


    status=Column(
        String(32)
    )


    created_at=Column(
        DateTime,
        server_default=func.now()
    )


    updated_at=Column(
        DateTime,
        server_default=func.now()
    )

