"""add crawl task priority

Revision ID: 0011_crawl_task_priority
Revises: 0010_book_favorites
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_crawl_task_priority"
down_revision = "0010_book_favorites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crawl_tasks",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("crawl_tasks", "priority")
