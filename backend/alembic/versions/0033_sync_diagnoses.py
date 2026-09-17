"""AI diagnoses of failed sync tasks (``sync_diagnoses``)

Revision ID: 0033_sync_diagnoses
Revises: 0032_book_kind
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0033_sync_diagnoses"
down_revision = "0032_book_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_diagnoses",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="ok"),
        sa.Column("classification", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.String(length=16), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("change_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"),
                  nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["crawl_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_sync_diagnoses_task"),
    )
    op.create_index("ix_sync_diagnoses_source", "sync_diagnoses", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_sync_diagnoses_source", table_name="sync_diagnoses")
    op.drop_table("sync_diagnoses")
