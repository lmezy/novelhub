"""add custom tags and bookshelf groups

Revision ID: 0017_custom_tags_bookshelf
Revises: 0016_crawl_task_resume_at
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_custom_tags_bookshelf"
down_revision = "0016_crawl_task_resume_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_tags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "show_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "name", name="uq_custom_tags_owner_name"),
    )
    op.create_index("ix_custom_tags_name", "custom_tags", ["name"])

    op.create_table(
        "book_custom_tags",
        sa.Column(
            "book_id",
            sa.String(),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "custom_tag_id",
            sa.String(),
            sa.ForeignKey("custom_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_book_custom_tags_book_id", "book_custom_tags", ["book_id"])
    op.create_index(
        "ix_book_custom_tags_custom_tag_id",
        "book_custom_tags",
        ["custom_tag_id"],
    )

    op.create_table(
        "bookshelf_groups",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "show",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name", name="uq_bookshelf_groups_user_name"),
    )
    op.create_index("ix_bookshelf_groups_user_id", "bookshelf_groups", ["user_id"])

    op.create_table(
        "book_favorite_groups",
        sa.Column(
            "favorite_id",
            sa.String(),
            sa.ForeignKey("book_favorites.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "group_id",
            sa.String(),
            sa.ForeignKey("bookshelf_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_book_favorite_groups_favorite_id",
        "book_favorite_groups",
        ["favorite_id"],
    )
    op.create_index(
        "ix_book_favorite_groups_group_id",
        "book_favorite_groups",
        ["group_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_book_favorite_groups_group_id", table_name="book_favorite_groups")
    op.drop_index("ix_book_favorite_groups_favorite_id", table_name="book_favorite_groups")
    op.drop_table("book_favorite_groups")
    op.drop_index("ix_bookshelf_groups_user_id", table_name="bookshelf_groups")
    op.drop_table("bookshelf_groups")
    op.drop_index("ix_book_custom_tags_custom_tag_id", table_name="book_custom_tags")
    op.drop_index("ix_book_custom_tags_book_id", table_name="book_custom_tags")
    op.drop_table("book_custom_tags")
    op.drop_index("ix_custom_tags_name", table_name="custom_tags")
    op.drop_table("custom_tags")
