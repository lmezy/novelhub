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
- **书源之间互不排队**：`SYNC_WORKER_CONCURRENCY` 默认 `0` = 每个书源一个 worker 并行跑；
  设成正数才限制同时运行的书源数。同一个书源同时只会跑一个任务（不同任务按优先级排队）。
- 并发模型对齐 Legado：`SYNC_THREAD_COUNT`（默认 9，封顶 9）；单任务内书籍并发
  `SYNC_BOOK_CONCURRENCY`（默认 3）、章节并发 `SYNC_CHAPTER_CONCURRENCY`（默认 9）。
- 书源目录里的多个分类默认 4 个并发抓取（`YUEDU_EXPLORE_CONCURRENCY`）。
  **并发数不等于请求频率**：每个书源自己的 `concurrentRate` / `CRAWL_DELAY_MS` 限速器仍然
  逐个放行请求，并发只是把“等上一页”的空闲时间填满，站点看到的请求频率不变。

> 每个书源同时最多用 1 个任务 session + `SYNC_BOOK_CONCURRENCY` 个书 session，而连接池是
> `pool_size=10 + max_overflow=20`。同时跑 12 个书源时会接近上限，此时多出来的取连接请求会
> 排队（不会报错）。如果日志里出现 `PoolTimeout`，把 `SYNC_WORKER_CONCURRENCY` 设成 8 左右即可。
> 另外线上 crawler 容器限制为 0.5 CPU / 1G 内存，同时跑很多书源时会成为瓶颈；
> 想真正并行更多书源，需要在 NAS 的 compose 里放宽 crawler 的 `cpus`/`memory`
> （以及 `YUEDU_PLAYWRIGHT_CONCURRENCY`，每只 Chromium 约 200–300MB）。
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

「最近任务」列表的排序是**正在执行 → 失败 → 其余（按时间倒序）**，由后端
`/api/crawl/tasks` 返回（前端 SyncPage 再按同一规则兜一次），方便一眼看到在跑的和刚失败的。

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

> 2026-09-14 修：httpx 的超时异常（`ConnectTimeout`/`ReadTimeout`/`PoolTimeout`）在
> httpx 0.28 里 `str(exc)` 是空串，旧代码只按错误文本判断瞬态，于是代理抖动被当成
> “书源规则/Cookie 坏了”，满 10 本就把整个任务判失败。现在按异常类型判定瞬态，并且
> 日志里的错误文本至少是异常类名（如 `ConnectTimeout`）而不是空白。
> 如果还看到这条，先看**同一条日志里的错误文本**：真是规则问题时，错误文本会说明是解析
> 失败，而不是空串或超时类名。
>
> 2026-09-15 修：一条代理故障曾经在几毫秒内把**所有**并发同步都打断——重试路径会
> `aclose()` 掉进程内共享的 httpx 客户端，而当时正在用这个客户端的其它书/章节立刻失败
> （日志里是 `ReadError` / `ClosedResourceError` / `pop from an empty deque`）。
> 现在换客户端只是「退休」旧客户端（新请求用新连接池，老请求跑完再关），并且这三类流
> 级错误也算瞬态，不会再凑够 10 本把任务判成「被反爬」。

### 章节报 Chapter returned empty content

现在会区分三种真实原因：

- `该书在源站已被删除或禁用` / `章节在源站已被删除或禁用`：站点提示页（爱丽丝书屋 54334
  这类），重试无用，只能删书或等源站恢复；
- `Upstream server returned a transient 5xx error page`：Cloudflare 520 等瞬态错误，会重试；
- 其余才是真的取不到正文（选择器不匹配、需要 Cookie、正文全在图片里）。

### 站点要求验证码 / 人机验证 / Cloudflare “Just a moment”

