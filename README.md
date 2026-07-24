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

- SQL Models：已建立核心小说、章节、来源、阅读进度模型
- Alembic migration：`backend/alembic/versions/0001_initial_schema.py`
- Plugin framework：已提供 `local_markdown` 示例插件
- Storage abstraction：正文按 `storage/books/author/book/*.md` 独立保存
- API：`/api/books`、`/api/sources`、`/api/sync/book`、`/api/chapters/{id}`


## Local Markdown Import

Create source:

```bash
curl -X POST http://localhost:8000/api/sources \
  -H "Content-Type: application/json" \
  -d '{"id":"local","name":"Local Markdown","plugin_name":"local_markdown","enabled":true}'
```

Sync a folder containing `metadata.json` and chapter `.md` files:

```bash
curl -X POST http://localhost:8000/api/sync/book \
  -H "Content-Type: application/json" \
  -d '{"source_id":"local","url":"file:///app/storage/imports/demo-book"}'
```

