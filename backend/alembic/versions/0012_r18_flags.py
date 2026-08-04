"""add r18 flags

Revision ID: 0012_r18_flags
Revises: 0011_crawl_task_priority
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_r18_flags"
down_revision = "0011_crawl_task_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("is_r18", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("r18_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "books",
        sa.Column("is_r18", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("books", "is_r18")
    op.drop_column("users", "r18_enabled")
    op.drop_column("sources", "is_r18")
