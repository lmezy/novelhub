# 全站同步

全站同步在后台通过 crawler 容器（Celery worker）执行：遍历书源的发现/分类分页，直到没有新书为止，然后逐本下载书籍、章节、元数据、标签和作者信息，并把结果摘要写入 `crawl_tasks.result`。

## 使用方式

1. 先导入书源。
2. 启动全站同步任务：

```bash
curl -X POST http://localhost:8088/api/crawl/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source": "yuedu_xxx", "max_pages": 500}'
```

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
- crawler worker 固定并发数为 1，同一时间只跑一个爬取任务。
- Admin 里配置的代理会写入共享 storage，backend 和 crawler worker 都能读取。

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

### 后端反复重启，日志报 StringDataRightTruncation

`alembic_version.version_num` 列只有 32 字符，迁移 ID 不能超过该长度。当前分类迁移已改为 `0009_categories`，更新后端代码后重新启动 backend 即可。
