"""add bookmarks table

Revision ID: 0030_bookmarks
Revises: 0029_crawl_import_filters
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_bookmarks"
down_revision = "0029_crawl_import_filters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "book_id",
            sa.String(),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            sa.String(),
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_bookmarks_user_book", "bookmarks", ["user_id", "book_id"])
    op.create_index("ix_bookmarks_chapter", "bookmarks", ["chapter_id"])


def downgrade() -> None:
    op.drop_index("ix_bookmarks_chapter", table_name="bookmarks")
    op.drop_index("ix_bookmarks_user_book", table_name="bookmarks")
    op.drop_table("bookmarks")
