"""Per-source request interval (``sources.sync_interval_seconds``)

Revision ID: 0034_source_sync_interval
Revises: 0033_sync_diagnoses
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op


revision = "0034_source_sync_interval"
down_revision = "0033_sync_diagnoses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("sync_interval_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sources", "sync_interval_seconds")
