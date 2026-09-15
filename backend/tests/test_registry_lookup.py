"""Plugin lookup must not poison the shared asyncpg connection pool.

``get_plugin(<source id>)`` runs its DB lookup either through ``asyncio.run``
(no loop yet) or inside a helper thread's throwaway loop.  A *pooled* asyncpg
connection returned from such a loop stays bound to it, so the next caller on
the real loop dies with::

    RuntimeError: Task <Task ...> got Future <...> attached to a different loop
    Exception terminating connection <AdaptedConnection ...> (Event loop is closed)

The crawler container logged exactly that every cookie health check; the
lookup now uses the unpooled ``LookupSessionLocal``.
"""

from unittest.mock import patch


def test_lookup_engine_is_unpooled_and_separate_from_the_shared_engine():
    from sqlalchemy.pool import NullPool

    from app.core.database import LookupSessionLocal, SessionLocal, engine, lookup_engine

    assert isinstance(lookup_engine.pool, NullPool)
    assert lookup_engine is not engine
    assert LookupSessionLocal.kw["bind"] is lookup_engine
    assert SessionLocal.kw["bind"] is engine


def test_registry_lookup_uses_the_unpooled_session():
    import asyncio

    from app.crawler import registry

    used: list[str] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, model, key):
            used.append(key)
            return SimpleSource()

    class SimpleSource:
        plugin_name = "yuedu"
        config = {"bookSourceUrl": "https://example.com"}

    with patch("app.core.database.LookupSessionLocal", lambda: FakeSession()):
        plugin = asyncio.run(registry._lookup_source_async("yuedu_test_source"))

    assert used == ["yuedu_test_source"]
    assert type(plugin).__name__ == "YueduPlugin"
