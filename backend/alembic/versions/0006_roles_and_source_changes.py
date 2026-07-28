"""add user roles and source_changes table

Revision ID: 0006_roles_and_source_changes
Revises: 0005_source_config
Create Date: 2026-07-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0006_roles_and_source_changes'
down_revision: Union[str, None] = '0005_source_config'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('role', sa.String(16), nullable=False, server_default='user'))
    op.execute("UPDATE users SET role = 'admin' WHERE is_admin = true")
    op.drop_column('users', 'is_admin')

    op.create_table(
        'source_changes',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action', sa.String(16), nullable=False),
        sa.Column('source_id', sa.String(), nullable=True),
        sa.Column('source_data', JSONB, nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('reviewer_id', sa.String(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('source_changes')
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default='false'))
    op.execute("UPDATE users SET is_admin = true WHERE role = 'admin' OR role = 'super_admin'")
    op.drop_column('users', 'role')
