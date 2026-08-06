from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services.bookshelf import list_user_groups


class _CapturingSession:
    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(all=lambda: [])


@pytest.mark.asyncio
async def test_list_user_groups_does_not_group_eager_user_columns():
    db = _CapturingSession()

    result = await list_user_groups(db, SimpleNamespace(id="user-1"))

    assert result == []
    sql = str(db.statement.compile(dialect=postgresql.dialect()))
    assert "GROUP BY bookshelf_groups.id" not in sql
    assert "GROUP BY book_favorite_groups.group_id" in sql
