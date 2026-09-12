# 爬取队列

全站同步任务存放在 `crawl_tasks`，由 crawler 容器的队列 worker 执行：按 `priority` 从高到低、
再按创建时间取任务。**每个书源都有自己的 worker**：`SYNC_WORKER_CONCURRENCY` 默认 `0`，
表示不限（一个书源一个 worker 并行跑，互不排队）；设成正数才限制同时运行的书源数。
同一个书源永远只有一个任务在跑（不同任务按 `priority`/创建时间排队）。

> 旧版本是“全局 3 个槽位”，一个跑几小时的全站任务会把第 4 个书源卡在队列里等几小时
> （线上 2026-09-12 实测约 4 小时），这就是“每次只同步三个书源”的来源。

## 能做什么

- 暂停排队中或运行中的任务；暂停发生在翻页/换书边界，worker 立刻空出来跑别的任务；
- 继续已暂停的任务（从保存的页继续，已下载章节按 URL 去重）；
- 取消排队 / 暂停 / 运行中的任务；
- 把任务置顶（`move-front`），插到其它排队任务前面。

想让新站点先跑：暂停当前任务 → 置顶新任务 → 新任务立即开始 → 之后再继续旧任务。

## API

```bash
curl -X POST http://localhost:8088/api/crawl/tasks \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source":"yuedu_xxx","max_pages":20,"priority":0}'

curl -X POST http://localhost:8088/api/crawl/tasks/<task_id>/pause
curl -X POST http://localhost:8088/api/crawl/tasks/<task_id>/resume
curl -X POST http://localhost:8088/api/crawl/tasks/<task_id>/move-front
curl -X POST http://localhost:8088/api/crawl/tasks/<task_id>/cancel
```

字段说明与常见报错见 [full-site-sync.md](full-site-sync.md)。
