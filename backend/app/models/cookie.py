from sqlalchemy import (
    Column,
    String,
    Text,
    DateTime
)

from sqlalchemy.sql import func

from .base import Base



class Cookie(Base):


    __tablename__="cookies"



    id=Column(
        String,
        primary_key=True
    )


    source=Column(
        String(100),
        nullable=False
    )


    cookie_data=Column(
        Text,
        nullable=False
    )


    expired_at=Column(
        DateTime
    )


    created_at=Column(
        DateTime,
        server_default=func.now()
    )
