"""AI diagnoses of failed sync tasks.

One row per analysed task.  The row keeps the model's *structured* answer plus
the evidence snapshot it was given, and -- importantly -- a reference to the
``source_changes`` proposal it produced.  Nothing in this table is ever applied
to a book source automatically: a diagnosis is advice, and applying it is an
explicit admin approval on the proposal.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from .base import Base


class SyncDiagnosis(Base):
    __tablename__ = "sync_diagnoses"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_sync_diagnoses_task"),
        Index("ix_sync_diagnoses_source", "source_id"),
    )

    id = Column(String, primary_key=True)
    task_id = Column(
        String,
        ForeignKey("crawl_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id = Column(
        String,
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=True,
    )
    #: ``ok`` when the model answered, ``failed`` when the AI call itself broke
    #: (so the UI can explain "analysis failed" instead of showing nothing).
    status = Column(String(16), nullable=False, default="ok")
    #: site_side | cookie | rate_limit | proxy | config | removed_books | unknown
    classification = Column(String(32), nullable=True)
    confidence = Column(String(16), nullable=True)
    summary = Column(Text, nullable=True)
    #: Full structured answer: reasoning, next_steps, proposed_changes, evidence.
    payload = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    model = Column(String(120), nullable=True)
    tokens_used = Column(Integer, default=0, nullable=False)
    #: ``source_changes.id`` created from this diagnosis, if any.
    change_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


__all__ = ["SyncDiagnosis"]
