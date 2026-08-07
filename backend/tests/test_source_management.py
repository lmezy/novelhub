from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.sources import update_source
from app.schemas.source import SourceUpdate


@pytest.mark.asyncio
async def test_update_source_changes_metadata_and_book_r18():
    source = SimpleNamespace(
        id="src-1",
        name="Old Name",
        url=None,
        plugin_name="yuedu",
        enabled=True,
        is_r18=True,
        config={"bookSourceUrl": "https://example.com"},
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=source)
    db.execute = AsyncMock()
    db.scalars = AsyncMock(
        return_value=SimpleNamespace(
            unique=lambda: SimpleNamespace(all=lambda: [])
        )
    )
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with patch("app.api.routes.sources.search_service") as search_mock:
        result = await update_source(
            "src-1",
            SourceUpdate(name="New Name", enabled=False, is_r18=False),
            SimpleNamespace(id="u1", role="admin"),
            db,
        )

    assert result is source
    assert source.name == "New Name"
    assert source.enabled is False
    assert source.is_r18 is False
    assert db.execute.await_count == 1
    assert db.commit.await_count == 1
    assert not search_mock.index_book.called


@pytest.mark.asyncio
async def test_update_source_404_when_missing():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await update_source(
            "missing",
            SourceUpdate(name="X"),
            SimpleNamespace(id="u1", role="admin"),
            db,
        )

    assert exc_info.value.status_code == 404
