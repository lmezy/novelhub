"""add user-selected book display cover

Revision ID: 0022_book_display_cover
Revises: 0021_category_r18
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_book_display_cover"
down_revision = "0021_category_r18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("display_cover", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("books", "display_cover")
