"""add user non r18 enabled

Revision ID: 0013_user_non_r18_enabled
Revises: 0012_r18_flags
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_user_non_r18_enabled"
down_revision = "0012_r18_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "non_r18_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "non_r18_enabled")
