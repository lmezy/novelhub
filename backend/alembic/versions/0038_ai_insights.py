"""Saved AI analyses per book.

Revision ID: 0038_ai_insights
Revises: 0037_book_category_index
Create Date: 2026-10-21

A reader's question and the model's answer are kept per book so a later, similar
question can be answered on top of the earlier analysis instead of from zero.
Shared by every reader of that book; ``stale`` flips when the chapter the answer
quoted is re-fetched.
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_ai_insights"
down_revision = "0037_book_category_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_book_insights",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column(
            "book_id",
            sa.String(),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            sa.String(),
            sa.ForeignKey("chapters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("answered_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_ai_book_insights_book_updated", "ai_book_insights", ["book_id", "updated_at"]
    )
    op.create_index("ix_ai_book_insights_chapter", "ai_book_insights", ["chapter_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_book_insights_chapter", table_name="ai_book_insights")
    op.drop_index("ix_ai_book_insights_book_updated", table_name="ai_book_insights")
    op.drop_table("ai_book_insights")
