# NovelHub 交接记录

本文件只保留“下次还会用到”的信息：环境、红线、历史根因索引、待用户处理事项、排错入口。

> **新增记录请追加到文末**，一节写清「现象 → 根因 → 改动文件 → 验证」即可。
> 不要粘贴长日志、逐条命令输出、历史测试次数或已被后续修复取代的中间状态
> （这些内容占了旧版 60KB，全是噪音）。

---

## 1. 环境与红线

**代码**：`D:\work\git\novelhub`（分支 `develop`）。只改本地代码；不改 `yuedu/`
（开源阅读源码，仅作规则格式参考）；不动线上容器，除非用户明确要求。

**线上**：NAS `nas.19961113.xyz:10022`（SSH config 里的 `master`，用户 `894654222`）。
密钥登录被服务器拒绝，需用密码 + paramiko。部署目录
`/volume1/docker/NovelHub/novelhub`，`docker-compose.yaml` 使用主机网络与预构建镜像
`lonezy/novelhub-{backend,crawler,scheduler,frontend}`。

**线上数据库**：

```bash
docker exec novelhub-postgres psql -h 127.0.0.1 -p 15432 -U novelhub -d novelhub
# crawl_tasks(id, source, status, mode, error, result, progress, max_pages, ...)
```

**日志**：`docker logs novelhub-crawler`（同步任务、Celery、章节/书籍错误都在这里）。
loguru 时间戳是容器本地时间（CST）；`docker logs --since` 用同一个时钟。

**代理**：`/app/storage/proxy_config.json` → `http://127.0.0.1:27890`
（NAS 上的 metacubexd / mihomo 混合端口）。crawler 是 host 网络，容器内 `127.0.0.1`
就是 NAS 本机，所以这个地址是对的；但 mihomo 会周期性重启，重启后连接池里的
keep-alive 连接会变成半开状态（已在 2026-09-10 修，见第 3 节）。

**前端镜像**：线上 `frontend` 容器跑的是 `npm run dev`，构建时把源码 COPY 进镜像，
所以改了前端必须 `docker compose build frontend` 再启动，只重启容器不会生效。

**红线**：不改 `yuedu/`；不把正文写进数据库（走 Storage 抽象）；不绕过验证码 /
WAF / 登录限制；不为单个站点写死逻辑；不在仓库和文档里留任何凭据。

---

## 2. 当前状态（2026-09-12）

- 后端全量测试 **406 passed**：`cd backend && python -m pytest -q`
- Source Engine 闭环已完成并可用：导入书源 → 搜索 → 目录 → 正文 → Storage/DB/搜索 →
  网页阅读。当前工作重心是**同步稳定性与线上排错**，不是新增架构能力。
- 线上仍跑着旧镜像；本地改动要 `docker compose build backend crawler` +
  `docker compose up -d backend crawler` 才生效。
- 待用户处理（代码修不了，属站点侧防护，见第 4 节）：SiS文學網 / 御宅屋 /
  第一版主（Cloudflare 挑战）、菠萝猫（GoEdge 验证码）、搬山人（浏览器也被挑战）
  需浏览器过验证后导入 Cookie；UAA 书源依赖完整 Legado JS 运行时，建议换源。

---

## 3. 历史根因索引

同类症状先在这里找答案，再决定要不要读代码。