> 2026-09-12 起先看错误里的第二句：写「书源已配置 Cookie 但仍被站点拦截」说明 Cookie
> 已经发出去了、页面也确实被 WAF 拦了——Cookie 过期，或与当前出口 IP / User-Agent 不匹配
> （Cloudflare 的 `cf_clearance` 同时绑定这两者）。用与 NovelHub 同一条代理线路的浏览器
> 重新验证后重新导入即可。只有书源没配 Cookie 时才会提示「把 Cookie 导入书源」。

这类页面在服务端无法绕过（代码也不会去绕）。处理办法：在浏览器里（出口 IP 与代理一致）
打开站点通过验证，把 Cookie 导入「设置 → 书源 → Cookie / 账号」，再重新同步。
已知情况：

- 菠萝猫（boluomao.com）：GoEdge 图形验证码；
- SiS文學網（b.sis.la）、御宅屋（yswhub.cc）、第一版主（banzhu…net）：Cloudflare 挑战页；
- 搬山人小说网（banshanren.com）：连 Playwright 浏览器也会被挑战（2026-09-12 实测：
  同一个任务里连续几十章被判拦截），同样需要 Cookie 或换一个能过验证的代理节点；
- 禁忌书屋 cool18：页面里出现“请稍后再试”属正常文案，已不会被误判成拦截。

### 配好 Cookie 还是报“验证码/人机验证”，但浏览器里明明是正常页面

2026-09-12 修：Cloudflare 会给**正常页面**也注入一段 bot-management 脚本
（`/cdn-cgi/challenge-platform/scripts/jsd/main.js`），旧代码把 `challenge-platform`
当成拦截标记，于是每个页面都被判成验证码页——同步直接中止，错误还提示“请导入 Cookie”
（用户看到的就是“Cookie 明明导入了还是报拦截”）。现在只有真正的拦截页标记
（`Just a moment`、`cf_chl_opt`、`chl_page`、`challenge-form`、`turnstile` …）才算拦截。

同一次修复还解决了两类“Cookie 书源同步 0 本书”的原因：

- 书源用 `@js:JSON.stringify({...})` 声明请求头（绅士漫画声明了桌面版 Chrome UA 和
  Referer），旧代码把这段 JS 直接丢给 `json.loads`，必然解析失败、声明的 UA 被丢掉；
  站点于是返回**手机版页面**，书源自己的规则匹配不上。现在 `header` 规则会用 Node 运行时
  求值（带 `baseUrl`），结果按书源缓存，每个请求只是查一次字典。
- `bookList` / `chapterList` 写成 XPath 风格（`//div[@class='xxx']/ul/li`）时，旧代码按 `@`
  切碎规则、又把 `//…` 交给 CSS 解析器，两条路都得到 0 个元素；`discover_books` 还会用
  `/novel/123` 这类路径启发式再过滤一次，把规则本来匹配上的书全部丢掉。现在 `@` 按 Legado
  的括号配对规则切分，XPath 风格选择器翻译成等价 CSS（`li[1]` 等索引仍按 Legado 语义），
  且**书源声明了 `bookUrlPattern` 时才用它过滤**，否则以书源自己的 `bookList` 为准。

### 正常页面被判成“限流/反爬”，整本书同步中止

2026-09-13 修：弱标记（`限流`、`访问异常`、`访问频繁`、`请求频繁`）以前只要在**整页任意位置**
找到确认词就算命中。御宅屋（yswhub.cc）每页侧栏会列出相关小说，其中一本叫《限流情缘一线牵》，
而 Cloudflare 的 bot-management 脚本里必然出现 `challenge`——两者相隔 800 字符，却足以让每个
章节页被判成限流页，任务报“书源已配置 Cookie 但仍被站点拦截”并直接中止。
现在确认词必须出现在标记**前后 120 字符内**（且不会用标记自身确认自己），
`限流情缘一线牵` 这类书名不再误判；真正的 `请求频繁，请稍后继续访问` 仍然会被拦下。

### 正常页面被判成验证码页：小说正文里出现了“已被限制 / 身份验证”这类词

