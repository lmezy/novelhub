""""add config JSONB column to sources table

Revision ID: 0005_source_config
Revises: 0004_source_credentials
Create Date: 2026-07-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0005_source_config'
down_revision: Union[str, None] = '0004_source_credentials'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sources', sa.Column('config', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('sources', 'config')
