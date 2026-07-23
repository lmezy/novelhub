# NovelHub


Personal Novel Digital Library


## Requirements


- Docker
- Docker Compose


## Install


Clone:


git clone xxx

cd NovelHub



Copy environment:


cp .env.example .env



Modify password:



nano .env



Start:


docker compose up -d --build



## Services


|Service|Port|
|-|-|
|API|8000|
|Frontend|5173|
|PostgreSQL|5432|



## Test


API:

http://localhost:8000


Frontend:

http://localhost:5173



## Storage


books:

storage/books


covers:

storage/covers


backup:

storage/backup



## Next


Stage 1:

- SQL Models
- Alembic migration
- Plugin framework
- Storage abstraction
- Event system

