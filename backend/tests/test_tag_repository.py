from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Tag
from app.repositories.tag import TagRepository


@pytest.mark.asyncio
async def test_get_or_create_returns_existing_tag():
    db = AsyncMock()
    existing = Tag(id="tag-1", name="all-ages")
    db.scalar = AsyncMock(return_value=existing)

    tag = await TagRepository(db).get_or_create("all-ages")

    assert tag is existing
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_inserts_missing_tag_without_duplicate():
    db = AsyncMock()
    created = Tag(id="tag-2", name="all-ages")
    db.scalar = AsyncMock(side_effect=[None, created])
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    tag = await TagRepository(db).get_or_create("all-ages")

    assert tag is created
    db.execute.assert_awaited_once()
    db.add.assert_not_called()
