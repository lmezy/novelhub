from sqlalchemy import Column,String

from .base import Base



class BookTag(Base):


    __tablename__="book_tags"



    book_id=Column(
        String,
        primary_key=True
    )


    tag_id=Column(
        String,
        primary_key=True
    )

