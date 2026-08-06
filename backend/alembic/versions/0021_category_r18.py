"""add r18 flag to categories

Revision ID: 0021_category_r18
Revises: 0020_user_invite_codes
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_category_r18"
down_revision = "0020_user_invite_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column(
            "is_r18",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("categories", "is_r18")
