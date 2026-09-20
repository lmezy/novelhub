# NovelHub 交接记录

本文件只保留“下次还会用到”的信息：环境、红线、历史根因索引、待用户处理事项、排错入口。

> **新增记录请追加到文末**，一节写清「现象 → 根因 → 改动文件 → 验证」即可。
> 不要粘贴长日志、逐条命令输出、历史测试次数或已被后续修复取代的中间状态
> （这些内容占了旧版 60KB，全是噪音）。
> **已经沉淀成结论的历史小节请继续压缩**：现象一句、根因要点、改动落点（文件/函数）、
> 一条仍然有效的提醒；细节去读代码，不要复述实测表格。

---

## 1. 环境与红线

**代码**：`D:\work\git\novelhub`（分支 `develop`）。只改本地代码；不改 `yuedu/`
（开源阅读源码，仅作规则格式参考）；不动线上容器，除非用户明确要求。

**线上**：NAS `nas.19961113.xyz:10022`（SSH config 里的 `master`，用户 `894654222`）。
密钥登录被服务器拒绝，需用密码 + paramiko。部署目录
`/volume1/docker/NovelHub/novelhub`，`docker-compose.yaml` 使用主机网络与预构建镜像
`lonezy/novelhub-{backend,crawler,scheduler,frontend}`。
**最近一次部署：2026-09-19 21:53**（四个镜像重建，代码 = `91b444f`；三个镜像自检
`grep -c task_concurrency_limit` = 3，`max_connections` = 200 已生效）。
**第 39、40 节都改了 backend 与 frontend（§40 = 正文搜索命中片段），重建这两个镜像即一并生效。**

**线上数据库**（注意不是默认端口，`psql` 要带 `-h 127.0.0.1 -p 15432`，密码见 `.env`）：

```bash
docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" novelhub-postgres \
  psql -h 127.0.0.1 -p 15432 -U novelhub -d novelhub -P pager=off
# crawl_tasks(id, source, status, mode, error, result, progress, max_pages, ...)
```

**线上 Meilisearch**：`novelhub-search`，host 网络 `http://127.0.0.1:17700`（Key 同 `.env` 的
`MEILI_KEY`）。宿主机有 `curl`，可以直接
`curl -s -H "Authorization: Bearer $MEILI_KEY" http://127.0.0.1:17700/indexes`；
索引只有 `books`/`chapters`，`pagination.maxTotalHits=10000` 是搜索页数上限（见第 28 节）。

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

**仓库卫生（2026-09-19 清理后）**：`*.pyc` / `__pycache__/`、`.pytest_cache/`、`.env`、
`.commandcode/` 已从版本库移除并写进 `.gitignore`（文件仍留在本地，运行时照常）。
**`.env` 曾经被跟踪过**，里面的 `POSTGRES_PASSWORD`/`JWT_SECRET`/`COOKIE_SECRET`/`MEILI_KEY`
已经进了 git 历史 —— 需要当作泄露处理（换掉这些值，见第 4 节）。

---

## 2. 当前状态（2026-09-19）

- 后端全量测试 **708 passed**：`cd backend && python -m pytest -q`
- Source Engine 闭环已完成并可用：导入书源 → 搜索 → 目录 → 正文 → Storage/DB/搜索 →
  网页阅读。当前工作重心是**同步稳定性与线上排错**，不是新增架构能力。
- **AI 功能已补齐并在线可用**（第 18/19/20/27 节）：后端配置/上下文/流式/划词/RAG +
  前端 AI 设置页与阅读器 AI 面板，同步失败会自动诊断（可关）。使用说明见
  [ai-assistant.md](ai-assistant.md)。
- 并发模型：**一个书源一个 worker**（`SYNC_WORKER_CONCURRENCY=0` 默认不限），书源之间
  不再排队；同一书源同时只跑一个任务。
- **每书源可配「同步间隔」**（第 21 节）：`sources.sync_interval_seconds`，
  设置 → 书源 → 编辑里填「秒/请求」，不填沿用书源 `concurrentRate`。给搬山人这类有拉取
  间隔限制的站点用。
- **阅读路径已提速**（第 22 节）：`/books/{id}/sources` 12s → 0.12s（同名书匹配下推到 SQL，
  同步路径同一个 helper 也一起受益）；章节图片/封面补 `Cache-Control` 与 **304**；
  阅读器与书详情页的次要请求不再挡在正文前面。详见
  [reading-performance.md](reading-performance.md)。
- **搜索已修正**（第 28/29 节）：模糊=每个字都要出现、多条件不再恒空、**同一字段上的多条件 AND
  改走引擎联合查询**（两个正文条件不再恒为 0，可翻页）；线上书本 33.5k / 章节 116.7k，
  `maxTotalHits=10000` 仍是硬顶。
- 仓库已是"本地代码 = 线上代码"的状态（2026-09-19 部署）；**此后新改动仍需
  `docker compose build <服务>` + `up -d`**，线上镜像不会自动跟随本地代码。
  **第 29/30/31 节的改动（搜索联合查询 + progress 500 + 5xx 误判/目录猜测 + 搜索上一页）
  尚未部署**，生效要重建 backend/crawler（前端那处还要重建 frontend）。
- crawler 侧近 24h 无崩溃、无回归（第 30 节）：失败任务全部核到站点侧（CF 挑战/520）或
  代理侧（节点抖动），没有「同步过的书被改判失败」。
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
| 重试后报“书源未返回可同步的书籍”，但前一次已同步 68 本 | 重试尝试的目录全部返回 200 但解析为空，被判成书源没书 | 见第 7 节 |
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
| 任务同步了几百本书，最后却被判 failed（详情里明明 `books_synced=244`） | 翻到下一目录页时 `discover_books` 抛错（站点转人机验证），`discover_and_sync_all` 循环没兜住 → 整个任务算失败，成果只在库里 | 见第 30 节（本 run 有成果时停止翻页并按正常结果收尾） |
| 高级搜索（多条件）怎么填都是 0 条 | 多条件路径每个条件只取 1000 条候选（`CANDIDATE_LIMIT`），`category=言情` 一类条件命中 1847 本，交集被截断后恒为空 | 见第 28 节（`_candidate_window()`：books 元数据 10000） |
| 两个**正文**条件的 AND 恒为 0、翻页也 0 | 每个正文条件各自只取前 300 名候选（按相关性），而「铃」「仙」各命中近万章，两个前 300 几乎不重叠 → 交集恒为空 | 见第 29 节（`_same_field_conjunction`：同属性 AND 改走引擎联合查询） |
| 首页每次加载报 `GET /api/progress` 500 | `ReadingProgressOut.chapter_id` 必填 `str`，而该列是 `ON DELETE SET NULL`（重同步会置空） | 见第 29 节（`schemas/progress.py` 改 `str \| None`） |
| 站点一切正常，整本书却报 `Upstream server returned a transient 5xx error page`（中文成人文学网 9/9 本全挂） | “5xx 错误页”判定用了 `cloudflare` + `error` 裸子串，而正常页面必带 `cloudflareinsights` beacon、Blogger 页面自带 `'iserror': false` | 见第 31 节（`_looks_like_upstream_error`） |
| 书页能识别、书名作者都对，目录却是 0 章 | 书源没写 `ruleBookInfo.tocUrl`，`_find_toc_url` 猜到的“目录页”其实是全站索引；书源自己的 `ruleToc` 在那页只解析出“章节=该索引页”的自引用条目 | 见第 31 节（`fetch_book` 在书页上重跑 ruleToc） |
| 高级搜索点「上一页」：页码变了，列表还是当前页 | 路由 watcher 每次都从 `ADVANCED_CACHE_KEY`（只存“最后访问的那一页”）重画，把刚切回的页覆盖掉了 | 见第 31 节（`BooksPage.vue`） |

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
3. **部署**：改完本地代码后重建对应服务（`docker compose build backend crawler` /
   前端再加 `frontend`）+ `up -d`。**2026-09-19 已部署到第 28 节为止的代码**，
   之后的新改动同样要重建才生效；线上镜像不会自动跟随本地代码。
4. **失效书籍**：源站已删除的书（如爱丽丝书屋 54334）重试也无法修好，只会在
   `crawl_tasks.error` 里给出明确原因；需要时在管理端删除该书。
5. **AI 配置**：已配好（线上在用 `deepseek-flash`，见第 19 节）。模型名会随厂商改版，
   报 400 时点 **设置 → AI → 拉取模型** 按服务端返回的名字选。RAG 需要单独配向量服务。
   详见 [ai-assistant.md](ai-assistant.md)。
6. **换掉 `.env` 里的密钥**：`.env` 曾被提交进 git（`POSTGRES_PASSWORD` / `JWT_SECRET` /
   `COOKIE_SECRET` / `MEILI_KEY`），现已停止跟踪并写进 `.gitignore`，但历史里还在。
   如果这个仓库推送过远端，请把这四个值都换一遍（换 `COOKIE_SECRET` 后旧 Cookie 需要
   重新导入：它也是加密 Cookie 的密钥）。

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
2. 提交前运行 `cd backend && python -m pytest -q`；改前端再跑
   `cd frontend && npm run typecheck && npm run build`。
3. 修复尽量落在“为什么失败”的那一层，并补一个能复现的测试（先确认它在改动前会失败）。
4. 文档只写长期有用的结论：本文件（索引 + 追加小节）和
   [full-site-sync.md](full-site-sync.md)（用户视角的排错清单）。
5. 只改本地代码。线上要生效必须 `docker compose build <服务>` + `up -d`，
   并核对容器里的文件确实是这版（`docker exec ... grep <新函数名>`）。
6. 线上验证一律**只读**：改动文件可以放进容器 `/tmp` 用 importlib 加载跑真实数据，
   跑完删掉；`docker diff <容器>` 里不应出现任何 `app/**.py` 的修改。

---

## 7. 2026-09-12：重试后误报“书源未返回可同步的书籍”

**现象**：要撸小说的全站任务已同步 68 本、10 本因 520 失败，自动重试却以
`书源未返回可同步的书籍，请检查书源规则、Cookie 或站点验证状态。` 收尾，已同步计数被报成 0。

**根因**：① 重试时每个分类页都 HTTP 200 但解析出 0 本（代理抖动典型表现），
`discover_and_sync_all` 的空结果分支不区分「站点真没书」和「这次抓取失败」；② `progress`
计数是“本次尝试”的，重试不累计；③ `start_page > max_pages`（已跑完）也走同一分支。

**改动落点**：`services/sync.py`（超预算→`done`；库内已有书→抛“网络”瞬态错误触发重试；
全新源才保留该错误）；`services/crawl_runner.py`（`_carry_result_counters` 累计计数）；
`services/cookie_health.py`（`rollback()` 后不再读 ORM 属性，修 2 点任务整体的
`MissingGreenlet`）；`yuedu/__init__.py`（分类页 0 本时打带 URL 的 warning）。

## 8. 2026-09-12：Cookie 书源仍报验证码、同步 0 本；全站同步只跑 3 个书源

**现象**：御宅屋（已导入 Cookie）报 anti-bot 但浏览器里页面正常；绅士漫画全站解析出 0 本；
第 4 个书源排队约 4 小时；两本站的书都只同步出 1 章，URL 是 `/cdn-cgi/l/email-protection`。

**根因**（线上实测复现）：

1. **CF 误判**：Cloudflare 给**正常页面**也注入
   `/cdn-cgi/challenge-platform/scripts/jsd/main.js`，而 `challenge-platform` 是裸子串标记 →
   每个 CF 站点的每个页面都被判验证码页（实测浏览器拿到的是 140KB 正常页面）。
2. **`@js:` 的 `header` 被丢弃**：绅士漫画的 header 是
   `@js:JSON.stringify({"User-Agent":…Chrome/142…,"Referer":baseUrl,…})`，旧代码 `json.loads`
   必失败 → 用默认 Android UA 请求 → 站点返回**手机版文档**（无 `gallary_wrap`）→ 规则解析不到书。
3. **XPath 风格列表规则解析成 0 元素**：`bookList=//div[@class='gallary_wrap']/ul/li` 被
   `split("@")` 撕碎（属性里的 `@`）+ 交给 CSS 解析抛 `Invalid character '/'` 被吞；
   `discover_books` 又用 `/novel/123` 路径启发式把规则已命中的书全过滤掉。
4. **全局只有 3 个 worker 槽**（`SYNC_WORKER_CONCURRENCY=3`），长任务占满后第 4 个源一直 pending。
5. **`chapterList` 是「元素规则 + `@js:` 脚本」**，旧代码只认整条 JS → 回退链接扫描，
   把页脚邮箱保护链接当成章节。

**改动落点**：`yuedu/__init__.py`（`CF_CHALLENGE_MARKERS` 换成真实拦截页标记并共用同一份列表、
`@js:` header 用 `JsRuntime` 求值 + 无 Node 兜底 + 缓存、拦截文案区分「没配 Cookie / 配了仍被拦」、
只有书源声明了 `bookUrlPattern` 才做路径过滤、目录规则命中即权威、目录分类 4 路并发
`YUEDU_EXPLORE_CONCURRENCY`）；`rule_engine.py`（`_split_element_steps` 按 Legado 括号配对切 `@`、
`//div[@class='x']/ul/li`/`//li[1]` 等翻译成 CSS、支持「元素规则 + `@js:` 步骤」、
`_eval_xpath` 兜底不再抛异常）；`crawl_runner.py` + `core/config.py` + `scheduler/app/tasks.py`
（**一个书源一个 worker**：`SYNC_WORKER_CONCURRENCY=0` 不限、同源不并发）；`core/logging.py`
（stdlib 日志桥接进 loguru）。

**未做/已知**：依赖完整 Legado 运行时（`Reload(...)`/`java.importScript`）的源仍建议换源（UAA）。
一源一 worker 后可以一口气跑满所有源：每源约占 1 个任务连接 + `SYNC_BOOK_CONCURRENCY` 个书连接
（池是 10+20），源特别多时用 `SYNC_WORKER_CONCURRENCY=8` 之类的上限，或放宽线上 crawler 的
0.5 CPU / 1G 内存限制。

## 9. 2026-09-12：Cookie 书源里个别书报 maximum recursion depth exceeded

**现象**：绅士漫画全站能发现并入库书籍，但少数书（`photos-index-aid-342704`、`-337834`、
`-359759`）报 `Failed to sync book …: maximum recursion depth exceeded`，其余书正常。

**根因**（线上影子回归复现）：这些书的 `ruleBookInfo.name` 是 `{{book.name}}`，而按 URL 打开
书页时 `book` 上下文为空，`_try_eval_js` 的兜底「JS 求不出值就把输入原样返回」把**整页 HTML**
当成了模板值 → 这段 HTML 又被当规则交给 `_eval_css`：页面里 `|` 前存在未闭合 `[`/`(` 时
`_chomp_balanced` 返回 False 却位置不前进（Legado 此处直接 `throw`）→ 原地递归 / 死循环。
只有「`|` 前有不闭合括号」的页面会触发，所以同源只有个别书失败。

**改动落点**：`rule_engine.py`（`RuleUnbalancedError`：括号不平衡时抛错、不再递归；
`_eval_css`/`_eval_json` 捕获后按单片段求值——字段为空而不是整本书失败；`_lookup_variable`
解析 `{{book.name}}`/`{{chapter.title}}`，未知上下文返回空串；`_try_eval_js_value` 不再
“原样返回输入”）；`yuedu/__init__.py`（解析 `ruleBookInfo` 前 `engine.set_book({})`，
避免上一本书的上下文串味）。

