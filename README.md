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
| AI (optional) | -- | Configure via AI_PROVIDER env |
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

### Stage 2 - AliceSW Plugin (done)
Cookie login (login.py), HTTP crawler with rate limiting (crawler.py), HTML parsing for bookshelf/book/chapters (parser.py), incremental update detection (updater.py), site configuration (config.py). All five modules integrated via the plugin class.

### Stage 3 - Web System (done)
**Pages:** Home (library + recent reads), Book Detail (chapters, EPUB export, re-sync, admin delete), Reader (dark mode, font sizing, TOC sidebar, scroll-progress save, chapter nav), Search (books + chapters scope with Meilisearch), Login/Register, Admin (sources/cookies/sync/logs tabs with bookshelf sync).

### Stage 4 - Advanced Features (in progress)
- **EPUB export** -- done (GET /api/books/{id}/epub)
- **Backup service** -- done (daily incremental + weekly full, tar.zst compression, restore support, /api/backup/*)
- **AI assistant** -- done (POST /ai/chat, /ai/summary, /ai/person, /ai/timeline; multi-provider: OpenAI/Claude/Qwen/Ollama/Hermes)
- **RAG (semantic search)** -- done (chapter chunking, embedding via AI provider, JSONB vector storage, cosine similarity search, /api/rag/*)
- **AI Chat panel** -- done (reader page sidebar, auto-switches to RAG when indexed)
- **Celery Beat tasks** -- done (daily sync, daily incremental backup, weekly full backup)
- **NAS optimization** -- done (PostgreSQL tuned for UGREEN DX4600, resource limits, log rotation)
- **Storage abstraction** -- done (local filesystem, S3/MinIO, WebDAV; switch via STORAGE_BACKEND env)
- **Rate limiting** -- done (Redis-based middleware, 10/min/auth, 200/min/api)
- **Mobile responsive** -- done (NavBar hamburger menu, responsive layout)
- **API Token UI** -- done (Admin page Tokens tab for create/revoke)
- **Qidian plugin** -- done (skeleton registered in crawler registry)
- **Fanqie plugin** -- done (skeleton registered in crawler registry)
- **System health** -- done (GET /api/health/detailed with PostgreSQL/Redis/Meilisearch/disk checks + Admin Status tab)
- **Global dark mode** -- done (toggle in NavBar, persisted to localStorage, Tailwind darkMode class)
- **Docker health checks** -- done (postgres pg_isready, redis-cli ping, backend curl /health)
- **Security hardening** -- done (bcrypt passwords, AES-GCM cookie encryption, API token auth via X-API-Token header, admin-only routes)
- **Plugin template** -- done (crawler/plugins/plugin_template.py with documented interface, plus qidian skeleton)
- **Rate limiting** -- done (Redis-based middleware, 10/min/auth, 200/min/api)
- **Mobile responsive** -- done (NavBar hamburger menu, responsive layout)
- **API Token UI** -- done (Admin page Tokens tab for create/revoke)
- **Plugin template** -- done (`crawler/plugins/plugin_template.py` with documented interface)
- Multi-source plugins and mobile UI remain.

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
