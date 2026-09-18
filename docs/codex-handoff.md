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

## 2. 当前状态（2026-09-18）

- 后端全量测试 **690 passed**：`cd backend && python -m pytest -q`
- Source Engine 闭环已完成并可用：导入书源 → 搜索 → 目录 → 正文 → Storage/DB/搜索 →
  网页阅读。当前工作重心是**同步稳定性与线上排错**，不是新增架构能力。
- **AI 功能已补齐**（第 18 节）：后端配置/上下文/流式/划词/RAG + 前端 AI 设置页与阅读器
  AI 面板。使用说明见 [ai-assistant.md](ai-assistant.md)。
- 并发模型：**一个书源一个 worker**（`SYNC_WORKER_CONCURRENCY=0` 默认不限），书源之间
  不再排队；同一书源同时只跑一个任务。
- **每书源可配「同步间隔」**（第 21 节）：`sources.sync_interval_seconds`，
  设置 → 书源 → 编辑里填「秒/请求」，不填沿用书源 `concurrentRate`。给搬山人这类有拉取
  间隔限制的站点用。
- **阅读路径已提速**（第 22 节）：`/books/{id}/sources` 12s → 0.12s（同名书匹配下推到 SQL，
  同步路径同一个 helper 也一起受益）；章节图片/封面补 `Cache-Control` 与 **304**；
  阅读器与书详情页的次要请求不再挡在正文前面。详见
  [reading-performance.md](reading-performance.md)。
- 线上仍跑着旧镜像；本地改动要 `docker compose build backend crawler` +
  `docker compose up -d backend crawler` 才生效（AI/前端改动还要加 `frontend`）。
- 待用户处理（代码修不了，属站点侧防护，见第 4 节）：SiS文學網 / 御宅屋 /
  第一版主（Cloudflare 挑战）、菠萝猫（GoEdge 验证码）、搬山人（限速，可配同步间隔缓解）
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
| 配好 Cookie 仍报“验证码/人机验证”，浏览器里页面正常 | Cloudflare 正常页面也注入 `challenge-platform/scripts/jsd/main.js`，被判成拦截 | 见第 8 节 |
| Cookie 书源同步 0 本书（绅士漫画/wn09.shop） | ①`header` 的 `@js:` 规则没执行，声明 UA 被丢→站点返回手机版页面 ②`bookList` 的 XPath 风格规则解析出 0 元素 ③`discover_books` 又用 `/novel/123` 路径启发式把规则命中的书全部过滤 | 见第 8 节 |
| 全站同步里第 4 个书源排队几小时 | 全局只有 `SYNC_WORKER_CONCURRENCY`(3) 个槽位 | 见第 8 节 |
| 目录只出 1 章、章节是 `/cdn-cgi/l/email-protection` | `chapterList` 是“元素规则 + `@js:` 脚本”，引擎只认整条 JS，回退后被通用链接扫描捡到邮箱保护链接 | 见第 8 节 |
| 同一书源大部分书正常、个别书报 `maximum recursion depth exceeded` | `ruleBookInfo.name` 是 `{{book.name}}`，无 book 上下文时模板回退成整页 HTML，被当成 CSS 规则切分；解析器遇到不闭合括号时原地递归 | 见第 9 节 |
| 日志一直刷 `JsRuntime JS error: Cannot read properties of null (reading '0')` | jsoup shim 的 `java.getString` 只认 CSS，书源的 XPath 规则返回空串；且 shim 把“上一步结果”当成 `java.getString` 的求值内容（Legado 用的是当前列表项/页面） | 见第 10 节 |
| 日志出现 `java.getWebViewUA is not a function` / `Unexpected end of JSON input` | shim 缺 `getWebViewUA()`；`java.get(key)` 被当成 HTTP 请求（Legado 单参数是 `java.put` 的变量存储） | 见第 10 节 |
| 漫画书章节报 `Chapter returned empty content` | 章节规则失效后通用解析只找文字容器，图片型章节没有兜底 | 见第 10 节 |
| 正常页面被判“限流/反爬”，一本书就中止整个任务 | 弱标记（`限流` 等）在整页任意位置找确认词，书名《限流情缘一线牵》+ CF 脚本里的 `challenge` 即命中 | 见第 12 节 |
| 漫画章节图片全部打不开 / 图片请求 401 | 图片靠 `<img>` 加载、不带 Bearer；接口只认 Bearer | 见第 12 节 |
| 个别书报 `[Errno 36] File name too long` | 书名做目录名，超过文件系统 255 字节分量上限 | 见第 12 节 |
| 重同步某本书报 `reading_progress_chapter_id_fkey` 外键错误 | 删除失效章节时用户阅读进度仍指向它 | 见第 12 节 |
| 日志里 `Failed to sync book … (url): `（错误文本为空）→ 满 10 本中止整个任务 | httpx 超时异常 `str()` 是空串，旧代码只按文本判定瞬态，代理抖动被当成规则/Cookie 失败 | 见第 13 节 |
| 同一份任务日志里 `Task … got Future … attached to a different loop` | Celery 任务每次 `asyncio.get_event_loop()` 换循环，SQLAlchemy 连接池里的 asyncpg 连接绑在旧循环上 | 见第 13 节 |
| 漫画书每个章节固定只有 12 张图片 | 书源 `imgInfoList` 只含相册第 1 个索引页（12 条），旧代码拿它当页数上限 | 见第 13 节 |
| 相册第一张（封面）总是丢 | `//…/li[1]` 被当成 0-based Legado 索引（= 第二个 li）；Legado 对 `/` 开头规则走 XPath（1-based） | 见第 13 节 |
| 一批不相关的书/章节在同一瞬间失败：`ReadError` / `ClosedResourceError` / `pop from an empty deque`，最后报「同步连续失败超过 10 本…反爬」 | 重试路径 `aclose()` 了进程内共享的 httpx 客户端，正在用它的其它并发请求当场全挂 | 见第 14 节 |
| 日志刷 `Exception terminating connection <AdaptedConnection …>`（Event loop is closed）+ `got Future … attached to a different loop` + `get_plugin failed for '<source id>'` | `registry.get_plugin(<source id>)` 用**池化**引擎在一次性事件循环（helper 线程里的 `asyncio.run`）里查库，把绑在死循环上的 asyncpg 连接还进了共享连接池 | 见第 14 节 |
| 日志里 `Failed to fetch content image <url>: `（冒号后空白） | 图片下载失败时直接打印 httpx 异常，`str(exc)` 是空串 | 见第 14 节 |
| 風月文學網任务每次都报“验证码/人机验证”失败，浏览器里页面正常 | 正文里“我的手**已被限制**在厚实手套中”命中强标记 `已被限制`（整页裸子串） | 见第 15 节 |
| 禁忌书屋任务报“验证码/人机验证”，浏览器正常 | 正文“经过严格的**身份验证**”+ 同段“无**人机**”：`身份验证` 是上下文标记、`人机` 是确认词，而 `人机`⊂`无人机` | 见第 15 节 |
| 日志刷 `Failed to fetch content image …`（ClosedResourceError / ConnectError / 404） | 图片下载不重试 anyio 流错误、代理失败后只试直连、`404` 也重试 3 次 | 见第 15 节 |
| 日志刷 `Future exception was never retrieved` / `Task exception was never retrieved` | cookie 健康检查的 `asyncio.wait_for` 超时把进行中的 `page.goto` 取消掉，Playwright 自己的导航 future 没人读 | 见第 17 节 |
| 没配 Cookie 的书源被报「书源已配置 Cookie 但仍被站点拦截」 | `_captcha_hint` 读的 `_cookie` 混进了站点自己 `Set-Cookie` 的会话 cookie（御宅屋的 `fontsize`） | 见第 17 节 |
| AI 每次调用都慢 2 秒多 | `httpx.AsyncClient()` 每次重建 TLS 上下文，解析 certifi 证书包（本机 2.45s）；`LLMClient` 每次请求都新建客户端 | 见第 18 节（`ai_client.default_ssl_context()` 模块级缓存） |
| `claude` provider 永远 401/404 | 用 OpenAI 的 `/chat/completions` + `Bearer` 调 Anthropic | 见第 18 节（`/v1/messages` + `x-api-key`） |
| AI 回答总是从第 1 章说起 / 摘要只覆盖前 30 章 | 上下文固定取「书的前 N 章」，与阅读位置无关 | 见第 18 节（`AIService.build_context`） |
| 阅读器 AI 回答半天不出字 | nginx 默认 `proxy_buffering`，SSE 被整体缓冲 | 见第 18 节（响应头 + nginx `proxy_buffering off`） |
| 分类页「明明有书」却报 `returned no books`、任务以 `书源未返回可同步的书籍` 失败 | `bookList`/`chapterList` 里的 `\|\|`/`&&` 组合规则没按 Legado 切分，整条丢给 soupsieve 抛 `Invalid character '\|'` 后被吞 | 见第 26 节（`_get_elements_from_root`） |
| `exploreUrl` 是 `<js>` 但站点正常，仍报「发现规则是 Legado JS 脚本，当前环境无法执行」 | shim 缺 `java.connect`（书源 `try/catch` 把 TypeError 变成字符串，日志里看不到 JS 报错） | 见第 26 节（`java.connect`/`getBody`/`selectFirst`/`equals`） |
| 日志刷 `JsRuntime eval error: Object of type Tag is not JSON serializable` | 纯 JS 字段规则的输入是 bs4 `Tag`，`json.dumps` 在脚本运行前就抛错 | 见第 26 节（`json_safe()`） |
| 搜索结果被全部过滤、只剩首页链接；书页无 `<title>` 导致 `no usable metadata` | host-only 的 `bookUrlPattern`（只有 scheme+host）当过滤器用；`{{book.name}}` 没有 book 上下文 | 见第 26 节（`_book_url_pattern()`/`_extract_labelled_title()`） |
| AI 诊断说「书源未配置 Cookie」，但设置 → 书源明明显示「已保存 Cookie」 | 诊断证据只带任务报错 + 规则 JSON，**完全没带 cookies 表的状态**；书源 `header` 里本来就不会有 cookie 字段 | 见第 27 节（`collect_login_state()` + 提示词口径） |
| 搜索「铃铛」返回一堆只有「铃」或只有「铛」的结果 | Meilisearch 的 CJK 匹配是**逐字**的，`matchingStrategy: last` 只要求最后一个字命中；模糊模式把引擎结果原样返回 | 见第 28 节（`_condition_score` 模糊改为「每个字都要出现」） |
| 高级搜索（多条件）怎么填都是 0 条 | 多条件路径每个条件只取 1000 条候选（`CANDIDATE_LIMIT`），`category=言情` 一类条件命中 1847 本，交集被截断后恒为空 | 见第 28 节（`_candidate_window()`：books 元数据 10000） |

---

## 4. 需要用户做的事（长期有效）

1. **Cookie / 验证码类站点**：SiS文學網（b.sis.la）、御宅屋（yswhub.cc）、
   第一版主（banzhu…net）是 Cloudflare 挑战页，菠萝猫（boluomao.com）是 GoEdge
   图形验证码，搬山人小说网（banshanren.com）连 Playwright 浏览器也会被挑战
   （2026-09-12 实测：alicesw / banshanren 的任务都因“连续 5 章被拦截”中止）。
   无头浏览器过不去，需在浏览器（出口 IP 与代理一致）通过验证后，
   把 Cookie 导入「设置 → 书源 → Cookie / 账号」。导入后不需要再等验证。

   （2026-09-16 补充：爱丽丝书屋 alicesw.com 有自己的 `访问验证` 页，连续同步约一天
   ——约 1300 章、已按 `CRAWL_DELAY_MS=1200` 限速——之后开始返回，属真实拦截；
   代码能正确识别为拦截页并中止任务，不会把它当正文存下来。）
2. **代理**：保持 `http://127.0.0.1:27890`；mihomo 节点本身不稳定时，同步会出现
   瞬态 520/超时，任务会自动重试 2 次，不需要手动重发。
3. **部署**：改完本地代码后重建 `backend` 与 `crawler`（前端改动才需要 `frontend`）。
   线上镜像不会自动跟随本地代码。
4. **失效书籍**：源站已删除的书（如爱丽丝书屋 54334）重试也无法修好，只会在
   `crawl_tasks.error` 里给出明确原因；需要时在管理端删除该书。
5. **AI 配置**：线上 backend 目前没有任何 AI 配置（连 `AI_*` 环境变量都没有）。
   部署第 18 节的改动后，用管理员账号进入 **设置 → AI** 填服务地址、模型与 API Key，
   点「测试连接」确认；国内直连 OpenAI/Anthropic 需要打开「使用代理」
   （留空即复用设置 → 代理里的 mihomo 地址）。RAG 还需要单独配一个向量服务。
   详见 [ai-assistant.md](ai-assistant.md)。

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
2. 提交前运行 `cd backend && python -m pytest -q`（当前 434 passed）；
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

## 8. 2026-09-12：Cookie 书源仍报验证码、同步 0 本；全站同步只跑 3 个书源

**现象**：

1. 御宅屋（yswhub.cc，已导入 Cookie）任务失败：
   `Site returned an anti-bot/captcha page (… 请在浏览器中访问该网站通过验证后，把 Cookie
   导入书源再同步)`——而同一个 Cookie 在浏览器里页面完全正常。
2. 绅士漫画（wn09.shop，刚导入 Chrome Cookie）全站同步解析出 0 本书。
3. 要同步第 4 个书源时它排队约 4 小时才轮到（“每次只同步三个书源”）。
4. 御宅屋 / 绅士漫画的书只同步出 1 章，URL 是 `/cdn-cgi/l/email-protection`。

**根因**（都在线上实测复现）：

1. **WAF 误判**：Cloudflare 会给正常页面注入 bot-management 脚本
   `/cdn-cgi/challenge-platform/scripts/jsd/main.js`；`challenge-platform` 是裸子串标记，
   于是**每个** Cloudflare 站点（御宅屋/禁忌书屋/搬山人…）的每个页面都被判成验证码页，
   直接中止任务并给出“请导入 Cookie”的错误方向。实测浏览器已经拿到 140KB 正常页面，
   `_is_blocked_page` 返回 True。
2. **`header` 规则是 JS 时被丢弃**：绅士漫画的 header 是
   `@js:JSON.stringify({"User-Agent":"…Chrome/142…","Referer":baseUrl,…})`，旧代码交给
   `json.loads` 必然失败（`baseUrl` 不是合法 JSON），插件于是用默认的 Android UA 请求，
   站点返回**手机版文档**（实测 50KB、无 `gallary_wrap`），书源自己的规则自然解析不到
   书籍（桌面版 UA 下是 68KB、含 `gallary_wrap`）。
3. **XPath 风格列表规则解析为 0 元素**：`bookList=//div[@class='gallary_wrap']/ul/li`。
   ①`_get_elements` 用 `rule.split("@")` 切分，属性选择器里的 `@` 把规则撕碎；
   ②`_legado_before_elements` 把 `//…` 交给 CSS 解析器，soupsieve 抛
   `Invalid character '/'`，被 `except` 吞掉后返回空。`discover_books` 还有第三层过滤：
   用 `/novel/123` 形状的路径启发式把规则已命中的 URL 全部丢掉。
4. **队列只有全局 3 槽**：`SYNC_WORKER_CONCURRENCY=3`，一个跑几小时的全站任务占满槽位，
   第 4 个书源一直 `pending`（线上 15:16 建的任务 19:26 才启动）。
5. **`chapterList` 是“元素规则 + `@js:` 脚本”**：`_js_code_from_rule` 只认整条规则是 JS，
   组合规则返回空 → 回退到通用链接扫描 → 把页脚 Cloudflare 邮箱保护链接当成章节。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/crawler/plugins/yuedu/__init__.py` | 抽出 `CF_CHALLENGE_MARKERS`：删掉裸标记 `challenge-platform`/`cf-chl`，改用真实的拦截页标记（`just a moment`/`cf_chl_opt`/`cf-chl-`/`chl_page`/`challenge-form`/`cf-turnstile`…），`_is_challenge_page` 共用同一份列表（顺带省掉正常页面 25s 的假等待） |
| 同上 | `@js:` 形式的 `header` 规则用 `JsRuntime` 求值（带 `baseUrl`/`sourceUrl`），纯 JSON/无 Node 时走正则兜底；结果按 `baseUrl+规则` 缓存 |
| 同上 | 拦截错误文案区分「没配 Cookie」/「配了 Cookie 仍被拦（过期或 IP/UA 不匹配）」；顺手修掉 4 处对 stdlib logger 用 loguru `{}` 占位符导致诊断被 TypeError 吞掉的调用 |
| 同上 | `_explore_items_from_html`/`discover_books`：先解析相对 URL，只有书源声明了 `bookUrlPattern` 才用路径启发式过滤，否则以书源 `bookList` 为准 |
| 同上 | `fetch_book`：目录来自 `ruleToc` 时不再用 `_is_chapter_url` 形状启发式二次过滤（规则命中即权威） |
| 同上 | `fetch_explore`：同一书源的多个目录分类默认 4 路并发（`YUEDU_EXPLORE_CONCURRENCY`），请求频率仍由该书源限速器决定 |
| `backend/app/crawler/plugins/yuedu/rule_engine.py` | `_split_element_steps`：按 Legado 的括号配对规则切分 `@`（`RuleAnalyzer`） |
| 同上 | `_xpath_list_rule_to_css`/`_xpath_step_to_css`：把 `//div[@class='x']/ul/li`、`//li[1]`、`//a[@href]` 翻译成等价 CSS；无法翻译的（`text()`/`contains()`/`::` 轴/嵌套谓词）返回 None，保持原行为 |
| 同上 | `_eval_list_rule`：支持“元素规则 + `@js:` 步骤”（脚本只做 `java.put` 时保留元素） |
| 同上 | `_eval_xpath`：先拆 `##pattern##replacement` 再求值，并对结果应用替换；兜底 CSS 失败返回 None 而不是抛异常（原来一条封面规则能中断整本书同步） |
| `backend/app/services/crawl_runner.py` | worker 改为**一个书源一个 worker**：`SYNC_WORKER_CONCURRENCY<=0` 不限；`_next_pending_tasks()` 返回 `(task_id, source)` 并按“已在跑的 source”排除；同一书源永不并发跑两个任务 |
| `backend/app/core/config.py` | `SYNC_WORKER_CONCURRENCY` 默认 0（=不限），新增 `sync_source_concurrency()` |
| `scheduler/app/tasks.py`、`backend/app/api/routes/yuedu.py` | 每日同步 / 批量导入同步同样改为“一个书源一个 worker” |
| `backend/app/core/logging.py` | 新增 `InterceptHandler` + `install_stdlib_logging_bridge()`，插件的 stdlib 日志进入 loguru（有级别/时间戳），`crawl_runner.main()` 启动时安装 |

