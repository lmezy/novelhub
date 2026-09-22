# 爬取队列

全站同步任务存放在 `crawl_tasks`，由 crawler 容器的队列 worker 执行：按 `priority` 从高到低、
再按创建时间取任务。**每个书源都有自己的 worker**：`SYNC_WORKER_CONCURRENCY` 默认 `0`，
表示不限（一个书源一个 worker 并行跑，互不排队）；设成正数才限制同时运行的书源数。
同一个书源永远只有一个任务在跑（不同任务按 `priority`/创建时间排队）。

> 旧版本是“全局 3 个槽位”，一个跑几小时的全站任务会把第 4 个书源卡在队列里等几小时
> （线上 2026-09-12 实测约 4 小时），这就是“每次只同步三个书源”的来源。

## 队列挂了会自愈

队列循环（`backend/app/services/crawl_runner.py::_worker_loop`）有两层保护：

- **循环自己不死**：每轮循环体都在 `try/except Exception` 里，数据库抖一下只记一条
  `Crawl queue loop error; retrying:` 并退避 5 秒后继续轮询（`_LOOP_ERROR_BACKOFF_SECONDS`）。
  以前这里的 `_next_pending_tasks` 是裸调用，一次数据库故障就能让整个队列永久停摆。
- **挂了会被重启**：循环每轮写一次心跳文件（`backend/app/core/heartbeat.py`，默认
  `/tmp/novelhub_queue_heartbeat`）。crawler 容器的 PID 1（`crawler/app/main.py`）除了看子进程
  「退出了没」，还看心跳是否还新：超过 `SYNC_QUEUE_HEARTBEAT_TIMEOUT_SECONDS`（默认 300 秒；
  队列循环最多 2 秒一轮，所以只有真挂死会触发）没动就退出 → `restart: always` 重启容器 →
  启动时的 `_reset_stale_running_tasks()` 把控死在 `running` 的行改回 `pending` 重新跑。
  心跳文件写不进去时只打印一行警告并退回“只看子进程退出”，不会造成重启死循环。

> 2026-09-22 线上：数据库后端被信号 13（Broken pipe）打死，集群 reinitializing 24 秒。这次
> 抖动让队列循环的下一次轮询抛异常、协程直接结束，而 `asyncio.run` 的收尾又卡在一个不接受
> 取消的协程上——进程活着、CPU 0、队列一小时不消费任何任务，期间新建的两个任务一直显示
> 「排队」。上面两条就是针对它补的。

## 任务级看门狗（`SYNC_TASK_STALL_SECONDS`）

这是和上面**互不相干**的另一层：它盯的是任务行里的进度字段（`crawl_runner._progress_marker`），
60 分钟没变就把**那一个任务**判失败并释放队列槽位——队列本身照跑其它源。

所以这层只有在“进度证据足够细”时才是准的：

- 图片计数（`current_images_done` / `current_images_total`）在每张图开始下载前更新，见
  `sync._process_content_images` 的 `image_progress_cb`；
- 章节级进度不再只等第 10 次才写库：`_CHAPTER_PROGRESS_COMMIT_SECONDS`（30 秒）一到就写。

一个图集章节可能有几百张图、还是串行下载的（`MAX_CONTENT_IMAGES_PER_CHAPTER`），整章要几小时；
以前这期间任务行一个字都不动（线上 2026-09-22 两个漫画源就是这么被判成“没进度”杀掉的，
而被杀前 2~11 分钟日志里还在取图）。

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
