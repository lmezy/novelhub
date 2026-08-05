# 全站同步

全站同步在后台通过 crawler 容器（Celery worker）执行：遍历书源的发现/分类分页，直到没有新书为止，然后逐本下载书籍、章节、元数据、标签和作者信息，并把结果摘要写入 `crawl_tasks.result`。

## 使用方式

1. 先导入书源。
2. 启动全站同步任务：

```bash
curl -X POST http://localhost:8088/api/crawl/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source": "yuedu_xxx", "max_pages": 0}'
```

`max_pages` 是“发现/分类页数”上限，不是书本数量；设为 `0` 表示不限制页数，任务会一直翻页直到书源没有下一页或没有新书为止。

3. 轮询任务状态：

```bash
curl http://localhost:8088/api/crawl/tasks/<task_id>
```

Admin 的 `Sync` 页也提供“全站同步”按钮，启动后会自动轮询进度。

## 爬取限速

为避免被目标网站识别为爬虫，每次 HTTP 请求之间会加入安全延迟：

- 优先使用书源 JSON 里的 `concurrentRate`（毫秒）；
- 未配置时使用环境变量 `CRAWL_DELAY_MS`，默认 `1200`；
- 每次延迟额外叠加 200-600ms 随机抖动；
- 遇到 429/5xx 会自动重试并退避；
- crawler 队列 worker 可按 `SYNC_WORKER_CONCURRENCY` 并行跑多个爬取任务；单个任务内章节下载按 `SYNC_CHAPTER_CONCURRENCY` 并发，默认 `9`。
- 并发模型对齐 Legado：`SYNC_THREAD_COUNT` 默认 `9`，最大封顶 `9`；目录分页和正文分页也按这个线程数并行抓取。
- 全站同步默认按 `SYNC_BOOK_CONCURRENCY` 并发处理书籍，默认 `3`；调大前请确认书站能承受请求量。
- 书源里的 `concurrentRate` 默认仍会生效；如果明确愿意承担被限流/封禁的风险，可设置 `SYNC_IGNORE_RATE_LIMIT=true`，让章节并发直接使用 `SYNC_CHAPTER_CONCURRENCY`。
- 多个书源任务可以通过 `SYNC_WORKER_CONCURRENCY` 并行执行，默认 `3`；手动全站同步、书源导入并同步、每天 `03:00` 的自动同步都会并行处理多个书源，每个任务使用独立数据库会话。
- 设置 `SYNC_PAGE_BATCH_SIZE`（默认 `0`）后，全站任务每处理完 N 页就保存 `next_page` 并自动重新排队，间隔由 `SYNC_BATCH_INTERVAL_MS` 控制，避免单个任务长时间连续占用。
- Admin 里配置的代理会写入共享 storage，backend 和 crawler worker 都能读取。

几十上百万本书的站点不建议做全站同步。更合理的做法是同步书架/手动选择要看的书，或给全站任务设置较小的 `max_pages`；全站同步更适合几千本以内的小站。

## 任务进度

`crawl_tasks.progress` 会按页更新：

```json
{"pages_checked": 12, "books_found": 480, "books_synced": 320, "books_failed": 2}
```

Admin 的“全站同步”卡片会显示进度条和已检查页数。

## 迁移

本功能新增 `crawl_tasks.result` 字段：

```bash
cd backend
alembic upgrade head
```

Docker 部署时 backend 容器启动会自动执行迁移。

## 批量删除

```bash
curl -X POST http://localhost:8088/api/books/batch-delete \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ids": ["book-id-1", "book-id-2"]}'
```

管理员的 Home 页面也支持勾选多本书后批量删除。

## 常见问题

### 同步/导入显示 0 本书，日志报 All connection attempts failed

如果 Admin -> Proxy 启用了代理，但代理地址填的是宿主机 `127.0.0.1`，Docker 容器内无法访问该地址。请把代理改为容器可访问的地址（宿主机网关或 `host.docker.internal`），或关闭代理。NovelHub 现在会在代理连不上时自动尝试直连。

### 批量删除报 relation "book_categories" does not exist

这是旧数据库缺少分类表迁移导致的。运行 `docker compose restart backend` 或手动执行 `cd backend && alembic upgrade head` 即可补建 `categories` 与 `book_categories` 表。

### 全站同步显示 0 本书但页面请求成功

