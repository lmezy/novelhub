"""add book ownership and source contributor metadata

Revision ID: 0027_book_owner_source_contributor
Revises: 0026_user_nickname
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_book_owner_source_contributor"
down_revision = "0026_user_nickname"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column(
            "owner_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_books_owner_id", "books", ["owner_id"])
    op.execute(
        """
        UPDATE books
        SET owner_id = sources.owner_id
        FROM sources
        WHERE books.source_id = sources.id
          AND sources.owner_id IS NOT NULL
        """
    )

    op.add_column(
        "sources",
        sa.Column(
            "submitter_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "show_contributor",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "show_contributor")
    op.drop_column("sources", "submitter_id")
    op.drop_index("ix_books_owner_id", table_name="books")
    op.drop_column("books", "owner_id")
