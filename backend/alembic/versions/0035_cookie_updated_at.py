"""Track when a stored Cookie was last written (``cookies.updated_at``)

Revision ID: 0035_cookie_updated_at
Revises: 0034_source_sync_interval
Create Date: 2026-09-20

``cookies`` only had ``created_at``, which the AI diagnosis rendered as
「Cookie 保存时间」.  Updating a Cookie replaced ``cookie_data`` but left that
timestamp alone, so a Cookie pasted this morning was reported as saved 40 days
ago and the model blamed an expired Cookie that was in fact fresh.

Existing rows are backfilled from ``created_at`` rather than from ``now()``:
stamping them with the migration's run time would have made every stale Cookie
look freshly written.
"""

import sqlalchemy as sa
from alembic import op


revision = "0035_cookie_updated_at"
down_revision = "0034_source_sync_interval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cookies", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE cookies SET updated_at = created_at")
    op.alter_column("cookies", "updated_at", server_default=sa.func.now())


def downgrade() -> None:
    op.drop_column("cookies", "updated_at")