如果 YueDu 书源的 `ruleExplore` / `ruleSearch` 规则与网站当前 HTML 不匹配，旧版可能只返回没有 `bookUrl` 的空条目。现在会过滤空条目，并自动回退到通用列表页解析；相对链接也会按当前列表页拼接。重新部署 backend 后再发起一次全站同步即可。

### 同步到分类、章节显示小说名、正文报 content missing

旧版 YueDu 规则引擎没有完整处理书源里常见的 `|` 规则分隔符、字面量回退和 `replaceRegex` 数组，导致规则解析失败后把分类链接、书页链接当成书籍和章节。现在会按 `bookUrlPattern` 过滤发现结果，过滤与书页 URL 相同的目录链接，并支持单 `|` 规则与 `replaceRegex` 数组。章节接口也会对缺失正文返回明确的 404 或空正文，不再报 Pydantic 校验错误。已经同步错的分类书籍需要先删除，再重新同步。

### 后端反复重启，日志报 StringDataRightTruncation

`alembic_version.version_num` 列只有 32 字符，迁移 ID 不能超过该长度。当前分类迁移已改为 `0009_categories`，更新后端代码后重新启动 backend 即可。

### 同步任务报 chapters_book_id_fkey 或 MissingGreenlet

旧版章节身份用的是目录里的位置序号，目录顺序变化后会把同一章当作新章节，并可能在章节入库前写入 `chapters`，触发外键错误；进度回调在会话回滚后再次读取过期 ORM 属性时还会报 `MissingGreenlet`。

现在章节身份改为章节 URL（与 Legado 的 `BookChapter.url` 一致），同步时会自动把旧的位置 ID 升级为 URL，不再重复下载；进度回调也只读写内存中的进度字典，不会在回滚后触发懒加载。需要重新构建并启动 crawler 容器：

```bash
docker compose up -d --build crawler backend scheduler
```

如果之前同步产生了重复章节，删除对应书籍后重新同步即可。

如果 `chapters_book_id_fkey` 仍然出现，说明书籍行在写入章节前丢失或被并发删除。现在同步开始前会校验 `books` 行是否存在，缺失时自动用同一 `book_id` 重建书籍行后再写章节；中途被删除也会在后续章节写入前恢复，避免整本书的章节全部失败。

## 任务控制

全站同步任务会写入 `crawl_tasks`，前端在 Admin 页面启动后由全局状态持续轮询，离开页面再回来仍会显示当前任务。运行中的任务可以暂停、继续或取消。

同步进度集中在 `/sync` 页面展示：可以发起全站同步、查看当前任务的页数/书籍/章节进度、暂停/继续/取消，以及查看最近任务列表。书源导入现在也会先创建同步任务，再跳转到该页面查看进度。

`/sync` 页面的书源选择框支持多选，一次会为每个选中的书源创建一个全站同步任务；队列 worker 会按 `SYNC_WORKER_CONCURRENCY` 同时运行多个任务，而不是等前一个完成。

## 自动更新与错误章节

- Celery Beat 每天 `03:00` 自动执行 `daily_sync_all`：有 Cookie 的书源同步书架，没有 Cookie 的书源对库内已有书籍逐本检查新章节。它不会自动全站发现新书，发现新书仍需手动发起全站同步。
- 再次同步同一本书时按章节 URL 去重：已有章节跳过，只有新章节会下载；失败章节会在下次同步时重新尝试，不会永久跳过。
- 如果某章内容抓错，普通再次同步不会覆盖已存在的章节。管理员可以在阅读页点击“重同步本章”，只重新抓取并覆盖当前章节；也可以回到书籍详情页点击“重新同步”整本检查。

## 书架与书籍

首页 `/` 是书架，只显示当前用户收藏/标记的书籍；`/books` 是书籍页，显示仓库中所有已保存的书籍。书籍详情页和书籍卡片上都可以切换收藏状态。

## 本地添加与手动上传

Admin 的“本地/手动”页提供两种入口：

- 本地 Markdown：填写服务器上的书籍目录路径，目录内可直接放章节 `.md` 文件，也可附带 `metadata.json`。
- 手动上传：填写书名、作者、简介、标签，粘贴章节文本或选择 `.txt / .md` 文件；章节标题用 `## 章节标题` 分隔。