## 10. 2026-09-12：部署后日志仍刷 JsRuntime JS error（jsoup shim 能力缺口）

**现象**：任务成功（`books_failed: 0`）但 20 分钟内刷 399 条
`JsRuntime JS error: Cannot read properties of null (reading '0')`，另有零星
`java.getWebViewUA is not a function`、`Unexpected end of JSON input`；绅士漫画正文只有
107 字符（其实是一条图片 URL）——字段被静默丢掉。

**根因**（都是 `jsoup_shim.js` 与 Legado 的语义缺口，线上逐条复现）：

1. `java.getString` 只认 CSS，而书源写 XPath（`//li/div[@class='info']/…/text()`）→ 空串 →
   后续 `.match(...)[0]` 抛 null 错（顺带丢掉 `49P/137P` 页数标签）。
2. 求值内容不对：Legado 是对**当前解析内容**（列表项元素 / 页面）求值，旧实现给的是上一步结果。
3. 属性选择器漏了 `=`：`[class='info']` 退化成“有 class 就算命中”，翻译后选错节点。
4. `java.get(key)` 被实现成 HTTP（Legado 单参数是 `java.put` 的变量存取，双参数才是 HTTP）→
   `JSON.parse('')` → `Unexpected end of JSON input`，图片列表永远为空。
5. 缺 `java.getWebViewUA()`（要撸小说的 `header` 会调它）。
6. 图片型章节没有兜底出口（找不到文字容器就直接报空正文）。

**改动落点**：`jsoup_shim.js`（`java.getString` 支持 XPath 子集与 `##regex##replacement`、
补 `nth-of-type`/`getWebViewUA()`、修属性选择器 `=` 全系列、`java.get(key)` 读变量存储）；
`js_runtime.py` + `rule_engine.py`（新增 `content`/`_js_content`：JS 里的 `src`/`__nhSetContent`
指向当前解析内容而不是上一步结果）；`yuedu/__init__.py`（`_parse_chapter_content_generic`
增加图片兜底，没有文字容器时把内容图输出成 markdown 图片）。

**顺带的需求（同步页排序）**：最近任务按「正在执行 → 失败 → 其余（时间倒序）」排，
后端 `CrawlTaskRepository.list_recent` 用 `CASE` 保证 limit 内先取到在跑/失败的任务，
前端 `SyncPage.vue` 再用同一个 rank 兜一次。

## 11. 2026-09-13：要撸小说连续 520；绅士漫画只保存一条图片 URL

**现象**：要撸小说同一本书从某一章开始连续 `Cloudflare/520`，任务仍逐章轰炸整本书；
绅士漫画部分书的正文是 `#全话阅读` + 一条裸图片 URL。

**根因**：① 源站确实返回 520（站点/代理侧，HTTP 与浏览器都是 CF 错误页），旧逻辑只把单章记 failed；
② 正文 JS 的图片域名正则写死旧域名 `wnimg1.ru`（站点已改 `wnimg2.cfd`）→ 空；CDN 的 `verify`
签名是按 URL 单独生成的，不能拿 `imgInfoList` 直接拼地址。

**改动落点**：`yuedu/__init__.py`（TOC 阶段的 `imgInfoList` 快照存实例上避免并发覆盖；
正文为空且有图片清单时沿 `a.btnnext`/`rel=next` 逐页取正文主图、每页保留自己的签名、
上限 `YUEDU_GALLERY_MAX_PAGES`；优先取正文图片容器以免把顶部广告当章节）；`services/sync.py`
（连续 5 章瞬态错误就跳过该书，`SYNC_MAX_CONSECUTIVE_CHAPTER_FAILURES`；纯 Markdown/HTML 图片
章节算有效内容，单条裸 URL 算无效并在重同步时回填）。

## 12. 2026-09-13：御宅屋被误判反爬；图片 401、长标题写不进、删章节撞外键

**现象**：御宅屋同步到 293 本后报 anti-bot 但浏览器正常；绅士漫画正文图片全 401；
个别书 `[Errno 36] File name too long`；另有 `reading_progress_chapter_id_fkey` 外键错误。

**根因**：① 弱标记（`限流` 等）在**整页**范围找确认词——侧栏书名《限流情缘一线牵》+ CF 脚本里的
`challenge`（相隔 792 字符）即命中，而 anti-bot 会直接 `raise`，一本书就能中止整个任务；
② 章节图片存的是 `/api/chapters/<id>/images/<file>`，浏览器 `<img>` 不带 Bearer，接口只认 Bearer；
③ 目录按「作者/书名」命名，单分量 255 字节上限，日中文长标题可达 345 字节；
④ 重同步删除旧章节时 `reading_progress.chapter_id` 外键仍指向它。

**改动落点**：`yuedu/__init__.py`（弱标记的确认词必须在前后 120 字符内、且标记不能自己确认
自己；分类页 0 本时把字节数/标题/正文开头写进告警）；`services/auth.py` + `routes/auth.py` +
`routes/chapters.py`（媒体 Cookie `novelhub_media`，`<img>` 也能鉴权）；`services/storage.py`
（`safe_segment` 按 UTF-8 边界截断 + `~sha1[:8]`）；`services/sync.py`（删章节前先解引用
progress/bookmarks）；迁移 `0031`（外键改 `ON DELETE SET NULL`）。

## 13. 2026-09-14：代理抖动被误判成反爬（4 个全站任务被中止）；漫画相册固定只剩 12 张

**现象**：09-13 的 4 个全站任务都以「同步连续失败超过 10 本…反爬」收尾，日志里
`Failed to sync book …: ` **冒号后什么都没有**；每个 beat 周期偶发
`got Future … attached to a different loop`；绅士漫画每章永远只有 12 张图且从第 2 张开始（缺封面）。

**根因**（线上实测复现）：

1. 容器内 httpx `ConnectTimeout` 的 `str()` 是**空串**（`ConnectError` 才有文本），而书级/章级瞬态
   判定只看错误**文本** → 代理抖动时连错 10 本就把整个任务判失败；`Request failed after retries`
   （429/5xx 重试耗尽）同样不含 5xx/timeout 字样，也被漏判。
2. Celery 6 个任务都用 `asyncio.get_event_loop().run_until_complete(...)`，每次换循环，而连接池把
   asyncpg 连接绑在旧循环上 → 第二个任务必报 “attached to a different loop”。
3. 相册模式把 `imgInfoList` 长度当页数上限（服务端只拿到第 1 个索引页的 12 条）；
   `MAX_CONTENT_IMAGES_PER_CHAPTER=50` 也会截断。
4. `//div[@class='gallary_wrap tb']/ul/li[1]` 以 `/` 开头，在 Legado 是 **1-based XPath**，
   旧代码把结尾 `[1]` 当 0-based Legado 索引 → 选到第二个 `li`，章节从 `00002` 开始。

**改动落点**：`services/sync.py`（`TRANSIENT_EXCEPTION_NAMES` 按异常 **MRO 类名**判瞬态；
`describe_error()` 空文本回退类名；`content_image_limit()` 默认 512）；
`scheduler/app/tasks.py`（`_run_async()`：每个 worker 进程复用一个事件循环）；
`yuedu/__init__.py`（相册沿「下一张」走到结束、`YUEDU_GALLERY_MAX_PAGES=512`、重试分支记录状态码）；
`rule_engine.py`（`/`、`//`、`./` 开头一律当 XPath、位置谓词 1-based）。

**仍有效**：相册正文是「一张图一次请求」，90 张 ≈ 90 次页面 + 90 次图片请求且受限速；
旧 12 张章节会在**下次同书同步**时自动重抓（`_is_truncated_gallery`）。

## 14. 2026-09-15：代理抖动一次打断所有并发同步；连接池被一次性事件循环污染

**现象**：同一瞬间多本不相关的书失败（`pop from an empty deque` 29 条 + `ClosedResourceError`/
`ReadError`），20 秒内 30 本失败、两个全站任务被判「连续失败超过 10 本…反爬」；
每天 2 点的 cookie 健康检查报 `Exception terminating connection … Event loop is closed` +
`get_plugin failed for '<source id>'`；另有 `Failed to fetch content image <url>: `（冒号后空白）。

**根因**（线上容器复现）：

1. `_get`/`_post` 的重试分支遇到传输错误就 `_reset_http_client(proxy)`，而它把**进程内共享**的
   httpx 客户端 `pop` 出来并立即 `aclose()`——一源一 worker + 书/章并发后有十几个请求共用它，
   代理一抖就把其他请求当场全挂（异常类型取决于请求处于哪一步：读 body 是 `ReadError`、
   流被关是 `ClosedResourceError`、空队列是 `IndexError: pop from an empty deque`）。
2. `registry.get_plugin(<source id>)` 没有运行中的 loop 就 `asyncio.run(...)`、有 loop 就开 helper
   线程 `asyncio.run(...)`，两者都是**一次性事件循环**却用了共享的**池化**引擎 → 连接归还后仍绑在
   已关闭的 loop 上，下个任务取到它就报不同的 loop / Event loop is closed。
3. 图片下载失败日志打印 httpx 异常，`str()` 为空。

**改动落点**：`core/database.py` + `crawler/registry.py`（`lookup_engine`/`LookupSessionLocal`：
一次性 loop 上的短查询用 `NullPool`，绝不进共享池）；`yuedu/__init__.py`（`_reset_http_client`
改为**退休**旧客户端：先摘掉、后台 `YUEDU_HTTP_RETIRE_SECONDS`(45s) 后再关，
`YUEDU_HTTP_MAX_RETIRED_CLIENTS` 限制积压）；`services/sync.py`（瞬态名单补 anyio 流错误与
`pop from an empty deque`）；图片失败日志补异常类名。

**仍有效**：本节的改动只保证「代理抖动不再误伤同进程里的其它并发同步、不再被误判成反爬」；
520/502 之类站点侧错误按设计重试。

## 15. 2026-09-16：正文里的词把正常页面判成验证码页；正文图片大面积取不到

**现象**：風月文學網每次都在同一本书上报 anti-bot（Playwright 实际渲染出 166KB 正常正文）；
禁忌书屋同样以 captcha 收尾；24h 内 2836 条 WARNING 大多是
`Failed to fetch content image <url>`（ConnectError / 404 / ClosedResourceError / ConnectTimeout）。

**根因**（用真实页面在线上容器复现）：

1. **强标记是整页裸子串**：`已被限制` 命中了《隸孃》正文「我的手**已被限制**在厚實手套中」。
2. **确认词太短**：cool18 正文「经过严格的**身份验证**和安检后」附近有「无**人机**在天空中盘旋」
   （`身份验证` 是上下文标记、`人机` 在确认词表里，而 `人机` ⊂ `无人机`）。
3. **图片下载只认 httpx 异常**：连接中途断开抛的是 anyio `ClosedResourceError` / 空 deque，
   落到 `except Exception` 直接放弃；代理失败后立刻改走直连，而这些 CDN 直连必然失败
   （实测走代理 3/5 成功、直连 0/5）；`404` 也重试 3 次退避（一张图一天被重试 478 次）。
4. 图片 CDN 失败会 `_mark_transport_failure(proxy)`，而线路健康度是按**书源域名**记的，
   于是 CDN 抖动把书源页面的线路打进 60s 冷却。

**改动落点**：`yuedu/__init__.py`（新增 `PROSE_GATE_MARKERS`：`已被限制`/`被限制访问`/
`请启用javascript` 移入「需 90 字符内有确认词」的上下文标记；`人机`→`人机验证`、去掉裸 `频繁`；
标记不能自己确认自己；`is_transient_transport_error()` 覆盖 anyio 流错误并在重试前换连接；
图片 404/410 立即放弃并缓存死链 30 分钟；CDN 失败不再污染书源线路健康度）。

## 16. 2026-09-16：小说和漫画混在一起（需求：小说一页、漫画一页）

**背景**：书库只有一个列表，23k 本书里混着 1849 本图集；数据里没有 novel/comic 字段，
只能从书源类型、标签/分类、正文形态间接判断。

**判定规则**（`services/book_kind.py`，不为单站点写死）：① 书源自报图片源
（Legado `bookSourceType == 2`）；② 标签/分类命中漫画关键词（漫画/漫畫/图集/圖集/画集/畫集/
写真/寫真/comic/manga/webtoon）；③ 章节正文只有图片标记、去掉标记几乎没有文字。
`動漫改編` 是風月文學網的**小说**标签，故意不在关键词里。自动判定只从 novel 升到 comic，
只有管理员「重新识别」勾了严格重算才会反向改写。

**改动落点**：`models/book.py`（`books.kind` + 索引）与迁移 `0032`；`services/book_kind.py`
（`classify_book`/`reclassify_books`）；`services/sync.py`（同步时定 kind，正文判定为图集则升级）；
`routes/books.py`（browse/home/favorites 支持 `kind=`、`POST /books/reclassify`）；
`routes/categories.py`（`?kind=`）；前端 `/novels`、`/comics` 路由 + 导航切换 + 管理端「重新识别」。

**仍有效**：旧书由迁移回填，判错的用「设置 → 索引 → 小说 / 漫画识别 → 重新识别」
或 `POST /api/books/reclassify?scan_content=true&source_id=<源>` 重算。

## 17. 2026-09-17：cookie 健康检查的超时掐断 Playwright 导航；御宅屋被误报「Cookie 已过期」

**现象**：24h 内 8 条 ERROR，其中 4 条是每天 2 点 cookie 健康检查（两条间隔 56s/63s ≈
`COOKIE_CHECK_ITEM_TIMEOUT` 60s）的 `Future exception was never retrieved` +
`TargetClosedError`，以及任务收尾的 `Task exception was never retrieved` +
`PipeTransport.run → InvalidStateError`；御宅屋任务报「书源已配置 Cookie 但仍被站点拦截」——
可这个源**根本没配过 Cookie**。

**根因**（线上容器复现）：① 健康检查用 `asyncio.wait_for(_check_one(...), 60)`，而浏览器路径最坏
`goto` 45s + 挑战等待 25s = 70s，超时必然落在导航中间，取消 `page.goto` 会把 Playwright 自己的
导航 future 丢下没人读 → GC 时 asyncio 打 ERROR；② `_captcha_hint` 读 `self._cookie`，
而该字段同时被 `_capture_cookie_jar`/`_capture_playwright_cookies` 写入站点自己 `Set-Cookie`
的会话 cookie（御宅屋渲染一页就下发 `fontsize=16px`）→「没配 Cookie」被说成「配了但过期」。

**改动落点**：`yuedu/__init__.py`（`_fetch_with_playwright` 拆成 `_launch_chromium` +
`_render_page`：渲染跑在 `ensure_future` 里并用 `asyncio.shield` 等待，调用方超时/取消时**不取消
导航**，改为关浏览器让它收尾再 `await` 取回异常；新增 `_configured_cookie`，`_captcha_hint`
改用它判断「有没有配 Cookie」）。

**仍有效**：御宅屋当时那 5 章被判拦截是**真实拦截**（连续同步两天的站点限速），不是误判。

## 18. 2026-09-17：AI 功能补齐（配置、上下文、流式、划词、RAG）

