from sqlalchemy import Column,String,Text

from .base import Base



class Author(Base):


    __tablename__="authors"


    id=Column(
        String,
        primary_key=True
    )


    name=Column(
        String(100),
        nullable=False
    )


    description=Column(
        Text
    )

