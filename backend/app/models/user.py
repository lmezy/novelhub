from sqlalchemy import Boolean, Column, String, DateTime

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


    role=Column(
        String(16),
        nullable=False,
        default="user"
    )

    r18_enabled=Column(
        Boolean,
        nullable=False,
        default=False
    )

    non_r18_enabled=Column(
        Boolean,
        nullable=False,
        default=True
    )

    can_manage_visibility=Column(
        Boolean,
        nullable=False,
        default=False
    )

    approved=Column(
        Boolean,
        nullable=False,
        default=True
    )


    created_at=Column(
        DateTime,
        server_default=func.now()
    )

