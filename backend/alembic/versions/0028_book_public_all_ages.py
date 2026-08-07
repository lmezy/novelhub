"""add public and all-ages confirmation flags to books

Revision ID: 0028_book_public_all_ages
Revises: 0027_book_owner_source_contributor
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_book_public_all_ages"
down_revision = "0027_book_owner_source_contributor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "books",
        sa.Column(
            "all_ages_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("books", "all_ages_confirmed")
    op.drop_column("books", "is_public")
