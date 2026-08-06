"""add deleted account reservation

Revision ID: 0019_deleted_accounts
Revises: 0018_user_approval
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_deleted_accounts"
down_revision = "0018_user_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deleted_accounts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_deleted_accounts_username",
        "deleted_accounts",
        ["username"],
    )
    op.create_index(
        "ix_deleted_accounts_email",
        "deleted_accounts",
        ["email"],
    )
    op.create_index(
        "ix_deleted_accounts_deleted_at",
        "deleted_accounts",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_deleted_accounts_deleted_at", table_name="deleted_accounts")
    op.drop_index("ix_deleted_accounts_email", table_name="deleted_accounts")
    op.drop_index("ix_deleted_accounts_username", table_name="deleted_accounts")
    op.drop_table("deleted_accounts")