2026-09-16 修：有些词既是“验证码页标记”，也是普通中文里会出现的词，按**整页裸子串**
匹配就会把正文当验证页：

- 風月文學網（h528）《隸孃》正文里有一句「我的手**已被限制**在厚實手套中」，
  而 `已被限制` 原本是强标记 → 浏览器明明拿到了 166KB 的完整正文，仍然报
  `anti-bot/captcha page`，并且每次重试都先抓到这本书，任务永远跑不完。
- 禁忌书屋（cool18）正文里「经过严格的**身份验证**和安检后」+ 同段出现的「无**人机**」，
  被“身份验证 + 人机”判成需要人机验证（`人机` 是 `无人机` 的一部分）。

现在 `已被限制`、`被限制访问`、`请启用JavaScript` 这类会和正文撞车的措辞只在
**前后 90 字符内有确认词**（验证码 / captcha / challenge / 限制访问 …）时才算拦截页，
`人机`、`频繁` 这类短词也换成了完整说法（`人机验证`、`访问过于频繁`）。
真正的验证码页、限流页、Cloudflare 挑战页仍然会被识别。

### 任务报“目录本次未返回任何书籍”，但书源本身正常

每个分类页都返回 0 本＝代理/站点那一轮的静默失败（HTTP 200 但没有列表）。任务会按网络类
错误在 60s/120s 后自动重试；日志里现在的 `Explore kind … returned no books` **会带上页面摘要**
（字节数、标题、正文开头），一眼能看出站点到底回了个什么页面（例如限流提示页）。

### 目录正常，但某些书只同步出 1 章、内容是邮箱链接或乱码

个别书源的 `chapterList` 是“元素规则 + `@js:` 脚本”的组合（绅士漫画：
`//div[@class='gallary_wrap tb']/ul/li[1]@js:…`，脚本用 `java.put` 存图片地址）。旧代码只认
“整条规则就是 JS”，这种组合解析为空，于是回退到通用链接扫描，把页脚里 Cloudflare 的
`/cdn-cgi/l/email-protection`（邮箱保护链接）当成唯一章节。现在会先取元素、再把这个脚本
当作一步执行，脚本只做 `java.put` 时保留原元素。

### 同一书源大部分书正常，个别书报 `maximum recursion depth exceeded`

书源把 `ruleBookInfo.name` 写成 `{{book.name}}`（绅士漫画 wn09.shop 等）：NovelHub 打开书页
时只知道 URL、拿不到 Legado 的 book 对象，旧版模板解析不出来就回退成“返回输入本身”，
也就是把整页 HTML 当规则交给了 CSS 解析器；页面里一旦出现「`|` 之前有不闭合的 `[`/`(`」，
解析器就会原地递归（`maximum recursion depth exceeded`）。所以同一书源里只有少数书失败，
Cookie 与网络其实都正常。

2026-09-12 修：模板不再把整页当规则，解析器也按 Legado 的语义在括号不平衡时报错并降级
（该字段留空、调用方回退到页面标题）。重新同步这些书即可，不需要动 Cookie。

### 日志一直刷 `JsRuntime JS error: Cannot read properties of null (reading '0')`

这是书源自己的 JS 规则在报错，不是网络/Cookie 问题：规则的 `java.getString("//...")` 写的是
XPath，而服务端 JS 环境旧版只支持 CSS，取到空串后 `split(...)[0].match(...)[0]` 就抛错
（同一书源每个列表项一条）。副作用是那些字段（绅士漫画的页数标签等）被丢掉。
2026-09-12 修：`java.getString` 支持 XPath，并且按 Legado 的语义对“当前列表项/页面”求值；
同类修复还包括 `java.get(key)` 读 `java.put` 变量、`java.getWebViewUA()`。重新同步即可，
标签会恢复（如 `137P`）。

### 漫画书章节报 `Chapter returned empty content`