**背景**：项目一直「预留了 ai 接口」但从未可用：配置只有环境变量（改模型要重建容器）、
`RAGService` 硬编码 OpenAI 地址、`claude` provider 走 OpenAI 协议（永远 401/404）、
上下文永远取「书的前 N 章」、`tokens_used`/`chapters_covered` 恒为 0、
AI 请求不走项目代理、`json.loads` 遇到围栏就丢整个回答、
`httpx.AsyncClient()` 每次重建 TLS 上下文（本机 2.45s/次）、SSE 被 nginx 缓冲。

**改动落点**：新增 `services/ai_config.py`（DB 级配置 + 10 个 provider 预设，Key 用 `enc:`
AES-GCM 加密、回前端只给掩码）、`services/ai_client.py`（OpenAI 兼容 + Anthropic 两套协议、
退避重试、流式、embedding、`AIError` 把 401/404/429 翻译成可执行提示、走 `proxy_config`、
TLS 上下文模块级缓存）；重写 `services/ai.py`（按**阅读位置**取上下文、RAG 命中优先、摘要
map-reduce、人物/时间线等距抽样 + 容忍围栏）、`services/rag.py`（向量化走同一 provider/代理/
Key 体系，先查库再决定是否向量化）；`routes/ai.py`/`routes/rag.py`/`routes/admin.py`
（chat 带 `chapter_number`/`mode`/`history`、`X-Accel-Buffering: no`、`GET/PUT /admin/ai`、
`POST /admin/ai/test`）；前端 `AIChat.vue` 重写、新增 `AISelectionToolbar.vue`（划词 5 种操作）、
`AdminPage.vue` 的 AI 标签页、`api/stream.ts` 手写 SSE 帧解析；`nginx/*.conf` 加
`proxy_buffering off` + 3600s 超时。

**仍有效**：RAG 建索引是**同步请求**（大书一两分钟，没做后台任务化）；对话历史不落库；
RAG 没按正文 hash 做增量失效（内容变了要 `force=true` 重建）；向量检索是进程内 numpy 余弦，
单本上限 2000 片段。

## 19. 2026-09-17：AI 测试连接报 400「模型名不被支持」（DeepSeek 模型名已变）

**现象**：填好 DeepSeek Key 后点「测试连接」报
`HTTP 400 … The supported API model names are deepseek-flash, deepseek-v4-pro,
but you passed DeepSeek-V4.1-Flash.`

**根因**：不是 Key/代理/网络，是**模型名写错**：第 18 节的预设默认 `deepseek-chat`，
而该端点只接受 `deepseek-flash`/`deepseek-v4-pro`。代码侧两个问题：预设的默认模型名会随厂商
改版过期；报错虽然带出服务端原文，但要用户自己从 JSON 里挑名字，也没有「你到底提供哪些模型」的入口。

**改动落点**：`ai_client.py`（`extract_supported_models()`、`models_endpoint()` +
`list_models()` → `GET {base}/models`、`_error_hint` 在 400 时直接给结论、`diagnose()` 收敛
「测对话 + 找可用模型名 + 测向量」）；`routes/admin.py` + `routes/ai.py`（`POST /admin/ai/models`；
`test` 失败时返回 `available_models`/`current_model`）；`ai_config.py`（DeepSeek 默认改
`deepseek-flash`）；前端「拉取模型」按钮 + 可用模型一键填入。

**处置**：模型名变了就点 **设置 → AI → 拉取模型**，按服务端返回的列表选，别手填。

## 20. 2026-09-17：同步报错时让 AI 分析（诊断 + 补丁提案走审批，AI 绝不直接改配置）

**需求**：同步报错时让 AI 帮忙 —— 确认的范围是**只诊断 + 生成补丁提案走人工审批**，
触发方式是手动按钮 + 任务失败自动跑一次（可关）。

**设计约束（改这块前先读）**：

1. 红线「AI/RAG 只做消费端，不参与爬取/解析/下载」「不为单站点写死逻辑」「不绕过验证码/WAF/
   登录限制」⇒ **任何 AI 产出的配置改动都必须经管理员批准**，且不允许出现绕过验证码/伪造身份的建议。
2. 线上失败绝大多数是**站点侧**（CF 挑战、520/502、代理抖动、限速、源站删书，见第 11/13/14/15/17 节），
   对它「改配置」不但没用还会**静默把好源改坏** ⇒ 服务端硬校验：分类不是配置类问题时
   **强制丢弃**模型给的规则修改建议。

**改动落点**：新增 `models/sync_diagnosis.py` + 迁移 `0033`、`services/ai_diagnosis.py`
（采集证据 + `sanitize_diagnosis()` 校验并丢弃非配置类建议 + `describe_changes()` 附真实当前值、
`build_patch()`）、`services/source_patch.py`（`config` 深度合并 + 只允许白名单列 + 非法补丁直接拒）；
`routes/ai.py`（`GET/POST /ai/diagnose/{task_id}`、`.../propose`，均管理员）；
`services/crawl_runner.py`（`_spawn_auto_diagnosis` 后台跑，绝不阻塞队列、永不抛异常）；
前端 `SyncPage.vue` 诊断面板 + `AdminPage.vue` 审批 diff。

**仍有效**：自动诊断每次失败任务调用一次模型（默认开，吵或费 token 就在设置里关）；
AI **不会**自动改书源、不自动导 Cookie、不处理验证码；「自动执行白名单（重试/降速/临时禁源）」
没做。证据里**不带 Cookie 的值**（第 27 节只给名字）。

## 21. 2026-09-18：线上同步报错排查 + 每书源「同步间隔」（拉取间隔）

**需求**：① 排查线上同步报错；② 搬山人这类站点有拉取间隔限制，要求**每个书源可配一个同步间隔**
并在启动任务时读取。确认语义：间隔 = **每次上游请求之间**；不配 = 沿用现状（书源 `concurrentRate`
优先，否则 `CRAWL_DELAY_MS=1.2s/请求`）。

**排查结论**（只读线上）：166 个任务的失败全是**站点侧** —— 要撸小说连续 520（CF 回源错误）、
搬山人「连续 5 章被反爬」（书源自带 `concurrentRate=1000` 远快于站点真实限制）、御宅屋 403、
禁忌书屋/風月 Cookie 或人机验证。顺带发现一个真 bug：`crawl_tasks.created_at` 是 DB 本地时间
`now()`，而 `started_at/finished_at` 写的是 UTC → 同步页显示「结束早于开始」，时长全是负的。

**改动落点**：新增 `core/clock.py`（`naive_now()` = 本地时间；`crawl_runner`/`routes/crawl.py`/
`routes/invites.py`/`routes/auth.py` 共 14 处改用它）；`sources.sync_interval_seconds`
（`NULL`=未配置、`0`=不限速、`N>0`=每 N 秒 1 次）+ 迁移 `0034`；新增
`services/source_interval.py`（`clamp_sync_interval()`/`apply_source_interval()`，
**只对实现了 `set_request_interval_seconds` 的插件生效**，避免进程级单例插件的限速漏到下一个源）；
`yuedu/__init__.py`（限速优先级 `SYNC_IGNORE_RATE_LIMIT` > 手配间隔 > `concurrentRate` >
`CRAWL_DELAY_MS`；请求槽仍是类级、按 base_url 共享）；`services/sync.py`
（`_source_plugin()` 统一 5 处建插件 → **任务启动时读源表**；手配间隔时章节并发降为 1）；
`source_patch.py` + `schemas/source.py` + 前端书源表单/列表徽章。

**仍有效**：
- 线上真要解决搬山人，得在 **设置 → 书源 → 编辑 → 同步间隔** 填 `60`（或按实测调整）。
  代价很直接：1 本书 50 章 ≈ 50 分钟，27 本全站 ≈ 20 小时以上，只配真正需要的源。
- **时区已统一（第 32 节已修完，本行原先写着"还有残留"）**：约定是
  `core/clock.py::naive_now()` = naive 本地墙钟。`deleted_accounts.deleted_at` 是 schema 里
  **唯一** `DateTime(timezone=True)` 列，`account.py` 必须继续用 aware UTC；JWT `exp`、
  备份文件名与 manifest 时间戳也按各自规范保留 UTC。

## 22. 2026-09-18：点开书籍/章节要等很久（`/books/{id}/sources` 12 秒 + 图片零缓存）

**现象**：点开书籍/漫画、点进章节都要等很久。

**实测**（NAS 上对着 nginx 计时）：`GET /books/{id}` 0.028s、`/chapters` 0.13s、
`/chapters/{id}` 0.026s、图片 0.037s，而 **`GET /books/{id}/sources` 10-11s**；
前端两处串行 `await` 正好把它放在正文前面（`ReaderPage.onMounted` 与
`BookDetailPage.onMounted` 的 `loading=false` 在最后）。

**根因**：① `list_book_sources` 与 `SyncService._find_same_title_books` 都是**把整库查出来再在
Python 里比标题**（`select(Book)` + tags `lazy="joined"` + categories/custom_tags selectin），
等于对 24k 本书全量 eager load；后者还在**每本全站书同步结束时**被调用，同步也被它拖慢。
② `FileResponse` 只处理 `Range`、**不处理 `If-None-Match`**，也没有 `Cache-Control` →
每次重看一页都重下整张图（线上图片 47,300 张 / 31.5 GB）。③ 正确性陷阱：Python 的 `\s` 匹配
`\xa0`，Postgres 的 `[[:space:]]` 不匹配，全库正好 4 条书名含 NBSP，直接下推 SQL 会让它们静默消失。

**改动落点**：新增 `services/book_title.py`（归一化模式的唯一定义：`normalize_title()` Python 版 +
`normalized_title_sql()` 走**绑定参数**的 `regexp_replace`）；`routes/books.py` +
`services/sync.py`（标题匹配推进 SQL，Python 比较保留为最终确认）；新增
`api/file_response.py`（`cached_file_response()`：补 `Cache-Control` + 实现 `If-None-Match`/
`If-Modified-Since` → 304，含 `W/`、逗号列表、`*`）；`routes/chapters.py`（图片
`private, max-age=604800, immutable`、封面 `max-age=300`）；前端把 `loadAlternates`/书签/进度等
次要请求挪到后台跑。详见 [reading-performance.md](reading-performance.md)。

**仍有效**：>5MB 的大图（571 张）没解决，一章 20 张图在中等带宽下就是几十秒（上传带宽限制），
要做「按需缩放 + 缓存」需要 Pillow 和清晰度取舍；`/books/home` 0.86s 与目录一次返回 2484 章
（675 KB）本次未动。

## 23. 2026-09-18：crawler 刷 601 条空章节报错；小说/漫画搜索混出；搜索翻页要等几十秒

**现象**：① crawler 日志 72h 内 `Failed to sync chapter … Chapter returned empty content` **601 条**，
全部来自御宅屋；② 小说页/漫画页搜索两类结果混出；③ 搜索翻页要几十秒。

**根因**：

1. **假章节**：御宅屋的 `ruleBookInfo.tocUrl`（`/indexlist/<id>/`）的 `<ul id="jsList1">` 由 JS 填充
   （真实浏览器渲染后**也是空的**，说明这些书源站本来就没章节）。`ruleToc` 解析出 0 条后代码
   **退回扫描书籍详情页**，而详情页上唯一的 `/read/*.html` 是「相关推荐」，于是一本书被造出 8 个
   假章节（每章都是一本书的详情页），正文必然为空：约 75 本 × 8 ≈ 601 条，还会累积到
   `SYNC_MAX_CONSECUTIVE_FAILURES` 而中止整个全站任务。
2. **搜索混出**：`books.kind`（第 16 节）只落在 Postgres，Meilisearch 文档没有 `kind` 字段、
   也不在 `FILTERABLE_ATTRIBUTES` 里，`/search/advanced` 也没有 kind 参数 → `/novels`、`/comics`
   共用同一次全库查询。
3. **翻页慢**：`_search_field` 没设 `attributesToRetrieve`，一次候选抓取把**每个候选章节的完整正文**
   （单章上限 10 万字）一起拉回来（旧 `CANDIDATE_LIMIT=5000`；实测 5000 条取 content = 37.8s / 313MB），
   而每点一次下一页都重跑这个查询再在 Python 里排序。

**改动落点**：`crawler/base.py` + `yuedu/__init__.py`（新增 `EmptyTocError`：`ruleBookInfo.tocUrl`
指向的独立目录页没解析出章节时抛它，不再退回扫书页；`_find_toc_url` 猜出来的 URL 仍保留旧回退）；
`services/sync.py`（`EmptyTocError` 记为**跳过**，既不中止任务也不刷章节错误）；
`services/search.py`（`BOOK_RETRIEVE_ATTRS`/`CHAPTER_RETRIEVE_ATTRS` **不含 `content`**、
`CONTENT_CANDIDATE_LIMIT=300`、`kind` 进过滤属性与 `_kind_filter()`、`index_book()` 保证带 `kind`、
`sync_book_kinds()` 回填）；`routes/search.py`（`kind` 参数 + `POST /search/index/kinds`）、
6 处 `index_book`/`index_chapter` 补 `kind`、`main.py` 启动后台回填；
前端 `BooksPage.vue`（搜索带 kind、缓存键含 kind、已取回的页存 sessionStorage）。

**仍有效**：御宅屋那批「源站无章节」的书会出现在任务明细里标为「已过滤 / 目录」，
不要期望它们同步成功；`SearchPage.vue`（独立 `/search` 页）没有 kind 上下文，仍是全库搜索。
候选窗口/页数上限的现状见第 28 节。

## 24. 2026-09-18：为什么别人几百页还快 —— 单条件搜索的深分页

> **本节的结论已被第 28 节取代**：当时把「模糊模式交给引擎」，但引擎的中文匹配是**逐字**的，
> `铃铛` 会把 `铃木`/`铃雨` 一起返回。第 28 节改成「引擎只提供候选，打分与门槛留在 Python」，
> 精确/模糊都走「扫一次 + 缓存 + 深分页」。下面只留仍然成立的部分。

**仍然成立的实测结论**（决定了为什么打分必须在 Python 里做）：这个索引上的中文，
Meilisearch 给不出可用的「整串/短语」语义 ——

| 查询 | 引擎结果 |
|---|---|
| `白骨精` + 默认 `matchingStrategy: "last"` | 404 条，`穿成白骨肿么破` 的命中位置是第 7 个字「破」（charabia 切成 白/骨/精，last 只要求最后一个片段） |
| `白骨精` + `matchingStrategy: "all"` / 短语 | 时好时坏（`"剑来"` 0 条，而真含「剑来」的有 100+ 条） |
| `id` 作为 filterable 属性 + `filter: id IN [...]` | 可用（本页补水靠它） |
| 扫描窗口 `id` + 被搜字段（books/chapters 各 10000） | 0.02s / 0.69s |

**结果**：单条件搜索 = 扫一次候选 → Python 打分 → 缓存排名 → 只按 id 补水当前页；
`page`/`hitsPerPage` 那套「引擎原生深分页」在第 28 节被删掉了，因为引擎的 `totalHits`
对中文本身就是错的（`铃铛` = 19，实测只有 1 条）。

## 25. 2026-09-18：一章几十万字只读到一部分，后面的内容取不到

**现象**：一章 30 万字，阅读器只显示一部分，后面的取不到；刷新后又回到开头。

