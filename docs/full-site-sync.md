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

## 迁移

本功能新增 `crawl_tasks.result` 字段：

```bash
cd backend
alembic upgrade head
```

Docker 部署时 backend 容器启动会自动执行迁移。
