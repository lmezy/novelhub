from pydantic_settings import BaseSettings


class Settings(BaseSettings):


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



    CRAWL_DELAY_MS:int=0

    SYNC_CHAPTER_CONCURRENCY:int=9
    SYNC_BOOK_CONCURRENCY:int=3
    SYNC_BOOK_CONTINUOUS:bool=False
    SYNC_IGNORE_RATE_LIMIT:bool=False
    SYNC_THREAD_COUNT:int=9
    SYNC_WORKER_CONCURRENCY:int=3
    SYNC_PAGE_BATCH_SIZE:int=0
    SYNC_BATCH_INTERVAL_MS:int=5000


    class Config:

        env_file=".env"



settings=Settings()

def sync_thread_count() -> int:
    """Effective concurrent thread pool size, capped like Legado's MAX_THREAD."""
    return max(1, min(int(settings.SYNC_THREAD_COUNT), 9))