**验证**：

- `cd backend && python -m pytest -q` → **421 passed**（新增 15 项：假拦截回归、JS header 规则
  含无 Node 兜底、header 缓存、Cookie 文案、`@` 配对切分、XPath→CSS 与不支持形态、XPath 变换、
  组合 TOC 规则、目录并发、书源过滤开关、worker 一源一 worker/同源不并发/不限并发）。
- 线上影子回归（把改动后的 `__init__.py`/`rule_engine.py` 传到容器 `/tmp` 后跑真实站点，
  不动线上代码）：
  - 绅士漫画 `yuedu_f34d61039a65`：改动前 `discover_books(page=1)` → **0 本、UA 是安卓**；
    改动后 → **399 本、UA 是书源声明的 Chrome/142**，`fetch_book` 得到正确书名 + 1 章
    （`全话阅读` → `/photos-view-id-…html`），章节正文 111 字符（图片标签）。
  - 御宅屋 `yuedu_123bca8ecb6a`：浏览器能拿到 140KB 正常页面（标题「耽美小说_御宅屋|御书屋」），
    页面里唯一命中的“拦截标记”是 `challenge-platform` JSD 脚本 → 这就是误判来源；
    新标记列表下该页面不再被判拦截。

**未做/已知**：

- 依赖完整 Legado Android 运行时（`Reload(...)`/`java.importScript`）的书源仍建议换源（UAA）。
- 漫画站的 `ruleContent` 仍依赖 `java.ajax`/`java.put` 链路，正文是图片标签；
  阅读器是否渲染图片内容属于前端话题，不在本次改动范围。
- “一个书源一个 worker”意味着一口气可以跑满所有书源：每源约占 1 个任务连接 +
  `SYNC_BOOK_CONCURRENCY` 个书连接，而连接池是 10+20。书源特别多时用
  `SYNC_WORKER_CONCURRENCY=8` 之类的上限，或改大 `pool_size`。
  线上 crawler 容器限 0.5 CPU / 1G 内存，并行度提高后建议在 NAS compose 里放宽，
  否则 CPU 会先到瓶颈（`YUEDU_PLAYWRIGHT_CONCURRENCY` 也要按内存调整）。
- `tests/` 与 `app/**/__pycache__` 里的 `.pyc` 会被本地测试改写（仓库一直在跟踪它们），
  提交时一并带上即可。

## 9. 2026-09-12：Cookie 书源里个别书报 maximum recursion depth exceeded

**现象**：绅士漫画（wn09.shop，已导入 Chrome Cookie）全站同步能发现、入库书籍，但同一书源
里少数书（`photos-index-aid-342704`、`-337834`、`-359759`）失败，日志/任务是
`Failed to sync book …: maximum recursion depth exceeded`，其余书都正常。

**根因**（线上影子回归复现）：

1. 这些书的 `ruleBookInfo.name` 是 `{{book.name}}`。NovelHub 打开书页时只拿到 URL，
   `book` 上下文为空，`_try_eval_js` 走到兜底「JS 求不出值就把输入原样返回」——输入正是
   整页 HTML，于是模板被替换成整页 HTML。
2. `_eval_field` 把这段 HTML 当规则交给 `_eval_css`：分析器在页面里的 `|` 处切分，若 `|`
   之前存在未闭合的 `[`/`(`，`_chomp_balanced` 返回 False 后位置不前进（Legado 在这里直接
   `throw Error("…后未平衡")`），旧实现却用同一位置继续递归（尾段扫描里则是死循环）。
   只有「`|` 前有不闭合括号」的页面触发，所以同源只有个别书失败。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/crawler/plugins/yuedu/rule_engine.py` | 新增 `RuleUnbalancedError`；`_split_head`/`_split_tail` 按 Legado 语义在括号不平衡时抛错，不再递归/死循环；`_eval_css`/`_eval_json` 捕获后按单片段求值（字段为空，而不是整本书失败） |
| 同上 | `_substitute_inner_rules` 用新的 `_lookup_variable` 解析 `{{book.name}}`/`{{chapter.title}}`（含 `set_chapter_context` 的对象），未知 book/chapter 返回空串；新增 `_try_eval_js_value`，模板求值不再回退成“原样返回输入”，运行时返回输入本身时也视为未解析 |
| `backend/app/crawler/plugins/yuedu/__init__.py` | `fetch_book` 解析 `ruleBookInfo` 前先 `engine.set_book({})`，避免同一引擎里上一本书的 `book.name` 被下本书的模板读到 |
| `backend/tests/test_rule_engine_legado.py`、`backend/tests/test_yuedu_plugin.py` | 新增 6 项回归：不平衡规则抛错而非递归/死循环、`_eval_css` 容错、无 book 上下文时 `{{book.name}}` 不再返回整页、`{{sourceUrl}}`/`{{chapter.title}}` 仍可解析、书页 `{{book.name}}` 回退到页面标题 |

**验证**：`cd backend && python -m pytest -q` → **428 passed**（后续第 10 节又加了用例）。线上影子回归（改动后的
`rule_engine.py`/`__init__.py` 放进容器 `/tmp/shadow` 后跑真实站点）：
上述 3 本原本失败的书全部成功（书名取自页面标题、1 章、章节正文 ~107 字符）；
同一内核下御宅屋 `yswhub.cc/read/91164.html`（Cookie + Cloudflare）36 章、要撸小说
`yaoluku.com/book/57213/`（webView）12 章正文正常，说明改动没有波及其它书源。

## 10. 2026-09-12：部署后日志仍刷 JsRuntime JS error（jsoup shim 能力缺口）

**现象**：第 9 节修复部署后再同步，日志里仍然刷
`JsRuntime JS error: Cannot read properties of null (reading '0')`（20 分钟内 399 条，
每条对应一个列表项），另有零星的 `java.getWebViewUA is not a function` 和
`Unexpected end of JSON input`；绅士漫画的章节正文只有 107 字符（其实是一条图片 URL）。
任务本身是成功的（`books_failed: 0`），但这些报错说明字段被静默丢掉了。

**根因**（都是 `yuedu/jsoup_shim.js` 与 Legado 的语义缺口，线上逐条复现）：

1. **`java.getString` 只认 CSS**：书源的规则写 XPath（绅士漫画 `ruleExplore.kind` 里
   `java.getString("//li/div[@class='info']/div[@class='info_col']/text()")`），shim 用
   CSS 选择器去匹配，必然返回空串，接着 `pages.split('，')[0].match(...)[0]` 就抛
   “Cannot read properties of null”。顺带丢了“49P/137P”这类页数标签。
2. **求值内容不对**：Legado 的 `java.getString` 是对当前解析内容（列表项元素/页面）求值，
   而我们把它设成了规则链上一步的中间值，即使支持 XPath 也找不到节点。
3. **CSS 属性选择器漏了 `=`**：`[class='info']` 退化成“只要有 class 属性就算命中”，
   翻译出来的 XPath 选择器会选错节点（这也是第 1 条能“看似选到东西”的原因）。
4. **`java.get(key)` 被实现成 HTTP**：Legado 单参数是 `java.put` 的变量存储，
   双参数才是 HTTP。绅士漫画的 `ruleContent` 用它取 `imgInfoList`，于是
   `JSON.parse('')` → `Unexpected end of JSON input`，图片列表永远为空。
5. **缺 `java.getWebViewUA()`**：要撸小说的 `header` 规则会调用它。
6. 第 4 条修好之后，绅士漫画的章节规则会“正常地”返回空（书源自己的
   `wnimg1.ru` 前缀已过期），于是章节报 `Chapter returned empty content`——
   通用正文兜底只找文字容器，图片型章节没有出口。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/crawler/plugins/yuedu/jsoup_shim.js` | `java.getString` 支持 XPath 子集（`//tag[@attr='v']/child/text()`、`@attr`、`[n]`→`:nth-of-type`），`@` 步骤按括号/引号配对切分，支持 `##regex##replacement`；补 `nth-of-type` 与 `getWebViewUA()`；修属性选择器 `=`（含 `~=`/`\|=`/`^=`/`$=`/`*=`）；`java.get(key)` 读变量存储、`java.get(url, headers)` 仍走 HTTP |
| `backend/app/crawler/plugins/yuedu/js_runtime.py` | `_eval_js_impl`/`eval_js_sync`/`eval_js` 新增 `content` 参数：JS 里的 `src` 与 `__nhSetContent`（即 `java.getString` 的根）指向当前解析内容，而不是上一步结果 |
| `backend/app/crawler/plugins/yuedu/rule_engine.py` | 新增 `_js_content`，在 `_extract_list`（每个 item）/`_extract_book_info`/`_extract_content`/`_eval_list_rule` 里设置并透传给 JS 运行时 |
| `backend/app/crawler/plugins/yuedu/__init__.py` | `_parse_chapter_content_generic(html, base_url)` 增加图片兜底：没有文字容器时把内容图（排除 logo/验证码/导航图标）输出成 markdown 图片，阅读器可直接渲染 |
| `backend/tests/test_rule_engine_legado.py`、`backend/tests/test_yuedu_plugin.py` | 新增 5 项回归：`java.getString` 的 XPath 取文本/属性、`java.get`/`java.put` 变量存储、`getWebViewUA`、绅士漫画 kind 规则产出 `49P`、图片型章节不再为空 |

**验证**：`cd backend && python -m pytest -q` → **434 passed**；前端 `vite build` 通过。
线上影子回归（改动文件放容器 `/tmp/shadow`）：

- 绅士漫画 `discover_books(page=1)` → **399 本，JS 报错 0 条**，标签出现 `137P/229P/152P`；
- 第 9 节那 3 本原本失败的书：章节正文由“一条 URL/空”变成 178 字符的 markdown 图片；
- 御宅屋（Cookie + Cloudflare）36 章、要撸小说（webView，`header` 用 `java.getWebViewUA()`）
  12 章正文都正常，说明 shim 改动没有波及其它书源。

**顺带的需求（同步页排序）**：最近任务改为“正在执行 → 失败 → 其余（按时间倒序）”。
排序在后端仓库层 `CrawlTaskRepository.list_recent` 用 `CASE` 完成（保证 limit 内先取到在跑/失败
的任务），前端 `SyncPage.vue` 再用同一个 rank 兜一次，避免任务状态原地变化后仍留在旧位置。

## 11. 2026-09-13：要撸小说连续 520；绅士漫画只保存一条图片 URL

**现象**：

1. 要撸小说（`yuedu_b38b98d309e3`）同步时，同一本书从某一章开始连续得到
   `Upstream server returned a transient 5xx error page (Cloudflare/520 etc.)`；
   任务仍继续请求该书后续几十章，crawler 日志被同一种错误刷满。
2. 绅士漫画（`yuedu_f34d61039a65`）部分书（例如 `photos-index-aid-343500`）章节正文是
   `#全话阅读` + 一条裸 `img5.wnimg2.cfd/.../002.jpg?verify=...`，阅读器只能显示文字。

**根因**：

1. 线上实测这些要撸章节/书籍的源站响应确为 Cloudflare `520`（HTTP 与浏览器路径都返回
   Cloudflare 错误页）；这是源站/代理侧故障，不是 Cookie 或规则错误。原逻辑只把单章记为
   failed，仍会继续请求整本书的每个章节。
2. 绅士漫画书源目录阶段的 `chapterList` 能拿到 12 张图片的 `imgInfoList`，但正文 JS 的
   图片域名正则仍写死旧域名 `wnimg1.ru`；当前站已改为 `wnimg2.cfd`，于是 JS 返回空。
   通用兜底只抓当前页面的图片，且当前 CDN 的 `verify` 签名是逐 URL 生成的，不能拿
   `imgInfoList` 直接拼完整图片地址。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/crawler/plugins/yuedu/__init__.py` | 在 `fetch_book` 后把 TOC 脚本产生的 `imgInfoList` 快照保存在插件实例上，避免并发同步时被 Node 全局变量覆盖 |
| 同上 | 正文规则为空且存在图片清单时，沿 `a.btnnext`/`rel=next`/“下一张”链逐页提取 `#imgarea`、`.gallery`、`#picarea` 等正文主图；每页保留各自的 `verify` 查询串，达到清单数量即停止（硬上限 512 页） |
| 同上 | 漫画页优先取正文图片容器，避免把页面顶部广告图当成章节；旧数据中的单条裸图片 URL 会被识别为规则失败并走上述图片链 |
| `backend/app/services/sync.py` | 连续 5 章为 5xx/超时/连接类瞬态错误时提前终止当前书（本次任务跳过该书，后续同步再试），不再对已 520 的整本书逐章轰炸；阈值可用 `SYNC_MAX_CONSECUTIVE_CHAPTER_FAILURES` 调整 |
| 同上 | `_chapter_has_real_content`：纯 Markdown/HTML 图片章节视为有效；只有一条裸图片 URL 的旧章节视为无效，重同步时自动回填 |
| `backend/tests/test_yuedu_plugin.py`、`backend/tests/test_sync_service.py` | 新增图片清单、相册翻页、广告图过滤、图片章节健康判断、瞬态章节终止的回归测试 |

**验证**：`cd backend && python -m pytest -q` → **439 passed**。
线上影子回归（只放容器 `/tmp/shadow`，不动线上代码）：`photos-index-aid-343500` 的正文从 1 条
裸 URL 变为 **12 张图片**，每张使用当前页面独立签名，广告图未混入。

## 12. 2026-09-13：御宅屋任务被误判反爬；绅士漫画图片 401、长标题写不进去

**现象**（线上 `novelhub-crawler` 日志 + `crawl_tasks`）：

1. 御宅屋（`yuedu_123bca8ecb6a`）任务同步到 293 本后失败：
   `Browser request failed: https://yswhub.cc/read/91116.html (RuntimeError: Site returned an
   anti-bot/captcha page …)`；同一个 URL 用浏览器打开完全正常。
2. 要撸小说（`yuedu_b38b98d309e3`）任务报“书源目录本次未返回任何书籍（网络/代理波动…）”
   （全分类页解析 0 本），以及若干章节 520。
3. 绅士漫画（`yuedu_f34d61039a65`）章节正文是本地图片，但阅读器里每张图都打不开
   （图片请求 401）；同一书源另有书报 `[Errno 36] File name too long`、
   `ForeignKeyViolationError: reading_progress_chapter_id_fkey`。

**根因**（都在线上实测复现，不动线上代码）：

1. **弱标记在整页范围确认**：`_is_blocked_page` 的弱标记（`限流` 等）只要在页面任意位置找到确认词就
   判定命中。yswhub 侧栏的相关书籍里有本《限流情缘一线牵》（`限流`），而 Cloudflare 的
   bot-management 脚本必然含 `challenge`（相隔 792 字符），于是每个章节页都被判限流。
   关掉检测器后实测该页是 13113 字节的正常页面（标题「创世之书：少年激斗篇…」），
   `fetch_book`/`fetch_chapter_content` 都能出正文 —— 纯粹误判，且 `_record_outcome` 对
   anti-bot 直接 `raise`，一本书就能中止整个任务。
2. **要撸小说**：探针实测 11 个分类页都能出 10 本、章节正文正常，属于代理/站点那一轮的静默
   失败（HTTP 200 但无列表），现有“瞬态 → 自动重试”逻辑是对的，只是日志只有 URL、看不出
   站点回了什么页面。
3. **绅士漫画图片 401**：章节里存的是 `/api/chapters/<章节 id>/images/<文件>`（实测抽样 40 章
   全是本地图片、没有外链），而浏览器取 `<img>` 不带 `Authorization` 头，该接口只认 Bearer。
4. **长标题**：书目录用「作者/书名」命名，单分量 255 字节上限；御宅屋/绅士漫画的日中文标题
   可达 345 字节。容器里用线上代码 `os.makedirs` 复现出与日志一致的
   `[Errno 36] File name too long`。
