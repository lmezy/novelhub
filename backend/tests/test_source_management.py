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
async def test_update_source_sets_and_clears_the_sync_interval():
    source = SimpleNamespace(
        id="src-1",
        name="搬山人",
        url="https://www.banshanren.com",
        plugin_name="yuedu",
        enabled=True,
        is_r18=False,
        config={"bookSourceUrl": "https://www.banshanren.com"},
        sync_interval_seconds=None,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=source)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    admin = SimpleNamespace(id="u1", role="admin")

    await update_source("src-1", SourceUpdate(sync_interval_seconds=60), admin, db)
    assert source.sync_interval_seconds == 60

    # An explicit null clears it back to "use the source's own rate".
    await update_source("src-1", SourceUpdate(sync_interval_seconds=None), admin, db)
    assert source.sync_interval_seconds is None

    # Omitting the field must not touch a stored value.
    source.sync_interval_seconds = 30
    await update_source("src-1", SourceUpdate(name="改名"), admin, db)
    assert source.sync_interval_seconds == 30


def test_source_interval_schema_bounds():
    from pydantic import ValidationError

    assert SourceUpdate(sync_interval_seconds=0).sync_interval_seconds == 0
    assert SourceUpdate(sync_interval_seconds=3600).sync_interval_seconds == 3600
    for bad in (-1, 3601):
        with pytest.raises(ValidationError):
            SourceUpdate(sync_interval_seconds=bad)


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


@pytest.mark.asyncio
async def test_update_source_changes_scope_owner_in_place():
    source = SimpleNamespace(
        id="user:u1:yuedu:hash",
        name="My Source",
        url="https://example.com",
        plugin_name="yuedu",
        enabled=True,
        is_r18=False,
        config={},
        owner_id="u1",
        show_contributor=True,
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
            "user:u1:yuedu:hash",
            SourceUpdate(scope="global"),
            SimpleNamespace(id="u1", role="admin"),
            db,
        )

    assert result is source
    assert source.owner_id is None
    assert source.show_contributor is True


@pytest.mark.asyncio
async def test_update_source_nonadmin_cannot_make_global():
    source = SimpleNamespace(
        id="user:u1:yuedu:hash",
        name="My Source",
        url="https://example.com",
        plugin_name="yuedu",
        enabled=True,
        is_r18=False,
        config={},
        owner_id="u1",
        show_contributor=True,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=source)

    with pytest.raises(HTTPException) as exc_info:
        await update_source(
            "user:u1:yuedu:hash",
            SourceUpdate(scope="global"),
            SimpleNamespace(id="u1", role="user"),
            db,
        )

    assert exc_info.value.status_code == 403
