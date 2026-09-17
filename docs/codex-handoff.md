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

## 2. 当前状态（2026-09-17）

- 后端全量测试 **569 passed**：`cd backend && python -m pytest -q`
- Source Engine 闭环已完成并可用：导入书源 → 搜索 → 目录 → 正文 → Storage/DB/搜索 →
  网页阅读。当前工作重心是**同步稳定性与线上排错**，不是新增架构能力。
- **AI 功能已补齐**（第 18 节）：后端配置/上下文/流式/划词/RAG + 前端 AI 设置页与阅读器
  AI 面板。使用说明见 [ai-assistant.md](ai-assistant.md)。
- 并发模型：**一个书源一个 worker**（`SYNC_WORKER_CONCURRENCY=0` 默认不限），书源之间
  不再排队；同一书源同时只跑一个任务。
- 线上仍跑着旧镜像；本地改动要 `docker compose build backend crawler` +
  `docker compose up -d backend crawler` 才生效（AI 还涉及 `frontend`）。
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
