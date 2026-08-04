# Crawl Queue

Full-site crawl tasks are stored in `crawl_tasks` and executed by the crawler
worker one at a time. The worker always picks the queued task with the highest
`priority` value, then the oldest task.

## What you can do

- Pause a queued or running task.
- Resume a paused task.
- Cancel a queued, paused, or running task.
- Move any queued/paused task to the front (`move-front`).
- Select any task in the Sync page to control it without waiting for the
  currently selected task.

A running task is paused at the next page/book boundary and the worker is
freed, so it can start another queued task immediately. To make a new site
run first:

1. Pause the current task (the worker stops it and saves the next page).
2. Move the new task to the front.
3. The new task starts immediately.
4. Resume the old task later; it continues from the saved page and skips
   chapters that were already downloaded.

## API

Create a task:

```bash
curl -X POST http://localhost:8088/api/crawl/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source":"yuedu_xxx","max_pages":500,"priority":0}'
```

Control a task:

```bash
curl -X POST http://localhost:8088/api/crawl/tasks/<task_id>/pause
curl -X POST http://localhost:8088/api/crawl/tasks/<task_id>/resume
curl -X POST http://localhost:8088/api/crawl/tasks/<task_id>/move-front
curl -X POST http://localhost:8088/api/crawl/tasks/<task_id>/cancel
```

## Migration

The `priority` column is added by:

```bash
cd backend
alembic upgrade head
```

Docker backend startup runs migrations automatically.
