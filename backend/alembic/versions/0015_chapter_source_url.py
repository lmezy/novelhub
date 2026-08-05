"""store chapter source URLs as stable identities

Revision ID: 0015_chapter_source_url
Revises: 0014_user_can_manage_visibility
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_chapter_source_url"
down_revision = "0014_user_can_manage_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "chapters",
        "source_chapter_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=500),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "chapters",
        "source_chapter_id",
        existing_type=sa.String(length=500),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
