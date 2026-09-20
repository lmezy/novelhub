from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Docker compose keeps operational variables alongside application
    # settings in .env. Ignore those unrelated keys in local test runs.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


    PROJECT_NAME:str="NovelHub"


    VERSION:str="0.1.0"



    DATABASE_URL:str


    # Connections this process's SQLAlchemy pool may open: ``pool_size`` are kept
    # open, ``max_overflow`` further ones are opened on demand and closed again.
    # Every process owns its own pool (each uvicorn worker, the queue worker and
    # each celery worker), so the sum across the stack has to stay under the
    # database's ``max_connections``.  The defaults keep the historical
    # hard-coded 10 + 20; lower them per service when the stack shares a small
    # database.
    DB_POOL_SIZE:int=10
    DB_MAX_OVERFLOW:int=20



    REDIS_HOST:str="redis"


    REDIS_PORT:int=6379



    MEILI_HOST:str="http://meilisearch:7700"


    MEILI_KEY:str



    JWT_SECRET:str


    JWT_ALGORITHM:str="HS256"


    JWT_EXPIRE_MINUTES:int=1440



    COOKIE_SECRET:str



    STORAGE_PATH:str="/app/storage/books"
    LOCAL_IMPORT_ROOTS:str="/app/storage/imports,/imports,/library"



    CRAWL_DELAY_MS:int=1200
    SYNC_RATE_COOLDOWN_EVERY:int=300
    SYNC_RATE_COOLDOWN_SECONDS:int=10

    SYNC_CHAPTER_CONCURRENCY:int=9
    SYNC_BOOK_CONCURRENCY:int=3
    SYNC_BOOK_CONTINUOUS:bool=False
    # Stop a book early when the upstream keeps returning transient 5xx
    # pages.  The task is retried later instead of hammering every chapter
    # of a book while the origin is unavailable.
    SYNC_MAX_CONSECUTIVE_CHAPTER_FAILURES:int=5
    # Upper bound for the images downloaded and rewritten inside a single
    # chapter.  Manga/photo albums routinely hold 100+ pages, so this has to
    # stay well above the old 50-image cap.
    MAX_CONTENT_IMAGES_PER_CHAPTER:int=512
    SYNC_IGNORE_RATE_LIMIT:bool=False
    SYNC_THREAD_COUNT:int=9
    # How many book sources may sync at the same time.  0 (or less) means
    # "one worker per source": every source runs in its own task and a source
    # never runs two tasks at once, so a long full-site sync of one site can no
    # longer block the other eleven.  Set a positive number to keep a ceiling.
    SYNC_WORKER_CONCURRENCY:int=0
    SYNC_PAGE_BATCH_SIZE:int=0
    SYNC_BATCH_INTERVAL_MS:int=5000
    # A paused/cancelled task only stops at the next checkpoint inside the sync,
    # and until it reaches one it still owns a worker slot, its source and its
    # pooled connection.  A source stuck behind a dead proxy or an anti-bot gate
    # can go a very long time without reaching a checkpoint, which is how a
    # queue whose every slot is "busy" stops consuming new tasks for good.
    # After this many seconds the worker stops waiting and cancels the task's
    # coroutine: the row already holds the state the user asked for, and the
    # next resume continues from the last checkpointed page.
    SYNC_TASK_STOP_GRACE_SECONDS:int=120
    # How often the worker re-reads the state of the tasks it is running.
    SYNC_TASK_SUPERVISE_INTERVAL_SECONDS:int=15
    # A running task that reports no progress at all for this long is treated as
    # hung: it is failed with an explicit message and taken off the queue so its
    # slot comes back.  0 disables the watchdog.  The default is deliberately
    # generous, because a source throttled to one request per minute can
    # legitimately report a page only every few minutes.
    SYNC_TASK_STALL_SECONDS:int=3600


settings=Settings()

def db_pool_capacity() -> int:
    """Connections this process's pool can serve at the same time.

    The upper bound of one SQLAlchemy pool, and therefore the hard ceiling on
    anything that holds a connection for a long time (see
    ``crawl_runner.task_concurrency_limit``).  Never below 1, so a mistyped
    ``0/0`` cannot make every caller wait for a connection that can never exist.
    """
    try:
        size = int(settings.DB_POOL_SIZE)
    except (TypeError, ValueError):
        size = 10
    try:
        overflow = int(settings.DB_MAX_OVERFLOW)
    except (TypeError, ValueError):
        overflow = 20
    return max(1, size + overflow)


def sync_thread_count() -> int:
    """Effective concurrent thread pool size, capped like Legado's MAX_THREAD."""
    return max(1, min(int(settings.SYNC_THREAD_COUNT), 9))


def sync_source_concurrency() -> int:
    """How many *book sources* may sync in parallel.

    ``0`` means unlimited (one worker per source, which is what Legado does:
    every source carries its own ``concurrentRate`` limiter, so running many
    sources at once does not increase the request rate any single site sees).
    A positive value keeps a global ceiling for small hosts.
    """
    try:
        value = int(getattr(settings, "SYNC_WORKER_CONCURRENCY", 0))
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _seconds_setting(name: str, default: int) -> float:
    """Read a non-negative seconds setting, falling back to ``default``.

    Junk (``None``, ``"abc"``) and negatives must not be able to switch a safety
    net off in a way nobody notices, so both mean "use the default".
    """
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = default
    return float(value if value >= 0 else default)


def task_stop_grace_seconds() -> float:
    """How long a task that must stop may take to reach its own checkpoint."""
    return _seconds_setting("SYNC_TASK_STOP_GRACE_SECONDS", 120)


def task_supervise_interval_seconds() -> float:
    """How often the worker re-reads the state of the tasks it runs."""
    return max(1.0, _seconds_setting("SYNC_TASK_SUPERVISE_INTERVAL_SECONDS", 15))


def task_stall_seconds() -> float:
    """How long a running task may report no progress before it is hung.

    ``0`` disables the watchdog, which is the only way to keep a task that never
    reaches a checkpoint from holding its slot forever.
    """
    return _seconds_setting("SYNC_TASK_STALL_SECONDS", 3600)

