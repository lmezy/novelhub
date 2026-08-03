"""add categories and book_categories tables

Revision ID: 0009_categories
Revises: 0008_crawl_task_progress
Create Date: 2026-08-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_categories"
down_revision: Union[str, None] = "0008_crawl_task_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("color", sa.String(length=7)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "book_categories",
        sa.Column("book_id", sa.String(), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.String(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("book_id", "category_id"),
    )


def downgrade() -> None:
    op.drop_table("book_categories")
    op.drop_table("categories")