5. **FK**：重同步会把旧的空/反爬章节删掉重抓，`reading_progress.chapter_id` 是普通外键，
   用户读过其中一章时删除失败 → 整本书同步失败。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/crawler/plugins/yuedu/__init__.py` | 新增 `WEAK_BLOCK_CONFIRMATIONS`/`WEAK_BLOCK_WINDOW`/`has_weak_block_marker`：弱标记的确认词必须在前后 120 字符内（且不包含标记自身），`_is_blocked_page` 改用它；`_explore_page_diagnostics` + `_describe_fetched_page`：分类页解析 0 本时把「字节数/标题/正文开头」写进 `Explore kind … returned no books` 警告 |
| `backend/app/services/auth.py` | 新增 `MEDIA_COOKIE_NAME`/`MEDIA_COOKIE_PATH`、`_user_from_jwt`、`get_current_user_media`（Bearer 或媒体 Cookie 均可，权限/可见性判断不变） |
| `backend/app/api/routes/auth.py` | `login`/`register`/`me` 种 `novelhub_media` Cookie（HttpOnly、SameSite=lax、Path=/api/chapters、随 JWT 过期），老会话下次打开应用即自愈 |
| `backend/app/api/routes/chapters.py` | 章节图片接口改用 `get_current_user_media` |
| `backend/app/services/storage.py` | `safe_segment` 对超过 `MAX_SEGMENT_BYTES`(240) 的分量按 UTF-8 边界截断并追加 `~sha1[:8]`，同名稳定、异名不撞 |
| `backend/app/services/sync.py` | 删除失效章节前 `_release_chapter_references`：`reading_progress` 置空、`book_versions`/`bookmarks` 清理，不再让一本书的删除失败拖垮同步 |
| `backend/app/models/reading_progress.py`、`backend/alembic/versions/0031_reading_progress_chapter_set_null.py` | `reading_progress.chapter_id` 外键改为 `ON DELETE SET NULL`（迁移用 pg_constraint 查名，可重复执行） |
| `backend/tests/test_yuedu_plugin.py`、`test_chapter_image_auth.py`、`test_sync_service.py`、`test_cover_storage.py`、`test_invites.py`、`test_migrations.py` | 新增 15 项回归（yswhub 书名误判、弱标记邻近确认、页面摘要、Cookie/Bearer/无凭据三种图片鉴权、图片路由依赖、失效章节先解引用、长标题截断与落盘、迁移 head） |

**验证**：`cd backend && python -m pytest -q` → **454 passed**。线上影子回归（只放容器 `/tmp`，
不动线上代码/数据库）：

- 御宅屋 91116：线上插件 `_get` 抛 anti-bot；改动后的插件同一 URL 返回 13113 字节、
  `_is_blocked_page=False`，`fetch_book`→《创世之书：少年激斗篇》+ 2719 字正文；
  构造的真实限流页仍判 True。
- 绅士漫画图片：把改后的 backend 复制到容器 `/tmp/shadow_backend` 起 18099 端口影子服务，
  `/auth/me` 下发 `novelhub_media=…; HttpOnly; Max-Age=86400; Path=/api/chapters; SameSite=lax`，
  带该 Cookie 取图片 → **200 image/gif 5181 字节**；无凭据/坏 Cookie → 401；Bearer 仍 200。
- 长标题：线上逻辑 `os.makedirs` 复现 `File name too long`，改后同一书名写出 238 字节分量且文件存在。
- 迁移 0031：在线上库用事务跑一遍 `DO $$…$$` → 约束变为 `ON DELETE SET NULL`，`ROLLBACK` 后恢复原样（数据库未改动）。

**未做/已知**：要撸小说的 520 与“目录 0 本”是站点/代理侧瞬态，代码按设计自动重试；
章节 5xx 连续 5 章会跳过该书（第 11 节）。老会话在升级后需要刷新一次页面才会带上媒体 Cookie。

## 13. 2026-09-14：代理抖动被误判成反爬（4 个全站任务被中止）；漫画相册固定只剩 12 张

**现象**（线上 `novelhub-crawler` 日志 + `crawl_tasks`）：

1. 09-13 21:45 的 4 个全站任务（風月文學網 h528 / 御宅屋 / 禁忌书屋 / 绅士漫画）全部以
   `同步连续失败超过 10 本，已中止任务以避免持续请求被反爬的站点` 收尾，用户看到的是
   “检查书源规则、Cookie 或站点验证状态”，但书源与 Cookie 都正常。
2. 这些失败的书在日志里是 `Failed to sync book <书名> (<url>): ` ——**冒号后面什么都没有**，
   按站点统计：h528 34/43、御宅屋 25/25、绅士漫画 22/23、禁忌书屋 19/19 都是空错误文本。
3. 日志里每个 beat 周期偶发 `Task tasks.auto_sync_check[…] raised unexpected: RuntimeError(
   "Task <Task …> got Future … attached to a different loop")`。
4. 绅士漫画每本书的章节正文永远只有 12 张图（相册实际 90+ 张），且从第 2 张开始
   （封面 `00001` 缺失）。

**根因**（都在线上容器里实测复现）：

1. **空错误文本 = httpx 超时**：容器内 httpx 0.28.1 实测 `http://10.255.255.1:81`（黑洞地址）
   抛出 `ConnectTimeout`，`str(exc)` 是**空串**（`ConnectError` 才有 "All connection attempts
   failed"）。`SyncService._is_transient_book_fetch` / `_is_transient_chapter_error` 只按错误
   **文本**判定瞬态，空文本必然落到“非瞬态（规则/Cookie）”分支：代理/mihomo 抖动时连错
   10 本就把整个任务判失败，并且日志、`crawl_tasks.error` 里都看不出真正原因。
   （`crawl_runner._is_transient_task_error` 早就按类名兜底，只有书/章这一层漏了。）
2. **`Request failed after retries: <url>` 同样漏判**：`_get`/`_post` 里 429/5xx 的重试分支
   `continue` 时不设置 `last_error`，3 次都失败后抛的是这条没有“5xx/timeout/connection”
   字样的消息，于是 h528 一整批 5xx 也被算成非瞬态。
3. **Celery 每次任务换事件循环**：`scheduler/app/tasks.py` 6 个任务都用
   `asyncio.get_event_loop().run_until_complete(...)`；SQLAlchemy 异步引擎的连接池把
   asyncpg 连接绑在创建它的循环上，第二次任务拿到旧连接就报 “attached to a different loop”。
   线上复现：同一函数连调 3 次，第 2 次必挂；改成“每进程复用一个循环”后连调 4 次全过。
4. **12 张图片**：绅士漫画 `chapterList` 的 `@js:` 脚本要跨相册索引页（`photos-index-page-N-aid-X`）
   `java.ajax` 汇总 `imgInfoList`，服务端只拿到第 1 页的 12 条；而 `fetch_chapter_content`
   把 `len(imgInfoList)` 当成相册页数上限（`max_pages = max(20, gallery_limit+1)`，且
   数量达标就 `break`），正文的相册链在第 12 张被截断。顺带 `sync._process_content_images`
   的 `MAX_CONTENT_IMAGES_PER_CHAPTER = 50` 也会在 50 张处截断。
5. **封面丢失**：`//div[@class='gallary_wrap tb']/ul/li[1]` 以 `/` 开头，Legado
   （`AnalyzeRule.kt`: “ruleStr.startsWith("/") -> Mode.XPath”）按 **XPath** 解析，位置是
   1-based；`_parse_legado_index` 却把结尾的 `[1]` 当成 0-based Legado 索引，选中了第二个
   `li`（= 相册第 2 张的 view 页），于是章节从 `00002` 开始。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/services/sync.py` | 新增 `TRANSIENT_EXCEPTION_NAMES`/`_exception_names`：按异常 **MRO 类名**判定瞬态（`TimeoutError`/`TimeoutException`/`TransportError`/`ConnectTimeout`/`ReadTimeout`/`PoolTimeout`/`RemoteProtocolError`…），书级与章级分类都先走类型判定；`Request failed after retries` 补进瞬态标记 |
| 同上 | 新增 `describe_error()`：`str(exc)` 为空时回退到异常类名并带上 `__cause__/__context__`，替换书/章/封面失败、书架同步、`details[].error` 里所有 `str(exc)`，日志再也不会出现“冒号后空白” |
| 同上 | 新增 `content_image_limit()`（默认 `MAX_CONTENT_IMAGES_PER_CHAPTER=512`，可用环境变量覆盖）替代写死的 50 张；目录整轮 0 本时把插件的分类页诊断（字节数/标题/正文开头）追加进错误文本，520 不再伪装成“书源规则问题” |
| `backend/app/core/config.py` | 新增 `MAX_CONTENT_IMAGES_PER_CHAPTER:int=512` |
| 同上 | 新增 `_is_truncated_gallery()`，`_reconcile_chapter_ids` 按书源 `imgInfoList` 长度把旧的「正好 N 张图」章节判为过期：受影响的漫画书再同步一次就会自动重抓完整相册 |
| `backend/app/crawler/plugins/yuedu/__init__.py` | 相册模式不再按 `imgInfoList` 长度截断：沿“下一张/下一页”走到相册结束，上限 `YUEDU_GALLERY_MAX_PAGES`（默认 512）并在触顶时告警；`_gallery_page_limit()` 新helper；429/5xx 重试分支记录状态码，`Request failed after retries` 现在带 `(HTTP 5xx)` |
| `backend/app/crawler/plugins/yuedu/rule_engine.py` | `_parse_legado_index`：`/`、`//`、`./` 开头的规则一律按 XPath 处理，结尾 `[n]` 不再当 0-based 索引；`_xpath_step_to_css` 位置谓词按 1-based（`[0]` 兼容为第一个） |
| `scheduler/app/tasks.py` | 新增 `_run_async()`：每个 worker 进程复用一个事件循环（`asyncio.new_event_loop` + `set_event_loop`），6 个任务全部改用它，连接池不再跨循环 |
| `backend/tests/test_sync_service.py`、`test_yuedu_plugin.py`、`test_rule_engine_legado.py` | 新增 9 项回归：空文本超时=瞬态、`describe_error`、图片上限可配置、空目录错误带站点诊断、截断相册判过期、相册走到结束、页数上限生效、`//…/li[1]` 取第一个、章节目录 URL 取第一个 `li` |

**验证**：

- `cd backend && python -m pytest -q` → **461 passed**。
- 线上影子回归（只把改动文件放进容器 `/tmp`，不动线上代码/数据）：
  - 绅士漫画 `photos-index-aid-384155`（标题写着 80 ish images）：线上插件 `fetch_book` 得到
    的章节 URL 是 `photos-view-id-32938410`（第 2 张）；改后是 `…32938411`（第 1 张），
    `fetch_chapter_content` 返回 **91 张图**（`![00001]` … `![00091]`），旧代码同一本书是 12 张。
  - Celery 循环问题：容器内同一函数连调 3 次，旧写法第 2 次报 `got Future … attached to a
    different loop`，新写法（复用一个循环）连调 4 次全部成功；改后的 `tasks.py` 在容器内
    可正常导入并连续执行。
  - httpx 空文本：容器内实测 `ConnectTimeout` 的 `str()` 为 `''`，新分类返回瞬态、新错误
    文本为 `ConnectTimeout`（旧代码为空白）。

**未做/已知**：相册正文是「一张图一次请求」，一本 90 张的漫画约 90 次页面请求 + 90 次图片
下载，受书源 `concurrentRate`/`CRAWL_DELAY_MS` 限速，全站同步这类相册站会明显变慢；
需要限量时调 `YUEDU_GALLERY_MAX_PAGES` 或只同步书架。旧数据的 12 张章节会在**下一次
同书同步**时自动重抓（`_is_truncated_gallery`）；相册本来就 ≤12 张书每次同步都会重抓一遍，
量小可接受。

## 14. 2026-09-15：代理抖动一次打断所有并发同步；连接池被一次性事件循环污染

**现象**（线上 `novelhub-crawler` 日志 + `crawl_tasks`，用户报「同步书源时 crawler 里有报错」）：

1. 09-14 23:36–23:57 出现一阵「同一瞬间多本不相关的书失败」：日志里是
   `Failed to sync book …: pop from an empty deque`（29 条）夹杂 `ClosedResourceError` /
   `ReadError`，20 秒内 30 本失败，`ed747ff1`（绅士漫画）与 `357cca4f`（御宅屋）两个全站任务
   被判「同步连续失败超过 10 本…反爬」中止 —— 书源与 Cookie 都是好的。
2. 09-15 02:00–02:04（每天 2 点的 cookie 健康检查）每 60 秒一条
   `Exception terminating connection <AdaptedConnection …>` + `Event loop is closed`，
   伴随 `Task … got Future … attached to a different loop`、
   `get_plugin failed for 'yuedu_7f952bd23f9a'`；该次检查报 `10 failed`。
3. 同一批日志里还有 `Failed to fetch content image <url>: `（冒号后空白，看不出原因）。
4. 09-15 18:55–18:59 要撸小说/h528 的 502/520 属站点/代理侧，不改代码。

**根因**（都在线上容器里复现）：

1. **共享 httpx 客户端被当场拆掉**：`_get`/`_post` 的重试分支只要遇到传输错误就
   `await _reset_http_client(proxy)`，而它 `pop` 出**进程内共享**的客户端并立即
   `aclose()`。一个书源一个 worker + 书/章并发之后，同一时刻有十几个请求共用这个客户端，
   代理一抖 → 其中一个请求的重试把客户端关掉 → 其它请求同时失败。线上容器实测：
   部署版代码 + 6 个并发取书，只要 1 次 reset，6 个请求全部 `ReadError`；
   同一脚本改后 45 秒内 267 次 reset → 0 失败。
   异常类型取决于请求处于哪一步（连接中 / 读 body / 流被关），所以同阵里既有 `ReadError`、
   `ClosedResourceError`，也有 `IndexError: pop from an empty deque`（`deque.pop()` 空队列
   就是这条消息；anyio 自己的 socket 读队列会把它转成 `ClosedResourceError`）。
   `ClosedResourceError` 与 deque 文本都不在瞬态名单里 → 记满 10 本 → 任务被误判「反爬」。
2. **`get_plugin(<source id>)` 把池化连接还给了死循环**：`registry.get_plugin` 查 source id
   时，没有运行中的 loop 就直接 `asyncio.run(...)`，有 loop（celery 任务、FastAPI）时又开
   helper 线程 `asyncio.run(...)`。两者都跑在**一次性事件循环**上，用的却是共享的**池化**
   引擎：连接归还后仍绑在那个已关闭的 loop 上，下一个任务在真正的 loop 上取到它 →
   `attached to a different loop` / 关连接时 `Event loop is closed`。线上容器实测：先正常
   查一次库（连接入池），再 `get_plugin('yuedu_2ca378a79b50')`，立刻复现这两条错误 +
   `get_plugin failed`（正是 2 点那批日志）。
3. 图片下载失败走的是 `logger.warning("…: %s", last_error)`，httpx 异常 `str()` 为空。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/core/database.py` | 新增 `lookup_engine` + `LookupSessionLocal`（`poolclass=NullPool`）：一次性/异地 loop 上的短查询用完即关，绝不进共享池 |
| `backend/app/crawler/registry.py` | `_lookup_source_async` 改用 `LookupSessionLocal` |
| `backend/app/crawler/plugins/yuedu/__init__.py` | `_reset_http_client` 改为**退休**旧客户端：先从 `_clients` 摘掉（重试立刻用新连接池），由后台任务在 `YUEDU_HTTP_RETIRE_SECONDS`（默认 45s > 25s 读超时）后关闭；`YUEDU_HTTP_MAX_RETIRED_CLIENTS`（默认 4）限制积压，溢出时取消最老的（关闭写在 `finally`，取消也会关） |
| 同上 | `fetch_content_image` 失败日志在 `str(exc)` 为空时打印异常类名 |
| `backend/app/services/sync.py` | `TRANSIENT_EXCEPTION_NAMES` 增加 anyio 流错误（`ClosedResourceError`/`BrokenResourceError`/`BusyResourceError`/`IncompleteReadError`）；新增 `TRANSIENT_MESSAGE_MARKERS`（`pop from an empty deque`），书级/章级分类都先过它 |
| `backend/tests/test_yuedu_plugin.py`、`test_sync_service.py`、`test_registry_lookup.py` | 新增 6 项回归：退休不立刻关、退休数量有上限、图片失败日志带类型、anyio/deque 判瞬态且不误伤普通 `IndexError`、lookup 引擎是 NullPool、registry 用未池化的 session |

**验证**：

- `cd backend && python -m pytest -q` → **467 passed**。
- 线上影子回归（把改动后的 3 个文件写进容器 `/tmp/shadow_backend` 并前置 `sys.path`，
  不动线上镜像/数据）：
  - 连接池：改动前 `get_plugin('yuedu_2ca378a79b50')` 报
    `Exception terminating connection … Event loop is closed` +
    `got Future … attached to a different loop`，最后 `Unknown plugin or source`；
    改动后连续 3 次都正常解析出 `YueduPlugin`，共享池查询全程正常，没有任何 loop 报错。
  - 客户端退休：改动前「1 次 reset → 6 个并发取书全失败（6×`ReadError`）」；改动后同一
    脚本、45 秒内 267 次 reset → 0 失败。

**未做/已知**：要撸小说 520、h528 502、御宅屋偶发 `Chapter returned empty content`、
Playwright `Page.goto` 超时都属站点/代理侧，代码按设计重试；本节的改动只保证「代理抖动
不再误伤同一进程里的其它并发同步，也不会再被误判成反爬」。改动要
`docker compose build backend crawler` + `up -d` 后才在线上生效。

## 15. 2026-09-16：正文里的词把正常页面判成验证码页（風月文學網/禁忌书屋任务必失败）；正文图片大面积取不到

**现象**（线上 `novelhub-crawler` 日志 + `crawl_tasks`，用户报「同步书源时 crawler 里有报错」）：

1. 風月文學網 h528（`yuedu_2ca378a79b50`）的全站任务每次都在同一本书上失败：
   `Browser request failed: http://www.h528.com/post/20050.html (RuntimeError: Site returned an
   anti-bot/captcha page …)`；09-14、09-16 两次任务报的是同一本书（《隸孃（01-12）》），
   也就是**每次重试都必然撞死**，书源根本跑不完。
2. 禁忌书屋 cool18（`yuedu_7f952bd23f9a`）同样以 captcha 收尾（`tid=14521293`）。
3. 24h 内 `WARNING` 2836 条，绝大多数是 `Failed to fetch content image <url>: <原因>`：
   `ConnectError` 528、`404 Not Found` 478、`ClosedResourceError` 444、`ConnectTimeout` 80，
   加 `Configured proxy … retrying direct` 657 条。

**根因**（都在线上容器里用真实页面复现，页面已存 `/tmp/h528.html`、`/tmp/cool18.html`）：

1. **强标记是整页裸子串**：`STRONG_BLOCK_MARKERS` 里的 `已被限制` 命中了 h528《隸孃》正文
   「而是我的手**已被限制**在厚實手套中」——Playwright 明明渲染出 166KB 的正常正文
   （`<title>隸孃（01-12）- 風月文學網`），仍被判成拦截页。
2. **确认词太短**：cool18 正文「经过严格的**身份验证**和安检后」附近有「无**人机**在天空中
   盘旋」——`身份验证` 是上下文标记、`人机` 在确认词表里，而 `人机` 是 `无人机` 的一部分，
   于是 86KB 的正常帖子被判定需要人机验证。同一页的 `alert('举报失败，请稍后再试')` 是站点
   公共脚本（第 12 节已处理过），这次是另一条路径。
3. **图片下载路径只认 httpx 异常**：连接在请求中途断开时抛的是 anyio 的
   `ClosedResourceError` / `pop from an empty deque`，落到 `except Exception` 直接放弃（不重试）；
   代理失败后立刻改走直连，而 `img.321cdn.com` / `img5.wnimg2.cfd` 直连必然 `ConnectError`
   （线上实测：走代理 3/5 成功，直连 0/5）；`404` 也会重试 3 次并退避，
   `img.321cdn.com/img/88.webp` 一天被重试 478 次。
4. 附带：图片 CDN 失败会去 `_mark_transport_failure(proxy)`，而线路健康度是按**书源域名**
   记录的，于是 CDN 抖动会把书源页面的代理线路打进 60s 冷却，让页面请求先去试直连。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/crawler/plugins/yuedu/__init__.py` | 新增 `PROSE_GATE_MARKERS`（`已被限制`、`被限制访问`、`请启用javascript`、`请开启javascript`）并从 `STRONG_BLOCK_MARKERS` / `CF_CHALLENGE_MARKERS` 移入 `CONTEXTUAL_BLOCK_MARKERS`（需 90 字符内有确认词）；`CONTEXTUAL_BLOCK_HINTS` 的 `人机`→`人机验证`、`频繁`→`访问频繁/请求频繁/操作频繁/过于频繁`；`WEAK_BLOCK_CONFIRMATIONS` 同样去掉裸 `频繁` |
