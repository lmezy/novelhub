"""separate novels from comics with a derived ``books.kind`` column

Revision ID: 0032_book_kind
Revises: 0031_progress_set_null
Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op


revision = "0032_book_kind"
down_revision = "0031_progress_set_null"
branch_labels = None
depends_on = None


# Mirrors the keyword list in ``app.services.book_kind``.  The backfill is
# case-insensitive substring matching; the Python service normalises labels
# before matching, so it can catch a few more spellings on the next sync or on
# ``POST /api/books/reclassify``.
COMIC_LABEL_PATTERNS = [
    "%漫画%",
    "%漫畫%",
    "%图集%",
    "%圖集%",
    "%画集%",
    "%畫集%",
    "%写真%",
    "%寫真%",
    "%comic%",
    "%manga%",
    "%webtoon%",
]


def _sql_patterns() -> str:
    """Render the keyword list as SQL string literals (constants, no user input)."""
    return ", ".join(f"'{pattern}'" for pattern in COMIC_LABEL_PATTERNS)


def upgrade() -> None:
    # Existing books default to ``novel``; the two UPDATEs below promote the
    # comic sources and the books whose own labels say "comic".
    op.add_column(
        "books",
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="novel",
        ),
    )
    op.create_index("ix_books_kind", "books", ["kind"])

    op.execute(
        """
        UPDATE books
        SET kind = 'comic'
        WHERE source_id IN (
            SELECT id FROM sources
            WHERE lower(coalesce(config ->> 'bookSourceType', '')) = '2'
        )
        """
    )
    op.execute(
        f"""
        UPDATE books
        SET kind = 'comic'
        WHERE id IN (
            SELECT bt.book_id
            FROM book_tags bt
            JOIN tags t ON t.id = bt.tag_id
            WHERE lower(t.name) LIKE ANY (ARRAY[{_sql_patterns()}])
        )
        OR id IN (
            SELECT bc.book_id
            FROM book_categories bc
            JOIN categories c ON c.id = bc.category_id
            WHERE lower(c.name) LIKE ANY (ARRAY[{_sql_patterns()}])
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_books_kind", table_name="books")
    op.drop_column("books", "kind")
