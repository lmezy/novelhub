"""add chapter_embeddings table for RAG

Revision ID: 0002_rag_embeddings
Revises: 0001_initial_schema
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0002_rag_embeddings'
down_revision: Union[str, None] = '0001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chapter_embeddings',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('book_id', sa.String(), sa.ForeignKey('books.id', ondelete='CASCADE'), nullable=False),
        sa.Column('chapter_id', sa.String(), sa.ForeignKey('chapters.id', ondelete='CASCADE'), nullable=False),
        sa.Column('chunk_index', sa.Integer(), server_default='0'),
        sa.Column('content', sa.Text()),
        sa.Column('embedding', postgresql.JSONB()),
        sa.Column('token_count', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_chapter_embeddings_chapter', 'chapter_embeddings', ['chapter_id'])
    op.create_index('ix_chapter_embeddings_book', 'chapter_embeddings', ['book_id'])


def downgrade() -> None:
    op.drop_index('ix_chapter_embeddings_book', table_name='chapter_embeddings')
    op.drop_index('ix_chapter_embeddings_chapter', table_name='chapter_embeddings')
    op.drop_table('chapter_embeddings')