| 同上 | `has_contextual_block_marker`：标记自身与确认词同名时跳过该次（不再自己确认自己），窗口其余部分保持原样（`您的账号已被限制访问` 仍能命中） |
| 同上 | 新增 `is_transient_transport_error()`（httpx 传输错误 + anyio 流错误 + `pop from an empty deque`，普通 `IndexError`/`HTTPStatusError` 不算）并在 `_get`/`_post`/图片/封面的重试循环里用它，遇到这类错误先 `_reset_http_client` 再原地重试 |
| 同上 | `fetch_content_image`/`fetch_cover`：换掉坏连接后重试同一线路、`404`/`410` 立即放弃并写入 `_missing_image_urls`（30 分钟 TTL，去重死链）、图片 CDN 失败不再污染书源线路健康度、封面失败日志补异常类名 |
| `backend/tests/test_yuedu_plugin.py` | 新增 8 项回归：真实正文（已被限制/身份验证+无人机）不再误判、真实拦截页仍被识别、标记不自证、`频繁` 裸词不再确认、流错误判瞬态而普通 IndexError/HTTPStatusError 不判、图片遇流错误重试一次即成功、`404` 只请求一次且第二次命中缓存、`_get` 遇流错误重试 |

**验证**：

- `cd backend && python -m pytest -q` → **475 passed**。
- 线上影子回归（把改动后的 `__init__.py` 写到容器 `/tmp/shadow_yuedu.py`，前置加载，不动线上镜像/数据）：
  - 两个真实页面：部署版 `_is_blocked_page` 都是 `True`，改动后都是 `False`；
    Cloudflare 挑战页 / `limit_box` / `访问过于频繁` / GoEdge 验证码页四种真实拦截页两边都仍是 `True`。
  - `fetch_book`：h528 那本书部署版 33.7s 后抛 captcha 错，改动后 4.7s 成功（1 章、正文 110242 字符）；
    cool18 那帖部署版 35.9s 抛错，改动后 2.3s 成功（正文 56195 字符）。
  - 正文图片：三张以前必失败的 `img.321cdn.com` 图改动后都取到字节（1.0–1.9s）；
    `img/88.webp` 第一次 1.59s 返回 None，第二次 **0.00s** 命中缓存不再发请求。

**未做/已知**：`请启用JavaScript` 单独出现（没有验证码/挑战等确认词）不再算拦截页——它太常见于
普通页面的 `<noscript>`，误判代价远大于收益；真正的 Cloudflare JS 挑战仍由 `_is_challenge_page`
与 CF 标记识别。要撸小说 520、h528 502 等站点侧错误不变。改动要
`docker compose build backend crawler` + `up -d` 后才在线上生效。

## 16. 2026-09-16：小说和漫画混在一起（需求：小说一页、漫画一页）

**现象**：书库只有一个列表，23k 本书里混着绅士漫画的 1849 本图集；用户要求「小说一页、
漫画一页」。数据里**没有**任何 novel/comic 字段，只有书源（`sources.config->>'bookSourceType'`）
和各自的书标签/分类能间接看出来。

**根因**：`books` 表只区分 R18 与全年龄，没有「阅读形态」这一维度，前端也就只能混排。

**判定规则**（`backend/app/services/book_kind.py`，不为单站点写死）：

1. 书源自报图片源（Legado `bookSourceType == 2`，线上只有绅士漫画 `yuedu_f34d61039a65`，1849 本）；
2. 书的标签/分类命中漫画关键词（漫画/漫畫/图集/圖集/画集/畫集/写真/寫真/comic/manga/webtoon）；
3. 章节正文只有图片标记、去掉标记后几乎没有文字（兜底：自报 text 的图源也能认出来）。
   注意 `動漫改編` 是風月文學網的**小说**标签，故意不在关键词里。

自动判定只会从 novel 升到 comic，不会反向降级；只有管理员「重新识别」勾了严格重算才会改写。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/models/book.py` | 新增 `books.kind`（`novel`/`comic`，默认 `novel`）+ `ix_books_kind` |
| `backend/app/services/book_kind.py` | 新增：`is_comic_source / is_comic_label(s) / is_comic_content / resolve_kind / classify_book`，以及 `reclassify_books()`（分批重算，可选读正文） |
| `backend/app/services/sync.py` | 同步时按书源+标签定 kind，章节正文判定为图集时把书升为 comic（`sync_book`、`resync_chapter`、`_ensure_book_row`） |
| `backend/app/api/routes/books.py` | `/books/browse`、`/books/home`、`/books/favorites` 支持 `kind=novel|comic`；新增 `POST /books/reclassify?scan_content=&source_id=&force=`（管理员；`force` 才允许把误判的漫画改回小说）；`BookOut.kind` |
| `backend/app/api/routes/categories.py` | `GET /categories?kind=` 只返回该类型下真实存在的分类 |
| `backend/app/services/visibility.py` | 抽出 `apply_book_visibility()` 供 books/categories 共用（逻辑不变） |
| `backend/alembic/versions/0032_book_kind.py` | 加列 + 索引 + 回填（书源类型 2、标签/分类关键词） |
| `frontend/src/router/index.ts`、`pages/BooksPage.vue` | 新增 `/novels`、`/comics`，复用书库页逻辑，按 `kind` 过滤分类/书源/分页/搜索跳转，页头加「全部/小说/漫画」切换 |
| `frontend/src/pages/AdminPage.vue` | 设置 → 索引 → 「小说 / 漫画识别 → 重新识别」（可勾选读正文 / 严格重算），对应 `POST /books/reclassify` |
| `frontend/src/components/NavBar.vue`、`pages/HomePage.vue`、`components/BookCard.vue`、`stores/i18n.ts` | 导航加「小说 / 漫画 / 书库」；书架按类型筛选（存 `novelhub_shelf_kind`）；卡片给漫画加标记 |
| `backend/tests/test_book_kind.py`、`test_migrations.py` | 新增分类回归 + head 断言更新到 `0032_book_kind` |

**验证**：

- `cd backend && python -m pytest -q` → **483 passed**。
- 线上只读核算回填口径：`image_source` 1849 本、`label_tags` 1191 本、`label_categories` 1187 本，
  合并后 **comics 1856 / novels 21566**（总数 23422，两边互补）。
- 用真实正文跑 `is_comic_content()`：绅士漫画随机 10 章全部 `True`；7 个小说书源随机 10 章
  （含 72 字符的残章）全部 `False`。
- 前端 `vue-tsc --noEmit` 无错误、`vite build` 成功。

**未做/已知**：全文搜索（Meilisearch）不按 kind 过滤，`/novels`、`/comics` 页里的搜索结果仍是
全库结果；旧书由迁移回填，剩下判错的（图源自报 text、目录被解析成图集）用「设置 → 索引 →
小说 / 漫画识别 → 重新识别」或 `POST /api/books/reclassify?scan_content=true&source_id=<书源>` 重算。改动要
`docker compose build backend crawler frontend` + `up -d` 后在线上生效（迁移由 backend 启动时自动执行）。

## 17. 2026-09-17：cookie 健康检查的超时掐断 Playwright 导航，日志刷 ERROR；御宅屋被误报「Cookie 已过期」

**现象**（线上 `novelhub-crawler` 日志，用户报「同步书源时 crawler 里有报错」）：

1. 24 小时内 8 条 ERROR，其中 4 条是浏览器收尾噪声：
   - 02:04:19、02:05:15、02:06:18 —— 每天 2 点的 cookie 健康检查，两两间隔 56s / 63s（≈
     `COOKIE_CHECK_ITEM_TIMEOUT` 默认 60s）：`Future exception was never retrieved` +
     `TargetClosedError('Target page, context or browser has been closed')`；
   - 12:10:10 —— 御宅屋任务收尾时：`Task exception was never retrieved` +
     `PipeTransport.run → InvalidStateError: invalid state`。
2. 御宅屋（`yuedu_123bca8ecb6a`）12:10 的失败信息是
   `Site returned an anti-bot/captcha page (…书源已配置 Cookie 但仍被站点拦截：Cookie 可能已过期…)`，
   可这个**书源根本没配过 Cookie** —— 用户会去找一个不存在的 Cookie 重新导入。

**根因**（都在线上容器里复现）：

1. **超时会在导航途中取消 `page.goto`**：cookie 健康检查用
   `asyncio.wait_for(_check_one(cookie), timeout=COOKIE_CHECK_ITEM_TIMEOUT)`，而浏览器路径单次最坏要
   `goto` 45s + 挑战等待 25s = 70s，超时必然落在导航中间。取消 `page.goto` 会把 Playwright
   自己的导航 future 丢下没人读，GC 时 asyncio 就用 ERROR 打出「Future exception was never
   retrieved」。线上容器实测：部署版插件 + `wait_for(..., 2.5)` 取消同一本书 → **1 条**未取回异常。
2. **`_captcha_hint` 读的是 `self._cookie`**，而该字段同时被 `_capture_cookie_jar` /
   `_capture_playwright_cookies` 写入站点自己 `Set-Cookie` 的会话 cookie。御宅屋渲染一页就下发
   `fontsize=16px`，于是「没配 Cookie」被说成「配了 Cookie 但过期了」。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/crawler/plugins/yuedu/__init__.py` | `_fetch_with_playwright` 拆成 `_launch_chromium` + `_render_page`：渲染跑在 `asyncio.ensure_future` 里并以 `asyncio.shield` 等待，调用方超时/取消时**不取消导航**，改为关掉浏览器让渲染收尾、再 `await` 它取回异常，最后重抛 `CancelledError`；新增 `_close_browser`（关两次或在取消中被中断都不遮住真正的错误）；`_render_page` 用 `finally` 保证 context 先于 browser 关闭 |
| 同上 | 新增 `_configured_cookie`（由 `set_cookie` / `fetch_bookshelf` 写入），`_captcha_hint` 改用它判断「有没有配 Cookie」 |
| `backend/tests/test_yuedu_plugin.py` | 新增 2 项回归：`test_blocked_page_error_ignores_session_cookies_the_site_sets`、`test_cancelled_playwright_fetch_settles_the_navigation` |

**验证**：

- `cd backend && python -m pytest -q` → **488 passed**。两项新测试在**改动前**都会失败
  （`the in-flight navigation was cancelled`），确认它们真能复现问题。
- 线上影子回归（改动后的 `__init__.py` 送到容器 `/tmp/shadow_yuedu_plugin.py`，用 importlib
  单独加载；不动线上镜像、代码与数据库）：同一本书、同样 `wait_for(2.5)` 取消 ——
  部署版 `Future exception was never retrieved` **1 条**，改动后 **0 条**；
  只带站点会话 cookie（`fontsize=16px`）时改动后提示「把 Cookie 导入书源」，
  `set_cookie` 后才变成「已配置 Cookie 但仍被拦截」。

**未做/已知**：御宅屋 12:10 那 5 章被判拦截是**真实拦截**，不是误判 —— 同一 URL 现在用 httpx
与 Playwright 都能拿到 14KB 正常页面（标题 `【无限道淫棍路】（25-26）_万淫之首…`），当时是连续同步
两天的站点侧限速，代码按设计中止任务。本节只保证「超时/取消不再产生无意义的 ERROR」和「拦截文案
不再指向错误的排查方向」。改动要 `docker compose build backend crawler` + `up -d` 后才在线上生效。

## 18. 2026-09-17：AI 功能补齐（配置、上下文、流式、划词、RAG）

**背景**：项目一直「预留了 ai 接口」但从未真正可用。线上 `novelhub-backend` 容器里
**没有任何 `AI_*` 环境变量**；前端只有阅读器侧栏一个纯聊天面板，`summary/person/timeline/rag`
接口完全没有 UI 入口。

**根因**（读代码得出，未改线上）：

1. 配置只有环境变量，改模型/换 Key 都要重建容器；`RAGService` 还**硬编码** `AI_BASE_URL`
   默认 `https://api.openai.com/v1`，非 OpenAI 的 provider 会把文本 POST 到 OpenAI。
2. `claude` provider 用 OpenAI 的 `/chat/completions` + `Authorization: Bearer` 发请求，
   Anthropic 是 `/v1/messages` + `x-api-key`，**这条分支从来不可能成功**。
3. 上下文永远取「书的前 N 章」（`context_chapters=5` 就是第 1-5 章），跟读者当前读到哪章无关；
   `tokens_used`、`chapters_covered` 恒为 0 占位值；摘要只读前 30 章就截断。
4. AI 请求**完全不使用项目已有的代理配置**（`proxy_config.json` → mihomo），国内网络下
   OpenAI/Anthropic 必然连不上，而报错是一句 `str(exc)`。
5. 人物/时间线的 `json.loads` 一旦失败（模型习惯加 ```` ```json ```` 围栏）就丢掉整个回答，
   只留下一行 `Parsing failed`。
6. `httpx.AsyncClient()` **每次请求重建 TLS 上下文**：本机实测 2.45s/次
   （`ssl.create_default_context()` 只有 16ms，开销在解析 certifi 证书包）。
7. SSE 没有任何网关侧准备：nginx 默认会 `proxy_buffering`，整段回答会被缓冲到结束才吐给浏览器。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/services/ai_config.py`（新） | DB 级 AI 配置（`app_settings.ai_*`）+ 10 个 provider 预设（kind/默认 base_url/模型/向量模型/是否需要 Key）；`resolve_ai_config()` 纯函数（stored 覆盖 env）；API Key 用 `enc:` 前缀 AES-GCM 加密入库，回给前端只有掩码；`FIELD_TO_KEY` 让调用方用字段名、存储用 `ai_*` |
| `backend/app/services/ai_client.py`（新） | `LLMClient`：OpenAI 兼容 + Anthropic 两套协议（endpoint 归一化、system 提取、同角色消息合并）、429/5xx 退避重试、流式（OpenAI `data:` 帧 / Anthropic `content_block_delta`）、embedding 分批、`AIError` 把 401/404/429/超时/连接失败翻译成可执行的排查提示；代理走 `proxy_config`；**TLS 上下文模块级缓存** |
| `backend/app/services/ai.py`（重写） | `AIService` 配置改为 DB 读取；上下文按**阅读位置**取窗口（`build_context`，无位置时回退书首并说明）；RAG 命中优先并带章节出处；摘要改 **map-reduce**（分批 map + 合并 reduce，超上限等距抽样）；人物/时间线**等距抽样整本书** + `parse_json_list` 容忍围栏/散文；`transform` / `stream_transform` 支持划词 5 种操作；真实 token 统计 |
| `backend/app/services/rag.py`（重写） | 向量化改走 `LLMClient`（同一 provider/代理/Key 体系，可与对话模型不同）；**先查库再决定是否向量化**（未建索引的书不再白花一次 embedding 调用）；`index_status` / `list_indexed_books` / 分批 flush / `force` 与 `max_chunks`；检索用 numpy 矩阵（保留纯 Python 兜底） |
| `backend/app/api/routes/ai.py` | 新增 `GET /ai/status`、`POST /ai/chat/stream`、`POST /ai/transform`、`POST /ai/transform/stream`；`chat` 支持 `chapter_number`/`mode`/`history`；`summary` 支持 `max_chapters`/`style`；SSE 响应带 `X-Accel-Buffering: no` |
| `backend/app/api/routes/rag.py` | 新增 `GET /rag/status/{id}`、`GET /rag/index`（已索引书籍）；`POST /rag/index/{id}` 支持 `force`/章节范围/`max_chunks` |
| `backend/app/api/routes/admin.py` | 新增 `GET/PUT /admin/ai`（校验数值范围、掩码表示不修改 Key）与 `POST /admin/ai/test`（对话 + 向量双探测） |
| `frontend/src/api/stream.ts`（新） | `fetch` + 手写 SSE 帧解析（`EventSource` 不能带 Authorization 和 JSON body） |
| `frontend/src/components/AIChat.vue`（重写） | 四个标签页（问答/摘要/人物/时间线）、流式输出与停止、章节出处标签、上下文来源说明、未配置时给原因和「去设置 AI」按钮 |
| `frontend/src/components/AISelectionToolbar.vue`（新） | 划词浮层工具条（解释/翻译/润色/续写/问 AI）+ 流式结果卡片 + 复制 + 翻译目标语言切换并重新生成 |
| `frontend/src/pages/ReaderPage.vue` | 接入划词组件（桌面正文 / 手机滚动正文容器）、AI 侧栏传当前章节、`问 AI` 预填提问、点设置跳 `/admin?tab=ai`；侧栏宽度 80→96 |
| `frontend/src/pages/AdminPage.vue` | 新增 **AI** 标签页：provider 预设联动、Key 掩码状态、代理开关（默认复用爬虫代理）、温度/token/超时/上下文上限、向量服务独立配置、**测试连接**结果卡片、RAG 索引管理与已索引列表；支持 `?tab=ai` 深链 |
| `frontend/src/stores/i18n.ts` | 新增 60+ 中英词条（`ai_*` / `admin_ai_*` / `admin_tab_ai`） |
| `nginx/default.conf`、`nginx/nginx.conf` | `/api` 增加 `proxy_buffering off` + `proxy_cache off`（并给 `nginx.conf` 补 3600s 读写超时），否则 SSE 会被整体缓冲 |