**排查**（只读线上，用真实浏览器跑线上阅读器）：后端分块接口本身是对的（317,503 字分成
200,023 + 117,504 两块、拼接一致）；桌面滚到底部能追加第二块；**手机翻页模式**在读到末尾时确实
加载了第二块，但紧接着页面整体重载、正文退回 199,991 字且**没有任何新请求**（offset=0 来自
IndexedDB 缓存），之后 494 页要重翻。

**根因**：① 只有隐形的滚动触发点（距底部 900px 才 `loadNextContentChunk()`），一次只加载一块，
且页脚在正文只有一半时就显示「结束」；② 追加只在内存里，重载/重新挂载就丢（offset=0 命中
IndexedDB，看起来像“内容自己缩水”）；③ 分块缓存键只有 offset（`chunk:{id}:{offset}:{limit}`），
章节重同步变长后旧的 `next_offset: null` 会让阅读器**永久**认为已到底。

**改动落点**：`schemas/chapter.py` + `routes/chapters.py`（章节列表带 `hash`；新增
`GET /chapters/{id}/content/meta` 只回 `hash`+总长；分块响应也带 `hash`）；
`stores/books.ts`（缓存键改成 `chunk:{id}:{version}:{offset}:{limit}`，`version` 是内容摘要）；
`ReaderPage.vue`（先取 meta → 再取第一块 → 首屏后后台自动续传剩余块；「已加载 X / Y 字」+
「继续加载剩余 N 字」按钮；页脚在还有未加载内容时不再显示「结束」）；
`public/sw.js`（导航请求网络优先、`/assets/*` 缓存优先、**不拦截 `/api/*`**）。

**仍有效**：见 [reading-performance.md](reading-performance.md)「超长章节（一章几十万字）怎么读」。

## 26. 2026-09-18：crawler 两个任务失败的真根因（`||` 组合列表规则、shim 缺 java.connect）

**现象**：xbookcn（中文成人文学网-短篇）报 `书源未返回可同步的书籍`，21 个分类页全是
`Explore kind … returned no books`（页面有书、解析 0 本）；Icu（hq555.icu）报
「发现规则是 Legado JS 脚本，当前环境无法执行」，而该站当时 200/81KB、无验证码。

**根因**（都在线上容器里用真实页面复现）：

1. **列表规则里的 `&&`/`||`/`%%` 从未切分**：Legado 的 `AnalyzeByJSoup.getElements` 先按
   `splitRule("&&","||","%%")` 再逐段选择（`||` 取首个有结果、`&&` 拼接、`%%` 交错）。
   旧代码把整条丢给 soupsieve：`h3 a||.post-title a` → `Invalid character '|'`，异常被 `except`
   吞掉返回**空** → 组合规则一律 0 元素。
2. **shim 缺 `java.connect`**：Icu 的 `exploreUrl` 第一句就是 `java.connect(url).getBody()`，
   而 shim 只有 `org.jsoup.Jsoup.connect` → TypeError 被书源自己的 `try/catch` 接住并 `return "" + e`
   （所以日志里**没有** JS 报错）。同一脚本还用到 `Element.selectFirst` / `Element.equals`。
3. **纯 JS 字段规则的输入是 bs4 `Tag`**：`_eval_js_impl` 的 `json.dumps(input_value)` 直接抛
   `Object of type Tag is not JSON serializable`（日志刷了 24 条）→ 脚本没跑就失败（Icu 的
   `ruleSearch.kind` 就是这样丢的）。
4. **host-only 的 `bookUrlPattern` 把结果全过滤**：Icu 声明 `https://host:1678`（只有 scheme+host），
   「匹配必须走到路径结尾」永远不成立 → 24 条搜索结果全丢，只剩兜底扫到的首页链接。
5. **书页没有 `<title>`**：Icu 书页只有 `书名：风流穿越` 的正文和封面 `<img alt>`，
   而 `ruleBookInfo.name = "{{book.name}}"`（按 URL 打开时没有 book 对象）→ 书名空 →
   `sync_book` 判 `Book page returned no usable metadata`（章节其实能解析出 20 条）。

**改动落点**：`rule_engine.py`（`_get_elements_from_root`：先 `split_rule` 再逐段链式选择）；
`jsoup_shim.js`（补 `java.connect`（带源 `header`）/`__nhResponse.getBody()`/`selectFirst`/`equals`）；
`js_runtime.py`（`json_safe()`：非 JSON 可序列化的输入转字符串，元素 → 外层 HTML）；
`yuedu/__init__.py`（`_book_url_pattern()`：只有 scheme+host 的模式视为“无模式”，
`_is_book_url`/`_is_chapter_url`/`discover_books`/`_normalize_search_items` 统一改用它；
`_extract_labelled_title()` 从 og/ld+json/`书名：` 兜底取名）。

**验证**：9 项新测试在改动前全失败；线上影子回归 —— xbookcn 精选/现代情色 0 → **20/20 本**、
Icu 发现 **55 个分类 / 24 本**、书名 **风流穿越** + 20 章 + 5112 字正文，绅士漫画（399 本）与
御宅屋（50 章）无回归。

**仍有效**：依赖完整 Android 运行时的书源（UAA 的 `Reload(...)`/`java.importScript`）仍跑不了，
错误文案不变（见 [full-site-sync.md](full-site-sync.md)）；`%%` 交错语义已实现但线上暂无用例。

## 27. 2026-09-18：AI 说「书源未配置 Cookie」，可书源页明明写着「已保存 Cookie」

**现象**：用户在导入书源时**同时填了 Cookie**（设置 → 书源那条源显示「已保存 Cookie」），
但对该源失败任务点「AI 分析这次报错」，结论却是「书源未配置 Cookie，header 中只有 UA」，
`next_steps` 还让用户「去浏览器导出 Cookie 再导入」。

**排查（只读线上数据库）**：`cookies` 表里那条源**有一条**记录（`created_at 21:39:29`），
任务 `started_at 21:39:41`、诊断存于 `21:40:20` —— 任务跑的时候 Cookie 就在库里。
`sync_diagnoses` 里那条诊断的 `reasoning` 原文是模型**猜的**。导入流程本身没问题
（`import_yuedu_sources` 会把 `payload.cookie` 加密写进 `cookies` 表），问题在
`ai_diagnosis.collect_evidence()` 交给模型的证据里**只有任务报错 + 逐书明细 + 书源规则 JSON**，
完全没有 cookies 表状态；而 Legado 书源 JSON 的 `header` 里本来就不会出现 cookie 字段。
**是系统性问题**：线上 16 个书源**全部**有 Cookie 记录，这段代码与书源无关，任何源都可能被误判
（9 条已存诊断里 1 条明确写了「未配置 Cookie」）。

**改动落点**：`services/ai_diagnosis.py`（新增 `collect_login_state()` 读 `cookies` +
`source_credentials` → 「有没有/几条/名字/保存时间/过期时间/有没有自动登录凭据」、
`cookie_names()` **只取名字、绝不带值**、`_cookie_plaintext()` 区分旧版明文与密文解不开、
`render_login_state()`；证据挂到 `evidence.source["cookies"]` 并给任务补时间戳；
`render_evidence()` 增加 `## 登录状态（Cookie）`；`SYSTEM_DIAGNOSE` 增加口径：
「有没有 Cookie 只看这一节，`header` 里看不到不代表没配；已保存 ≠ 仍有效，被拦时说可能已过期」）。

**验证**：线上（只读、不调 AI）对真实那条任务重新收集证据 → `configured=True, count=1,
names=[_gid, cf_clearance, _gat_gtag_UA_99929_1, _ga_JKNXPWV2R8, _ga]`，渲染出「**已保存 Cookie**」，
且 `ss_userid=`/`cf_clearance=` 这些**值一个都没进证据**。

**已知缺口（未改）**：非管理员用「全局」scope 导入走审批流，而保存 Cookie 的代码在那段 `return`
之后 → **这一步填的 Cookie 会被丢掉**。Cookie 没有 owner 列，把个人会话 Cookie 写进待审批的
全局书源属权限问题，不适合顺手改：请在**审批通过之后**再导入一次 Cookie，或改用个人 scope。

**注意**：已经存在的那条错误诊断**不会自愈**（一个任务只自动分析一次），
要在同步页对该任务点「重新分析」。

## 28. 2026-09-18：搜索结果和搜索词没关系 / 多条件搜索全是 0 条

**现象**：① 搜「铃铛」会混进只带「铃」或只带「铛」的书；② 高级搜索（多条件）怎么填都是 0 条。

**排查（只读线上索引）**：

1. **引擎的中文匹配是逐字的**。把前端的查询参数原样发给 Meilisearch
   （`attributesToSearchOn: ["title"]`）：`铃铛` 返回 19 条，**只有 1 条**真含「铃铛」
   （《【小铃铛】》命中长度 2），其余只含「铃」（铃木/铃雨）或繁体「鈴」（被引擎归一成「铃」）；
   `白骨精` 返回 404 条、第一名是《穿成白骨肿么破》（命中位置是第 7 个字「破」）；
   `剑来` + `matchingStrategy: "all"` 直接 **0 条**（真含「剑来」的有 100+ 条）。
   即 charabia 把中文切成单字、默认 `matchingStrategy: "last"` 只要求最后一个片段命中，
   换 strategy 解决不了。上一版据此把「模糊」交给了引擎（第 24 节），代价就是这次的噪声。
2. **多条件 0 条是候选窗口截断**，不是 AND 逻辑错：每个条件只取 `CANDIDATE_LIMIT=1000` 条候选
   再在 Python 里打分，而 `category_names = 言情` 一个条件就命中 **1847** 本；
   《晴晴的乖巧日记》确实同时满足「书名含 晴晴的」与「分类=言情」，但排在 1000 名之外 →
   交集恒为空（`title + author` 这类命中少的组合反而正常，所以看着“时好时坏”）。

**改动落点**（`services/search.py`，其余文件不动）：

- `_condition_score(..., "fuzzy")`：**每个字都必须出现**（可乱序、可断开）才算命中，
  再按「整串命中 > 最长连续片段（`_longest_run`）> 引擎排序分」排；精确模式不变（连续子串）。
- `_single_condition_search()`：精确与模糊**都走**「扫一次候选 → Python 打分 → 缓存排名 →
  按 id 补水」，删掉 `_engine_page()`（引擎的 `totalHits` 对中文本来就是错的）。
- `_candidate_window(index_name, attr)`：多条件的候选窗口按索引区分 —— **books 元数据 10000**
  （实测 0.10-0.17s，覆盖全部命中）、chapters 仍 1000、`content` 仍 300（候选要带正文）。

**验证**（线上影子回归，只读真实索引）：

| 查询 | 改动前 | 改动后 |
|---|---|---|
| `title` 模糊 `铃铛` | 19 条（1 条真的） | **1 条**《【小铃铛】》，每条都含「铃铛」 |
| `title` 模糊 `白骨精` | 406 条（第一名《穿成白骨肿么破》） | **0 条**（实测 407 条候选里没有任何标题同时含 白/骨/精，0 是实话） |
| `title` 模糊 `剑来` | 113 条噪声（`"all"` 时 0 条） | **8 条**，全是《剑归来》系列 |
| `title=晴晴的 AND category=言情` | **0 条** | **1 条**《晴晴的乖巧日记》 |
| 其余多条件组合（title+category / author+category / category+tags / …） | 前三组 0 条 | 全部有结果 |

**「检索到底搜了多少库」的准确口径（2026-09-19 实测）**：

| 层 | 覆盖范围 |
|---|---|
| 过滤（kind / R18 / 可见性 / 标签 / 书源） | **全库**（引擎 filter 作用于全部文档） |
| 匹配（谁算命中） | **全库**（33,573 本书 / 116,678 章逐条算分） |
| 取回候选 + Python 打分 + 排序 + 翻页 | **有窗口**：books 元数据 10000、chapters 元数据 1000、`content` 300 |
| 引擎硬顶 `pagination.maxTotalHits` | **10000**（实测 `limit=30000` 仍只回 10000 条，books 窗口已顶到天花板） |

- 真实命中数（PG `LIKE` 只读）：书名含「的」= **12,466** > 窗口 → 该查询少看到约 2,466 本；
  含「白」620、含「剑」135、含「铃铛」1 → 都远小于窗口，**完整**。
  即：正常查询现在是全库的，只有「命中数 > 10000」的查询（基本只有单字/极常用字）会截断。
- 正文（`content`）是唯一明显受限的：只取 300 章候选 —— 实测提到 10000 要 **118 MB / 105 s**。

**未做/已知**：

- 「模糊」= **所有字都要出现**，所以字符写错的查询（`白骨精` vs `白骨睛`）会返回 0，
  而不是像以前那样返回一堆含单个字的噪声；想放宽就用更少的字。
- 单条件深分页仍受扫描窗口限制：元数据 10000 条（= 250 页 ×40，与 Meilisearch 的 `maxTotalHits`
  一致）、`content` 300 条（= 8 页）。chapters 索引在 10000 条宽度上要几十秒到几分钟，不能为页数
  把搜索拖死。
- `content` 搜索**本来就慢**（精确模式一直如此，线上实测一次 10-22 s），现在模糊也走同一条路；
  正文检索建议用更具体的短语。
- 想做到「书库元数据 = 全库」还要同时：把 `maxTotalHits` 提到覆盖全库（分页设置，不重建索引）、
  books 窗口同步提高、`PAGE_CACHE_MAX_ENTRIES` 调小（一组 33.6k 排名 ≈5MB，×16 ≈80MB/worker，
  现在约 24MB）。更干净的方案（第 24 节起一直记为「未做」）：**索引期**给书名/作者生成 n-gram
  字段并设成 filterable，让引擎直接用 `filter` 做子串匹配；正文不适合（每章几万个 n-gram）。

## 29. 2026-09-19：两个正文条件的 AND 恒为 0；`GET /api/progress` 500

**现象**：① 高级搜索两个正文条件（正文「铃」AND 正文「仙」）结果 0 条，翻页也是 0；
② 首页每次加载（`GET /api/progress?user_id=`）500，线上日志 3 条 `ResponseValidationError`。

**根因**：

1. **同一个字段上的多条件 AND 永远不可能有结果**。第 28 节把多条件的每个条件各自取一个候选窗口
   再在 Python 里求交集，窗口按索引区分（books 10000 / chapters 1000 / `content` 300）。
   但「正文铃」命中 **9 619** 章、「正文仙」命中 **10 000+** 章，两个条件各自的**前 300 名**
   （按相关性）几乎不重叠 → 交集恒为空。多条件搜索不是逻辑错，是**取候选的方式**错。
2. **`ReadingProgressOut.chapter_id` 声明成必填 `str`**，而 `reading_progress.chapter_id` 是
   `ON DELETE SET NULL`（迁移 0031，重同步删旧章节时置空）→ 只要该用户有一行被清空指针的进度，
   整个列表接口就 500。首页「继续阅读」因此一直拿不到数据（`catch {}` 静默）。

**改动落点**（`services/search.py`、`schemas/progress.py`）：

- `_same_field_conjunction()`：多条件 AND 且所有条件落在**同一个属性**上时，改走一条引擎联合查询。
  这是 Meilisearch 唯一能原生表达的 AND：`_conjunction_query()` 把各条件值空格连接，
  `matchingStrategy: "all"` 只返回**每个词都出现**的文档。实测「铃 仙」= **1 299 章**（0.01-0.4 s），
  而旧路径是 0。
