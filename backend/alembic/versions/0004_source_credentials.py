"""add source_credentials table for auto-login fallback

Revision ID: 0004_source_credentials
Revises: 0003_api_tokens
Create Date: 2026-07-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0004_source_credentials'
down_revision: Union[str, None] = '0003_api_tokens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'source_credentials',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('username', sa.String(255), nullable=False),
        sa.Column('password_encrypted', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source'),
    )


def downgrade() -> None:
    op.drop_table('source_credentials')