# NovelHub

Personal Novel Digital Library &mdash; self-hosted on NAS (UGREEN DX4600 Pro)

## Quick Start

```bash
cp .env.example .env
# Edit .env with your passwords
docker compose up -d --build
```

## Services

| Service | Port | Notes |
|---------|------|-------|
| Nginx gateway | 8088 | Unified entry |
| Backend API | 8000 | FastAPI |
| Frontend | 5173 | Vue 3 + Vite |
| PostgreSQL | 5432 | Database |
| Redis | 6379 | Cache / queue |
| Meilisearch | 7700 | Full-text search |

Access via `http://localhost:8088` for the full app.

## Development Stages

### Stage 0 - Initialization (done)
Project scaffold, Docker Compose, .env, README.

### Stage 1 - Core Framework (done)
- **Database** &mdash; 12 models: users, sources, authors, books, chapters, tags, cookies, crawl_tasks, crawl_logs, reading_progress, book_versions, book_tags
- **API** &mdash; `/api/auth/*`, `/api/books/*`, `/api/chapters/*`, `/api/sources/*`, `/api/sync/*`, `/api/progress/*`, `/api/tags/*`, `/api/cookies/*`, `/api/crawl/*`, `/api/search`
- **Repositories** &mdash; Typed data access layer for all models
- **Plugin system** &mdash; `NovelSourcePlugin` protocol with `local_markdown` example plugin
- **Storage** &mdash; Markdown chapter storage at `storage/books/author/book/*.md`
- **Event system** &mdash; `BookCreated`, `ChapterUpdated`, `SyncFailed`, `CookieExpired`, etc.
- **Search** &mdash; Meilisearch integration for books and chapter content
- **Logging** &mdash; loguru with console + rotating file output
- **CORS** &mdash; Configured for development
- **Error handling** &mdash; Global exception handlers
- **Scheduler** &mdash; Celery + Redis Beat for daily sync

### Stage 2 - AliceSW Plugin (pending)
Cookie login, book listing, chapter sync, incremental update.

### Stage 3 - Web System (pending)
Reader UI, search, user system.

### Stage 4 - Advanced Features (pending)
AI, RAG, EPUB export, multi-source, mobile.

## Local Markdown Import

Create source:
```bash
curl -X POST http://localhost:8088/api/sources \
  -H "Content-Type: application/json" \
  -d ''{"id":"local","name":"Local Markdown","plugin_name":"local_markdown","enabled":true}''
```

Sync a folder containing `metadata.json` and chapter `.md` files:
```bash
curl -X POST http://localhost:8088/api/sync/book \
  -H "Content-Type: application/json" \
  -d ''{"source_id":"local","url":"file:///app/storage/imports/demo-book"}''
```

## Storage Layout

```
storage/
  books/
    {author}/
      {title}/
        metadata.json
        000001.md
        000002.md
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2.x, Alembic |
| Database | PostgreSQL 17 |
| Cache/Queue | Redis 7 |
| Search | Meilisearch |
| Tasks | Celery |
| Frontend | Vue 3, TypeScript, Vite, Pinia |
| Crawler | requests, BeautifulSoup4, Playwright |
| Deploy | Docker Compose, Nginx |
