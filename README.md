# NovelHub

Personal Novel Digital Library - self-hosted on NAS (UGREEN DX4600 Pro)

NovelHub 是一个自托管的个人小说数字资产平台：导入阅读（YueDu / Legado）书源，抓取书籍和章节，管理个人书架，并提供网页阅读、全文搜索、AI 辅助阅读等功能。

## Quick Start

```bash
cp .env.example .env
# Edit .env with your passwords
docker compose up -d --build
```

启动后访问 `http://localhost:8088`。

首次启动会自动创建超级管理员账号：

- 账号：`admin@novelhub.local`
- 默认密码：见 `backend/app/main.py`

请登录后立即在“个人设置”中修改密码。

## 使用说明

### 1. 导入阅读书源

NovelHub 内置阅读（Legado / YueDu）书源导入引擎，可以直接使用书源仓库中的链接或 JSON：

1. 打开书源仓库，例如 <https://www.yckceo.com/yuedu/shuyuan/index.html>；
2. 进入“设置 -> 阅读书源导入”；
3. 粘贴书源 URL，或直接粘贴书源 JSON；
4. 选择书源范围并导入。

“高级 JSON”框只用于粘贴书源 JSON。如果误把浏览器 Cookie 文本粘进去，系统会检测不到有效书源，并自动回退到上方填写的 URL 重新抓取。

导入成功后，书源会出现在“设置 -> 书源”列表中，可以继续配置 Cookie 和账号密码（见下文）。

`yuedu/` 目录保留了开源阅读（Legado）源码，用于规则格式兼容和本地阅读器参考，构建与使用说明见 `yuedu/README.md`。

### 2. 个人书源

个人书源只负责导入书源，不会立即同步。

导入后请前往“同步”页面：

- 勾选要处理的书源；
- 点击“导入书籍”：从书源发现/分类页面抓取书籍；
- 点击“导入个人书架”：同步当前书源的个人书架，并自动加入自己的书架。

个人书源同步出来的书籍、书架、进度和搜索记录默认只有本人和管理员可见。

个人书籍可以在书籍详情页选择“公开为全年龄书籍”：

- 公开前必须确认该书为全年龄内容；
- 确认后会标记 `all-ages`，其他用户才能看到；
- 可以随时“取消公开”。

### 3. 全站书源

普通用户选择“全站书源”时，不会直接创建书源，而是提交给管理员审批。

- 管理员在“设置 -> 待审批”中通过后才会创建全站书源；
- 管理员批准后会自动触发全站同步；
- 全站书源会记录提交用户；
- 提交时可以选择“公开我的贡献标签”或隐藏贡献者信息。

全站同步时，如果同一本书同时存在 R18 和非 R18 全站版本：

- 系统会以 R18 为主，把该组全站书籍标记为 R18；
- 同时生成“R18 冲突确认”待审批项；
- 管理员批准则保持 R18，拒绝则恢复冲突前的标记。

### 4. Cookie 与书源账号密码

Cookie 和书源账号密码已经合并到“设置 -> 书源”页面：

1. 先导入书源（见第 1 节）；书源列表为空时，“Cookie / 账号”入口不会显示；
2. 在书源列表中找到对应书源；
3. 点击“Cookie / 账号”展开；
4. 在 Cookie 区域粘贴浏览器 Cookie 并保存、测试；
5. 在账号区域填写书源登录用户名和密码，可用于自动登录刷新 Cookie。

Cookie 获取方法见 [Cookie 获取指南](docs/cookie-guide.md)。

Cookie 粘贴整段浏览器 Cookie 即可（形如 `key1=value1; key2=value2`），
其中的 URL 编码值（例如 `lf_user_auth=think%3A%7B...%7D`）无需手动解码：
系统会按原文加密保存，并在请求时原样放入 `Cookie` 请求头，由站点自行解码。

