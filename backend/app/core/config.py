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



    # Password for the Redis container's ``requirepass``. Empty means no auth
    # (acceptable only while the port is not published to the host).
    REDIS_PASSWORD:str=""



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

    # Meilisearch ``pagination.maxTotalHits``: the highest rank the engine will
    # ever serve, and therefore the hard ceiling on how many hits one search can
    # see.  It used to be 10 000, which truncated any query matching more than
    # that -- 12 466 books matched 的 on the live index and 10 000 were reported.
    # The derived gram fields (``services/search_ngrams.py``) do the narrowing
    # that the truncated candidate windows used to do, so the ceiling is set
    # above the whole library (33.5k books / 117k chapters).  Raise it here if
    # the library outgrows it; it is an index *setting*, applied on startup, so
    # no reindex is needed.
    MEILI_MAX_TOTAL_HITS:int=200000
    # Whether the gram backfill also rewrites the chapters index.  Off by default
    # because it is not a metadata tweak: Meilisearch re-tokenises the whole
    # document on every update and a chapter carries up to CONTENT_INDEX_LIMIT
    # (100 000) characters of body, so gramming 732k chapters is a near-full
    # re-index of a 94 GB store -- measured on production as a single batch still
    # running after 19 minutes with 11 of the container's 12 GB in use.
    # ``books`` carry no body, so their backfill is cheap and is what the search
    # box needs.  Until this is enabled, a chapter-scoped title/author condition
    # keeps its windowed candidate path: correct, just bounded.
    MEILI_GRAM_BACKFILL_CHAPTERS:bool=False
    # Characters of chapter body kept in one index document.  Meilisearch
    # re-tokenises the whole document on every add, and CJK text is expensive:
    # measured on the live library (2026-10-18) a 100 000-character chapter costs
    # ~26 s of engine CPU, and 11 627 stored chapters are that big (the largest
    # is a whole TXT imported as one chapter, 1.38 MB).  At 100k a full rebuild
    # needed months; at 20k it is hours.  Body search still works -- the
    # candidate window re-scores far more text than this per query -- and the
    # full chapter is always in storage, this only limits the index copy.
    MEILI_CONTENT_INDEX_LIMIT:int=20000
    # Reuse saved AI analyses: when a reader asks something similar about the
    # same book again, the earlier question/answer is fed back into the prompt.
    # Set to false to keep every request stateless (and stop writing to
    # ai_book_insights).
    AI_INSIGHT_REUSE:bool=True
    # How many tasks the engine may still have queued before an index rebuild
    # waits for it.  Meilisearch merges consecutive tasks into one batch and a
    # batch is one transaction: on 2026-10-19 the live engine merged 3 787
    # chapter tasks (~380 000 documents) and could not finish it in 15 hours
    # (60-76 MB/s of index reads, nothing committed).  0 keeps the code default.
    MEILI_BACKLOG_LIMIT:int=0
    # How often an index rebuild re-checks the engine's queue while it waits,
    # and how long it may wait before continuing anyway (0 keeps the defaults).
    MEILI_BACKLOG_POLL_SECONDS:float=2.0
    MEILI_BACKLOG_MAX_WAIT_SECONDS:float=300.0

    SYNC_CHAPTER_CONCURRENCY:int=9
    # Chapter rows are committed in batches of this size instead of one commit
    # per chapter (a commit is a WAL fsync; a 3 000-chapter book used to spend
    # thousands of them).  A crash mid-book loses at most this many fetched
    # chapters -- their files are already written and the next sync refetches
    # the rows -- which is why the default stays small.
    SYNC_CHAPTER_COMMIT_BATCH:int=25
    # Images inside one chapter are downloaded this many at a time.  They are
    # pure network waits and an album chapter can hold hundreds of them: one at
    # a time, a single comic chapter spent minutes waiting for the image host
    # while the rest of the sync sat idle.  Keep it small -- the image host is
    # usually the same origin the chapter fetches are already rate-limited on.
    SYNC_IMAGE_CONCURRENCY:int=3
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

    # How many book sources one "search every source" request may query at the
    # same time.  Each source keeps its own ``concurrentRate`` limiter, so a
    # source never sees a higher request rate than a single-source search would
    # produce; this cap only bounds how many *different* sites are in flight, and
    # therefore how many sockets and how much memory the fan-out holds.
    SEARCH_FANOUT_CONCURRENCY:int=8
    # A single source's search may take this long before it is reported as
    # timed out.  Sites that answer slowly still contribute their results in a
    # later wave; the request as a whole is never held hostage by one dead host.
    SEARCH_FANOUT_SOURCE_TIMEOUT_SECONDS:float=20.0
    # Book sources are untrusted third-party hosts, so one request must not be
    # able to start an unbounded number of them.  ``0`` means "every enabled
    # source the caller may see".
    SEARCH_FANOUT_MAX_SOURCES:int=0
    # How many results one source may contribute to a fan-out search.  A source
    # that answers with its own paging is asked for this many on the first page.
    SEARCH_FANOUT_RESULTS_PER_SOURCE:int=20


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


def sync_book_concurrency() -> int:
    """Effective per-task book concurrency, clamped like the sync does.

    This is the single owner for "how many books one task syncs at once":
    ``SyncService.discover_and_sync_all`` computes it as
    ``min(max(1, SYNC_BOOK_CONCURRENCY), sync_thread_count())``.  The crawl
    queue's connection budget (see ``crawl_runner.task_concurrency_limit``)
    must use this same number -- a task holds one session for its whole life
    plus one session per concurrently syncing book -- otherwise the budget
    counts one connection per task while the sync actually holds four.
    """
    try:
        value = int(getattr(settings, "SYNC_BOOK_CONCURRENCY", 3))
    except (TypeError, ValueError):
        value = 3
    return max(1, min(value, sync_thread_count()))


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

