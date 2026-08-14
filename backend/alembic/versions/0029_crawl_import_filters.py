"""add import filters to crawl tasks

Revision ID: 0029_crawl_import_filters
Revises: 0028_book_public_all_ages
Create Date: 2026-08-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0029_crawl_import_filters"
down_revision = "0028_book_public_all_ages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crawl_tasks",
        sa.Column(
            "exclude_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "crawl_tasks",
        sa.Column(
            "exclude_categories",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("crawl_tasks", "exclude_categories")
    op.drop_column("crawl_tasks", "exclude_tags")
