from sqlalchemy import Column,String,Boolean,DateTime

from sqlalchemy.sql import func

from .base import Base



class User(Base):


    __tablename__="users"



    id=Column(
        String,
        primary_key=True
    )


    username=Column(
        String(64),
        unique=True,
        nullable=False
    )


    email=Column(
        String(128),
        unique=True
    )


    password_hash=Column(
        String(255),
        nullable=False
    )


    is_admin=Column(
        Boolean,
        default=False
    )


    created_at=Column(
        DateTime,
        server_default=func.now()
    )

