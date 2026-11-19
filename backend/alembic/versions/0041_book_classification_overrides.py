"""Keep manual book formats and category assignments across syncs."""

from alembic import op
import sqlalchemy as sa


revision = "0041_book_classification"
down_revision = "0040_book_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("books", sa.Column("kind_override", sa.String(16), nullable=True))
    op.add_column(
        "books",
        sa.Column("categories_manually_set", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("books", "categories_manually_set")
    op.drop_column("books", "kind_override")