书源的正文规则用的是图片列表（`java.ajax` 抓分页 + `java.put` 缓存），站点改版或规则过期时
会返回空，而通用正文兜底只找文字容器，于是整章判空。2026-09-12 起：没有文字容器时会把
页面里的内容图（自动排除 logo/验证码/导航图标）输出成 markdown 图片，阅读器能直接显示。

### 漫画书不管原本多少页，每个章节都只有 12 张图片

绅士漫画这类相册站书源的 `chapterList` 脚本只把**第 1 个相册索引页**（12 条）写进
`imgInfoList`，而旧代码把这份清单的长度当成了整个相册的页数上限，于是每本都被截到 12 张
（相册实际 90+ 张）。2026-09-14 起：清单只用来判断“这是图片相册”，正文沿站点的
“下一张/下一页”链一直走到相册结束，上限由 `YUEDU_GALLERY_MAX_PAGES`（默认 512）兜底；
正文图片下载上限也从 50 提到 `MAX_CONTENT_IMAGES_PER_CHAPTER`（默认 512）。
同一版修复还纠正了 XPath 规则 `li[1]` 被当成 0-based 索引的问题（现在 `[1]` = 第一个，
与 Legado `AnalyzeRule` 的 XPath 模式一致），相册封面（第 1 张）不再丢失。
已经存成 12 张的章节会在**下一次同步同一本书**时自动重抓（内容只比清单长度多不出图片时判为
过期），不需要逐章点“重同步本章”；一本 90 张的相册约 90 次请求，受书源限速约束，会比较慢。

### 漫画章节显示的是打不开的图片，图片请求返回 401

2026-09-13 修：同步时正文图片会下载到本地，章节里存的是
`/api/chapters/<章节 id>/images/<文件>`。浏览器加载 `<img>` 时**不会**带 `Authorization`
头，而这个接口以前只认 Bearer token，于是每张图都返回 `401`（正文能看、图全是坏的）。
现在登录/注册（以及每次打开应用时的 `/auth/me`）会额外种一个只作用于
`/api/chapters` 的 HttpOnly Cookie，图片接口两种凭据都接受，可见性规则不变。
已同步的书籍不需要重新同步；升级后刷新一次页面即可（应用启动会调 `/auth/me`）。

### 日志刷 `Failed to fetch content image …`，章节里图片缺失

2026-09-16 修：正文图片的下载路径原本只把 `httpx` 的异常当作可重试错误，于是

- 代理抖动让连接在请求中途断开时，抛的是 anyio 的 `ClosedResourceError` 或
  `pop from an empty deque`——两种都不在重试分支里，图片直接丢掉；
- 代理第一次失败后立刻改走**直连**，而这类 CDN（`img.321cdn.com`、`img5.wnimg2.cfd`）
  直连必然失败，等于白花一次连接超时；
- `404` 这种永久失效的地址也会重试 3 次（`img.321cdn.com/img/88.webp` 一天重试 478 次）。

现在图片下载与页面下载用同一套重试策略：换掉连接池里的坏连接后原地重试，`ClosedResourceError`
一类流错误同样重试，`404/410` 直接放弃并在 30 分钟内记住该地址（同一张死图不再重复请求），
图片 CDN 的失败也不再影响书源本身的线路健康度。同步过程中偶发的图片失败大多会自愈；
如果某本书仍有缺图，重新同步该书即可。

### 个别书报 `[Errno 36] File name too long`

2026-09-13 修：书籍目录用「作者/书名」命名，单个路径分量有 255 字节上限，而绅士漫画里
日文+中文的长标题轻松超过 300 字节，于是整本书同步失败（章节一个都写不进去）。
现在路径分量超过 240 字节会按 UTF-8 字符边界截断并追加内容摘要（如 `…~0619cd53`），
同名仍映射到同一目录、不同书名也不会撞车。受影响的书重新同步即可。

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