- `_conjunction_rank()`：联合查询**只取 `id`**（取正文属性要 12 s/1000 章），结果按引擎相关性
  排名并进 `_page_cache`；`_conjunction_search()` 按 offset/limit 切片，用
  `_hydrate_around()`（把查询词带回去，`attributesToCrop` 裁出命中附近的片段）补水本页，
  再用 `_conjunction_values()` 把这一页的正文取回来做**逐条复核**（引擎的中文匹配是逐字的，
  `匹配策略 all` 仍可能把「铃」「铛」当成两个词）。复核要求**每个条件都命中**，否则丢弃。
- 命中数：`engine_total` 与排名一起缓存（`_conjunction_totals`），所以翻页时总数不会从
  1 299 跳成 1 000；窗口取 `CONJUNCTION_CANDIDATE_LIMIT = 1000`（25 页 ×40）。
- 引擎不支持 `matchingStrategy`（<1.3）时回退到不带该参数的查询，不会把搜索变成硬失败。
- `progress.py`：`chapter_id: str | None = None`。

**验证**（线上影子回归，只读）：改动后 `正文铃 AND 正文仙` → `total=1000`（窗口上限）、
每页 34-40 条、第 1/2 页**零重叠**、抽查 40 条**没有一条**是假命中、每条约 3 s
（首次冷排名 13-27 s，之后走缓存）；单条件正文搜索不变（`铃` 263 条，翻页正常）。
后端 **698 passed**（含 6 个联合查询测试 + 2 个 progress NULL 测试）。

**仍有效/未做**：

- 联合查询窗口 1 000 = 25 页；命中 1 299 时只显示到 1 000（宁可比引擎少，也不给翻不到的页）。
- `content` 检索本身慢（首次冷排名十几秒），同一组条件翻页走缓存（TTL 120 s）。
- 复核只做前 200 条（`CONJUNCTION_VERIFY_MAX_HITS`），更深的页用引擎排名。

## 30. 2026-09-19：crawler「之前能同步的书现在判失败」——一条真 bug + 站点侧故障

**现象**：用户反馈部分书以前能同步，现在任务结束时被判失败（**Icu 同步了几百本之后才出现
JS 问题**）。

**排查（只读线上）**：近 24h crawler 只有 6 条 ERROR、0 次重启（`RestartCount=0`），
其余 285 条是 WARNING。核对到具体书目后分成两类：

| 失败任务 | 报错 | 实测（2026-09-19） |
|---|---|---|
| **Icu（hq555）** | `该书源的发现规则是 Legado JS 脚本…当前环境无法执行` | **真 bug，见下**；站点现在正常（`fetch_explore` 实测 **1392 条**），书源 JS 本身没问题 |
| 中文成人文学网-短篇（27 本全失败） | `Cloudflare 520/5xx` | 走代理 **403 + `Just a moment...`**（CF 挑战页），直连不通；该源 `books` 表里 **0 条**，从没同步成功过 |
| UAA / 禁漫天堂 | 发现规则是 Legado JS | 这两个是真需要完整 Legado 运行时（第 26 节的口径） |
| 爱丽丝书屋 | 连续 5 章被拦 | 站点真实限速/验证 |
| cool18 | 反爬/captcha | 浏览器路径也要过验证 |
| 要撸 / hq555 部分书 | ConnectError / ConnectTimeout | 代理节点抖动 |

**根因（Icu，`crawl_tasks.result` 是铁证）**：任务 `6efe719b` 的结果是
`{"books_found": 258, "books_synced": 244, "books_failed": 13}` —— **9 小时里同步了 244 本、
245 本书在库里（`00:10` → `09:24` 逐个入库）**，然后跑到**下一页目录**时
`fetch_explore` 返回 0 个分类（站点开始要求人机验证），`fetch_explore` 抛 RuntimeError，
`discover_and_sync_all` 的循环**没有兜住它** → 整个任务被判 failed，9 小时的成果在任务列表里
显示成「失败」。错误文案还说是「当前环境无法执行」，而同一个 JS 刚刚成功跑了 244 次。

**改动落点**（`services/sync.py::discover_and_sync_all`）：

- 发现页调用包进 try/except：**本 run 已经同步过书（或已发现书）时，停止翻页并把累计结果作为
  正常结果返回**（`done=True`），日志记 warning；第一页就崩、但该源库里已有书时同样返回结果
  （站点抖动，不该判失败）；**全新源 + 第一页就崩**仍然照旧抛错（保留自动重试与 AI 诊断）。
  `SyncPaused` 原样上抛，暂停/取消语义不变。
- 顺带把「有一个空目录 + 有 js 书源」那句文案改准：先查库里有没有书（有 → 网络/限速/验证的
  瞬态提示），再区分「JS 规则本身跑不了」与「JS 跑了但站点没给分类（要人机验证/限流/改版）」。

**验证**：新增 4 个测试（后页崩不掉成绩、第一页崩但库里有书不判失败、全新源仍失败、
JS 空结果不再说「环境无法执行」），后端 **702 passed**；线上只读跑了一遍 Icu 的
`fetch_explore` → 1392 条正常。**未在线上重跑整任务**（会真实抓几千次，没有必要）。

**仍有效/未做**：

- 站点要人机验证这件事代码解决不了：Icu 的 JS 自己会 `cookie.removeCookie` +
  `java.startBrowserAwait(baseUrl, '人机验证')`，按第 4 节的处置在浏览器过验证后导 Cookie。
- 其余失败任务（CF 520、代理抖动、限速）仍是站点/环境侧，见第 4 节。

**顺带发现（未改）**：h528（風月文學網）17247 本书的章节正文里混进了整页导航/广告
（正文规则没命中时回退到整页文本），章节标题被写成「书名 | 分站 | 分類 | 最新文章」。
这是**内容质量**问题（不是失败），要修得看该书的 `ruleToc`/`ruleContent` 回退顺序。

## 31. 2026-09-19：搜索「上一页」不生效；中文成人文学网整本报 5xx（其实是误判）

**现象**（用户反馈）：① 搜索有多页结果时，点「下一页」正常，点「上一页」页码会变但列表
还是当前页；② 全站同步里 `中文成人文学网-短篇(简体)`（xbookcn）**全程**报 5xx/520 代理失败，
`要撸小说`（yaoluku）偶尔报；③ 代理本身可用（浏览器能上网）。

**排查（只读线上 + 容器内影子回归）**：

1. **搜索翻页**：`BooksPage.vue` 的高级搜索把「最后一页」存进 `sessionStorage` 的
   `ADVANCED_CACHE_KEY`，而 `watch(() => route.query, loadCurrentView)` 每次 offset 变化都会
   从这份快照重画。点「上一页」时 `runAdvancedSearch(0)` 走**缓存命中**分支（只写按 offset
   分页的 `novelhub:books-search-pages`，不回写快照）→ `router.replace` 触发 watcher →
   快照里仍是第 2 页 → 列表被覆盖回第 2 页。页码来自 URL，所以看起来“页码动了、内容没动”。
2. **xbookcn 的 5xx 是误判**（决定性证据）：在 crawler 容器里用真实源码请求
   `https://blog.xbookcn.net/2022/02/blog-post.html`，`_get` 返回的是 **138,425 字节的正常
   Blogger 文章**（`<title>猎美陷阱-短篇成人情色小说</title>`），但
   `_looks_like_upstream_error()` 返回 **True** —— 命中的是 `("cloudflare" in html and
   "error" in html)`：正常页面里有 `static.cloudflareinsights.com/beacon.min.js`，
   Blogger 自己的配置里又写着 `'iserror': false`。于是 9 本书全部被判“上游 5xx”，
   连着 10 本非瞬态失败，任务以「Cloudflare 520/5xx…已中止」收尾。**代理和站点都没问题。**
3. **修完误判后还有第二层**：xbookcn 的 `ruleBookInfo.tocUrl` 是空的，依 Legado 语义目录就
   在书页上；而 `_find_toc_url` 从书页链接里猜到了 `/search/label/目录索引`（链接文字含
   “目录”）。书源自己的 `ruleToc`（`@js:[{name: book.name||"正文", url: baseUrl}]`）在那页上
   只能返回“章节 = 该索引页”这一条自引用记录，随后又被 `title == book_title` 丢掉 → **0 章**。
4. **yaolu 的 520 是真的**：`curl -x http://127.0.0.1:27890` 对 `www.yaoluku.com` 现在也稳定
   返回 Cloudflare `error code: 520`（`cf-ray …-KIX`，走的是 mihomo 的 `日本JP-HY2`）；一小时后
   同一路径恢复正常。属站点/边缘侧瞬态。但旧代码把 520 交给无头浏览器渲染（每个请求多花约
   50 s，渲染出来还是同一张错误页），浏览器再失败时报的是 `anti-bot/captcha`，被 `sync.py`
   当成**非瞬态**失败计数，这正是“一批 520 把任务判成被反爬中止”的来源。

**改动落点**：

- `plugins/yuedu/__init__.py::_looks_like_upstream_error`：只认 Cloudflare 自己的措辞/标记
  （`web server is returning an unknown error`、`error code: 52x`、`cf-error-details/-overview/
  -code`），其余要求“5xx 码 + error 字样同现”且页面 < 30 KB（`UPSTREAM_ERROR_PAGE_MAX_CHARS`）。
- `_is_transient_upstream_status()`：`429` 与全部 5xx 统一按**可重试的上游故障**处理；`_get`/
  `_post` 里 `403` 才走 `_with_403_fallback` + 无头浏览器（520 不再渲染），失败后抛
  `Request failed after retries: … (HTTP 52x)` —— 命中 `TRANSIENT_BOOK_MARKERS` 的
  `request failed after retries`，于是 520-527 不再计入“连续失败中止任务”。
- `fetch_book`：猜出来的目录页（`toc_url_from_rules == False`）如果**没解析出章节**、或解析出的
  条目**全都指向它自己**，就在书页上重跑书源自己的 `ruleToc`（Legado 的默认语义）；
  书源自己声明的 `tocUrl` 不受影响，猜错时仍保留通用扫描兜底。
- `frontend/src/pages/BooksPage.vue`：新增 `advancedCacheKey()/rememberAdvancedPage()`，
  缓存命中分支也回写 `ADVANCED_CACHE_KEY`；`loadCurrentView` 恢复高级搜索时优先取
  **URL 里 offset 对应的那一页**，取不到才回退到快照。

**验证**：新增 6 个后端测试（改动前 5 个失败）；`pytest` **708 passed**；
`npm run typecheck && npm run build` 通过。线上只读影子回归（`/tmp` 加载改动文件，跑完删除，
`docker diff` 无 `app/**.py` 改动）：xbookcn `fetch_book` → 书名「猎美陷阱」/作者「坑神」/
**1 章**，`fetch_chapter_content` → **44,213 字**正常正文。

**仍有效/未做**：

- 真 520（yaolu）代码修不了：站点回源失败期间任何客户端都一样，靠 `SYNC_TASK_MAX_AUTO_RETRIES`
  的 60s/120s 自动重试，或把该源的「同步间隔」调大以减少触发概率。
- `UPSTREAM_ERROR_PAGE_MAX_CHARS = 30000` 是“正常文章不可能这么小”的工程判断：
  真被超大的自建 5xx 页面挡住时，HTTP 状态码那一层仍会重试。

## 32. 2026-09-19：拆分 `YueduPlugin` 上帝类 + 统一时区 + 收敛瞬态异常体系

**背景（不是线上故障，是维护性）**：`plugins/yuedu/__init__.py` 涨到 **5,972 行**，其中
`YueduPlugin` **一个类 142 个方法** —— HTTP 客户端池、线路健康度、Playwright、TOC 猜测、
通用解析、标记判定、图片相册、书架、登录全塞在一起。第 7～31 节每一个 bug 都藏在这一个文件里。

**改动落点（三件，互相独立）**：

1. **按职责拆成 18 个模块**，`__init__.py` 从 5,972 行降到 ~434 行，只留 7 个方法
   （`__init__`/`configure`/`_normalize_source_config`/`display_name`/`source_group`/
   `set_request_interval_seconds`/`update_book` = 插件协议面 + 共享类级缓存）。
   拆法是 **mixin**：`class YueduPlugin(UrlsMixin, ParsingMixin, ExploreMixin, BookMixin,
   ChapterMixin, ImagesMixin, BookshelfMixin, AuthMixin, RenderMixin, PageKindMixin,
   TransportMixin)`。方法体一行未改，全部按 AST 按名搬迁，因此**对外行为不变**。

   | 模块 | 行数 | 职责 |
   |---|---|---|
   | `transport.py` | 1057 | 客户端池 + 退休机制、线路健康度、限流、`header` 规则、DoH、`_get`/`_post` |
   | `parsing.py` | 862 | 通用 HTML → 书名/作者/封面/标签 + 文本清洗 |
   | `explore.py` | 807 | `exploreUrl`、分类、目录分页模板学习、关键词搜索 |
   | `book.py` | 557 | `fetch_book`、TOC 解析与猜测 |
   | `render.py` | 389 | Playwright 渲染、浏览器信号量、挑战等待 |
   | `urls.py` | 364 | 书页/章节页判定、`{{page}}` 模板、`,{...}` URL 选项 |
   | `chapter.py` | 358 | 正文与图片章节、相册翻页 |
   | `bookshelf.py` | 294 | 书架解析（Cookie 体检用） |
   | `markers.py` | 236 | 页面判定词表（验证码/5xx/已删除）+ 窗口确认 |
   | `selectors.py` | 224 | 通用选择器/导航/目录词表 |
   | `page_kind.py` | 189 | `_is_blocked_page`/`_looks_like_upstream_error` 等判定 |
   | `images.py` | 149 | 正文图片下载 + 死链缓存 |
   | `auth.py` / `errors.py` / `common.py` | 36/23/17 | Cookie 与登录；错误分类再导出；规范 logger |

   **`logger` 名字没变**（`common.py` 用字面量 `"app.crawler.plugins.yuedu"`，不是
   `__name__`），所以日志里的 grep 前缀照旧。`from app.crawler.plugins.yuedu import
   is_transient_transport_error / has_contextual_block_marker` 仍可用（`__init__` 再导出）。

2. **时区统一**（第 21 节标注的"还有残留"已清完，那行已改）。约定 = `core/clock.py` 的
   naive 本地墙钟。修了 `cookie_health.py`、`alicesw/login.py`、`ai_diagnosis.py`、
   `repositories/cookie.py`、`token_service.py`、`routes/source_changes.py`。
   **两处是隐性崩溃**：`expired_at` 是 naive 列却与 aware UTC 比较 →
   `TypeError: can't compare offset-naive and offset-aware datetimes`，只在 Cookie 填了
   过期时间时触发，会让整个 2 点体检崩掉。
   `repositories/cookie.py::list_active()` 另有真 bug：`Cookie.expired_at is None` 是 Python
   身份比较（恒 `False`），`False | BinaryExpression` 直接抛 `TypeError` —— 该方法**从未执行成功过**，
   已成 `.is_(None)`。`account.py` 是 schema 里唯一 aware 列，**保留 aware UTC**。

