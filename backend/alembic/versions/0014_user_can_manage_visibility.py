"""add user can manage visibility

Revision ID: 0014_user_can_manage_visibility
Revises: 0013_user_non_r18_enabled
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_user_can_manage_visibility"
down_revision = "0013_user_non_r18_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "can_manage_visibility",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "can_manage_visibility")
