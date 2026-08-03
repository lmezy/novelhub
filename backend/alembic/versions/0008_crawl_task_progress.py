"""add progress JSONB to crawl_tasks

Revision ID: 0008_crawl_task_progress
Revises: 0007_crawl_task_result
Create Date: 2026-08-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0008_crawl_task_progress'
down_revision: Union[str, None] = '0007_crawl_task_result'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('crawl_tasks', sa.Column('progress', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('crawl_tasks', 'progress')
