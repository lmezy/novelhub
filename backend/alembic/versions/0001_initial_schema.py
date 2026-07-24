"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "authors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text()),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=255)),
        sa.Column("plugin_name", sa.String(length=100)),
        sa.Column("enabled", sa.Boolean(), default=True),
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(length=100), unique=True),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False, unique=True),
        sa.Column("email", sa.String(length=128), unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), default=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "books",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source_id", sa.String(), sa.ForeignKey("sources.id")),
        sa.Column("author_id", sa.String(), sa.ForeignKey("authors.id")),
        sa.Column("source_book_id", sa.String(length=255)),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("cover", sa.String(length=255)),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(length=32)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "source_book_id", name="uq_books_source_external_id"),
    )
    op.create_table(
        "book_tags",
        sa.Column("book_id", sa.String(), sa.ForeignKey("books.id"), primary_key=True),
        sa.Column("tag_id", sa.String(), sa.ForeignKey("tags.id"), primary_key=True),
    )
    op.create_table(
        "chapters",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("book_id", sa.String(), sa.ForeignKey("books.id")),
        sa.Column("chapter_number", sa.Integer()),
        sa.Column("source_chapter_id", sa.String(length=255)),
        sa.Column("title", sa.String(length=255)),
        sa.Column("content_path", sa.String(length=500)),
        sa.Column("hash", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("book_id", "source_chapter_id", name="uq_chapters_book_external_id"),
    )
    op.create_table(
        "book_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("chapter_id", sa.String(), sa.ForeignKey("chapters.id")),
        sa.Column("content_hash", sa.String(length=128)),
        sa.Column("change_type", sa.String(length=32)),
        sa.Column("content_path", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "reading_progress",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id")),
        sa.Column("book_id", sa.String(), sa.ForeignKey("books.id")),
        sa.Column("chapter_id", sa.String(), sa.ForeignKey("chapters.id")),
        sa.Column("position", sa.Integer(), default=0),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "book_id", name="uq_reading_progress_user_book"),
    )
    op.create_table(
        "crawl_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), default="pending"),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "crawl_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String()),
        sa.Column("level", sa.String(length=20)),
        sa.Column("message", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "cookies",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("cookie_data", sa.Text(), nullable=False),
        sa.Column("expired_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("cookies")
    op.drop_table("crawl_logs")
    op.drop_table("crawl_tasks")
    op.drop_table("reading_progress")
    op.drop_table("book_versions")
    op.drop_table("chapters")
    op.drop_table("book_tags")
    op.drop_table("books")
    op.drop_table("users")
    op.drop_table("tags")
    op.drop_table("sources")
    op.drop_table("authors")