**验证**：

- `cd backend && python -m pytest -q` → **560 passed**（新增 72 项）。
  - `tests/test_ai_service.py`：配置解析（env 兜底/stored 覆盖/无 Key 的本地 provider/掩码回显/
    加密往返）、OpenAI 与 Anthropic 请求体、401/5xx/代理错误的文案、SSE 解析（两套协议）、
    embedding 分批与顺序、窗口上下文落在当前章附近、history 截断、摘要 map-reduce 与抽样、
    JSON 围栏/散文解析、划词校验、RAG 排序/未索引不调向量化、TLS 上下文只建一次。
  - `tests/test_ai_client_live.py`：对**真实本地 HTTP 服务**验证非流式回答、SSE 逐段送达
    （`stream: true` 真的上了线）、embedding、Anthropic 用 `x-api-key` 与 `/v1/messages`、
    开启代理后请求不再直达目标，以及 `AIService` 真的把「读者所在章节的正文」写进了请求体。
  - `tests/test_ai_api.py`：`/ai/status` 的可用性与原因、chat 透传阅读位置与出处、
    provider 失败返回可读 502、`/ai/chat/stream` 与 `/ai/transform/stream` 的 SSE 帧、
    `/admin/ai` 保存时 Key 被加密且不回显、掩码不覆盖已存 Key、数值范围 422、RAG 状态 404/502。
- 前端 `npm run typecheck`（vue-tsc）无错误、`npm run build` 成功。
- 性能：TLS 上下文缓存后，本机 5 项真实 socket 测试从 16s 降到 3.7s；
  实测 `httpx.AsyncClient()` 构造 2.45s → 0.002s。

**未做/已知**：

- **本次只改本地代码，没有部署**（用户要求）。线上要生效需要
  `docker compose build backend frontend` + `up -d`（以及 `nginx` 配置如果用的是仓库里那份）。
- 线上 backend 至今没有 AI 配置：部署后必须去 **设置 → AI** 填服务地址与 Key，
  否则阅读器侧栏只会显示「AI 还没有配置好」及其原因。
- RAG 建索引是**同步请求**，大书耗时较长（网关超时已放到 3600s）；没有做后台任务化。
- 对话历史不落库，刷新页面即丢失；RAG 索引没有按正文 hash 做增量失效，
  章节内容变了要手动 `force=true` 重建。
- 向量检索是进程内余弦计算（numpy），单本上限 2000 片段；书特别大时需要调 `max_chunks`
  或后续换成 pgvector。

## 19. 2026-09-17：AI 测试连接报 400「模型名不被支持」（DeepSeek 模型名已变）

**现象**：用户在 **设置 → AI** 填好 DeepSeek 的 API Key 后点「测试连接」，对话接口报
`AI 服务拒绝了请求（HTTP 400）：通常是模型名不被支持或参数超限。服务端信息：
{"error":{"message":"The supported API model names are deepseek-flash, deepseek-v4-pro,
but you passed DeepSeek-V4.1-Flash."}}`

**根因**：不是 Key、不是代理、不是网络 —— 模型名写错。第 18 节的预设里 DeepSeek 默认模型是
`deepseek-chat`，而当前该端点只接受 `deepseek-flash` / `deepseek-v4-pro`；用户手填的
`DeepSeek-V4.1-Flash` 两者都不是。代码这边的两个问题：① 预设的默认模型名会随厂商改版过期；
② 报错虽然把服务端原文带出来了，但要用户自己肉眼从 JSON 里挑名字，而且没有别的办法问服务端
「你到底提供哪些模型」。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/services/ai_client.py` | 新增 `extract_supported_models()`：从 `supported API model names are X, Y` 这类报错里提取模型名；新增 `models_endpoint()` + `LLMClient.list_models()`（`GET {base}/models`，兼容 `data`/`models`/裸数组三种响应，Anthropic 自动补 `/v1`）；`_error_hint` 在 400 且能解析出模型名时直接给结论（「当前填的是 X，该服务只接受 Y、Z」）；新增 `diagnose()` 把「测对话 + 失败时找可用模型名 + 测向量」收敛成一处 |
| `backend/app/api/routes/admin.py` | 新增 `POST /admin/ai/models`（可用表单里**尚未保存**的 provider/base_url/api_key 去查，Key 留空则用已存的）；`POST /admin/ai/test` 改用 `diagnose()`，失败时响应带 `available_models` + `current_model` |
| `backend/app/api/routes/ai.py` | `/ai/test` 同样改用 `diagnose()`（去掉重复实现） |
| `backend/app/services/ai_config.py` | DeepSeek 预设默认模型 `deepseek-chat` → `deepseek-flash`（附注释：模型名会变，正解是按钮拉取） |
| `frontend/src/pages/AdminPage.vue` | 「模型」旁边加 **拉取模型** 按钮 + 可用模型按钮（点一下填入，向量字段单独一组候选，会过滤出 embedding 类模型）；「测试连接」失败且带 `available_models` 时，把这些名字做成按钮并提示「填入后记得保存再测」 |
| `frontend/src/stores/i18n.ts`、`docs/ai-assistant.md`、`README.md` | 新增词条；排错表补 400 这一行；DeepSeek 默认模型改成 `deepseek-flash` |

**验证**：

- `cd backend && python -m pytest -q` → **569 passed**（新增 9 项）。
  - 用用户贴的**原始报错文本**做断言：`extract_supported_models()` →
    `["deepseek-flash", "deepseek-v4-pro"]`，普通报错返回空；`_error_hint(400, …)` 会输出
    「模型名不被支持：当前填的是「DeepSeek-V4.1-Flash」，该服务只接受 deepseek-flash、
    deepseek-v4-pro」。
  - `tests/test_ai_client_live.py` 的本地桩服务新增 `GET /v1/models` 与「模型名不对就返回 400
    并列出可用名字」两条路径：验证 `list_models()` 打到 `/v1/models`、`chat()` 拿到的错误文本
    包含正确模型名、`diagnose()` 在失败时给出 `available_models`。
- 前端 `npm run typecheck` / `npm run build` 通过。

**给用户的处置**：把「模型」改成 `deepseek-flash`（或 `deepseek-v4-pro`）→ 保存 → 重新
「测试连接」；以后模型名再变，直接点「拉取模型」按服务端返回的列表选。

## 20. 2026-09-17：同步报错时让 AI 分析（诊断 + 补丁提案走审批，AI 绝不直接改配置）

**需求**：用户问「AI 能在书源同步报错的时候，针对报错进行配置上的修改吗」。确认后的范围是
**只诊断 + 生成补丁提案走人工审批**，触发方式是**手动按钮 + 任务失败自动跑一次（可关）**。

**设计约束（重要，改这块前先读）**：

1. `docs/NovelHub-AI-Development-Context.md` 的红线写着「AI/RAG 只做消费端，不参与爬取/解析/下载」
   「不为单站点写死逻辑」「不绕过验证码/WAF/登录限制」。所以**任何 AI 产出的配置改动都必须经
   管理员批准**，且不允许出现绕过验证码/伪造身份之类的建议。
2. 线上失败绝大多数是**站点侧**（Cloudflare 挑战、520/502、代理抖动、站点限速、源站删书 ——
   见第 11/13/14/15/17 节）。对这类失败去「改配置」不但没用，还会**静默把好源改坏**。
   因此服务端有一道硬校验：分类不是配置类问题时，**强制丢弃**模型给出的规则修改建议。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/models/sync_diagnosis.py`（新）+ `alembic/versions/0033_sync_diagnoses.py`（新） | `sync_diagnoses` 表：一行一个任务（`uq_sync_diagnoses_task`），存分类/置信度/结论/`payload`（判断依据、建议下一步、建议修改、证据快照）/模型/token/`change_id`；`task_id`、`source_id` 外键级联 |
| `backend/app/services/ai_diagnosis.py`（新） | 采集证据（任务报错 + 逐书/逐章失败按错误文本分组带样本 + 该书源最近 5 次任务 + 书源规则 JSON，规则按 key 分块渲染、单条过长截断但**每个 key 都可见**）；`sanitize_diagnosis()` 校验并**丢弃非配置类的规则建议**、限制条数/长度/风险取值；`describe_changes()` 给每条建议附上**真实当前值**并标记「模型记错了」（引用被截断的值不算不一致）；`build_patch()` 把 `config.a.b` 展开成嵌套补丁；`diagnose_task()` / `auto_diagnose_task()`（后者自开 session、自查开关与状态、不抛异常） |
| `backend/app/services/source_patch.py`（新） | `apply_source_patch()`：`config` 深度合并（不清掉其它规则）+ 仅允许 `name/url/plugin_name/enabled/is_r18` 五列、布尔可转换、非法/超大/不可序列化的补丁直接拒 |
| `backend/app/api/routes/ai.py` | 新增 `GET /ai/diagnose/{task_id}`、`POST /ai/diagnose/{task_id}`（`force` 重跑）、`POST /ai/diagnose/{task_id}/propose`（生成待审批 `SourceChange`，重复调用幂等）。三个都是**管理员权限**（回答里会引用书源规则原文） |
| `backend/app/api/routes/source_changes.py` | 新增 `update` 动作与审批落地：管理员直接生效，普通用户进 pending；`approve` 调 `apply_source_patch`，`reject` 什么都不写 |
| `backend/app/services/crawl_runner.py` | 任务跑完后 `_spawn_auto_diagnosis(task_id)`：`asyncio.create_task` 后台跑（慢模型绝不阻塞队列，引用放 `_diagnosis_tasks` 防 GC），内部自查开关/状态/是否已分析过，永不抛异常 |
| `backend/app/services/ai_config.py`、`ai.py` | 新增设置 `ai_auto_diagnose`（默认开，可用 `AI_AUTO_DIAGNOSE` 覆盖）；`not_configured_reason()` 抽到 ai_config 供诊断复用 |
| `frontend/src/pages/SyncPage.vue` | 选中任务后加载/展示诊断：分类徽章、置信度、结论、判断依据、建议下一步、建议修改（字段/当前值/建议值/理由/风险/「与当前值不一致」标记）、「提交为待审批提案」；仅管理员可见 |
| `frontend/src/pages/AdminPage.vue` | AI 设置新增「同步失败后自动 AI 分析」开关；**审批面板渲染 `update` 提案的完整 diff** 与「来自 AI 分析」标记 |
| `frontend/src/stores/i18n.ts`、`docs/ai-assistant.md`、`README.md` | 新词条 20+；`ai-assistant.md` 新增「同步报错诊断」一节（能改什么、不能改什么、接口、怎么读结论） |

**验证**：

- `cd backend && python -m pytest -q` → **612 passed**（新增 43 项）。
  - 安全回归（重点）：`site_side`/`proxy` 分类下模型硬塞的规则建议被**丢弃并计数**；
    诊断全程 `sources.config` **字节级不变**（测试里对 config 做深拷贝前后比对）；
    补丁只能改白名单字段与 `config` 合并（其它规则保留、`owner_id` 之类不落库）；
    非 JSON 回答、不可序列化/超大补丁、未知任务都按预期拒绝；
    `auto_diagnose_task` 在功能关闭/任务成功/已分析/AI 报错/内部异常时都**返回 None 且不抛**。
  - 迁移链断言更新到 `0033_sync_diagnoses`。
- 前端 `npm run typecheck`、`npm run build` 通过；i18n 中英各 863 键、无单边键
  （顺带修掉了本次编辑引入的一处 zh 词条被并行的笔误）。

**未做/已知**：

- 本次仍**只改本地代码**；线上生效需要 `docker compose build backend crawler frontend` +
  `up -d`，迁移由 backend 启动时执行（新增了 `0033`，部署时注意）。
- 自动诊断每次失败任务调用一次模型（默认开）。全站同步每天跑 12 个源、每个源几本失败
  → 每天约十几次调用，觉得吵或费 token 就在设置里关掉。
- AI 仍然**不会**自动改书源、自动导 Cookie、自动处理验证码；第 2 层「提案 + 人工批准」
  是这次做到的边界，再往上（自动执行白名单：重试/降速/临时禁源）这次没做。

---

## 21. 2026-09-18：线上同步报错排查 + 每书源「同步间隔」（拉取间隔）

**需求**：①「线上容器同步报错了，排查一下」；②搬山人这类站点有「拉取间隔」限制（例如一分钟一次），
要求**在书源页面给每个书源配一个同步间隔**，不配就用默认间隔，且**启动同步任务时读取并使用**该值。
用户确认：间隔语义 = **每次上游请求之间**的间隔；默认（不配）= 沿用现状（源自带 `concurrentRate`
优先，否则 `CRAWL_DELAY_MS`=1.2s/请求）。

**排查过程与结论**（只读线上，未改任何线上配置/数据）：

```bash
# 任务态与报错（psql 端口是 15432，不是 5432）
docker exec novelhub-postgres psql -U novelhub -d novelhub -p 15432 -P pager=off \
  -c "select id,source,status,created_at,finished_at,error from crawl_tasks order by created_at desc limit 20;"
docker logs --tail 5000 novelhub-crawler 2>&1 | grep -E "ERROR|Traceback"
```

- 容器全部健康（backend healthy）；日志里**没有任何代码崩溃**（只有一处任务失败 ERROR）。
- 失败分类（166 个任务：failed 121 / cancelled 24 / completed 15 / running 4 / paused 4 /
  completed_with_errors 3）全都是**站点侧**：
  - **要撸小说 `www.yaoluku.com`**：经代理连续 3 次 **HTTP 520**（Cloudflare 回源错误），
    直连 000（NAS 无直连能力）。→ 目录页 0 本书 → 任务经 2 次自动重试后失败。
    **站点侧问题，改代码/配置都无效**。
  - **搬山人 `www.banshanren.com`**：此刻代理直取首页 **200**，但同步是「连续 5 章被反爬拦截」。
    → 站点限速，书源自带 `concurrentRate=1000`（1 秒 1 次）远快于站点真实限制。**这就是需求 ②**。
  - **御宅屋 `yswhub.cc`**：代理直取 **403**；**爱丽丝书屋** 200；禁忌书屋/風月文學網 是 Cookie/人机验证。
