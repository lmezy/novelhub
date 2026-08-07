"""add one-time invite codes

Revision ID: 0024_one_time_invites
Revises: 0023_user_source_settings
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_one_time_invites"
down_revision = "0023_user_source_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "code",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "used_by",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_invites_code", "invites", ["code"], unique=True)
    op.create_index("ix_invites_created_by", "invites", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_invites_created_by", table_name="invites")
    op.drop_index("ix_invites_code", table_name="invites")
    op.drop_table("invites")
