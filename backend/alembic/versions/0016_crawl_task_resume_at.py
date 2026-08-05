"""add crawl task resume_at

Revision ID: 0016_crawl_task_resume_at
Revises: 0015_chapter_source_url
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_crawl_task_resume_at"
down_revision = "0015_chapter_source_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crawl_tasks",
        sa.Column("resume_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("crawl_tasks", "resume_at")
