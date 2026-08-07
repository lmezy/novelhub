"""add user settings, source ownership, and task ownership

Revision ID: 0023_user_settings_source_ownership
Revises: 0022_book_display_cover
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0023_user_source_settings"
down_revision = "0022_book_display_cover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "owner_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_sources_owner_id",
        "sources",
        ["owner_id"],
    )
    op.add_column(
        "crawl_tasks",
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_crawl_tasks_user_id",
        "crawl_tasks",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_crawl_tasks_user_id", table_name="crawl_tasks")
    op.drop_column("crawl_tasks", "user_id")
    op.drop_index("ix_sources_owner_id", table_name="sources")
    op.drop_column("sources", "owner_id")
    op.drop_column("users", "settings")
