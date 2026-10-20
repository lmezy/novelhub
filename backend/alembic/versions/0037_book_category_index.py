"""Index ``book_categories`` by category.

Revision ID: 0037_book_category_index
Revises: 0036_query_indexes
Create Date: 2026-10-20

``book_categories`` only had its composite primary key ``(book_id,
category_id)``, so every query that filters *by category* -- the library page's
per-category sections and ``/books/browse?category=`` -- fell back to a
sequential scan of 62 000+ rows per category.  Measured on the live library:
``/books/home`` issued 44 queries and took 1.76 s; with this index and the
collapsed query it takes ~0.4 s.

``IF NOT EXISTS`` keeps the migration idempotent: the index was created on the
live database by hand first, and re-running must not fail.
"""

from alembic import op

revision = "0037_book_category_index"
down_revision = "0036_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_book_categories_category_id "
        "ON book_categories (category_id, book_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_book_categories_category_id")
