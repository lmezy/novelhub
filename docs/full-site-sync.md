# 全站同步

全站同步在后台由 crawler 容器的队列 worker 执行：遍历书源的发现/分类分页直到没有新书，
再逐本下载书籍、章节、元数据、标签和作者信息，结果摘要写入 `crawl_tasks.result`。

## 使用方式

1. 先导入书源。
2. 启动任务（也可以在「同步」页面点“导入书籍 / 全站同步”）：

```bash
curl -X POST http://localhost:8088/api/crawl/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source": "yuedu_xxx", "max_pages": 20}'
```

`max_pages` 是**发现/分类页数**上限，不是书本数量；`0` 表示不限页数。几十万本书的大站
不建议做全站同步，`max_pages` 给小一点、或只同步书架更合理。

创建任务时可以排除标签和分类。匹配发生在书页元数据解析完成后、入库和下载章节之前，
被过滤的书计入 `books_filtered`，不算失败：

```json
{"source": "yuedu_xxx", "max_pages": 20, "exclude_tags": ["耽美"], "exclude_categories": ["言情"]}
```

3. 轮询任务状态：`curl http://localhost:8088/api/crawl/tasks/<task_id>`
   （Admin 的「同步」页会自动轮询并显示进度）。

## 限速与并发

- 请求间隔优先用书源 JSON 里的 `concurrentRate`，未配置时用 `CRAWL_DELAY_MS`（默认 1200ms），
  另叠加 200–600ms 随机抖动；长任务每 `SYNC_RATE_COOLDOWN_EVERY`（默认 300）次请求暂停
  `SYNC_RATE_COOLDOWN_SECONDS`（默认 10）秒。
- 并发模型对齐 Legado：`SYNC_THREAD_COUNT`（默认 9，封顶 9）；单任务内书籍并发
  `SYNC_BOOK_CONCURRENCY`（默认 3）、章节并发 `SYNC_CHAPTER_CONCURRENCY`（默认 9）；
  `SYNC_WORKER_CONCURRENCY` 控制同时运行多少个书源任务。
- 无头浏览器（webJs / webView 书源）受 `YUEDU_PLAYWRIGHT_CONCURRENCY`（默认 3）限制，
  并且同样走书源限速，避免几十个 Chromium 同时打一个站点。
- 429/5xx 自动退避重试；设置 `SYNC_IGNORE_RATE_LIMIT=true` 才会忽略书源自身的限速。
- `SYNC_PAGE_BATCH_SIZE`（默认 0）> 0 时，任务每处理 N 页就保存 `next_page` 并重新排队，
  间隔由 `SYNC_BATCH_INTERVAL_MS` 控制。
- Admin 里配置的代理会写进共享 storage，backend / crawler 都能读到。

## 任务进度与控制

`crawl_tasks.progress` 按页更新（`pages_checked / books_found / books_synced / books_failed`），
任务可暂停、继续、取消、置顶（`/api/crawl/tasks/<id>/{pause,resume,cancel,move-front}`）。
暂停发生在翻页/换书的边界，worker 立刻空出来去跑别的任务；恢复时从保存的页继续，
已下载的章节按章节 URL 去重，不会重复下载。

任务失败但已经同步了一部分书时，累计数量保留在 `result` 里（自动重试不会把它们清零，
也不会重复累加）。

## 自动更新

- Celery Beat 每天 `03:00` 跑 `daily_sync_all`：有 Cookie 的书源同步书架，没有 Cookie 的
  书源对库内书籍检查新章节。它**不会**自动全站发现新书，发现新书要手动发起全站同步。
  自动任务有 `AUTO_SYNC_MAX_PAGES`（默认 3）兜底，不会变成无限量爬取。
- 再次同步同一本书按章节 URL 去重；失败章节下次会重试；已存在的章节不会被覆盖，
  需要覆盖用阅读页的“重同步本章”或书详情页的“重新同步”。
- cookie 健康检查（`check_cookie_health`）每项最多 `COOKIE_CHECK_ITEM_TIMEOUT`（默认 60s），
  避免被反爬的站点把 2 点的任务拖成数小时。

## 常见报错排查

### 同步/导入 0 本书，日志报 All connection attempts failed

Admin → 代理里填的是别的主机地址，容器访问不到。crawler/backend 在 NAS 上用的是 host
网络，`http://127.0.0.1:27890`（metacubexd / mihomo 混合端口）是对的；代理在别的机器上时
要改成容器可达的地址或关闭代理。代理连不上时程序会自动尝试直连，但每次都会先白等一个
连接超时。

### 任务报“书源未返回可同步的书籍”，但书源本身正常

