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


    #: When ``cookie_data`` was last written.  ``created_at`` only says when the
    #: row appeared, so an updated Cookie kept reporting its first import date
    #: (the AI diagnosis read "Cookie 保存于 40 天前" for a Cookie refreshed that
    #: same morning).  ``onupdate`` covers every writer, including auto-refresh
    #: by credentials and the cookie health check.
    updated_at=Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now()
    )
