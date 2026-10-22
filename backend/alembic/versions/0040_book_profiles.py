"""Book profiles for cross-book semantic discovery (pgvector).

Revision ID: 0040_book_profiles
Revises: 0039_book_hidden
Create Date: 2026-10-22

Why a second vector table?  ``chapter_embeddings`` answers questions *inside*
one book; this one answers "find me other books like this scene", which needs
one small vector per book instead of thousands per book.  Four segments per
book keep enough plot detail to match a specific scene; the whole library is
~185 000 rows (~1.3 GB), not the 70-100 GB a chapter-level global index costs.

The ``vector`` column is fixed at 1024 dimensions: every embedding model the
project recommends for Chinese text (BAAI/bge-m3, text-embedding-v3,
embedding-3) emits 1024.  A different dimension needs a new table (or a new
migration), not a runtime cast, because pgvector indexes require a fixed
dimension.

Requires the ``pgvector/pgvector:pg17`` image (same data directory as
``postgres:17``).  ``CREATE EXTENSION`` needs a superuser, which the compose
file's ``POSTGRES_USER`` is.
"""

from alembic import op

revision = "0040_book_profiles"
down_revision = "0039_book_hidden"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS book_profiles (
            book_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            segment INTEGER NOT NULL DEFAULT 0,
            excerpt TEXT,
            embedding vector(1024) NOT NULL,
            model VARCHAR(120) NOT NULL DEFAULT '',
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (book_id, segment)
        )
        """
    )
    # HNSW: ~185k rows is tiny for it, and the query path stays a single
    # ``ORDER BY embedding <=> $1 LIMIT n`` without scanning the table.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_book_profiles_embedding
        ON book_profiles USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS book_profiles")