- 顺带发现一个**真 bug**：`crawl_tasks.created_at` 是 DB 的 `now()`（容器本地 CST），而
  `started_at/finished_at/resume_at` 是 Python 的 `datetime.now(timezone.utc)`（UTC）。四个容器
  `TZ=Asia/Shanghai`，于是同步页显示「创建 23:06 / 开始 15:37 / 结束 15:39」——**结束早于开始**，
  时长全是负的。schema 里其它表都是 `now()`（本地），前端用 `new Date(v).toLocaleString()`
  把无时区的 ISO 串当本地时间，所以**本地时间才是这个项目的约定**。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/core/clock.py`（新） | `naive_now()` = `datetime.now()`；模块 docstring 写清「DB `now()` 是本地时间 + 前端按本地时间渲染 ⇒ Python 写入也必须是本地 naive」。`crawl_runner.py`（9 处）、`api/routes/crawl.py`（5 处）、`api/routes/invites.py` + `api/routes/auth.py`（邀请码过期校验）改用它 |
| `backend/app/models/source.py`、`alembic/versions/0034_source_sync_interval.py`（新） | `sources.sync_interval_seconds INT NULL`：`NULL`=未配置（用书源 `concurrentRate`，再退 `CRAWL_DELAY_MS`）、`0`=显式不限速、`N>0`=每 N 秒最多 1 次请求 |
| `backend/app/services/source_interval.py`（新） | `clamp_sync_interval()`（布尔/负数/垃圾/超 3600 都归一）、`source_sync_interval(source)`、`apply_source_interval(plugin, source)`。**只对实现了 `set_request_interval_seconds` 的插件生效**：`YueduPlugin` 每次新建实例所以安全，其它插件是进程级单例，故意不实现该钩子 → 一个源的限速不会漏到下一个源 |
| `backend/app/crawler/plugins/yuedu/__init__.py` | `set_request_interval_seconds()`；`_sleep_rate_limit()` 优先级变成 `SYNC_IGNORE_RATE_LIMIT` > **手配间隔** > `concurrentRate` > `CRAWL_DELAY_MS`（`0` 直接返回）。请求槽状态仍是**类级、按 base_url** 共享，所以跨书籍/跨任务持续生效 |
| `backend/app/services/sync.py` | 新增 `_source_plugin(source)`，5 个建插件的地方统一走它（`sync_book`/`sync_bookshelf`/`_book_plugin`/`discover_and_sync`/`discover_and_sync_all`）——**任务启动时读源表**，所以跑到一半改配置要等下一个任务才生效；`_chapter_concurrency(config, source_interval)` 在手配了间隔时返回 1（否则 9 个章节任务全堵在限速器上） |
| `backend/app/crawler/registry.py`、`api/routes/sources.py`（远程搜索）、`cookies.py`（测 Cookie）、`credentials.py`（自动登录） | 这些入口也按同一间隔请求：registry 按 source_id 查库时顺手下发，其余三处显式 `apply_source_interval()` |
| `backend/app/schemas/source.py` | `sync_interval_seconds` 加进 Create/Update/Out，`ge=0, le=3600`。Update 用 `exclude_unset` 区分「没传」和「显式 null（清空）」 |
| `backend/app/services/source_patch.py` | 加进 `PATCHABLE_COLUMNS`：`"60"`/`60` 都收，负数归 0、超限归 3600，`True`/`"soon"` 之类直接忽略并保留原值，`null`/`""` 清空 |
| `backend/app/services/ai_diagnosis.py` | `SOURCE_FIELDS` 加该列；证据里新增「请求间隔（sync_interval_seconds）」一行（`_render_interval`：未配置/不限速/每 N 秒）；提示词补一条「连续多章多本被拦且 concurrentRate 很小 ⇒ 这是站点限速，应提高 sync_interval_seconds 而不是改解析规则」；`value_at_path`/`describe_changes` 现在也解析**源字段**（之前只解析 config，导致 `sync_interval_seconds` 的审批 diff 永远显示「缺失」） |
| `frontend/src/pages/AdminPage.vue` | 书源表单新增「同步间隔（秒/请求）」数字输入 + 说明；列表显示 `N 秒/请求` 徽章（0 显示「不限速」）；提交前校验 0～3600 |
| `frontend/src/pages/SyncPage.vue` | 源列表显示间隔徽章；勾选了限速源时提示「会按该间隔逐个请求，全站同步可能很久」 |
| `frontend/src/stores/i18n.ts` | 新增 7 个键（中英各一份） |

**验证**：

- `cd backend && python -m pytest -q` → **631 passed**（新增 19 项）。
  - `tests/test_source_interval.py`（新，10 项）：间隔归一化/`0`/`null`/垃圾值；**限速器本身**
    用假 `asyncio`（只记 sleep，其余委托真模块）断言首请求不等待、第二次按 60s 等待、
    手配 60s 压过 `concurrentRate=1000`、`0` 从不等待、`SYNC_IGNORE_RATE_LIMIT` 仍然最高优先级。
  - `_chapter_concurrency`：`{"concurrentRate":"3/1000"}` 无间隔=3、配 60s=1、配 0=3。
  - `_source_plugin` 把 60 下发给插件；没有 `sync_interval_seconds` 属性的老行/插件不报错。
  - API：PUT 能设/能清空、不传字段不动原值、`-1`/`3601` 被 pydantic 拒。
  - 补丁白名单：`"60"`→60、`999999`→3600、`-30`→0、`True`/`"soon"` 忽略且原值不变、`null` 清空。
  - 迁移链断言更新到 `0034_source_sync_interval`。
- 前端 `npm run typecheck`、`npm run build` 通过。
- 时间戳修复顺带更新了 `tests/test_invites.py` 的自有时间基准（原来用 UTC）。

**未做/已知**：

- **仍然只改本地代码**。线上生效：`docker compose build backend crawler frontend` + `up -d`，
  迁移 `0034` 由 backend 启动时自动执行（只加一列，向后兼容）。
- 线上要真正解决搬山人，还得在 **设置 → 书源 → 编辑 → 同步间隔** 填 `60`（或按实测调整）。
  代价很直接：1 本书 50 章 ≈ 50 分钟，27 本书全站同步 ≈ 20 小时以上；只配真正需要的源。
- 要撸小说（520）**改什么都无效**，属于源站/线路问题；可换代理节点或等源站恢复。
- 同一类时区问题**还有残留**（本次没动，属另一批）：`services/cookie_health.py`、
  `repositories/cookie.py`、`plugins/alicesw/login.py` 把用户填的本地 `expired_at` 与 UTC `now()`
  比较 → **Cookie 被判过期的时刻偏晚 8 小时**；`services/token_service.py`、`api/routes/source_changes.py`、
  `services/account.py` 写的是 UTC（与自身比较一致，但前端显示早 8 小时）。要统一就照
  `core/clock.py` 的说明一次性改完，别只改一半。

---

## 22. 2026-09-18：点开书籍/章节要等很久（`/books/{id}/sources` 12 秒 + 图片零缓存）

**需求**：「点击书籍或者漫画之后，资源要加载很长时间，点击章节后也需要加载很长时间才能加载正文或者图片」。

**排查（只读线上，未改任何线上数据/配置）**：在 NAS 上直接对着 nginx 计时每个阅读路径接口：

```bash
curl -s -o /dev/null -w '%{http_code} %{time_total}s %{size_download}\n' \
  -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18088/api/books/<id>/sources
```

| 接口 | 实测 |
|---|---|
| `GET /api/books/{id}` | 0.028s |
| `GET /api/books/{id}/chapters`（2484 章，675 KB） | 0.13s |
| **`GET /api/books/{id}/sources`** | **10.31s / 11.18s** |
| `GET /api/chapters/{id}`（正文） | 0.026s |
| `GET /api/chapters/{id}/images/{f}`（394 KB webp） | 0.037s |
| `GET /api/books/home` | 0.86s |

前端两处串行 `await` 正好把慢接口放在正文前面：`ReaderPage.onMounted` 是
`fetchChapters → loadAlternates(10s) → loadBookmarks → loadChapter`；
`BookDetailPage.onMounted` 的 `loading=false` 在最后，所以整页都在等它。

**根因 1（性能）**：`list_book_sources` 与 `SyncService._find_same_title_books` 都是**把整库查出来再在
Python 里比标题**：`select(Book).options(selectinload(Book.author))`，而 `Book.tags` 是
`lazy="joined"`、`categories`/`custom_tags` 是 selectin，等于对 24k 本书做了全量 eager load。
后者还在**每本全站书同步结束时**调用（`_handle_global_r18_conflicts`），所以同步也在被它拖慢、
crawler 长期 100% CPU。

**根因 2（正确性陷阱）**：Python 的 `\s` 匹配 `\xa0`(NBSP)，Postgres 的 `[[:space:]]` 不匹配。
全库 23,979 条书名里正好 4 条含 NBSP——如果把归一化直接下推到 SQL 而不管这个差异，
这 4 本书的「其他书源」会静默消失。

**根因 3（图片）**：`starlette.responses.FileResponse` 只处理 `Range`，**不处理 `If-None-Match`**，
且响应里没有 `Cache-Control`。实测带 `If-None-Match` 命中时仍返回 `200` + 394,658 字节，
即每次重看一页都在重新下载整张图。线上图片 47,300 张 / 31.5 GB（中位数 408 KB，最大 14.8 MB，
>1MB 的有 8,465 张共 18.8 GB）。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/services/book_title.py`（新） | 归一化模式的**唯一定义**：括号/引号 + 所有非 ASCII 空格逐个列出 + `\s`；`normalize_title()`（Python）与 `normalized_title_sql()`（`regexp_replace(lower(col), :pattern, '', 'g')`，模式走**绑定参数**，避免模式里的引号转义问题） |
| `backend/app/api/routes/books.py` | `list_book_sources` 把标题匹配推进 SQL（Python 比较保留为最终确认，它只会删行不会多行）；`_normalize_book_title` 改为调用共享的 `normalize_title`；封面改用 `cached_file_response` |
| `backend/app/services/sync.py` | `_find_same_title_books` 同样推进 SQL；标题归一化为空时直接返回空（不再全表扫描） |
| `backend/app/api/file_response.py`（新） | `cached_file_response(request, path, max_age=..., immutable=...)`：补 `Cache-Control`，并实现 `If-None-Match`/`If-Modified-Since` → **304**（含 `W/`、逗号列表、`*` 处理）；给 `FileResponse` 传 `stat_result`，构造时就有 etag |
| `backend/app/api/routes/chapters.py` | 章节图片：`private, max-age=604800, immutable`（文件名是源 URL hash 且从**不覆盖**已有文件）；封面：`max-age=300` + 304（重新同步会覆盖同名封面文件） |
| `frontend/src/pages/ReaderPage.vue` | `onMounted` / bookId watcher：正文优先，`loadAlternates()`、`loadBookmarks()` 改后台跑 |
| `frontend/src/pages/BookDetailPage.vue` | 先渲染书籍+章节目录并结束 loading；进度（「继续阅读」目标）、标签、分类、分组、其他书源、书签全部移入后台异步块 |
| `docs/reading-performance.md`（新） | 阅读路径的请求清单、实测数字、三条「别踩回去」的约束、仍慢的地方（大图） |

**验证**：

- 线上只读复现（用真实 ORM + 真实配置跑新旧两版查询体，`book_title.py` 以独立模块推入容器 `/tmp` 导入，
  **没有修改容器里的代码、没有重启容器**）：
  - OLD `12.26s` / `11.64s` → NEW `0.127s` / `0.100s`，候选数量一致。
  - 全库 23,979 条书名：Python 归一化 vs Postgres 归一化 **0 处不一致**（含那 4 条 NBSP）。
  - 抽样 60 本来自全部 181 个同名组（365 本有同名伙伴，最大一组 3 本）：
    **新旧返回的候选集合完全一致，0 处 mismatch**。
- `cd backend && python -m pytest -q` → **641 passed**（新增 10 项：
  `test_book_title.py` 4 项含 NBSP/全角/引号/转义与 SQL 渲染；`test_file_response_cache.py`
  6 项含 etag 命中 304、`W/` 与列表形式、陈旧 etag 仍回 200、`If-Modified-Since`）。
  另在两处既有测试里加了「语句里必须有 `regexp_replace`」的断言，防止有人改回 Python 全表过滤。
- 前端 `npm run typecheck`、`npm run build` 通过。
- 顺带清理：验证用的临时脚本与推入容器 `/tmp` 的文件已删除（容器代码未改）。

**未做/已知**：

- **仍然只改本地代码**。线上生效要 `docker compose build backend frontend` + `up -d`
  （本次**没有**新迁移，`0034` 是上一轮的）。
- 大图问题（>5MB 的 571 张）**没解决**：一章 20 张图在中等带宽下就是几十秒，
  属于上传带宽限制。要做「按需缩放 + 缓存」需要 Pillow 和清晰度取舍，等用户确认。
- `GET /api/books/home` 0.86s（首页并发聚合多个书源 browse）与章节目录一次返回全部章节
  （2484 章 675 KB）本次未动。

## 23. 2026-09-18：crawler 刷 601 条空章节报错；小说/漫画搜索混出；搜索翻页要等几十秒

用户报三件事：①crawler 容器后台报错；②在小说页/漫画页搜索，两类结果一起冒出来；
③搜索翻页要等很久。

**排查（只读线上，未改任何线上代码/数据/配置）**：把 `docker logs novelhub-crawler --since 72h`
（约 19 MB）导出后按错误签名分组：

| 条数 | 签名 |
|---|---|
| **601** | `Failed to sync chapter … : Chapter returned empty content`，**全部来自 yswhub.cc（御宅屋）** |
| 86 | `Configured proxy http://127.0.0.1:27890 request failed (…); retrying direct`（mihomo 节点抖动，属预期） |
| 8/7/3/3 | `ConnectError` / `pop from an empty deque` / `ConnectTimeout` |
| 5 | 任务级失败（yaoluku 520/502、cool18/h528 验证码） |

搜索侧在 backend 容器里直接对 Meilisearch 计时（115,155 章 / 31,895 本）：

| 操作 | 实测 |
|---|---|
| 章节候选 `limit=5000`（旧值）取 `content` | **37.8s，313 MB** |
| `advanced_search(title)`（旧） | 2.9s（缓存热）/ 冷启动 66s |
| `advanced_search(content)`（旧） | **110.5s** |

**根因 1（crawler）**：御宅屋的 `ruleBookInfo.tocUrl` 指向 `/indexlist/<id>/`，该页的
`<ul id="jsList1">` 现在由 JavaScript 填充（真实浏览器渲染后**也是空的**，说明这些书在源站
本来就没有章节）。`ruleToc` 因此解析出 0 条，代码随后**退回扫描书籍详情页**——而书籍页上
唯一的 `/read/*.html` 链接是「相关推荐 / 作者其他作品」，于是一本书被造出 8 个「章节」，
每个都是一本书的详情页，取正文时必然 `Chapter returned empty content`。约 75 本 × 8 条
≈ 601 条。这些书此前还会被算作**同步失败**，累积到 `SYNC_MAX_CONSECUTIVE_FAILURES`(10)
就会中止整个全站任务。

**根因 2（搜索混出）**：`books.kind`（第 16 节）只落在 Postgres 里。Meilisearch 的
`books`/`chapters` 文档没有 `kind` 字段，也不在 `FILTERABLE_ATTRIBUTES` 里，
`/search/advanced` 的请求体更没有任何 kind 参数——`/novels`、`/comics` 共用同一个
全库查询，所以两类结果一起出现（第 16 节「未做/已知」里记过，这次补上）。

**根因 3（翻页慢）**：`_search_field` 没设 `attributesToRetrieve`，一次候选抓取会把
**每个候选章节的完整正文**（单章上限 10 万字）一起拉回来；而 `CANDIDATE_LIMIT=5000`
意味着**每点一次下一页**都重跑一遍这个查询并重新在 Python 里排序。`content` 字段尤其致命：
1000 个候选 ≈ 96 MB JSON、18–25 秒（每次请求都如此）。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/crawler/base.py` | 新增 `EmptyTocError`：书源声明的目录页没有解析出任何章节 |
| `backend/app/crawler/plugins/yuedu/__init__.py` | `fetch_book` 记下 `toc_url` 是否来自 `ruleBookInfo.tocUrl`；当它指向**独立目录页**且该页规则/通用扫描都没解析出章节时抛 `EmptyTocError`，不再退回扫描书籍详情页（`_find_toc_url` 猜出来的 URL 不适用这条，仍保留旧回退） |
| `backend/app/services/sync.py` | `discover_and_sync_all` 把 `EmptyTocError` 记为**跳过**（`books_filtered` + `details[].filter_type="目录"`），既不中止任务也不刷章节错误；不再进入「连续失败」计数 |
| `backend/app/services/search.py` | 新增 `BOOK_RETRIEVE_ATTRS`/`CHAPTER_RETRIEVE_ATTRS`（**不含 `content`**）并传给每次候选查询；`CANDIDATE_LIMIT` 5000 → **1000**，`content` 字段单独用 `CONTENT_CANDIDATE_LIMIT = 300`；`kind` 进 `FILTERABLE_ATTRIBUTES`；`_kind_filter()` + `search_books/search_chapters/advanced_search(kind=…)`；`index_book()` 保证文档带 `kind`；新增 `index_missing_kind()` / `sync_book_kinds()`（用 `update_documents` 只合并 `kind`；可按 `book_ids` 精准刷新，也可整库回填） |
| `backend/app/services/book_kind.py` | `reclassify_books()` 返回 `changed_book_ids`，让索引只刷新真正变过的书与其章节 |
| `backend/app/api/routes/search.py` | `AdvancedSearchRequest.kind`（`novel|comic`）、`GET /search?kind=`、`POST /search/index/kinds`（整库回填） |
| `backend/app/api/routes/books.py`、`sources.py`、`source_changes.py`、`services/manual_import.py` | 6 处 `index_book` / `index_chapter` 文档补 `kind` |
| `backend/app/api/routes/books.py` | `POST /books/reclassify` 结束后把**变过的**书推给索引（失败只记在 `result["index"]`，不影响重新识别本身） |
| `backend/app/main.py` | 启动时后台跑一次 `sync_book_kinds()`（先用 `kind NOT EXISTS` 探测，正常重启是 no-op） |
| `frontend/src/pages/BooksPage.vue` | 轻量搜索与高级搜索都带上 `kind`；缓存键含 `kind`（`/novels` 的缓存不会漏进 `/comics`）；把已取回的搜索页存进 `sessionStorage`（最多 12 页），翻页/返回不再重跑查询 |
| `backend/tests/test_yuedu_plugin.py`、`test_sync_service.py`、`test_search_service.py`、`test_book_kind.py` | 新增 10 项回归：御宅屋式书页只出 `EmptyTocError` 且只请求书页+目录页、无 `tocUrl` 规则时仍回退扫书页、`EmptyTocError` 记为跳过不中止任务、候选窗口与取回字段（含 `content` 单独窗口）、`kind` 过滤串、`index_book` 必带 kind、`kind NOT EXISTS` 探测、无变化时不动索引、`reclassify_books` 上报 `changed_book_ids` |

**验证**：

- `cd backend && python -m pytest -q` → **652 passed**。前端 `npm run typecheck`、`npm run build` 通过。
- 线上影子回归（改动后的 `yuedu/__init__.py` 单独加载进 crawler 容器 `/tmp`，**不动线上镜像/代码/数据库**）：
  - `https://yswhub.cc/read/104039.html` → 抛 `EmptyTocError`（修复前造出 8 个假章节）；
  - 对照组 `https://yswhub.cc/read/39108.html` → 仍然解析出 50 章，URL 正常。
- 线上只读计时（改动后的 `search.py` 推入 backend 容器 `/tmp` 导入，读真实索引）：
  - `title` 搜索 **0.04s**（旧 2.9–66s）；`chapter_title` 0.40s；`content` **0.25s**（旧 110.5s）；
  - `content` 候选窗口 300 / 500 / 700 / 1000 分别约 0.65s / 1.4s / 1.2s / **18–25s**，故取 300。
