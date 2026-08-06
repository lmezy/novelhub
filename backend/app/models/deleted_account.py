from sqlalchemy import Column, DateTime, String
from sqlalchemy.sql import func

from .base import Base


class DeletedAccount(Base):
    __tablename__ = "deleted_accounts"

    id = Column(String, primary_key=True)
    username = Column(String(64), nullable=False)
    email = Column(String(128), nullable=True)
    deleted_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
