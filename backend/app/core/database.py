from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.pool import NullPool

from .config import settings

def _pool_kwargs() -> dict:
    """Pool sizing for ``engine``, taken from the configured connection budget.

    Kept in one place because the budget is also the hard ceiling for anything
    that holds a connection for a long time (see
    ``app.services.crawl_runner.task_concurrency_limit``): if the engine stopped
    honouring these two settings, that ceiling would be sized against a pool the
    process does not actually have.
    """
    return {
        "pool_size": int(settings.DB_POOL_SIZE),
        "max_overflow": int(settings.DB_MAX_OVERFLOW),
    }


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={
        "statement_cache_size": 0,
    },
    **_pool_kwargs(),
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

# Short-lived lookups that cannot run on the caller's event loop (resolving a
# source id to its plugin config, see ``app.crawler.registry``) must not use
# the pooled engine above.  A pooled asyncpg connection stays bound to the loop
# that created it, so a lookup running on a throwaway loop (``asyncio.run`` in
# a helper thread) hands a connection back to the pool that every later caller
# on the real loop then fails to use:
#
#   RuntimeError: Task <Task ...> got Future <...> attached to a different loop
#   Exception terminating connection <AdaptedConnection ...> (Event loop is closed)
#
# NullPool opens one connection for the lookup and closes it before the
# throwaway loop disappears, so nothing cross-loop ever reaches the pool.
lookup_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    poolclass=NullPool,
    connect_args={
        "statement_cache_size": 0,
    },
)

LookupSessionLocal = async_sessionmaker(
    lookup_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

async def get_db():
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