- Meilisearch 语义在**临时索引**上验证后删除（不碰生产索引）：`update_documents` 只合并
  `kind`（`title`/`source_id` 保留）、`kind NOT EXISTS` 可用、`kind = "novel"|"comic"` 各自命中。

**未做/已知**：

- 只改本地代码。线上生效要 `docker compose build backend crawler frontend` + `up -d`；
  **没有新迁移**。首次启动会在后台回填两个索引的 `kind`（约 2.4 万本书 + 11.5 万章），
  完成后重启是 no-op；也可以手动 `POST /api/search/index/kinds`。
  之后「设置 → 索引 → 重新识别」只会刷新 kind 真的变了的书及其章节。
- 搜索候选窗口从 5000 收到 1000（`content` 300），也就是单个查询最多给 25 页
  （`content` 8 页）结果、`total` 以窗口为上限——这是「翻页从几十秒降到亚秒」的代价。
  **同一天的下一条（第 24 节）把单条件搜索改成了引擎原生分页，页数上限已经拿掉。**
- Meilisearch 在 crawler 写入新章节后会丢弃查询缓存，当天首次搜索仍可能冷启动几秒到二十几秒，
  与本次改动无关。
- 御宅屋那批「源站无章节」的书现在会出现在任务的「同步书籍明细」里，标为「已过滤 / 目录」，
  需要的话按书源更新规则或在管理端忽略；不要期望它们同步成功。
- `SearchPage.vue`（`/search` 独立搜索页）没有 kind 上下文，仍是全库搜索；`/novels`、`/comics` 已分开。

## 24. 2026-09-18：为什么别人几百页还快 —— 单条件搜索改成引擎原生深分页

> 本节里的「模糊模式交给引擎（相关度排序 + 原生分页）」**已被第 28 节取代**：
> 引擎的中文匹配是逐字的，`铃铛` 会把 `铃木`/`铃雨` 一起返回。第 28 节改成
> 「引擎只提供候选，打分/门槛留在 Python」，精确与模糊都走「扫一次 + 缓存 + 深分页」。

**现象/需求**：用户问「为什么其他小说网站能有几百页，而且速度还那么快」。第 23 节修完之后
搜索已经是亚秒级，但**页数**仍是自造的：单查询最多 25 页（`content` 8 页）。

**根因（都是「在 Python 里做搜索引擎该做的事」）**：

1. `advanced_search` 每点一次下一页都做「拉整个候选窗口 → 逐条打分 → 全量排序 → 切出 40 条」，
   所以**第 250 页和第 1 页的工作量一样**，窗口大小既是耗时上限也是页数上限；
2. `total` 用的是 `len(在 Python 里造出来的实体列表)`，不是引擎的命中数；
3. 候选查询为了打分必须把正文字段一起取回（1000 章 ≈ 96MB / 18–25s）。

而引擎本来就支持：`maxTotalHits` 我们早就设成 10000（= 250 页），**线上实测原生 `offset=9960`
只要 0.128s**，`page`/`hitsPerPage` 还会直接返回精确的 `totalHits`/`totalPages`。天花板完全是自造的。

**关键实测（决定了实现方式）**：这个索引上的中文，Meilisearch **给不出可用的「整串/短语」语义**：

| 查询 | 引擎结果 |
|---|---|
| `白骨精` + 默认 `matchingStrategy: "last"` | 404 条，其中 `穿成白骨肿么破` 的「命中位置」是**第 7 个字「破」**（charabia 切成 白/骨/精，last 只要求最后一个片段命中） |
| `白骨精` + `matchingStrategy: "all"` / 短语 `"白骨精"` | 时好时坏（`"白骨精"` 1 条，`"剑来"` **0 条**，而 `剑来` 有 113 条） |
| `id` 作为 filterable 属性 + `filter: id IN [...]` | 可用（实测通过） |
| 扫描窗口 `id` + 被搜字段：books 10000 条 / chapters 10000 条 | 0.02s / 0.69s |
| 本页 40 条按 id 补水 + crop 正文 | 1.17s（含 crop） |

结论：**模糊模式交给引擎（相关度排序 + 原生分页），精确模式保留 Python 裸子串匹配**
（引擎做不到，而且正是它把 `穿成白骨肿么破` 这类噪声挡在外面），用「扫描一次 + 结果缓存」
换取深分页。

**改动**（`backend/app/services/search.py`，其余文件不动）：

| 位置 | 改动 |
|---|---|
| `FILTERABLE_ATTRIBUTES` | 加 `id`（按 id 补水需要） |
| `METADATA_CANDIDATE_LIMIT = 10_000` | 精确模式下非正文字段的扫描窗口 → 最多 250 页 |
| `PAGE_CACHE_TTL_SECONDS = 120` / `PAGE_CACHE_MAX_ENTRIES = 16` | 单条件精确结果的进程内缓存（存 `(score, id)`，最坏约 1.5MB/条） |
| `_engine_page()`（新） | 一次 `page`/`hitsPerPage` 查询搞定匹配+排序+分页；`total` 取 `totalHits`；章节用 `attributesToCrop` 出摘要（正文不整段返回） |
| `_scan_condition()`（新） | 只取 `id` + 被搜字段（+ `author` / `book_id` / `book_title`）后按现有 `_condition_score` 打分排序 |
| `_hydrate()`（新） | 用 `filter: id IN [...]` 取回**本页 40 条**的完整字段；失败只告警并降级（`id` 变成 filterable 是异步设置，首个请求可能撞上） |
| `_serialize_engine_hit()`（新） | 引擎分页/补水结果的序列化（与旧 `_serialize_book/_serialize_chapter` 同形状，`matched_fields` 为当前条件字段） |
| `_single_condition_search()`（新）+ `advanced_search` 路由 | 单条件且 scope 落在单一索引时走新路径；**多条件仍走候选窗口**（跨字段 AND/OR 压不成一条引擎查询） |

**验证**：

- `cd backend && python -m pytest -q` → **660 passed**（新增 8 项：引擎分页参数与 `totalHits`、
  第 250 页不扫候选、多条件仍走 Python 打分排序、精确模式只扫一次且第二页命中缓存、
  按 id 补水、正文用 300 窗口 / 元数据用 10000 窗口、缓存过期、补水失败降级不 500）。
- 线上影子回归（改动后的 `search.py` 推入 backend 容器 `/tmp` 导入，**不动线上代码/数据**，
  读真实索引）：

| 路径 | 实测 |
|---|---|
| `title/fuzzy 白` offset=0 / 40 / 400 | 0.231s / 0.016s / 0.011s，`total=506` |
| `title/fuzzy 白` offset=4000（超过命中数） | 0.004s，空页（引擎正确） |
| `content/fuzzy 白`（章节索引） | **total=10000（250 页）**，0.26–0.72s，每页带 106–111 字摘要 |
| `kind=comic` + 引擎分页 | 过滤生效（索引回填 kind 前为 0 条） |
| 多条件 `title+author` | 0.187s，仍走候选窗口 |

  精确路径的「扫描→打分→缓存→按 id 补水」在单元测试里逐条断言；`id` filterable 与
  `update_documents` 合并语义此前已在**临时索引**上验证过（用完即删）。

**未做/已知**：

- 只改本地代码。线上生效要 `docker compose build backend crawler frontend` + `up -d`
  （前端本次无需改动，`total` 变大后分页 UI 自动跟着算）。**没有新迁移。**
- 部署后 `id` 进入 filterable 会触发 Meilisearch 对两个索引做一次**设置级重建**，
  期间搜索会慢一些；Meilisearch 设置是异步生效的，极端情况下最初几个请求会走
  「补不到显示字段」的降级分支（只告警，不会报错）。
- **多条件搜索仍是候选窗口**（25 页上限）。要也做深分页，需要把多条件压成一条引擎查询
  （例如索引期生成 n-gram 字段或改成 `filter` 表达式），属于索引结构改动，未做。
- `content` 字段在**精确**模式下仍要取回正文打分，窗口仍是 300（8 页）；模糊模式不受限。
- 单条件精确结果缓存 120 秒且只存在进程内：backend 多 worker 时各自一份，同步中的新章节
  最多 2 分钟后才可能出现在该查询的后续页里（第 1 页永远是新的）。

## 25. 2026-09-18：一章几十万字只读到一部分，后面的内容取不到

**现象**：用户报「书库里有的书一章有几十万字，点进去阅读只有一部分、没有加载完全，
后面的部分也获取不到」。

**排查（只读线上；用真实浏览器跑线上阅读器）**：

1. 后端分块接口本身是对的。在 backend 容器里用管理员 token 直接调
   `GET /api/chapters/{id}/content?offset=&limit=`：317,503 字的章节分成
   199,999 + 117,504 两块，第二块 `next_offset: null`，拼起来与文件完全一致。
   库里 153 个章节文件 >300 KB，最大 1.38 MB（约 46 万汉字）。
2. 用 Playwright 跑线上阅读器（1280×900）：打开 317k 字的章节，首屏 200,023 字，
   滚到底部后第二块自动追加 → 317,527 字，**桌面滚动这条路径是通的**。
3. 换成手机（390×844，翻页模式）：把进度停在末尾再点一下翻页，第二块确实加载
   （317,495 字）——但**紧接着页面被整体重载**（`/src/main.ts`、`/@vite/client`
   重新请求，NAV 事件指向同一 URL），正文立刻退回 199,991 字，而且**没有任何新请求**
   （offset=0 那块来自 IndexedDB 缓存）。之后 494 页要从头再翻一遍。
4. 不点任何东西等 165 秒：不会自发重载 → 重载与交互有关，不是环境抖动。

**根因**（三条，都是「长章节的剩余内容没有可靠的取回路径」）：

1. **只有隐形的滚动触发点**：`onScroll` / `onMobileScroll` 在距底部 900px 内才
   `loadNextContentChunk()`，一次只加载一块；桌面页脚在正文只有一半时显示
   `reader_end`「结束」（实测：正文 200,023 / 317,527 字，页脚写着「结束」）。
2. **重载/重新挂载会丢掉已追加的内容**：追加只在内存里，`loadChapter` 从 offset 0
   重新开始；因为 offset=0 的块能从 IndexedDB 直接命中，连请求都不发，看起来就像
   「内容自己缩水了」。手机上任何一次刷新/切后台/进程回收都会回到第 1 页。