点击“测试”会尝试用该 Cookie 抓取对应书源的书架。测试失败通常是以下原因：

- 站点当前不可达或域名已变化（部分书源如爱丽丝书屋使用动态站源分发）；
- Cookie 已过期、失效，或绑定了签发时的 IP / 浏览器指纹；
- 该书源不支持书架抓取，或书架地址规则已过时。

### 5. 个人设置

“设置 -> 个人设置”中支持：

- 阅读字体、字号、语言、主题；
- 昵称；
- 邮箱；
- 修改账号密码。

### 6. 同步任务

“同步”页面支持：

- 选择多个书源；
- 导入书籍或导入个人书架；
- 查看、暂停、恢复、取消同步任务；
- 管理员可配置自动同步时间。

更多说明：

- [文档索引](docs/README.md)
- [全站同步](docs/full-site-sync.md)
- [书源搜索](docs/source-search.md)

### 7. 本地 Markdown 导入

“设置 -> 本地 / 手动 -> 本地 Markdown 导入”：

1. 在服务器上准备书籍目录，章节为 `.md` 文件，可附带 `metadata.json`；
2. 点击“选择目录”浏览已配置的导入根目录，或手动输入容器内可见的路径；
3. 勾选书籍后点击“批量导入所选”，或点击“导入全部”。

容器部署时，服务器上的书籍目录需要挂载到容器内才能被读取。默认会把
`./data/imports` 挂载到 `/imports`。如果书籍在服务器其他目录，修改 `.env`：

```dotenv
LOCAL_IMPORT_VOLUME=/mnt/books
LOCAL_IMPORT_ROOTS=/imports,/app/storage/imports,/library
```

然后重建后端和爬虫容器：

```bash
docker compose up -d --build backend crawler
```

`LOCAL_IMPORT_ROOTS` 中列出的目录会显示在“选择目录”弹窗中，未列出的已挂载目录仍可手动输入路径。

目录格式示例：

```text
storage/imports/
  demo-book/
    metadata.json
    000001.md
    000002.md
```

`metadata.json` 可包含 `title`、`author`、`description`、`status`、`tags`。未提供时自动从目录名、父目录名和正文样本推断。

本地扫描和导入会自动补全简介、状态、标签和分类，并对书名、作者、简介、标签及正文样本做 R18 检测；扫描列表会显示 R18、分类和标签。

也可以通过 API 导入单个目录：

```bash
curl -X POST http://localhost:8088/api/sync/book \
  -H "Content-Type: application/json" \
  -d '{"source_id":"local_markdown","url":"file:///app/storage/imports/demo-book"}'
```

### 8. 手动上传

“设置 -> 本地 / 手动 -> 手动上传”中可以粘贴章节文本，或选择 `.txt / .md` 文件读取。

- 选择文件后会自动识别书名、作者、简介、状态、标签和 R18 结果；
- 也可以粘贴文本后点击“自动识别”；
- 保存时后端会再次执行 R18 校验与自动分类，即使没有预览也会补全书籍信息和标签。

### 9. 阅读

桌面端阅读器支持字号、字体、主题、目录、AI 侧栏和进度保存。

手机端会自动进入分页阅读模式：

- 点击屏幕左侧或向右滑动：上一页；
- 点击屏幕右侧或向左滑动：下一页；
- 点击屏幕中间：呼出阅读菜单；
- 章节末尾继续翻页会进入下一章；
- 阅读器内切换章节不会堆积历史记录，返回书籍后可以正常回到书库。

### 10. 搜索与 AI

- 搜索页支持书籍、章节全文搜索；
- 配置 AI 后，阅读器侧栏可使用 AI 问答、章节摘要和 RAG 语义检索。

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

## 升级后迁移

如果从旧版本升级，需要先重建服务并执行数据库迁移：

```bash
docker compose up -d --build backend crawler scheduler frontend
docker compose exec backend alembic upgrade head
```

## Storage Layout

```text
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
