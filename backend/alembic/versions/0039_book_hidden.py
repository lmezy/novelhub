"""Quarantine flag for junk book records.

Revision ID: 0039_book_hidden
Revises: 0038_ai_insights
Create Date: 2026-10-21

A "cover wall" book holds someone else's cover images instead of a real chapter
(see app/services/content_quality.py).  Deleting it is irreversible and risky --
the library has thousands of legitimate one-chapter stories -- so the cleanup
hides the row instead: it disappears from every list and from the search index,
while the files stay on disk and the flag can be flipped back.
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_book_hidden"
down_revision = "0038_ai_insights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("books", "hidden")