3. **分块缓存键只有 offset**（`chunk:{id}:{offset}:{limit}`）：章节被重新同步变长后，
   旧缓存里的 `next_offset: null` 会让阅读器**永久**认为已经到底，除非清站点数据。
   `invalidateChapter` 只在管理端从阅读器手动「重同步本章」时才调用。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/schemas/chapter.py` | `ChapterOut.hash`（章节列表就带上内容摘要） |
| `backend/app/api/routes/chapters.py` | 新增 `GET /chapters/{id}/content/meta`（只回 `hash` + `total_length`，不传正文）；分块响应也带 `hash` |
| `frontend/src/stores/books.ts` | `Chapter.hash`、`ChapterContentChunk.hash`、`ChapterContentMeta`、`fetchChapterContentMeta()`；`fetchChapterChunk(id, offset, limit, version, refresh)` —— 缓存键改成 `chunk:{id}:{version}:{offset}:{limit}`，`version` 是内容摘要（老数据用 `len{总长}`），`refresh` 用于摘要未知时强制跳过缓存 |
| `frontend/src/pages/ReaderPage.vue` | 先取 meta 拿摘要/总长 → 再取第一块；首屏渲染后**后台自动续传**剩余块（`scheduleContentAutoLoad`，上限 8 块）；新增 `contentHasMore` / `contentRemaining` / `contentChunkError`，桌面与手机（翻页/滚动两种模式）都给出「已加载 X / Y 字」+「继续加载剩余 N 字」按钮，失败可重试（不再 `catch {}` 静默）；页脚在还有未加载内容时不再显示「结束」；滚动触发点改走自动续传 |
| `frontend/public/sw.js` | 导航请求网络优先（缓存只作离线回退）、`/assets/*` 缓存优先、**不拦截 `/api/*`**，并加 `activate` 清理旧缓存 —— 原来对所有请求 cache-first 且永不更新 |

**验证**：

- `cd backend && python -m pytest -q` → **663 passed**（新增 `tests/test_chapter_chunks.py` 3 项：
  分块必须首尾相接拼回全文且最后一块 `next_offset: null`、分块带 `hash`、meta 只回长度与摘要）。
- 前端 `npm run typecheck`、`npm run build` 通过。
- **端到端验证**（本地起 `vite` 把 `/api` 代理到线上 NAS，用本机 Playwright 跑**改动后的前端**
  读真实 317,503 字章节；没有改线上任何东西）：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 桌面打开（完全不滚动） | 200,023 字 | **317,527 字**（约 3s 内后台补齐） |
| 桌面刷新页面 | 退回 200,023 字 | **317,527 字** |
| 手机翻页模式 | 200k，需手动翻到第 318 页才补 | **312,720 字**，加载中显示「继续加载剩余 117504 字」，补完后提示消失 |
| 请求序列 | `offset=0`（+ 触发后 `offset=199999`） | `content/meta` → `offset=0` → `offset=199999`（自动） |
| 第二次进入同一章 | — | 0 个 content 请求（全部命中缓存） |

  另外确认 IndexedDB 里的键是 `chunk:{chapterId}:{version}:{offset}:{limit}` 形式。
  本次验证针对的线上 backend 还是旧镜像（没有 `/content/meta`），所以走的正是**降级分支**，
  说明「旧后端 + 新前端」也能正常工作。

**未做/已知**：

- 只改本地代码。线上生效要 `docker compose build backend frontend` + `up -d`
  （本次**没有** crawler 改动、没有新迁移）。
- 后台自动续传上限 8 块（160 万字符）；更长的章节仍需要点按钮，避免无限占带宽。
- 分块边界按段落（`content.rfind("\n", …)`）取整，所以块大小会在
  10 万–20 万字符之间浮动，属预期。
- `sw.js` 变更后浏览器要等旧 SW 被替换（新 SW `skipWaiting` + `clients.claim`，
  刷新一次即可生效）；旧缓存会在 `activate` 里删掉。

---

## 26. 2026-09-18：crawler 两个任务失败的真根因（`||` 组合列表规则、shim 缺 java.connect）

**现象**（`docker logs novelhub-crawler --since 72h`：4 条 ERROR + 117 条 WARNING；用户报
「crawler 容器后台报错」）：

1. `中文成人文学网-短篇(简体)`（`yuedu_c4a94f9ce7ce`，blog.xbookcn.net）的 `discover_all` 任务
   失败：`书源未返回可同步的书籍`。任务里 21 个分类页各有一条
   `Explore kind … returned no books on page 1: … [bytes=61193 title='精选作品-短篇成人情色小说'
   text='…猎美陷阱 姐姐的屁股…']` ——**页面有书，解析出 0 本**。
2. `Icu`（`yuedu_963f7dd31df3`，hq555.icu）的 `discover_all` 任务失败：
   `该书源的发现规则是 Legado JS 脚本（<js>/@js:），当前环境无法执行` —— 而该站点此刻用
   proxy 直取 `/so` 是 **200 / 81 KB**，页面里 `layui-tab-item-title` 5 个、没有验证码页面。
3. 其余 WARNING 全是站点/代理侧（h528 502、cool18 404/删帖、banshanren 人机验证、
   `img5.wnimg2.cfd` 图片连接失败、mihomo 抖动 `Configured proxy … retrying direct`），与本轮无关。

**根因**（都在线上容器里用真实页面复现，未改线上代码/数据）：

1. **列表规则里的 `&&`/`||`/`%%` 从未被切分**：Legado 的
   `AnalyzeByJSoup.getElements` 先 `RuleAnalyzer.splitRule("&&","||","%%")` 再逐段选择
   （`||` = 取第一段有结果的，`&&` = 拼接，`%%` = 交错）。我们的 `_get_elements` 把整条
   规则丢给 soupsieve：`h3 a||.post-title a||article h3 a` → `SelectorSyntaxError: Invalid
   character '|'`，而 `_legado_before_elements` 的 `except` 把它吞掉返回**空**，于是
   bookList/chapterList 组合规则一律 0 元素。xbookcn 的 `ruleExplore`/`ruleSearch` 都是
   这条 `||` 规则；Icu 的 `ruleSearch.bookList` 是
   `.layui-tab-item-novelContainer[-1]@…&&.layui-tab-item-novelContainer[-2]@…`。
2. **shim 缺 `java.connect`**：Icu 的 `exploreUrl` 是 `<js>`，第一句就是
   `java.connect(baseUrl + "/so").getBody()`。shim 里只有 `org.jsoup.Jsoup.connect`，
   `java.connect` 是 undefined → TypeError 被书源自己的 `try/catch` 接住并 `return "" + e`
   （所以日志里**没有** JS 报错），结果是非 JSON 字符串 → `_parse_explore_js` 返回 `[]` →
   落到「发现规则是 JS 脚本」的错误文案。同一个脚本还用到 `Element.selectFirst` 与
   `Element.equals`（shim 都没有）。
3. **纯 JS 字段规则的输入是 bs4 `Tag`**：`_eval_js_impl` 里 `json.dumps(input_value)` 对
   `Tag` 抛 `Object of type Tag is not JSON serializable`（容器日志里刷了 24 条
   `JsRuntime eval error`），脚本还没跑就失败 —— Icu 的 `ruleSearch.kind`
   （`<js>java.getString("a@href")…</js>`）就是这样丢掉 kind 的。绅士漫画的 kind 之所以没踩到，
   是因为它的规则前半段是 CSS，传给 JS 的已经是字符串。
4. **host-only 的 `bookUrlPattern` 把结果全过滤掉**：Icu 声明
   `bookUrlPattern = "https://ztopaq7zrz.hq555.icu:1678"`（只有 scheme+host）。
   `_is_book_url` 的「匹配必须走到路径结尾」规则对它永远不成立（余下整条路径），
   `require_pattern` 分支又对同 host 直接返回 False → 24 条搜索结果全被丢弃，
   只剩通用兜底扫出来的首页链接（书名 `首頁`）。
5. **书页没有 `<title>`**：Icu 的书页只有 `<p>书&nbsp;&nbsp;名：风流穿越</p>` 和封面
   `<img alt>`，而书源 `ruleBookInfo.name = "{{book.name}}"`（Legado 用搜索结果带的 book
   对象解析，NovelHub 按 URL 打开时没有这个对象）→ 书名空 → `sync_book` 直接判
   `Book page returned no usable metadata` 失败（章节其实解析出 20 条）。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/crawler/plugins/yuedu/rule_engine.py` | 新增 `_get_elements_from_root`：`_get_elements` 先按 `_RuleAnalyzer.split_rule(*SEPARATORS)` 切分再逐段链式选择，`\|\|`/`\|` 取首个非空、`%%` 交错、`&&` 拼接（与 Legado `AnalyzeByJSoup.getElements` 一致）；括号不平衡时退化为单段求值 |
| `backend/app/crawler/plugins/yuedu/jsoup_shim.js` | 新增 `java.connect(url[, headerJson])`（按 Legado 语义**立即**发请求并返回响应对象，带上源 `header`：`__nhSourceHeaders`）、`__nhResponse.getBody()`、`__nhEl.selectFirst`/`__nhElements.selectFirst`、`__nhEl.equals` |
| `backend/app/crawler/plugins/yuedu/js_runtime.py` | 新增 `json_safe()`：把非 JSON 可序列化的输入（bs4 `Tag` 等）转成字符串（元素 → 外层 HTML），`_eval_js_impl` 的 `input_value`/`context` 与 `_eval_js_with_context_impl` 都改用它 —— 元素变成 HTML 正好是 `src`/`java.getString` 的根 |
| `backend/app/crawler/plugins/yuedu/__init__.py` | 新增 `_book_url_pattern()`：只有 scheme+host 的 `bookUrlPattern` 视为「无法区分书页」（返回 `""`），`_is_book_url`、`_is_chapter_url`、`_explore_items_from_html`、`discover_books`、`_normalize_search_items` 统一改用它，书源自己的 `bookList` 重新成为权威；新增 `_extract_labelled_title()`（`og:novel:book_name`/ld+json/正文 `书名：X`），`_parse_book_generic` 在没有 `<title>` 时用它取名 |
| `backend/tests/test_rule_engine_legado.py` | 新增 6 项：`\|\|` 取首个匹配/回退、`&&` 拼接、`%%` 交错、括号内分隔符不被切开、chapterList 回退、`java.connect().getBody()`、`selectFirst`/`equals` |
| `backend/tests/test_yuedu_plugin.py` | 新增 6 项：xbookcn 式 `\|\|` 探页发现书籍、Icu 式 `&&` 搜索、纯 JS 字段规则读列表项元素、Icu 式 exploreUrl JS 产出分类（stub `__nhCurlRaw`）、host-only `bookUrlPattern` 不过滤/不吞章节、无 `<title>` 时用「书名：」取名 |

**验证**：

- 9 项新测试在**改动前全部失败**（先 `git checkout` 还原两个源文件跑一遍确认，再恢复改动）。
- `cd backend && python -m pytest -q` → **677 passed**（改动前 663）。
- 线上影子回归（把改后的 4 个文件写进容器 `/tmp/shadow_backend`，**没有改线上镜像/代码/数据**；
  探针只读站点与数据库）：

| 探针 | 部署版 | 改动后 |
|---|---|---|
| xbookcn `discover_books` 精选作品 / 现代情色 | 0 / 0 本 | **20 / 20 本**（《猎美陷阱》《姐姐的屁股》…） |
| Icu `get_explore_kinds()` | 0 个（`discover_books` 抛「发现规则是 JS 脚本」） | **55 个分类**（风流/阿宾/爸爸…） |
| Icu `discover_books(风流)` | — | **24 本**真实书籍（`/xs_ls/39898` …） |
| Icu `fetch_book` + 首章正文 | — | 书名 **风流穿越**、20 章、正文 **5112 字符** |
| 绅士漫画 wn09 `discover_books(page=1)`（回归） | 399 本 | **399 本** |
| 御宅屋 `fetch_book`/正文（回归） | 50 章 | **50 章**，正文可取 |

  验证用的 `/tmp/shadow_backend`、`/tmp/nhdbg` 已删除；线上容器 4 个文件的 md5 与开始前一致。

**未做/已知**：

- **只改本地代码**。线上生效要 `docker compose build backend crawler` + `up -d`（无新迁移）。
  部署后 xbookcn、Icu 这两个书源需要重发一次全站同步（旧任务里的失败记录不会自己重跑）。
- Legado 组合规则的 `%%`（交错）现在按 Legado 语义实现，但线上书源里暂无实际用例。
- 依赖完整 Android 运行时的书源（UAA 的 `Reload(...)`/`java.importScript`）仍然跑不了，
  错误文案不变：[full-site-sync.md](full-site-sync.md)「书源发现规则是 `<js>` 脚本」一节已更新，
  区分「shim 能跑」与「真需要 Legado」。
- Icu 的 `bookUrlPattern` 是 host-only，`_is_book_url` 现在按「无模式」处理：同源的书页/章节
  形状判定回落到路径启发式（`/xs_ls/…` 这类短路径不会被当成书页，也不影响
  `ruleToc` 命中即权威的既有策略）。
- Icu 的相册式漫画正文是图片，阅读器渲染属于前端既有能力，未在本轮验证。

## 27. 2026-09-18：AI 说「书源未配置 Cookie」，可书源页明明写着「已保存 Cookie」

**现象**：用户在「阅读书源导入」页导入书源时**同时填了 Cookie**（设置 → 书源里那条书源确实显示
已保存 Cookie），但对该书源失败任务点「AI 分析这次报错」，结论里写着
「书源未配置 Cookie，header 中只有 UA」，`next_steps` 还让用户「去浏览器导出 Cookie 再导入」——
用户已经导过了。问：别的书源是不是也有这个问题？

**排查（只读线上数据库）**：

1. `cookies` 表里 `yuedu_c4a94f9ce7ce`（中文成人文学网-短篇(简体)）**有一条**记录，
   `created_at = 21:39:29`；那次任务 `started_at = 21:39:41`、诊断存于 `21:40:20`。
   也就是说：任务运行时 Cookie 就在库里，诊断时也在库里。
2. `sync_diagnoses` 里那条诊断的 `reasoning` 第 5 条原文是「书源未配置 Cookie，header 中只有 UA，
   没有任何可用于通过站点验证的凭据」——**是模型猜的**。
3. 根因不在书源、也不在导入流程：`import_yuedu_sources` 会把 `payload.cookie` 加密写进 `cookies` 表
   （`yuedu.py` 第 970-985 行），这条链路是好的。问题在
   `ai_diagnosis.collect_evidence()` 交给模型的证据里**只有任务报错、逐书失败明细、书源规则 JSON**，
   完全没有 `cookies` 表的状态；而 Legado 书源 JSON 的 `header` 里本来就不会出现 cookie 字段
   （Cookie 是独立存在的），于是模型看着规则里「没有 cookie」就断言「没配置 Cookie」。
4. **是系统性问题**：线上 16 个书源**全部**有 Cookie 记录（各 1 条，均未过期），
   而这段证据代码与书源无关 —— 任何书源都可能被这样误判。9 条已存诊断里有 1 条明确写了
   「未配置 Cookie」（就是上面那条），其余只是碰巧没往 Cookie 上归因。

**改动**：

| 文件 | 改动 |
|---|---|
| `backend/app/services/ai_diagnosis.py` | 新增 `collect_login_state()`（读 `cookies` 表 + `source_credentials`，给出「有没有 / 几条 / 名字 / 保存时间 / 过期时间 / 是否已过期 / 有没有自动登录凭据」）、`cookie_names()`（**只取名字，绝不带值**）、`_cookie_plaintext()`（解密失败时区分「旧版明文」与「密文解不开」）、`render_login_state()`；`collect_evidence()` 把结果挂到 `evidence.source["cookies"]`，并给任务补上 `created_at/started_at/finished_at`；`render_evidence()` 增加 `## 登录状态（Cookie）` 一节；`SYSTEM_DIAGNOSE` 增加口径：「书源有没有 Cookie 只看这一节，`header` 里看不到不代表没配；已保存 ≠ 仍有效，被拦时说可能已过期，而不是说没配」 |
| `backend/tests/test_ai_diagnosis.py` | FakeDB 按查询的模型路由（Cookie / SourceCredential / 其他），新增 7 项：只列名字不带值、base64 密文不当成名字、证据里带 Cookie、已过期与已存凭据、没有 Cookie 时如实说、渲染出「已保存 Cookie」且不出现「没有保存 Cookie」、系统提示词的口径 |

**验证**：

- `cd backend && python -m pytest -q` → **690 passed**。
- 线上影子回归（把改后的 `ai_diagnosis.py` 放进 backend 容器 `/tmp` 导入，
  **只读数据库、不调 AI、不写任何行**），对**真实那条任务**重新收集证据：

| 检查 | 结果 |
|---|---|
| `collect_login_state("yuedu_c4a94f9ce7ce")` | `configured=True, count=1, names=[_gid, cf_clearance, _gat_gtag_UA_99929_1, _ga_JKNXPWV2R8, _ga], decrypted=True` |
| 渲染出的证据 | `## 登录状态（Cookie）` → 「**已保存 Cookie**（cookies 表 1 条）…」（含 `cf_clearance`） |
| 证据全文 | 不再出现「没有保存 Cookie」；且 `ss_userid=`/`cf_clearance=` 等**值一律不出现在证据里** |

**未做/已知**：

- **只改本地代码**。线上生效要 `docker compose build backend crawler` + `up -d`（无新迁移）。
- 已经存在的那条错误诊断**不会自愈**（诊断按任务缓存，一个任务只自动分析一次）：
  在同步页对该任务点「重新分析」才会按新证据重跑。
- Cookie 的**值**永远不进提示词（会发给外部模型服务），只给名字：像 `cf_clearance` 这种名字已经
  足够判断「浏览器验证过了」，而 `fontsize` 这类站点自己发的偏好 cookie 不会被误当作登录态。
- 如果 backend 与 crawler 的 `COOKIE_SECRET` 不一致，证据会写「值解密失败」并**仍然算已配置**，
  不会退回成「没有 Cookie」——这种情况该去查两个容器的环境变量，不是改书源。
- **已知缺口（本轮未改）**：非管理员用「全局」scope 导入书源时走审批流
  （`_submit_global_approvals` → source_changes），而保存 Cookie 的那段代码在它 `return` 之后，
  所以**这一步填的 Cookie 会被丢掉**，审批通过后书源是「没有 Cookie」的状态。
  Cookie 是按 source_id 存的、没有 owner 列，把某个用户的会话 Cookie 先写进一条待审批的
  全局书源里（或审批时自动带上）属于权限问题，不适合顺手改：目前请在**审批通过之后**再导入一次
  Cookie（或在「设置 → 书源」里给那条书源单独保存）。个人 scope 导入不受影响。

## 28. 2026-09-18：搜索结果和搜索词没关系 / 多条件搜索全是 0 条

**现象**：① 搜索「铃铛」时结果里混进只带「铃」或只带「铛」的书，甚至看着两个都不占；
② 高级搜索（多条件）怎么填都是 0 条。

**排查（只读线上索引，`novelhub-search`）**：

1. 直接把前端的查询发给引擎（`attributesToSearchOn: ["title"]`，就是代码里的参数）：

| 查询 | 引擎返回 |
|---|---|
| `铃铛` | `estimatedTotalHits=19`，逐条看：**只有 1 条**（《【小铃铛】（1-12）》，命中长度 2）真的含「铃铛」；其余 10 条只含「铃」（铃木/铃雨/铃铃铃二世…）、8 条连简体「铃」都没有（`鈴木`/`晶鈴`/`聖誕鈴聲`，繁体字形被引擎归一成「铃」） |
| `白骨精` | 404-407 条，第一名是《穿成白骨肿么破》（命中位置是第 7 个字「破」） |
| `剑来` + `matchingStrategy: "all"` | **0 条**（而真含「剑来」的有 100+ 条） |

   即：charabia 把中文切成**单字**，默认 `matchingStrategy: "last"` 只要求最后一个片段命中，
   `"all"` 又会把 `剑来` 这种词整条判死 —— 换 strategy 解决不了。
   （第 24 节据此把「模糊」交给了引擎，代价就是这次报的噪声。）
2. 多条件（0 条）的根因是**候选窗口截断**，不是 AND 逻辑写错：
   `_collect_condition` 每个条件只取 `CANDIDATE_LIMIT=1000` 条候选再在 Python 里打分，
   而 `category_names = 言情` 一个条件就命中 **1847** 本；
   《晴晴的乖巧日记》确实同时满足「书名含 晴晴的」和「分类=言情」，但它排在 1000 名之外
   → 交集恒为空。实测：`title=晴晴的 AND category=言情` = **0**（`title + author` 这类
   命中数小的组合反而正常，所以看起来「有时好有时坏」）。
3. 顺带量了窗口代价（线上真实索引，只读）：

| 查询 | 1000 条窗口 | 10000 条窗口 |
|---|---|---|
| books / `category_names=言情`（含全部显示字段） | 0.09 s / 0.9 MB | **0.10 s / 1.7 MB**（1847 条全覆盖） |
| books / `title=的` | 0.09 s | 0.32 s |
| chapters / `book_title=的` | 36 s | **249 s**（太贵，不能放） |
| chapters / `content=的` | 17 s | 105 s / 118 MB |

**改动**（`backend/app/services/search.py`，其余文件不动）：

| 位置 | 改动 |
|---|---|
| `_condition_score(..., "fuzzy")` | **每个字都必须出现**才算命中（可乱序、可不连续），再按「整串命中 > 最长连续片段 > 引擎排序分」排；少一个字就是 0。精确模式不变（连续子串） |
| `_longest_run()`（新） | 查询在字段里最长连续片段长度，只用于模糊排序 |
| `_scan_condition(..., mode=)` | 打分模式由调用方传入，模糊同样在扫描时过滤 |
| `_single_condition_search()` | 单条件**精确与模糊都走**「扫一次候选 → Python 打分 → 缓存排名 → 按 id 补水」；删掉 `_engine_page()`（引擎分页的 `totalHits` 对中文本来就是错的：`铃铛`=19 实测只有 1 条） |
| `_candidate_window(index_name, attr)`（新）+ `_search_field(..., limit=)`/`_collect_condition(..., limit=)` | 多条件的候选窗口按索引区分：books 元数据 10000（0.1-0.3 s，覆盖全部命中）、chapters 仍 1000（太贵）、`content` 仍 300（候选要带正文） |
| `backend/tests/test_search_service.py` | 改写 3 项（模糊按字门槛、深分页复用缓存、多条件模糊排序），新增 4 项：模糊单条件必须过滤而不是原样返回、深分页只扫一次、各索引的窗口取值、多条件 AND 保住排在 1000 名之外的合法书 |

**验证**：

- `cd backend && python -m pytest -q` → **690 passed**。
- 线上影子回归（把改后的 `search.py` 放进 backend 容器 `/tmp` 导入，只读真实索引）：

| 查询 | 改动前 | 改动后 |
|---|---|---|
| `title` 模糊 `铃铛` | 19 条（1 条真的） | **1 条**《【小铃铛】（1-12）》，且每条标题都含「铃铛」 |
| `title` 模糊 `白骨精` | 406 条（第一名《穿成白骨肿么破》） | **0 条**（实测：407 条候选里**没有**任何标题同时含 白/骨/精，0 是实话） |
| `title` 模糊 `剑来` | 113 条噪声（`matchingStrategy=all` 时 0 条） | **8 条**，全部是《剑归来》系列 |
| `title` 精确 `铃铛` | 1 条 | 1 条（未变） |
| `title=晴晴的 AND category=言情` | **0 条** | **1 条**《晴晴的乖巧日记》 |
| `title+category` / `author+category` / `category+tags` / `title+tags` / `title+author` | 前三组 0 条 | 全部 1 条 |
| 第二次同一查询 | — | 0.00 s（命中进程内排名缓存） |

**未做/已知**：

- **只改本地代码**。线上生效要 `docker compose build backend crawler` + `up -d`（无新迁移）。
  `SearchResult` 响应模型没有 `engine_paged` 字段，前端不受影响；前端**不需要重新构建**。
- 「模糊」现在是**所有字都要出现**（乱序/可断开），所以字符写错的查询（`白骨精` vs `白骨睛`）
  会返回 0，而不是像以前那样返回一堆含单个字的噪声；这也是用户要的「结果必须和搜索词有关」。
  想放宽就用更少的字。
- 单条件深分页仍受扫描窗口限制：元数据 10000 条（= 250 页 ×40，和 Meilisearch 的
  `maxTotalHits` 一致），`content` 300 条（= 8 页）。原因见上表的耗时：chapters 索引在
  10000 条宽度上要几十秒到几分钟，不能为了页数把搜索拖死。
- `content` 搜索**本来就慢**（精确模式一直如此，线上实测一次 10-22 s，窗口越小越省），
  现在模糊模式也走同一条路 —— 相关性换来的代价，正文检索建议用更具体的短语。
- 多条件的候选窗口仍是「有上限的候选集」：books 上超过 10000 条的极端条件（如 `title=的`
  命中 10000+）依然会截断，只是截断线从 1000 提到 10000。
