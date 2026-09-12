from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Docker compose keeps operational variables alongside application
    # settings in .env. Ignore those unrelated keys in local test runs.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


    PROJECT_NAME:str="NovelHub"


    VERSION:str="0.1.0"



    DATABASE_URL:str



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
    SYNC_IGNORE_RATE_LIMIT:bool=False
    SYNC_THREAD_COUNT:int=9
    # How many book sources may sync at the same time.  0 (or less) means
    # "one worker per source": every source runs in its own task and a source
    # never runs two tasks at once, so a long full-site sync of one site can no
    # longer block the other eleven.  Set a positive number to keep a ceiling.
    SYNC_WORKER_CONCURRENCY:int=0
    SYNC_PAGE_BATCH_SIZE:int=0
    SYNC_BATCH_INTERVAL_MS:int=5000


settings=Settings()

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

