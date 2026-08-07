"""add inviter tag to users

Revision ID: 0025_user_invite_tag
Revises: 0024_one_time_invites
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_user_invite_tag"
down_revision = "0024_one_time_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("invite_tag", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "invite_tag")
