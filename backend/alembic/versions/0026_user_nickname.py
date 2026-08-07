"""add user nickname

Revision ID: 0026_user_nickname
Revises: 0025_user_invite_tag
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_user_nickname"
down_revision = "0025_user_invite_tag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("nickname", sa.String(length=48), nullable=True),
    )
    op.execute(
        "UPDATE users SET nickname = '书友_' || substr(md5(random()::text), 1, 8) "
        "WHERE nickname IS NULL OR nickname = ''"
    )


def downgrade() -> None:
    op.drop_column("users", "nickname")