3. **瞬态错误只有一份清单**：原先插件一份（`plugins/yuedu/errors.py`）、`services/sync.py`
   一份，**已经漂移** —— `EndOfStream`/`WouldBlock` 在插件里可重试、在任务级却是永久失败，
   于是这种故障会计入「连续失败」并可能中止整个任务（正是第 13/14/15 节那类误判）。
   现统一到 `core/transient.py`，两侧共用同一个 `is_transient_transport_error()`。
   分类**故意仍按 MRO 类名**（不 `isinstance`）：异常来自 httpx/anyio/playwright/httpcore，
   按类名才与库无关，也才能在 `str(exc)` 为空时仍认出来。
   **`RequestError` 被显式排除**：`httpx.HTTPStatusError ⊂ httpx.RequestError`，列上它会让
   **每个** HTTP 状态错误（含 404）都变成"网络抖动"可重试；状态码该由状态码判定。

**验证**：`cd backend && python -m pytest -q` → **746 passed**（基线 708 + 时区 12 + 分类 26），
49.9s，0 failed。逐阶段验证：Stage A/B 后 720、C 后 720、D/E 后 720、WS2 后 746。
`test_source_interval.py` 的 3 个失败是**本次重构的合法副作用**（limiter 换模块后
`monkeypatch.setattr(yuedu_module, "asyncio", fake)` 打空，测试退化成真睡 60s，整轮从 50s 涨到 172s），
已改成按 `YueduPlugin._sleep_rate_limit.__module__` **动态解析拥有者模块**，以后再搬家不会再脆断。
**未部署**；线上仍是 2026-09-19 之前那版。

**仍有效/未做**：

- 拆分是**纯搬迁**：`__init__.py` 每个方法体一字未改，`git diff` 为 +102 / −5640（无行尾噪音）。
  要改行为就改对应模块，别再往 `__init__.py` 里加方法。
- `rule_engine.py`（1775 行）与 `jsoup_shim.js`（1237 行）**本次未拆**，仍是最大的两个文件；
  10 个测试里仍有以 `__init__` 模块全局为 patch 目标的写法，动 import 结构前先 grep
  `monkeypatch.setattr` / `patch(`。
- `repositories/cookie.py::list_active()` 目前在 `app/` 和 `tests/` 都**没有调用方**。
- `services/backup.py`、`services/jwt.py`、`core/events.py` 的 UTC 用法是**刻意保留**的
  （外部文件规范 / RFC 7519 / 不落库的内存排序），见第 32 节的判定口径。

## 33. 2026-09-19：按 Legado 规范查漏补缺（规则引擎 + JS 侧）

**背景**：以 `yuedu/` 里 Legado 的权威实现为规范，对规则引擎与 JS shim 做了一次系统对照，
产出 [legado-rule-spec-diff.md](legado-rule-spec-diff.md)（97 条逐条差异 + API 面覆盖表），
然后按「无争议的先修」推进。**细节都在那两份文档里，本节只留结论与索引。**

**修掉的真 bug**（都是"规则被切碎或取空"，两条直击 `bookList`/`chapterList`）：

| 编号 | 现象 | 根因 | 提交 |
|---|---|---|---|
| A-3 | `a\|\|b[x]\|\|c` 这类规则**丢掉中间片段**（`['a','','c']`） | `_split_tail` 把"片段起点"和"搜索游标"混成一个 `pos`，跳过括号组时起点也被推走；Legado 是两个变量 | `2df97cf` |
| A-5 | `chapterList=".list li##\s+\|\s+"` 返回 0 元素 | 未在切分前剥 `##` 后缀（Legado `AnalyzeRule.kt:707-709` 就是 `ruleStrS[0].trim()`），而 `SEPARATORS` 含裸 `\|` | `dac5e64` |
| A-4 | XPath 回退规则 `A\|\|B` 取不到值 | 整条含 `\|\|` 的规则被喂给 lxml → `XPathEvalError` → CSS 兜底也抛错 → None | `da0f94b` |
| M-1 | JSON 书源的 `{$.字段}` 跨字段引用解析为空 | 只实现了"当 JSONPath 直读"这一层，缺"先替换内嵌规则、失败再回退"这一层 | `7e17f78` |
| chapter 泄漏 | 无章节上下文的求值会**读到上一章的 title/url** | Node 子进程常驻 + `__nhSetVars` 是合并语义；与第 9 节「上下文串味」同类 | `a4be164` |

**补的能力**（纯新增，`java.*` 缺失的 API）：摘要/HMAC/`htmlFormat`（`8437b31`）、
byte/charset/URL 辅助（`203c98f`）、对称加密 `createSymmetricCrypto`+AES/DES/3DES 19 个（`d8ef160`）、
`toNumChapter` 及中文数字工具（`bfabd75`）、`timeFormat`/`timeFormatUTC`（`6dc568a`）、
JS 上下文 `source.*` 身份键（`13542b2`）、`java.*` 请求的 Referer 默认值（`a75158e`）。

**验证**：全量 `pytest` **802 passed**（起点 746）。每条修复/新增都有**变异验证**——
撤掉改动后判伪测试必须失败；其中 4 条还钉住了 Legado 的"怪但真实"行为
（`aesEncodeToString` 实际在解密、`toNumChapter` 丢弃匹配外文本、`span@text` 去重、
`sh` 单位是毫秒），防止被后人"顺手改好"。

**硬约束审计**（`b52e816..HEAD`）：未改 `yuedu/`；未改 `models/`、`alembic/`（数据库语义不变）；
未改 routes/schemas 的签名与响应模型（`source_changes.py` 只改了时间戳取值）；未提交凭据。

**刻意未做（都写明了理由，别反复重问）**：

- **5 条行为差异**（D-13 `@class` 多值属性、M-9 `@ownText` 与 `@textNodes` 雷同、
  M-10 去重范围过宽、M-8 `@html` 内层/外层、M-7 `@text` 换行）——
  属"哪种更好"的取舍，**等用户裁决**；建议见 legado-rule-spec-diff.md 第 1 节。
- **请求侧 `header` 注入**：✅ **已实现**（`47f66ca`）。落法是注入**原始规则**、
  由 shim 在自己的 JS 环境里求值（`__nhParseHeaders`）—— 同时避开"从
  `_build_js_context` 里再跑一次 JS 会无限递归"与"求值路径 async / 上下文构建 sync"
  两个死结，且没有时序缺口。支持纯 JSON、`@js:`、`<js>…</js>` 三种形式。
  连同 Referer 默认值（`a75158e`），书源声明的请求头现在在 `java.*` 路径上真正生效。
- **请求侧 Cookie 注入 / JS 侧限速**：见 [js-http-request-side.md](js-http-request-side.md) 第 3 级。
  限速的代价是"每次 JS 请求在 Node 内阻塞"，需先实测对并发同步的影响。
- **三处变量存储收敛**：`java.get/put`（`__nhCache`）、`source.get/put`/`Get`/`Put`（`__nhVars`）、
  规则 `@put`（引擎 `_variables`）互不相通，而 Legado 只有一个 ruleData 存储 ——
  所以「规则里 `@put`、脚本里 `java.get` 取」这条链是断的。独立规模，见同文档 §6.3。
- **`t2s`/`s2t`**：依赖第三方 JVM 词典（`com.github.liuyueyi.quick.transfer`），不实现。
- **RSA**（`createAsymmetricCrypto`/`createSign`）：hutool 的密钥解析回退链源码不在仓库，
  parity 无法核实；且 `decrypt` 默认用**公钥**语义反直觉。

**新发现的运行时限制**（与 Android 无关，别当 bug 查）：**单 DES 在 Node 17+ 不可用**
（OpenSSL 3 legacy provider，`des-ecb`/`des-cbc` 不在 `crypto.getCiphers()` 里）。
实现选择报出带原因的清晰错误而非静默返回 null。3DES 不受影响。
另：**GBK/GB2312/Big5 等 charset 在 Node 侧无内建支持**（需 iconv），同样报清晰错误。

## 34. 2026-09-19：第 33 节改动的影子回归（线上容器，只读）

**做法**：把改后的 `rule_engine.py` 与 `jsoup_shim.js` base64 送进 `novelhub-crawler` 的
`/tmp`，用 `importlib` 以相同模块名加载并重绑包属性（注意：**先导入包再加载新模块**，
否则 `js_runtime` 的导入会回到尚未执行完的模块里造成部分导入循环；且
`from package import rule_engine` 拿到的是**导入时绑定的旧模块对象**，探针要用
`sys.modules[...]`）。对**同一个真实书源**跑两遍（部署版 / 改后版）再对比。
跑完删除 `/tmp` 产物；`docker diff | grep 'app/.*\.py$'` **前后均为空**（源码零改动）。

**目标源**：`yuedu_2ca378a79b50`（風月文學網 h528，17,248 本书，规模最大且稳定可同步）。

**修复项在容器里确实生效**（旧 → 新）：

| 探针 | 旧 | 新 |
|---|---|---|
| A-3 `split_rule('a\|\|b[x="\|\|"]\|\|c')` | `['a', '', 'c']` | `['a', 'b[x="\|\|"]', 'c']` |
| A-4 `//div[@id='missing']\|\|//div[@id='b']` | `None` | `BBB` |
| A-5 `.list li##\s+\|\s+` | `[]` | `[<li>a</li>, <li>b</li>]` |
| M-1 `_eval_json({...}, '{$.a.b}')` | `None` | `42` |
| shim `java.digestHex('abc','SHA-256')` | `''`（日志报 `is not a function`） | `ba7816bf…15ad`（FIPS 180-4 向量） |

**真实流水线无回归**（旧 vs 新，逐项相同）：`discover_books(page=1)` **715 本**、
首本 `http://www.h528.com/post/29190.html`、书名 `義嫂11-20`、作者 `水臨楓 (4/15)`、
`chapter_count` **1**、首章正文 **5583 字**、正文开头一致。

**顺带在真实数据上复现了第 30 节记的 h528 内容质量问题**（两版一致，与本次改动无关）：
该书的章节标题是 `義嫂11-20\n分站\n分類\n最新文章` —— 书名+分站+分类+最新文章
这一整片站点导航被当成了章节标题。属内容质量（不是失败），修它要看该书的
`ruleToc`/`ruleContent` 回退顺序。

**注意**：影子回归只覆盖了这一个书源、并且只验证了"改动生效 + 无回归"；
它**没有**验证 header/Cookie 注入在真实站点上的效果（那需要看请求头与站点响应），
也没有覆盖反爬站点。这两点仍需在部署后对着 crawler 日志观察。

## 35. 2026-09-19：`java.createAsymmetricCrypto`（C-23 非对称加密，RSA 族）

**起点**：第 33 节的清单里，C-23 非对称那条被记为「**有不可核实缺口**」——
理由是「`KeyUtil.generatePrivateKey` 的密钥解析回退链在 hutool 里，源码不在本仓库」，
并且顺手把 `createSign` 也判成了「做不到」。

**这个前提是错的，而且错得很关键。** hutool 是公开依赖，`gradle/libs.versions.toml:40`
已经把版本钉死为 `hutool = "5.8.22"`，按 tag 取原文即可
（用 `https://cdn.jsdelivr.net/gh/dromara/hutool@v5.8.22/<path>`；
`raw.githubusercontent.com` 在本机 fetch 不到，jsdelivr 可以）。逐条核对之后：

* **不存在什么「回退链」**：`KeyUtil.generatePrivateKey(alg, byte[])` 就是
  `new PKCS8EncodedKeySpec(key)` 直接交给 `KeyFactory`，**不做 PEM 解析、不做 Base64 解码**。
* `createSign` 也**可做**：`Sign.java` 的方法面是 `sign`/`signHex`/`verify` 加 Legado
  自己的四个 `setXxxKey`；算法名是 JCE 的 `<摘要>with<RSA|ECDSA|DSA>`，Node 的
  `crypto.createSign(<digest>)` 会按密钥类型自动选方案。

**教训**：把「依赖的源码不在本仓库」当成「不可核实」，等于凭空给自己造了一个缺口。
依赖的版本号一旦钉死，它的源码就是可核实的规范 —— 先去翻 `libs.versions.toml`。

**本轮查出的、照直觉写就会错的四处**（细节见 `legado-rule-spec-diff.md` 第 1 节
「C-23 非对称加密」小节）：

1. **OpenSSL 的 PKCS#1 解密是隐式拒绝**：非法补码时返回一段伪随机数据而**不报错**，
   Java 在这里抛 `BadPaddingException`。实测 Wycheproof 的 invalid 向量经
   `privateDecrypt(RSA_PKCS1_PADDING)` 返回 126 字节垃圾。带 `try/catch` 回退的书源
   会**静默拿到垃圾内容**。→ 改用 `RSA_NO_PADDING` 取回原始块，在 JS 里按
   `RSAPadding.unpadV15` 自行校验（PS ≥ 8、`k - 11` 上限，block type 1/2 由持钥方决定）。
2. **`decrypt(data)` 默认用公钥**：Kotlin 是 `usePublicKey: Boolean? = true` 加
   `when(usePublicKey){ true -> PublicKey; else -> PrivateKey }`，`@JvmOverloads` 又生成了
   单参重载 —— **省略参数**走公钥，**显式传 `null`** 才走私钥。按 `arguments.length` 复刻。
3. **构造是急切的**：`BaseAsymmetric.init` 在两把 key 都为 `null` 时立刻生成一对
   1024 位密钥。所以「只 `setPrivateKey` 然后 `decrypt(data)`」用的是那把随机公钥。
4. **`OAEPWith…AndMGF1Padding` 在 Legado 里根本构造不出来**：`getAlgorithmAfterWith`
   只保留最后一个 `"with"` 之后的内容 → `"SHA-1AndMGF1Padding"` →
   `KeyPairGenerator.getInstance` 直接抛错。所以只有不含 `with` 的 `RSA/ECB/OAEPPadding`
   可用（默认 SHA-1/MGF1-SHA1），**这也顺带消掉了「MGF1 用哪个 hash」这个原本只能靠
   假设的分歧**。

**「只支持 RSA」是对齐不是缩水**：Legado 全仓库 grep `bcprov`/`bouncycastle` 命中 0，
`GlobalBouncyCastleProvider` 在类缺失时 `provider` 保持 `null`，且 Legado 从未调用
`setUseBouncyCastle`；所以 `Cipher.getInstance("EC"/"ECIES"/"SM2")` 在 Legado 里同样在
**构造阶段**抛 `NoSuchAlgorithmException`。

**验证**（`0a4089a`，13 条新测试，821 → 834 passed）：

* Wycheproof `rsa_pkcs1_2048_test.json` **全量** 33 组 67 条（42 valid / 25 invalid，
  覆盖 `InvalidPkcs1Padding`、`Sslv23Padding`、`InvalidCiphertextFormat`、CVE-2021-3580）
  逐条比对，**67/67 一致**：valid 解出原文，invalid 全部抛错。
* NoPadding 与**全部手工构造的补码块**都用**纯 Python `pow()`** 生成（完全不经过
  OpenSSL），所以那些断言考的是本实现自己的补码校验逻辑。
* OAEP 密文由 pyca/cryptography 生成。
* 13 处变异逐一施加，**13/13 被杀**。其中「去掉 `getAlgorithmAfterWith` 的截断」那一次
  一开始**活了下来** —— 因为 OAEP 那条断言只查消息里有没有 `AndMGF1Padding`，
  而被变异之后的「不支持的补码方式」错误消息恰好也含这个子串。改成必须出现
  `解析为 "SHA-1AndMGF1Padding"` 才被杀。**这正是变异测试的价值**：
  断言写松了，只有变异能发现。

**下一步**：`createSign`（已确认可做，顺带能覆盖 EC/DSA），以及第 33 节清单里剩下的
D（三处变量存储收敛）。本轮**未部署、未推送**。