| 现象 | 根因 | 修复位置 |
|---|---|---|
| 书页 0 章、整本同步失败 | `_is_book_url` 用非锚定 `bookUrlPattern`，章节 URL `/book/1/2.html` 命中 `book/\d+` 前缀被判成书页 | `yuedu/__init__.py::_is_book_url`（改为路径终结校验） |
| 并发章节串页 / 空正文 | 多个章节共用同一个规则引擎实例，互相覆盖 baseUrl、bookUrl、分页状态 | `fetch_chapter_content` 每章独立引擎 |
| 目录只同步一页就“完成” | `exploreUrl` 是没有 `{{page}}` 写死分类 URL，每页请求同一地址，去重后判定到底 | 目录分页模板自动学习（`_detect_page_template` 等） |
| 目录解析 0 本 | Legado 允许 `a[href*=/post/]` 这种没引号的属性选择器，soupsieve 直接抛错被静默兜底 | `rule_engine.normalize_css_selector()` |
| 标签是整条站点导航菜单 | 通用解析器把 `nav`/`header`/`footer` 里的分类链接当标签 | `_inside_navigation` / `_clean_tags` / `_clean_listing_kind` |
| 正常页面被判“反爬/验证码” | cool18 页面 JS 里一句 `alert('举报失败，请稍后再试')` 命中强标记 | 上下文判定 `has_contextual_block_marker()` |
| 章节 `Chapter returned empty content` | 三种原因被写成一个：源站已删除该书 / Cloudflare 520 / 真空白 | `_looks_like_removed_page`、章节路径也判断 5xx；永久失败不重试 |
| 任务永远卡在 `running` + `MissingGreenlet` | 书籍失败后 `rollback()` 让所有 ORM 实例 expired，错误处理又同步读 `task_obj.progress` | `crawl_runner`：错误分支只用内存快照 + 独立 session 落终态 |
| 一批 520 被当成“连续失败中止任务” | 瞬态失败被计入反爬计数器 | 瞬态单独计数 + 任务级自动重试（60s/120s，`SYNC_TASK_MAX_AUTO_RETRIES`） |
| 2 点 cookie 健康检查跑 8 小时并持续打源站 | 逐个 cookie 抓书架页 + 坏代理超时，无单项上限 | `COOKIE_CHECK_ITEM_TIMEOUT`（默认 60s）+ `rollback` 后不再读 ORM 属性 |
| 每章固定 10 分钟必失败，错误信息为空 | mihomo 重启后半开连接被 httpx 复用，挂到 60s 读超时 ×重试 | `_reset_http_client` 丢池化连接 + 通道冷却排序 |
| 容器里 1287 个僵尸 chrome | Playwright 的 Chromium 被挂到 PID 1，没人 `waitpid` | crawler 容器 `orphan-reaper` 线程 |
| 自动同步漏跑 / 后台无限量爬 | 每天模式是“分钟精确匹配”；自动任务 `max_pages=0` | 间隔模式 + 有界 `AUTO_SYNC_MAX_PAGES`（默认 3） |
| 每次同步都重抓同一页 | 进度里的 `next_page` 存的是“本次正在处理的页” | 设计如此：重开任务会重扫该页，靠章节 URL 去重 |
| 重试后报“书源未返回可同步的书籍”，但前一次已同步 68 本 | 重试尝试的目录全部返回 200 但解析为空，被判成书源没书 | 见第 5 节（2026-09-12） |

---

## 4. 需要用户做的事（长期有效）

1. **Cookie / 验证码类站点**：SiS文學網（b.sis.la）、御宅屋（yswhub.cc）、
   第一版主（banzhu…net）是 Cloudflare 挑战页，菠萝猫（boluomao.com）是 GoEdge
   图形验证码，搬山人小说网（banshanren.com）连 Playwright 浏览器也会被挑战
   （2026-09-12 实测：alicesw / banshanren 的任务都因“连续 5 章被拦截”中止）。
   无头浏览器过不去，需在浏览器（出口 IP 与代理一致）通过验证后，
   把 Cookie 导入「设置 → 书源 → Cookie / 账号」。导入后不需要再等验证。
2. **代理**：保持 `http://127.0.0.1:27890`；mihomo 节点本身不稳定时，同步会出现
   瞬态 520/超时，任务会自动重试 2 次，不需要手动重发。
3. **部署**：改完本地代码后重建 `backend` 与 `crawler`（前端改动才需要 `frontend`）。
   线上镜像不会自动跟随本地代码。
