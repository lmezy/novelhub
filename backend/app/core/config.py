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



    class Config:

        env_file=".env"



settings=Settings()