## 36. 2026-09-19：`java.createSign`（C-23 签名）—— C-23 收口

接着第 35 节把签名的另一半也做了（`ad166b1`），依据同样是 hutool 5.8.22 原文：

* `SecureUtil.createSignature` 把算法名**原样**交给 `Signature.getInstance`，
  所以 JCE 的 `<摘要>with<RSA|ECDSA|DSA>` 就是规范，而且**大小写不敏感**。
* `SignAlgorithm` 的 17 个常量里，那三个 PSS 用的是 `SHA256WithRSA/PSS` 这种写法
  （大写 `W` 加斜杠），枚举自己的注释写着 `// 需要BC库加入支持`。**Legado 未打包
  BouncyCastle，所以这三个名字在 Legado 里也抛 `NoSuchAlgorithmException`** ——
  本实现同样在构造时报错，是对齐而非缩水。这一点很值得记住：
  "我们不支持" 和 "Legado 也不支持" 是两件事，能查清就必须查清。
* `Sign.init` 先建 `Signature` 再 `super.init()`，后者无密钥时生成密钥对；
  DSA 在 L=1024 时 N 被标准固定为 160（不是猜的），EC 取 256 位曲线
  （Legado 无密钥时生成的密钥对没人能验，选哪条 256 位曲线不可观测）。

**实现**：RSA / ECDSA / DSA × MD5..SHA-512（含 SHA-512/224、SHA-3、RIPEMD160），
`sign` / `signHex` / `verify`，密钥仍是 PKCS#8 / SPKI DER，并与
`createAsymmetricCrypto` **共用密钥解析与类型校验**（所以 EC 钥匙丢给
`SHA256withRSA` 会像 Legado 一样被拒）。明确不支持且构造时报错：`NONEwithRSA`、
`MD2withRSA`、三个 PSS 名与 JDK 别名 `SHA256withRSAandMGF1`、`Ed25519`。

**一处已知差异**：`verify` 对长度错误的签名返回 `false`，Java 的 SunJCE 会抛
`SignatureException`。`verify` 不是 `JsHelp.md` 文档化的书源接口，取"验不过"
更安全，已注明。

**验证**（834 → 839 passed）：RSASSA-PKCS1-v1_5 是**确定性**的，所以签名结果与
pyca/cryptography **逐字节相等**（SHA-256 / SHA-1 / MD5）；ECDSA / DSA 随机，
验的是"pyca 的签名能验过、换消息的验不过、自签自验能过"；
Wycheproof `rsa_signature_2048_sha256_test.json` 的**已发布**向量与
`InvalidSignature` 向量分别验过/验不过。

变异测试 **11/11 被杀**，但其中「去掉 PSS 专用分支」**第一次活了下来**：
断言只查消息里有没有 `RSAandMGF1`，而通用错误消息会**回显算法名**，原串
`SHA256withRSAandMGF1` 里就含这个子串。改成只有该分支才产出的措辞
（`PSS 签名`）之后才被杀。

**这是本轮第二次被变异测试抓到"断言因为错误的原因通过"**（第一次是第 35 节
的 OAEP 那条）。规律很清楚：**凡是断言"错误消息里包含某个词"，只要那个词
会出现在回显的输入里，断言就是假的。** 断言必须挑只有目标分支才会产出的措辞。

**C-23 至此收口**：对称加密族、非对称加密、签名三块都补完并各自有独立来源的
向量验证。剩下的是第 33 节清单里的 D（三处变量存储收敛）。**未部署、未推送。**

## 37. 2026-09-19：变量存储收敛（第 33 节清单的最后一项 D）

`1b590b0`。这条从一开始就被记为"风险最高"，原因是它同时动 Python 的 `_variables`
和 JS 的两个存储，改错的表现是**静默丢变量**。做完之后的结论是：**要动手之前先弄清
Legado 到底有几个存储 —— 答案是三个，不是"一个"**。

**关键事实**：`AnalyzeRule.kt:776` 的 `bindings["java"] = this` 意味着 `java` 就是
`AnalyzeRule` 实例，所以 `java.put`/`java.get` 与 `@put`/`@get` **是同一组函数**：

```
@put:{k:v} -> putRule -> put(k, getString(v))   // :181,:399-403
java.put   -> put(key, value)                   // :740-749
@get:k     -> get(key)                          // :699
java.get   -> get(key)                          // :754-769
```

`put` 只写 chapter/book/ruleData/source 中第一个非空层，`get` 逐层回落、**链尾是
`source.get`**。而 `cache.*` 是 `CacheManager`、`source.put` 是 `BaseSource.put`
（`CacheManager` 的另一个前缀 `v_<sourceKey>_<key>`）—— 三者互不相通。

所以"三处变量存储收敛"的正确含义**不是把三个并成一个**，而是分成 Legado 真正有的
那三个：规则变量（四条路径共用）、`cache`、`source`。改前的实际状态是
`java.put` 与 `cache.*` 挤在同一个对象里（互相看得见），而 `@put` 与 `java.get`
分在两个对象里（永远看不见）—— 两个方向都错。

**两处判断上的坑，记下来**：

1. **B-5 那条「建议用例」是错的**。它写 `@get:{baseUrl}` 应读出页面 URL，但那是
   NovelHub 自有行为：Legado 从不 `put("baseUrl", …)`（全仓库只有 `ReadRssActivity.kt:487`
   的 `put("url", …)`，与书源无关），所以 `get("baseUrl")` 在没人 `@put` 过它时就是 `""`。
   照那条用例写测试，等于把 NovelHub 的偏差固化成"规范"。
2. **删除必须能跨语言同步**。`putVariable(key, null)` 在 Legado 是删除，所以 Python 侧
   的合并回写不能只做 upsert：JS 存储里**少了**的键要按删除处理。但这里有个安全边界 ——
   只有在真的收到那份上报时才这样做，否则进程崩掉/超时留下的空存储会被读成"脚本删光了
   所有变量"，一次失败就清空全书变量。为此加了 `last_rule_vars_seen`，并专门写了一条
   测试钉住它。

**跨进程上报**：bootstrap 在结果/错误块**之前**附带一份规则变量（`__CODEX_VARS_*`）——
放在前面是因为 `_read_result` 遇到 `__CODEX_RESULT_END__` / `__CODEX_ERROR_END__` 就
停止读取。抛异常的脚本同样要上报，因为它的 `java.put` 已经执行过了。

**验证**（839 → 846 passed）：7 条新测试；15 处变异逐一施加，**15/15 被杀**。

**没做的两件事**（都记在 spec-diff 的 D 节）：跨请求持久化（需要给 Book 加列，越界）、
`_variables` 里上下文与规则变量仍未分开（只有书源显式 `@put` 一个上下文同名键时才看得出来）。

**第 33 节清单至此全部处理完**：A-3/A-4/A-5/M-1、C-22/C-23 全部 API、M-7..M-10/D-13
的裁决、请求侧 B/C、B-5、D。**未部署、未推送。**

## 38. 2026-09-19：线上「同步后台报错」——不是规则引擎，是连接池预算 > `max_connections`

`f0615b6`（代码）+ NAS compose 两行（未提交，见下）。

**用户报告**：部署到线上后，同步后台出现报错。

**现象**（只读取证）：

```
novelhub-postgres  20:42:18.951 ~ 20:42:21.944  12 × FATAL: sorry, too many clients already
crawler            sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20
                   reached, connection timed out, timeout 30.00   (20:43:02, 20:45:31)
backend            asyncpg.exceptions.TooManyConnectionsError
                   → /api/books/... 与 /api/books/.../cover 返回 500
```

窗口只有 3 分钟，之后**自行恢复**（复查时最近 3 分钟 backend 5xx = 0、crawler 池错误 = 0）。

**先排除本次部署**（遇到"刚部署就报错"最该先做的一步）：

| 证据 | 结果 |
|---|---|
| `git log b52e816..HEAD -- backend/app/services/crawl_runner.py backend/app/core/database.py backend/app/core/config.py` | **空**（这三处本次会话没碰过） |
| 部署前 / 部署后的失败任务数 | **135 : 1**，且那 1 个是 `b.sis.la` 反爬验证码页 |
| 报错里提到 JS 的两类（`Unsupported URL: @js:`、`反爬规则依赖 Legado JS`） | 发生时间 08-17 ~ 09-19 00:09，**全部早于部署 20 小时以上** |
| `max_connections=50` 是不是部署时改的 | 不是。postgres 容器 **2026-09-10 17:26 创建后从未重建**，配置自 9/10 起未变 |

**真根因**：`run_crawl_task_async` 一进来就 `async with SessionLocal() as db:`（`crawl_runner.py:239`）并**持有到整站同步结束** —— 也就是说**每个并发任务独占一条连接**。而 `_free_slots()` 在不限并发（`SYNC_WORKER_CONCURRENCY=0`，默认值）时**返回固定 32**，它自己的池却只有 `pool_size=10 + max_overflow=20 = 30`：

```python
def _free_slots() -> int:
    if limit <= 0:
        return 32          # ← 32 > 30：第 31 个任务必然等 30s 池超时后失败
    return max(1, limit - len(active))
```

更根本的是整个栈的连接预算。**每个 Python 进程各有自己的池（10+20=30）**，而 backend 跑 `uvicorn --workers 2`、crawler 容器里同时有 `queue_worker` 和 `celery worker`（主进程 + ForkPoolWorker）、scheduler 还有若干进程：

```
backend      2 × 30 = 60      ← 光 backend 一个服务就超过 max_connections=50
crawler      queue_worker 30 + celery 主/子 60
scheduler    若干进程
                        理论 ≈ 150+；实测空闲时 50 条里也已占 30 条（25 条是应用连接）
```

**触发点**：20:32 重启 → crawler 启动时 `_reset_stale_running_tasks()` 把上次被 kill 时仍处于 `running` 的 `discover_all` 任务全部改回 `pending`，**20:32:56 一次性起了 7 个**；用户在 20:37 / 20:39 / 20:44 又起了几个 → **11 个全站同步并发**；再加上前端约 0.5s 一轮地轮询 `/api/crawl/tasks/{id}` → 20:42:18 打满 50。

**修法**（`f0615b6`，`backend/app/core/{config,database}.py` + `backend/app/services/crawl_runner.py`）：

1. `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` 让池大小可配（默认仍是 10/20，向后兼容），于是"栈共享一个小数据库"的部署可以按服务缩小池。
2. `db_pool_capacity()` 报告"一个池实际能同时服务多少连接"；`task_concurrency_limit()` 把并发上限（操作员的 `SYNC_WORKER_CONCURRENCY` **和**"一源一 worker"默认）**都夹到该预算内**，并留 4 条给队列循环与 `_write_task_row` —— 后者恰恰在"所有任务同时失败"时才需要连接。
3. `_worker_loop` 里 `limit <= 0` 的分支和那个固定的 32 一起删掉：`limit` 现在恒为正，循环条件简化为 `len(active) < limit`，`_free_slots()` 只负责"填满空位且不超预算"。

**验证**（846 → 851 passed）：5 条新测试；7 处变异逐一施加，**7/7 被杀** —— 包括"改回固定 32"、"操作员上限不再夹"、"槽位数等于整个池（不留预留）"、"worker 循环忽略预算"、"引擎不再读池配置"。

**线上 compose 两行**（`/volume1/docker/NovelHub/novelhub/docker-compose.yaml`，已备份为 `docker-compose.yaml.bak-20260919-211037`）：

```diff
-      -c work_mem=32MB
+      -c work_mem=16MB
-      -c max_connections=50
+      -c max_connections=200
```

`work_mem` 一起降是因为它按**每个排序/哈希节点**收费：连接数翻 4 倍而不动它，12G 的 postgres 容器最坏情况内存也会翻 4 倍。改 `command:` 必须**重建 postgres 容器**才生效（数据在 `./data/postgres` bind mount 上，重建不丢数据），所以这条和镜像重建放在同一个窗口做。只改文件不重建 = 不生效（`SHOW max_connections` 仍是 50）。

**顺带确认的第 1 节结论**：NAS 用预构建镜像 —— compose 里 backend/crawler/scheduler **只有 `image:` 没有 `build:`**，代码是 COPY 进镜像的（只有 `./storage` 与 `/imports` 是 bind mount），所以 `docker compose restart` 不会让代码生效，必须重建镜像。注意部署目录 `/volume1/docker/NovelHub/novelhub/backend` 里的源码停留在 8/10（`plugins/yuedu/` 还是拆分前那 4 个文件），**不是**构建镜像用的那份。

**部署机制（复核）**：绿联 Docker 可视化上的「重新部署」**不是 restart**，而是 compose v5.1.3 的 `up -d`：容器 label 里有 `com.docker.compose.*` 与 `replace`，四个镜像的 `RepoDigests` 非空（说明是从 Docker Hub 拉的），NAS 上 buildx 缓存最新记录停在 5～6 周前（NAS 不 build）。所以这次改 `command:` 后的一次点击会**连 postgres 一起重建**，`max_connections=200` 随之生效。

**一个已经踩到的坑**：线上镜像 build 于 `20:11–20:13`，而 `1b590b0`（变量存储收敛）提交于 `20:05:47` —— 镜像里却没有它（`__nhRuleVars=0`、`last_rule_vars=0`）。原因只能是构建时那个 checkout 落后于本地：提交还没 push，构建机 pull 不到。**构建前必须先 push、再在构建机上 pull**，并在 push 镜像前自检：

```bash
docker run --rm --entrypoint sh lonezy/novelhub-crawler:latest \
  -c "grep -c task_concurrency_limit /app/backend/app/services/crawl_runner.py"   # 必须 >= 1
```

**后续（已完成）**：2026-09-19 21:53 镜像重建并重新部署，postgres 同时重建，`SHOW max_connections` = 200。提交已推送。
---

## 39. 同步页三个现象 + 队列静默停摆 12 小时（2026-09-20）

**现象**（用户报）：①点同步后任务不显示；②点暂停任务直接从页面消失；③历史任务显示 100 条但无法清理删除。

**根因 1（①②，列表被饿死）**：`CrawlTaskRepository.list_recent` 用 `STATUS_RANK={running:0, failed:1}`
再按 `created_at desc` 排，前端要 `limit=100`。线上 `failed=137`，实测 TOP100 **全是 failed**：
新建的 `pending` 和刚暂停的 `paused` 都是 rank 2，被挤到 100 行之外。任务没丢，只是"排不上"；
`loadTasks()` 找不到活跃任务还会 `crawlStore.clear()`，选中面板一起空掉。

**根因 2（③，功能缺失）**：`/crawl/tasks` **从来没有 delete / clear 接口**，前端也没有按钮；
`crawl_logs` 还没有外键，删任务会留孤儿日志。

**根因 3（真正严重：worker 12 小时不消费）**：`_worker_loop` 把启动过的任务全放进 `active`，
只有协程返回才释放槽位、`running_sources`（同源互斥）和会话。暂停/取消只在 sync 的
checkpoint（`before_step` / `checkpoint_cb`）里被发现；源站被反爬/代理拖住时协程长期到不了
checkpoint，**槽位就被用户早已暂停的任务永久占住**。线上证据：15 paused / 24 cancelled /
**0 running**，9 个 `09:36:02` 新建的任务一直 pending，且这 9 个源与 paused 的源完全重合
（`pending_shadowed=9`），于是永远排不上。