4. **失效书籍**：源站已删除的书（如爱丽丝书屋 54334）重试也无法修好，只会在
   `crawl_tasks.error` 里给出明确原因；需要时在管理端删除该书。

---

## 5. 线上排错速查

```bash
# 最近任务与错误
docker exec novelhub-postgres psql -h 127.0.0.1 -p 15432 -U novelhub -d novelhub -c \
  "select id, source, status, left(coalesce(error,''),120), progress from crawl_tasks order by created_at desc limit 10;"

# 只看错误行
docker logs novelhub-crawler --since 6h 2>&1 | grep -E "\| ERROR|Crawl task"
```

**影子回归**（在容器里用改动后的代码抓真实站点，不动线上代码）：把本地文件内容通过
stdin 送进 `docker exec -i novelhub-crawler python -`，脚本里 `sys.path.insert(0,"/app/backend")`
后直接调用 `get_plugin(...)` / `plugin.discover_books()` / `_get()`。注意会真实请求外网。

---

## 6. 修改与验证约定

1. 动手前先 `git status --short`，理解并保留用户已有改动。
2. 提交前运行 `cd backend && python -m pytest -q`（当前 406 passed）；
   改前端再跑 `cd frontend && npm run build`。
3. 修复尽量落在“为什么失败”的那一层，并补一个能复现的测试。
4. 文档只写长期有用的结论：本文件（索引 + 追加小节）和
   [full-site-sync.md](full-site-sync.md)（用户视角的排错清单）。

---

## 7. 2026-09-12：重试后误报“书源未返回可同步的书籍”

**现象**：要撸小说（`yuedu_b38b98d309e3`）的全站任务 `f13de781` 已经同步了 68 本、
10 本因 Cloudflare 520 失败；任务按瞬态错误自动重试，重试尝试却以
`书源未返回可同步的书籍，请检查书源规则、Cookie 或站点验证状态。` 收尾 —— 书源和
Cookie 其实都正常，用户看到的是一条指向错误方向的报错，而且已同步的计数被当成 0。

**根因**：

1. 重试尝试的每个分类页都返回 HTTP 200、但解析出 0 本（代理抖动时的典型表现），
   `discover_and_sync_all` 里的“首页空结果”分支不区分“站点真的没书”和“本次抓取失败”，
   一律抛非瞬态的书源错误，于是不再重试、任务直接 failed。
2. `progress` 里的计数是“本次尝试”的，重试时不会带进 `result`，重试失败后报告数字丢失。
3. 顺带发现：`start_page > max_pages`（任务已超出自己的页数预算）也会走同一个分支，
   把一个“其实已经跑完”的任务报成失败。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/services/sync.py` | 目录全空时：已超出 `max_pages` → 直接返回 `done`；该源库内已有书 → 抛带“网络”字样的瞬态错误（触发任务级重试）；全新书源才保留原来的“未返回可同步的书籍” |
| `backend/app/services/crawl_runner.py` | 重试/失败时把本次尝试的计数累加进 `result`（`_carry_result_counters`），并把 `progress` 计数在新尝试开始时清零，避免重试重复累加 |
| `backend/app/services/cookie_health.py` | 修 `check_all_cookies` 在 `rollback()` 之后读 `cookie.source` 触发的 MissingGreenlet（2 点任务整体崩掉的真凶）：循环前先取快照 |
| `backend/app/crawler/plugins/yuedu/__init__.py` | 首页分类解析出 0 本时打 warning（含 URL），下次 0 本能在日志里直接看到 |

**验证**：`cd backend && python -m pytest -q` → **406 passed**（新增 5 项：空目录有书→瞬态、
空目录无书→原错误、resume 超预算→完成、重试计数不丢不重、cookie 健康检查过期 ORM 不崩）。
线上复核：`discover_books(page=1)` 当前稳定返回 10 本/分类，代理正常时可正常翻页。
