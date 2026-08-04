from sqlalchemy import Column, String, Boolean
from sqlalchemy.dialects.postgresql import JSONB

from .base import Base


class Source(Base):

    __tablename__ = "sources"

    id = Column(
        String,
        primary_key=True
    )

    name = Column(
        String(100),
        nullable=False
    )

    url = Column(
        String(255)
    )

    plugin_name = Column(
        String(100)
    )

    enabled = Column(
        Boolean,
        default=True
    )

    is_r18 = Column(
        Boolean,
        default=False,
        nullable=False
    )

    config = Column(
        JSONB,
        nullable=True
    )
