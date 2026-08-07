# NovelHub

Personal Novel Digital Library &mdash; self-hosted on NAS (UGREEN DX4600 Pro)

## Quick Start

```bash
cp .env.example .env
# Edit .env with your passwords
docker compose up -d --build
```

## 使用说明

### 1. 启动与登录

```bash
cp .env.example .env
# 修改 .env 中的数据库密码等配置
docker compose up -d --build
```

启动后访问 `http://localhost:8088`。首次启动会自动创建超级管理员 `admin@novelhub.local`，默认密码见 `backend/app/main.py`，请登录后立即修改。

### 2. 导入阅读（YueDu / Legado）书源

NovelHub 内置了开源阅读（Legado）书源导入引擎，可以直接使用书源仓库中的链接或 JSON：

1. 打开书源仓库，例如 <https://www.yckceo.com/yuedu/shuyuan/index.html>，复制目标书源的导入链接或 JSON；
2. 进入管理后台 -> “书源导入”；
3. 粘贴书源 URL 或 JSON；
4. 如需登录网站，粘贴浏览器 Cookie（可选）；
5. 勾选“同时从分类/排行榜页面发现小说”，点击“导入并同步全部”；
6. 也可以在“高级选项”里先“预览”，或仅导入书源、之后再到“同步”页面处理。

`yuedu/` 目录中保留了开源阅读（Legado）3.0 源码，用于规则格式兼容与本地阅读参考，其构建与使用说明见 `yuedu/README.md`。

### 3. 本地书籍批量导入

管理后台 -> “本地 / 手动” -> “本地 Markdown 导入”：

1. 在服务器上准备书籍目录，章节为 `.md` 文件，可附带 `metadata.json`；
2. 输入服务器上的根目录路径（例如 `/app/storage/imports`）并点击“扫描目录”；
3. 扫描结果会列出发现的书目，勾选支持“全选 / 反选 / 清空选择”；
4. 点击“批量导入所选”写入数据库，或点击“导入全部”直接批量导入本次扫描到的全部书籍；
5. “仅读取不导入”只解析并预览书籍，不写入数据库。

目录格式示例：

```text
storage/imports/
  demo-book/
    metadata.json
    000001.md
    000002.md
```

`metadata.json` 可包含 `title`、`author`、`description`、`status`、`tags`。未提供时自动从目录名/父目录名推导。

### 4. 书架分组与自定义标签

书架（首页）支持类似开源阅读（Legado）的分组管理：

1. 顶部“全部 / 分组”页签可快速切换书架分组；
2. “新建”输入分组名称即可创建，“管理分组”可重命名、显示/隐藏或删除；
3. 一本书可以同时属于多个分组；
4. 勾选书籍后可批量“移出书架”或“移动到分组”；
5. 书籍详情页收藏后，也可以直接勾选该书所属分组并保存；
6. “全部书籍”页同样支持全选/反选，普通用户也可批量加入书架，不再仅限管理员。

自定义标签：

1. 在书籍详情页输入标签名称并点击“添加标签”；
2. 可勾选“公开标签”让其他用户看到，或保持私有仅自己可见；
3. 公开标签会显示在书籍卡片和详情页，点击可查看打过此标签的人数；
4. 勾选“显示贡献者”时，点击标签会列出打标签的用户；不勾选则只显示人数；
5. 用户可以随时移除自己打的标签。

### 5. 在线书源与同步

- 在线书源需要先配置 Cookie，详见 [Cookie获取指南](docs/cookie-guide.md)；
- 管理后台“书源”页可以直接编辑现有书源的名称、URL、启用状态、R18/全年龄和规则 JSON，不再需要删除后重新导入；
- “Cookie”页支持对已有 Cookie 直接编辑更新，过期后只需粘贴新 Cookie 保存即可；
- “同步”页面可查看、暂停、继续、取消任务；
- 同步页面支持勾选多个书源并同时启动同步，也支持全选/反选/清空；
- 在同步页面开启“自动同步”并设置时间后，系统会每天到点自动为所有启用书源创建同步任务；
- 全站同步说明见 [全站同步](docs/full-site-sync.md)；
- 书源搜索与规则说明见 [书源搜索](docs/source-search.md)。

### 6. 阅读与检索

- 首页展示书架与最近阅读；
- 书籍详情页支持章节列表、重新同步、EPUB 导出；
- 阅读器支持字号、主题、目录、进度保存；
- 搜索页支持书籍和章节全文搜索；
- 配置 AI 后，阅读器侧边栏可使用 AI 问答、章节摘要和 RAG 语义检索。

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
- Playwright infrastructure -- done (shared PlaywrightCrawler base class for any JS-rendered site plugin)

## Local Markdown Import

推荐使用管理后台 -> “本地 / 手动”完成批量导入：

1. 准备书籍目录（章节 `.md` 文件，可选 `metadata.json`）；
2. 输入服务器目录路径并点击“扫描目录”；
3. 使用全选/反选快速勾选，点击“批量导入所选”或“导入全部”；
4. “仅读取不导入”可以只解析预览，不写数据库。

也可以通过 API 导入单个目录：

```bash
curl -X POST http://localhost:8088/api/sync/book \
  -H "Content-Type: application/json" \
  -d '{"source_id":"local_markdown","url":"file:///app/storage/imports/demo-book"}'
```

## Cookie Setup

Before syncing books from online sources, you need to provide login cookies.
See the [Cookie获取指南](docs/cookie-guide.md) for step-by-step instructions.

Quick steps:
1. Log into the novel site in your browser
2. F12 -> Application -> Cookies -> copy all as `name=value; name2=value2`
3. Paste into Admin -> Cookies -> Add Cookie
4. Click "Test Cookie" to verify it works
5. Save and trigger a sync

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
| Crawler (JS) | Playwright (Chromium headless) |
| Deploy | Docker Compose, Nginx |
