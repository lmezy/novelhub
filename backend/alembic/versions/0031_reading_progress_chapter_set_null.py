"""let a replaced chapter detach reading progress instead of blocking it

Revision ID: 0031_progress_set_null
Revises: 0030_bookmarks
Create Date: 2026-09-13
"""

from alembic import op


revision = "0031_progress_set_null"
down_revision = "0030_bookmarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A re-sync drops stale chapters (empty / anti-bot artifacts) so they can
    # be fetched again.  ``reading_progress.chapter_id`` was a plain FK, so a
    # user who had read one of those chapters made the whole book sync fail
    # with ForeignKeyViolationError.  Clearing the pointer keeps the progress
    # row (and the book on the "继续阅读" list) while the chapter is replaced.
    op.execute(
        """
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_attribute att
                    ON att.attrelid = con.conrelid
                    AND att.attnum = ANY (con.conkey)
                WHERE rel.relname = 'reading_progress'
                    AND con.contype = 'f'
                    AND att.attname = 'chapter_id'
            LOOP
                EXECUTE format(
                    'ALTER TABLE reading_progress DROP CONSTRAINT %I',
                    constraint_name
                );
            END LOOP;
            ALTER TABLE reading_progress
                ADD CONSTRAINT reading_progress_chapter_id_fkey
                FOREIGN KEY (chapter_id) REFERENCES chapters (id)
                ON DELETE SET NULL;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE reading_progress
            DROP CONSTRAINT IF EXISTS reading_progress_chapter_id_fkey;
        ALTER TABLE reading_progress
            ADD CONSTRAINT reading_progress_chapter_id_fkey
            FOREIGN KEY (chapter_id) REFERENCES chapters (id);
        """
    )
