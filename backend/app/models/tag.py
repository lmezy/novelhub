from sqlalchemy import Column,String

from .base import Base



class Tag(Base):


    __tablename__="tags"


    id=Column(
        String,
        primary_key=True
    )


    name=Column(
        String(100),
        unique=True
    )

