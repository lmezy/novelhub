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

- 后端全量测试 **434 passed**：`cd backend && python -m pytest -q`
- Source Engine 闭环已完成并可用：导入书源 → 搜索 → 目录 → 正文 → Storage/DB/搜索 →
  网页阅读。当前工作重心是**同步稳定性与线上排错**，不是新增架构能力。
- 并发模型：**一个书源一个 worker**（`SYNC_WORKER_CONCURRENCY=0` 默认不限），书源之间
  不再排队；同一书源同时只跑一个任务。
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
| 配好 Cookie 仍报“验证码/人机验证”，浏览器里页面正常 | Cloudflare 正常页面也注入 `challenge-platform/scripts/jsd/main.js`，被判成拦截 | 见第 8 节 |
| Cookie 书源同步 0 本书（绅士漫画/wn09.shop） | ①`header` 的 `@js:` 规则没执行，声明 UA 被丢→站点返回手机版页面 ②`bookList` 的 XPath 风格规则解析出 0 元素 ③`discover_books` 又用 `/novel/123` 路径启发式把规则命中的书全部过滤 | 见第 8 节 |
| 全站同步里第 4 个书源排队几小时 | 全局只有 `SYNC_WORKER_CONCURRENCY`(3) 个槽位 | 见第 8 节 |
| 目录只出 1 章、章节是 `/cdn-cgi/l/email-protection` | `chapterList` 是“元素规则 + `@js:` 脚本”，引擎只认整条 JS，回退后被通用链接扫描捡到邮箱保护链接 | 见第 8 节 |
| 同一书源大部分书正常、个别书报 `maximum recursion depth exceeded` | `ruleBookInfo.name` 是 `{{book.name}}`，无 book 上下文时模板回退成整页 HTML，被当成 CSS 规则切分；解析器遇到不闭合括号时原地递归 | 见第 9 节 |
| 日志一直刷 `JsRuntime JS error: Cannot read properties of null (reading '0')` | jsoup shim 的 `java.getString` 只认 CSS，书源的 XPath 规则返回空串；且 shim 把“上一步结果”当成 `java.getString` 的求值内容（Legado 用的是当前列表项/页面） | 见第 10 节 |
| 日志出现 `java.getWebViewUA is not a function` / `Unexpected end of JSON input` | shim 缺 `getWebViewUA()`；`java.get(key)` 被当成 HTTP 请求（Legado 单参数是 `java.put` 的变量存储） | 见第 10 节 |
| 漫画书章节报 `Chapter returned empty content` | 章节规则失效后通用解析只找文字容器，图片型章节没有兜底 | 见第 10 节 |

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