2026-09-12 起：如果该源库里**已有书**，目录整轮返回空会被当作网络/代理波动（提示里带
“网络”字样），任务在 60s / 120s 后自动重试，不再把已同步的成果判成败；只有全新书源
才会保留这条错误。重试仍失败时看日志里的 `Explore kind ... returned no books`，
它带着具体分类 URL。

### 只有首页返回 0 本书

书源的发现规则（`ruleExplore` 的 `bookList`）和站点当前 HTML 不匹配。解析器会退回通用
列表页解析，仍为 0 说明页面没有可识别的书籍链接：用浏览器确认页面结构，必要时换源。

### 目录只同步了一页（几百本）就显示“完成”

书源的 `exploreUrl` 是写死的分类 URL（没有 `{{page}}`），旧版每页请求同一地址，去重后
判定到底。现在会从第 1 页的分页链接自动学习模板（`/page/2`、`/index_2.html`、`?page=2`、
`?paged=2`），后续按模板翻页；页面 404/410 视为目录结束。规则自带 `{{page}}` 或 JS 模板
的书源不受影响。

> 風月文學網 h528 是“一篇文章=一本书”的短篇站，修好翻页后每页约 360 本，量级很大，
> 建议先用较小的 `max_pages` 或随时暂停/取消；任务可断点续跑。

### 任务卡在“运行中”不动，日志报 MissingGreenlet

一本书同步失败后 `rollback()` 会让 session 里所有 ORM 实例过期，旧版错误处理又同步读取
`task_obj.progress`，于是真正的错误被 greenlet 报错顶掉、任务永远停在 `running`。
现在错误分支只读内存里的进度快照，写终态时会用独立 session 兜底；crawler 重启时也会把
遗留的 `running` 任务改回 `pending` 自动续跑，不需要手动删任务。

### 一批书同步失败后报“同步连续失败超过 N 本，已中止任务”

这条只针对**非瞬态**失败（规则 / Cookie 类）。Cloudflare 520、浏览器超时、连接中断属瞬态：
它们单独计数，达到阈值时抛“上游/代理暂时不可用（网络…）”，由 worker 在 60s/120s 后自动
重试（`SYNC_TASK_MAX_AUTO_RETRIES`，默认 2）。成功一本即清零两种计数。

### 章节报 Chapter returned empty content

现在会区分三种真实原因：

- `该书在源站已被删除或禁用` / `章节在源站已被删除或禁用`：站点提示页（爱丽丝书屋 54334
  这类），重试无用，只能删书或等源站恢复；
- `Upstream server returned a transient 5xx error page`：Cloudflare 520 等瞬态错误，会重试；
- 其余才是真的取不到正文（选择器不匹配、需要 Cookie、正文全在图片里）。

### 站点要求验证码 / 人机验证 / Cloudflare “Just a moment”

这类页面在服务端无法绕过（代码也不会去绕）。处理办法：在浏览器里（出口 IP 与代理一致）
打开站点通过验证，把 Cookie 导入「设置 → 书源 → Cookie / 账号」，再重新同步。
已知情况：

- 菠萝猫（boluomao.com）：GoEdge 图形验证码；
- SiS文學網（b.sis.la）、御宅屋（yswhub.cc）、第一版主（banzhu…net）：Cloudflare 挑战页；
- 搬山人小说网（banshanren.com）：连 Playwright 浏览器也会被挑战（2026-09-12 实测：
  同一个任务里连续几十章被判拦截），同样需要 Cookie 或换一个能过验证的代理节点；
- 禁忌书屋 cool18：页面里出现“请稍后再试”属正常文案，已不会被误判成拦截。

### 书源发现规则是 `<js>` / `@js:` 脚本，同步报无法执行

例如 UAA 小说的 `exploreUrl` 是 `eval(String(Reload('https://…/xxx.js')))`，依赖完整
Legado Android 运行时（`source`、`cache`、`java.importScript`）和登录 token，
NovelHub 的 Node shim 跑不了这类脚本：请在 Legado 里搜索后走书源搜索/手动链接同步，
或在 yckceo 书源库换一个实现。

### 爱丽丝书屋（alicesw.com）域名解析异常

该域名曾被 DNS 污染（解析到 127.0.0.1）。插件会自动用 DoH（doh.pub → Cloudflare →
Google）解析真实 IP，并以 `IP + Host 头` 直连，结果缓存 300 秒，无需改书源 URL。
所有 DoH 端点都不通时，可在服务器 `/etc/hosts` 里写死真实 IP 作为双保险。

## 迁移

`crawl_tasks.result / progress / priority / resume_at` 等字段由 Alembic 管理
（`cd backend && alembic upgrade head`；Docker 启动 backend 时自动执行）。

## 批量删除

```bash
curl -X POST http://localhost:8088/api/books/batch-delete \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"ids": ["book-id-1", "book-id-2"]}'
```

管理员的 Home 页面也能勾选多本书后批量删除。
