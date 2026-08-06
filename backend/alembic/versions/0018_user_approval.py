"""add user approval and app settings

Revision ID: 0018_user_approval
Revises: 0017_custom_tags_bookshelf
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_user_approval"
down_revision = "0017_custom_tags_bookshelf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "approved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.execute(
        "INSERT INTO app_settings (key, value) "
        "VALUES ('registration_approval_enabled', 'false')"
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_column("users", "approved")