判定"循环根本没跑到调度"的方法（可复用）：`_next_pending_tasks` 的 `WHERE status='pending'`
没有索引 → 必然 seq scan，所以 **`pg_stat_user_tables.seq_scan` 冻结 = 该查询没执行**。
实测 `crawl_tasks.seq_scan` 60 秒零增长，同期 `idx_scan` 每分钟恰好 +30（= 前端每 2 秒轮询
`GET /crawl/tasks/{id}` 走主键那一次）。能跳过 `_next_pending_tasks` 的代码路径只有
`len(active) >= limit`（= 26）—— 即 26 个协程全部卡死。worker 进程本身健康：
PID 7 在 `do_epoll_wait`、CPU 不增长、池 12/30 没满，所以不是连接池问题。

**改动**：
- `backend/app/services/crawl_runner.py` 新增监督器：`_ActiveTask` 保存每个在跑任务的监督状态；
  `_read_active_task_states()` 一次查询批量读 `status/progress`；纯函数 `_abandoned_tasks()`
  判定谁该放弃（行状态离开 running/pending 超过 `SYNC_TASK_STOP_GRACE_SECONDS`，或 running 但
  progress 心跳 `SYNC_TASK_STALL_SECONDS` 未变）；`_supervise_active()` 取消协程（stalled 的先用
  `status='running'` 守卫写成 failed）；`_release_abandoned()` 回收"连取消都不理"的僵尸槽位
  （`released=True` 让它的 finally 不再去抢新任务的源）。`_worker_loop` 每次监督共用同一个 `now`，
  且监督清空 `active` 后要 `continue`（`asyncio.wait([])` 会抛 ValueError）。
- `backend/app/core/config.py`：`SYNC_TASK_STOP_GRACE_SECONDS=120`、
  `SYNC_TASK_SUPERVISE_INTERVAL_SECONDS=15`、`SYNC_TASK_STALL_SECONDS=3600`（0 = 关看门狗）+ `_seconds_setting()`。
- `backend/app/repositories/crawl_task.py`：`STATUS_RANK = {running:0, pending:1, paused:2}`，
  **failed 不再参与排名**（落进 CASE 的 ELSE，永远排在活跃任务之后）。
- `backend/app/api/routes/crawl.py`：`DELETE /crawl/tasks/{id}`（先删 `crawl_logs` 再删任务，
  pending/running 要求先取消）、`POST /crawl/tasks/clear-history`（只删四种终态；非管理员只删自己的，
  与列表同规则）。
- 前端：`SyncPage.vue`（排序与服务端一致、行内删除、清理历史按钮）、`stores/crawl.ts`
  （`POLLABLE=[pending,running]`，paused 不再每 2 秒轮询 —— 那正是这 12 小时里唯一的数据库活动）、
  `stores/i18n.ts`（zh/en 各 6 个新键）。

**验证**：851 → 862 passed；11 处变异逐一施加 **11/11 被杀**（改回 failed 排名、暂停不回收、
看门狗不触发、pending 也当过期、`0.0` 当未设置、僵尸保留源、循环不监督、设置不读环境、
删除不删日志、running 可删、清理历史连 pending 一起删）；`npm run typecheck` 与 `npm run build` 通过。

**待用户处理**：重建 **四个**镜像（本次含 frontend，上次只重建了三个），重新部署即可 ——
crawler 重启会自然清掉当前僵尸槽位，那 9 个 pending 任务会立刻开跑（源站仍在反爬，失败是源站问题）。
postgres 不用再动。

## 40. 正文搜索命中片段：手机上"看不到命中词"（2026-09-20）

**现象**（用户报）：正文搜索的结果，手机端和电脑端显示的内容不一致，手机端更少，而且**显示出来的
内容里不一定有命中词**。

**根因 1（后端，命中词压根不在片段里）**：单条件正文搜索走 `_single_condition_search` →
`_hydrate()`，而 `_hydrate` 用**空 query** 取正文（`search("")`）。Meilisearch 只围绕 query 词做
crop，空 query 下返回的就是**章节开头**，所以命中词通常根本不在片段中。线上实测（正文「老鸡婆」，
4 条命中）：3 条 `snippet.find(词) == -1`，片段开头是书名/作者/发布日期/pixiv 字数，即章节头部。

**根因 2（前端，两行截断是"按像素"不是"按字符"）**：两端都用 `line-clamp-2`，但一行能放多少字
取决于视口：桌面一行约 70+ 字，手机（360px、`text-xs`）一行约 26 字，两行 ≈ 52 字。片段若以命中词
为中心（旧 `_snippet(radius=80)`，或引擎 conjunction 的居中 crop，实测命中词落在 118 字窗口的第 73 字），
桌面两行看得到、手机两行看不到 —— 这就是"手机端更少、且看不到命中条件"。

**改动**：
- `backend/app/services/search.py`：`SNIPPET_LEAD_CHARS=12` / `SNIPPET_TAIL_CHARS=160`，`_snippet()`
  从"以命中词为中心"改为"以命中词为起点"（前留 12 字 + `...`，后留 160 字）；12 + `...` 保证命中词
  落在**手机第一行**内（360px 一行约 26 字），尾巴才是截断吃掉的部分。
- 正文片段改在 `_scan_condition()` 里生成 —— 那是唯一同时握着这批命中原文的地方（扫描窗口 300 行），
  页码缓存行变成 `(score, id, snippet)`；只有 `attr == "content"` 才带片段，元数据行仍是 `""`，
  缓存体积不变（正文 300 行 ≈ 60KB）。**不要再把正文片段交给 `_hydrate()` 的空 query crop。**
- 多条件 AND（引擎 conjunction）用 `_hydrate_around()` 的居中 crop，改为再锚定一次
  （`_serialize_scored_hit(..., values=...)`）。`_hydrate` 的空 query 语义保持不变（不会因 query
  过滤而丢文档，`_hydrate_around` 做不到这点）。
- 前端新增 `frontend/src/utils/snippet.ts`（`splitSnippet`）与 `components/SnippetText.vue`，
  SearchPage / BooksPage 共五处片段用它把命中词包成 `<mark>`。

**验证**：866 passed（原 862 + 4 新增）；6 处变异全部被 KILL（扫描不建片段、恢复对称窗口、lead 调大、
serialize 丢片段、conjunction 不锚定、元数据行带片段）。线上 A/B（把修好的 `search.py` load 进
backend 容器跑真实索引）：单条件正文的 ids 与 total **完全一致**，`snippet.find(词)` 由 -1 / 73
变成 15（= 12 + `...`）或更小。headless Chrome 在 360px 渲染真实片段另确认两点：`<span>/<mark>`
作为 `line-clamp-2` 的子元素**不会被块级化**；修复前手机首行是书名/作者，修复后首行就是高亮的命中词。

**排错入口**：`snippet` 字段可由正文扫描窗口缓存，所以改了 `_snippet` 的窗口参数后页码缓存键不含它
（`PAGE_CACHE_TTL_SECONDS=120`，两分钟内新参数不会立刻生效，属正常）。

## 41. Cookie 更新不改时间戳 /「自动 AI 分析」关不掉（2026-09-20）

**现象**（用户报）：
1. 失败的 AI 分析说 Cookie 是"很久以前"保存的，但用户确信自己更新过 Cookie。
2. AI 页面的「同步失败后自动 AI 分析」开关关不掉，刷新后又自己打开。

**根因 1（Cookie 的值换了，时间戳没换）**：`cookies` 表只有 `created_at`（首次入库），
`PUT /cookies/{id}` 只重写 `cookie_data`。AI 诊断把 `created_at` 渲染成「Cookie 保存时间」，
于是当天刚粘贴的 Cookie 被算成「间隔约 40 天」，模型据此判定 Cookie 已过期、让用户再导出一次。
线上实测（`sync_diagnoses` 对照 nginx 访问日志）：
- 爱丽丝书屋 `e3d1369d…`（`yuedu_31a56dc1e2a9`）：创建 2026-08-11 20:52:52；诊断 2026-09-20 17:24:01
  写「Cookie 保存于 2026-08-11、本次任务在 2026-09-20，间隔约 40 天」；而同一行在 **2026-09-20
  12:21:58** 刚被 PUT 更新过（200）。值是最新的，被算成 40 天前。
- 搬山人小说网 `4c7525f6…`（`yuedu_fc5c098852e0`）：创建 2026-08-13 16:35:00；诊断 2026-09-20 11:53:30
  写「Cookie 是 2026-08-13 写入的、距今已一个多月」；而该行在 2026-09-16 14:32:20 与 **2026-09-18
  12:52:43** 都被 PUT 更新过，距诊断只有两天。
所以**新 Cookie 确实替换了旧值**（用户问的就是这个）；坏的是"保存时间"这个字段。

**根因 2（开关被 Pydantic 静默丢掉）**：`AIConfigUpdate` 没声明 `auto_diagnose`。Pydantic 默认丢弃
未声明字段，`model_dump(exclude_unset=True)` 里就没有它，`set_ai_config` 永远写不到；线上
`app_settings` 里**根本没有 `ai_auto_diagnose` 这一行**。前端 `loadAIConfig` 用
`res.auto_diagnose !== false` 兜底成 true，所以开关每次都弹回打开；接口还返回 200、UI 显示「已保存」。
存储层 `FIELD_TO_KEY` 早就备好了 `auto_diagnose → ai_auto_diagnose`（`set_ai_config` 也认这个 bool），
断的只有请求模型这一环。

**改动**：
- `models/cookie.py` 加 `updated_at`（`server_default=func.now(), onupdate=func.now()`）。用 `onupdate`
  而不是只在路由里赋值：写 `cookie_data` 的入口有四个（`routes/cookies.py`、凭据自动登录
  `routes/credentials.py`、`cookie_health._try_refresh`、手动登录），ORM UPDATE 会自动带上时间戳。
- 新迁移 `0035_cookie_updated_at`：加列 → **用 `created_at` 回填** → 补 `DEFAULT now()`。回填绝不能用
  `now()`，否则所有老 Cookie 会一起变成"刚更新"，比原来的错更危险。`test_migrations` 的 head 断言同步改。
- `services/ai_diagnosis.py`：证据同时给「最近写入」与「首次保存」，口径里写明**判断新旧只看最近写入**；
  选行也从 `created_at` 改成 `updated_at or created_at`（就地更新的行不该被"先入库"顺序挑中）。
- `routes/admin.py`：`AIConfigUpdate` 补 `auto_diagnose: bool | None = None`。
- `routes/cookies.py`：更新落一条 info 日志（原来更新路径**完全没有日志**，排障只能翻 nginx）。
- 前端：书源面板的 Cookie 行显示「最近更新」；展开书源时把已存的 `expired_at` 回填进 `datetime-local`
  —— 原来面板对着一个已有 Cookie 却显示空日期，点「更新 Cookie」会把用户没碰过的 `expired_at`
  静默清成 null（既有 14 行数据的 `expired_at` 本来就都是空，属潜在坑）。

**验证**：866 → 873 passed（+7）。把 5 个源码文件 `git stash` 掉跑新测试 **6/6 失败**（另一个是
"新值确实替换旧值"的守卫测试，两边都过），`git stash pop` 后全绿；`npm run typecheck`、`npm run build`
通过。`alembic upgrade 0034_source_sync_interval:0035_cookie_updated_at --sql` 产出：
`ALTER TABLE cookies ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE` →
`UPDATE cookies SET updated_at = created_at` → `ALTER TABLE cookies ALTER COLUMN updated_at SET DEFAULT now()`。

**待用户处理**：重建 `lonezy/novelhub-backend` 与 `lonezy/novelhub-frontend` **两个**镜像并重新部署。
backend 的启动命令自带 `alembic upgrade head`，迁移会自动执行。部署后老 Cookie 的「最近写入」等于
它的首次保存时间（故意如此），再更新一次就会看到时间跳到当下。

## 42. 部署第 41 节后登录 502 九分钟：迁移在等 cookies 的表锁（2026-09-20）

**现象**（用户报）：重建 backend + frontend 镜像并重新部署后，登录 502，约 9 分钟后自己好了。

**直接原因**：后端的启动命令是 `alembic upgrade head && uvicorn ...`（`&&` 串联）。迁移卡在等表锁上，
**uvicorn 就一直没启动**，nginx 没有上游 → 任何请求（含登录）都是 502。与登录逻辑无关。

**现场证据**（pg_locks / pg_stat_activity，`cookies` 上共 19 条锁）：

| 进程 | 在做什么 | cookies 上的锁 | granted |
|---|---|---|---|
| 66654、66771、68782 | `idle in transaction`（读过 cookies 后事务一直没结束） | AccessShareLock | ✅ 已持有 |
| 68820（alembic） | `ALTER TABLE cookies ADD COLUMN updated_at` | AccessExclusiveLock | ❌ 排队 |
| 67718、67399 等 15 个 | `SELECT cookies …` | AccessShareLock | ❌ 被 ALTER 挡住 |

时间线：21:09:06 三个长事务拿到读锁 → 21:09:47 ALTER 开始排队 → 21:18:16 持锁事务结束、ALTER 立刻
完成、uvicorn 拉起、healthy。

**根因 owner（不是第 41 节的改动有错）**：`ALTER TABLE` 必须拿 `ACCESS EXCLUSIVE`，这是 Postgres 的
规定；真正的毛病是 **`services/sync.py` 把「读 Cookie」和「爬完整本书」放在同一个事务里**。5 处
`select(Cookie)` 都写在 `self.db`（爬取会话）上，Postgres 会把那次读拿到的 `ACCESS SHARE` 一直持有到
事务结束，于是 `cookies` 被锁几分钟。`ALTER TABLE` 只是把这件事暴露出来——**任何将来动 `cookies`
（或其它热表）的迁移都会再触发一次同样的 502**。

**改动**：`services/sync.py` 新增模块级 `load_source_cookie(source_id)`，用**自己的短会话**
（`async with SessionLocal() as db`）读 Cookie 并立即关闭；5 处调用点全部换用它。Cookie 从不被 sync
写入，所以唯一变化就是锁的生命周期。`routes/crawl.py::retry_task` 里还有一处同类写法（见下）。

**验证**：866/873 → **874 passed**。改动后未加 fixture 时 **23 个 sync 测试立刻失败**
（`ConnectionRefusedError`：新路径不再走 mock 的 `db`，而是去连真实 `SessionLocal`）——
这 23 个失败本身就是「5 处调用点确实全部走新路径」的证据。`test_sync_service.py` 加 autouse fixture
`_no_stored_cookie` 默认「该源没有 Cookie」，需要 Cookie 的两个用例显式 patch；新增
`test_stored_cookie_is_read_on_its_own_session` 断言独立会话被打开**并且被关闭**（锁就是在关闭时释放的）。

**待用户处理**：重建 `lonezy/novelhub-backend` **和 `lonezy/novelhub-crawler`** 两个镜像——
crawler 镜像内嵌了 `backend/` 这份代码（`COPY backend/ ./backend/`），它是真正压着锁的那一方，
只重建 backend 不解决问题。

**已发现但未改（同类，等确认）**：`backend/app/api/routes/crawl.py::retry_task` 第 291-294 行
在自己的请求会话（长事务）上读 Cookie，而那个 `plugin` 变量**后面根本没被使用**——`svc.sync_bookshelf()`
会自建 plugin 并自己设置 Cookie。也就是说这 4 行的唯一可观察效果就是压住 `cookies` 的读锁，直接删掉即可。
