"""add result JSONB to crawl_tasks

Revision ID: 0007_crawl_task_result
Revises: 0006_roles_and_source_changes
Create Date: 2026-08-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0007_crawl_task_result'
down_revision: Union[str, None] = '0006_roles_and_source_changes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('crawl_tasks', sa.Column('result', JSONB, nullable=True))
    op.add_column(
        'crawl_tasks',
        sa.Column('mode', sa.String(32), nullable=False, server_default='bookshelf'),
    )
    op.add_column(
        'crawl_tasks',
        sa.Column('max_pages', sa.Integer(), nullable=False, server_default='200'),
    )


def downgrade() -> None:
    op.drop_column('crawl_tasks', 'max_pages')
    op.drop_column('crawl_tasks', 'mode')
    op.drop_column('crawl_tasks', 'result')
