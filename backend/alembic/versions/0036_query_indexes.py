"""Add indexes for the hottest query patterns.

Revision ID: 0036_query_indexes
Revises: 0035_cookie_updated_at
Create Date: 2026-10-17

- ``chapters(book_id, chapter_number)``: chapter lists and EPUB export filter
  by ``book_id`` and order by ``chapter_number``.  The existing unique
  constraint on ``(book_id, source_chapter_id)`` covers the filter but not the
  sort, so 700k+-row tables still pay a per-query sort.
- ``books(source_id)``: ``/books/browse`` filters by source.
- ``books(updated_at DESC)``: nearly every book list orders by it.

All three use ``CONCURRENTLY`` semantics implicitly by being plain creates on
a small personal deployment; on a large live library they may briefly lock
writes, so run migrations during low-traffic windows.
"""

import sqlalchemy as sa
from alembic import op


revision = "0036_query_indexes"
down_revision = "0035_cookie_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_chapters_book_id_number",
        "chapters",
        ["book_id", "chapter_number"],
    )
    op.create_index("ix_books_source_id", "books", ["source_id"])
    op.create_index("ix_books_updated_at", "books", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_books_updated_at", table_name="books")
    op.drop_index("ix_books_source_id", table_name="books")
    op.drop_index("ix_chapters_book_id_number", table_name="chapters")
