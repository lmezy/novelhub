"""add user invite codes

Revision ID: 0020_user_invite_codes
Revises: 0019_deleted_accounts
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_user_invite_codes"
down_revision = "0019_deleted_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("invite_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("invited_by_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_invited_by",
        "users",
        "users",
        ["invited_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        "UPDATE users SET invite_code = replace(id::text, '-', '') "
        "WHERE invite_code IS NULL"
    )
    op.alter_column("users", "invite_code", nullable=False)
    op.create_index(
        "ix_users_invite_code",
        "users",
        ["invite_code"],
        unique=True,
    )
    op.create_index(
        "ix_users_invited_by_id",
        "users",
        ["invited_by_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_users_invited_by_id", table_name="users")
    op.drop_index("ix_users_invite_code", table_name="users")
    op.drop_constraint("fk_users_invited_by", "users", type_="foreignkey")
    op.drop_column("users", "invited_by_id")
    op.drop_column("users", "invite_code")
