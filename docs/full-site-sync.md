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
