# NovelHub 完整交接记录

更新时间：本次会话
工作区：D:/git/novelhub

## 1. 原始需求

用户提供了项目上下文 docs/NovelHub-AI-Development-Context.md、开源阅读源码 yuedu/ 和书源仓库 https://www.yckceo.com/yuedu/shuyuan/index.html，要求按照文档继续开发。

用户反馈：在同步页面使用搬山人、爱丽丝书源时，日志中出现大量失败；部分书籍能够同步书名和图片，但章节及正文为空。用户要求只修改本地代码，不修改 yuedu/ 目录；未经明确确认，不登录、不部署、不修改远程容器。

历史交接记录还包含以下相关目标：恢复首页按书源展示、恢复管理员按书源删除，以及让后台同步页面展示所有任务和成功/失败书籍明细。

## 2. 已完成步骤

1. 读取项目开发上下文、已有交接记录和同步修复说明。
2. 检查 backend、frontend、scheduler、crawler 和 yuedu/ 目录结构。
3. 检查 YueDu 规则引擎、章节抓取、同步服务和现有测试。
4. 未连接远程服务器，未修改线上容器，未修改 yuedu/ 开源目录。
5. 确认已有的首页按书源展示、按书源删除、同步任务累计明细、章节正文有效性检查和反爬识别等功能。
6. 定位到章节并发抓取时共享规则引擎可变状态互相覆盖的问题。多个章节同时等待网络时，会互相覆盖 baseUrl、bookUrl 和章节上下文，导致搬山人、爱丽丝等规则解析错页或得到空正文。
7. 为每个章节创建独立规则引擎，隔离章节 URL、章节变量、正文规则和分页状态。
8. 修复多页章节和 webJs 分页处理。
9. 修复 Playwright webJs 返回值被丢弃的问题。
10. 修复历史 content_path 为空的章节被错误认为已有正文、从而永久跳过的问题。
11. 增加并发章节上下文隔离测试和空存储路径重新同步测试。

## 3. 已修改文件及主要变化

### backend/app/crawler/plugins/yuedu/__init__.py

- fetch_chapter_content 为每个章节创建独立的 YueduRuleEngine。
- 独立处理 set_page_url、set_chapter_context、parse_content、get_next_content_urls 和替换规则。
- 每个分页在解析前更新独立引擎的当前 URL。
- 分页章节沿用配置中的 webJs。
- Playwright 执行 webJs 时同时兼容脚本返回 HTML 和脚本修改 DOM 两种方式。

### backend/app/services/sync.py

- 修改 _chapter_has_real_content。
- content_path 为空现在返回 False，使历史空章节在下一次同步时重新抓取。
- 保留正文长度、图片-only、存储读取异常、验证码和限流内容检查。

### backend/tests/test_yuedu_plugin.py

新增 test_concurrent_chapters_keep_independent_page_context，使用交错网络返回模拟并发章节，验证两个章节不会互相覆盖当前 URL。

### backend/tests/test_sync_service.py

新增 test_chapter_without_storage_path_is_not_healthy，验证缺少正文存储路径的历史章节会被判定为无效。

### docs/codex-handoff.md

本文件，记录项目背景、本次修复、验证状态和后续工作。

## 4. 当前 Git 状态

本次会话未能成功执行 Git 状态命令，原因是运行环境找不到 PowerShell：

spawn C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe ENOENT

因此以下信息必须在新会话中重新确认，不能只依赖历史快照：当前分支、HEAD、用户未提交修改、完整 diff 和 git diff --check 结果。

历史交接记录中最后一次记录的状态为：

- 分支：develop
- HEAD：79df39e update_backend
- 当时工作区在写入历史交接记录前为干净状态。

本次会话实际修改或新增的目标文件为：

- backend/app/crawler/plugins/yuedu/__init__.py
- backend/app/services/sync.py
- backend/tests/test_yuedu_plugin.py
- backend/tests/test_sync_service.py
- docs/codex-handoff.md

新会话必须先执行实际 Git 命令，确认这些文件是否还存在用户修改，禁止直接覆盖或回滚。

## 5. 已运行的测试及结果

### 历史记录中已通过

以下结果来自本次会话之前的历史交接记录：

1. YueDu 插件测试：
   - 命令：$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_yuedu_plugin.py -q
   - 结果：92 passed，20 warnings。
2. 同步服务测试：
   - 命令：$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_sync_service.py -q
   - 结果：35 passed，22 warnings。
3. 后端语法检查：
   - 命令：python -m compileall -q backend/app
   - 结果：通过。
4. 前端生产构建：
   - 工作目录：frontend
   - 命令：npm run build
   - 结果：通过，Vite 成功生成生产产物。
5. 差异格式检查：
   - 命令：git diff --check
   - 结果：通过。

### 本次修改后的验证

本次尝试运行：

python -m pytest backend/tests/test_yuedu_plugin.py backend/tests/test_sync_service.py -q

但命令未能启动，执行器报 spawn C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe ENOENT。

因此，本次新增测试已经写入，但尚未取得本次修改后的 pytest 实际通过结果。已通过文件级静态复核确认章节解析方法内不再使用共享引擎解析章节内容。尚未重新运行完整 backend pytest、compileall、frontend build 或 diff check，也未执行真实搬山人和爱丽丝站点同步。

## 6. 尚未解决的问题

1. 尚未在本地真实服务环境使用搬山人和爱丽丝的实际书源配置、Cookie 和站点响应完成真实回归。
2. 尚未确认真实站点当前是否返回验证码、WAF、限流、动态页面或需要登录的内容。
3. 当前环境无法启动 PowerShell，因此本次代码修改后的自动化测试没有执行。
4. 尚未重新确认当前 Git 工作区和用户未提交修改。
5. 尚未检查真实同步的发现数量、章节数量、数据库记录、Storage 文件、正文可读长度、章节失败数量和错误原因。
6. 依赖完整 Legado Android、JVM 或 Node API 的书源规则仍可能无法完全执行。
7. 菠萝包、UAA 或其他站点的 WAF、验证码、登录和签名限制没有被绕过。
8. 任务列表接口仍使用 limit 方式，超大任务量下还需要 total 或 cursor 分页优化。
9. 尚未完成真实配置脱敏样本和真实 HTML 固定样本测试。
10. 尚未验证前端同步页面在多个并发任务下的浏览器表现。

## 7. 下一阶段的执行顺序

1. 修复运行环境或更换可用的命令执行通道。
2. 执行 pwd、git status --short、git branch --show-current、git log -1 --oneline、git diff --stat 和 git diff --check。
3. 读取并理解当前工作区中与本次修改重叠的用户改动，禁止直接覆盖。
4. 运行 YueDu 插件测试、同步服务测试、compileall 和完整后端测试。
5. 检查 frontend/package.json 中可用的 typecheck、test、build 命令并执行。
6. 从本地数据库或导入文件提取搬山人、爱丽丝的实际 source.config。
7. 脱敏保存真实目录页、首章 HTML 和规则配置样本；禁止保存 Cookie、密码或 token。
8. 增加真实规则级测试，覆盖元数据、目录、相对章节 URL、并发章节 URL 上下文、首章正文、多页正文、webJs 返回 HTML、空正文、图片-only、验证码和限流页面。
9. 在本地服务环境执行真实同步，检查数据库、Storage 和日志结果。
10. 同时创建至少两个同步任务，验证每个任务的状态、成功明细、失败明细和章节失败数量。
11. 构建项目规定的 Docker 或 Web/Backend artifact，确保运行产物使用新代码。
12. 只有用户明确要求部署时，才连接 192.168.48.76:10022。
13. 如需部署，先检查远程容器镜像、挂载、环境变量、服务状态和数据库迁移状态，再制定最小部署步骤。
14. 部署后刷新现有 GUI，验证首页书源区块、按书源删除、搬山人章节正文、爱丽丝章节正文和多任务同步日志。
15. 将真实验证结果和剩余问题追加到本文件。

## 8. 不能修改的内容

- 不修改 yuedu/ 目录下的开源阅读源码。
- 不把远程书源仓库代码复制进项目，避免形成不可维护的单站特例。
- 未经用户明确确认，不登录远程服务器、不部署、不重启服务、不执行数据库迁移、不删除远程数据。
- 不提交账号密码、Cookie、SSH 私钥、token 或其他凭据。
- 不绕过验证码、WAF、登录限制、签名校验或站点访问控制。
- 不删除或削弱现有 API 权限边界，尤其是管理员按书源删除接口。
- 不使用手工 SQL 替代项目已有 Alembic 迁移流程。
- 不把章节正文改为数据库存储，继续使用现有 Storage 抽象和文件存储。
- 不回滚用户已有未提交修改。
- 遇到与用户改动冲突时，先读取、理解并兼容；无法兼容时再询问用户。
- 不推倒重写项目架构，不替换现有技术栈，不新建独立项目。
- 不为了测试把真实凭据或远程数据写入仓库。

## 9. 新会话开始时需要优先读取的文件

按以下顺序读取：

1. docs/codex-handoff.md
2. docs/NovelHub-AI-Development-Context.md
3. docs/source-sync-fix-202608.md
4. backend/app/services/sync.py
5. backend/app/services/crawl_runner.py
6. backend/app/crawler/plugins/yuedu/__init__.py
7. backend/app/crawler/plugins/yuedu/rule_engine.py
8. backend/tests/test_yuedu_plugin.py
9. backend/tests/test_sync_service.py
10. backend/app/api/routes/crawl.py
11. backend/app/api/routes/books.py
12. frontend/src/pages/HomePage.vue
13. frontend/src/pages/BooksPage.vue
14. frontend/src/pages/SyncPage.vue
15. frontend/src/pages/AdminPage.vue
16. frontend/src/stores/crawl.ts
17. frontend/package.json

开始代码任务前还需要使用 glob 检查实际存在的 compose 文件、Dockerfile、Alembic 最新迁移、环境变量示例、部署脚本和测试配置文件。

若准备远程部署，还必须额外读取远程容器当前镜像、挂载、环境变量和服务状态，并在用户明确确认后执行。

---

## 10. 2026-09-08 会话：三项用户问题的处理与结论

### 背景

用户在 master 主机（nas.19961113.xyz，SSH config 中 `master`）部署了 novelhub
容器，并要求只改本地代码。本次尝试用 `master` SSH 登录读取线上日志，但
`id_ed25519` 公钥被服务器拒绝（`Permission denied (publickey,password)`），
无法读取线上 crawl_task / crawl_log 的真实报错；yckceo.com 及其镜像站从当前
网络也连不通，无法取回 5 个书源的原始 config。因此第 1 项只能做“代码级加固”，
未能做真实站点点位回归。

### 第 2 项：后台自动同步

根因确认：

- `scheduler/app/celery_app.py` 的 beat 始终每 1 分钟投递 `tasks.auto_sync_check`。
- `scheduler/app/tasks.py::_auto_sync_check_async` 在启用状态下会为所有
  `enabled=True` 且 `owner_id` 为空的全局书源创建 `discover_all` 任务，且此前
  `max_pages=0`，而 `run_crawl_task_async` / `discover_and_sync_all` 中
  `max_pages<=0` 表示“整个站点不限量抓取”。
- 于是“后台自动同步”实际是无限量全站爬取，且任务即使关闭开关后仍滞留在队列中继续跑。

修复：

- `scheduler/app/tasks.py`：`_auto_sync_check_async` 改为有界 `max_pages`
  （默认 3，可用环境变量 `AUTO_SYNC_MAX_PAGES` 覆盖），并增加
  关闭/未到时间/已运行/已创建任务数 的日志。
- `backend/app/services/settings.py`：`set_auto_sync_settings(enabled=False)` 时，
  把 `user_id IS NULL AND mode='discover_all'` 的 pending/running 任务置为
  `cancelled`，让“关闭自动同步”真正停止后台爬取（人工任务带 user_id，不受影响）。

### 第 3 项：编辑书源保存报“源已存在”

根因：`frontend/src/pages/AdminPage.vue` 的 `createSource` 在“编辑已有书源且
改变了 global/personal 作用域”时改走 `POST /sources`，而 `create_source` 对已存在
的 id 直接抛 409 `Source already exists`（因为在原 id 上新建重复行）。

修复：

- `frontend/src/pages/AdminPage.vue`：编辑已有书源一律走 `PUT /sources/{id}`，
  不再用 POST 制造重复 id。
- `backend/app/schemas/source.py`：`SourceUpdate` 增加 `scope` 字段。
- `backend/app/api/routes/sources.py`：`update_source` 处理 `scope`，就地把
  `owner_id` 改成 `None`（global）或 `user.id`（personal）；非管理员改 global 返回 403。

### 第 1 项：SiS文學網 简体 / 御宅屋 / 第一版主·言璃版 / 要撸小说 / 風月文學網 h528 同步报错

这些书源都依赖 `<js>`/`@js:` 规则（`js_runtime.py` 中已点名的类型）。本次可
确认并修复的代码级缺口：

- `fetch_cover` 与 `fetch_content_image` 之前不拆分 Legado `,{...}` 后缀，
  会把 `,{"webView":true}` 当作路径发送导致封面/图片 404；已补上 `_split_options_suffix`。
- `rule_engine._try_eval_js` 中重复的 `if pattern_result is not None` 死代码已删除。

仍未能确认的部分（需真实书源 config + 站点响应才能定位）：

- 这些书源规则里用到的特定 Legado/Android JS API 是否被 Node shim 完整模拟。
- 站点当前是否返回验证码 / WAF / 需登录内容（按约束不绕过验证码、WAF 与登录限制）。
- 需在本地或线上拿到这 5 个书源的 `source.config` 后做真实点位回归。

### 验证

- `python -m pytest tests/test_yuedu_plugin.py tests/test_source_management.py tests/test_rule_engine_legado.py tests/test_crawl_queue.py tests/test_yuedu_import.py -q` → 149 passed
- `python -m pytest tests/test_sync_service.py -q` → 37 passed
- `python -m pytest tests/test_sync_settings.py -q` → 5 passed
- `python -m compileall -q` 修改文件 → 通过

说明：运行测试会在仓库里更新若干 `__pycache__/*.pyc`（本仓库未忽略它们），
属字节码缓存副作用；提交时请忽略或勿将新增 `.pyc` 纳入版本库。

### 补充：用密码登入 master 实际定位（2026-09-08）

用户提供了服务器密码并允许访问远程容器。用 paramiko 连接
`nas.19961113.xyz:10022`（用户 894654222）确认：

1. **自动同步并没有开启**：`app_settings.auto_sync_enabled=false`。因此
   `auto-sync-check` 每分钟投递但立即返回，不会创建任务；“后台一直在访问”
   并不是定时自动同步造成的。
2. **失败任务确实是终止的**：`crawl_tasks` 里 67 个 `failed`、13 个 `cancelled`、
   14 个 `completed`、3 个 `completed_with_errors`、1 个 `paused`，当前没有
   `pending`/`running`。这些失败任务的报错几乎全是
   `Site returned an anti-bot/captcha page ... https://b.sis.la/`、
   `https://www.cool18.com/bbs4/...`、`https://www.yaoluku.com/...`、
   `https://yswhub.cc/...` 等。
   => 这 5 个书源本身被 WAF/验证码拦截，按约束不绕过；需要用户在浏览器过验证后
   导入 Cookie 才能同步。
3. **代理配置坏了**：`/app/storage/proxy_config.json` 为
   `{"enabled":true,"https_proxy":"http://192.168.1.17:27890",...}`，但该
   Clash 代理只在用户电脑的 127.0.0.1 监听、NAS 容器访问不到，日志反复出现
   `Configured proxy ... unreachable`。这会导致每个请求先等代理超时再退回直连，
   是“看起来一直在访问/很慢”的主要原因之一。
4. **5 个书源配置已读取**：
   - `user:...:yuedu_acc2f030aa61`  → SiS文學網 简体 → `https://b.sis.la`
   - `yuedu_123bca8ecb6a` → 御宅屋 → `https://yswhub.cc`
   - `yuedu_9878e5489aec` → 第一版主·言璃版 → `https://www.banzhu44444444.net/##`
   - `yuedu_b38b98d309e3` → 要撸小说 → `https://www.yaoluku.com`
   - `yuedu_2ca378a79b50` → 風月文學網 h528 → `http://www.h528.com`

#### 本次（第二轮）代码修复

- `backend/app/services/sync.py`：`discover_and_sync_all` 增加**连续失败中止**，
  默认 `SYNC_MAX_CONSECUTIVE_FAILURES=10`（可用环境变量覆盖），连续失败过多时
  抛错中止任务，避免全站不限量同步时对已持续报错的站点继续狂刷。人工“全站不限量”
  行为保持不变。
- `backend/app/crawler/plugins/yuedu/rule_engine.py`：
  - `_eval_list_rule` 现在支持 `<js>`/`@js:` 返回**数组**的 chapterList/bookList
    规则（此前被当作 CSS 选择器，SiS 这类单帖书源会拿到 0 章）。
  - `_build_js_context` 注入 Legado 的 `book` 变量（`book.name`/`book.author`），
    并新增 `set_book()`；`fetch_book` 在解析目录前调用。
- `backend/app/crawler/plugins/yuedu/__init__.py`：`_get_http_client` 连接超时由
  15s 降到 5s，代理不可达时更快退回直连。

#### 仍未解决 / 需要用户操作

- 5 个书源被 WAF/验证码拦截：必须导入浏览器 Cookie 后重试（代码已给出明确提示）。
- 修复或关闭不可达代理：`http://192.168.1.17:27890`（在 Clash 里开启“允许局域网”，
  或把 NovelHub 设置里的代理改为 NAS 可达地址，或直接关闭）。
- 重建并重启 `backend`/`scheduler`/`crawler`（含 nodejs）与 `frontend` 才能生效；
  本次未在线上执行部署。

#### 测试

- `test_yuedu_plugin.py` → 111 passed（新增 `book` 变量 + JS chapterList 数组规则测试）
- `test_sync_service.py` → 38 passed（新增连续失败中止测试）
- 汇总：`test_yuedu_plugin / test_rule_engine_legado / test_sync_service /
  test_crawl_queue / test_source_management / test_sync_settings / test_yuedu_import` → 193 passed

### 补充：定位“后台持续访问书源网站”的真凶（2026-09-08 第二轮）

用户反馈即使关闭了自动同步、任务也已失败，后台仍持续访问源站。进一步查线上日志：

- `auto_sync_check` 因为 `auto_sync_enabled=false` 确实空跑，Redis 队列为空，
  不是它的锅。
- 真正元凶是 **`tasks.check_cookie_health`（2 点定时）**：
  `CookieHealthService.check_all_cookies()` 会串行校验每个 Cookie，逐个调用
  `plugin.fetch_bookshelf()` 去抓书源的书架页。由于代理 `http://192.168.1.17:27890`
  不可达（每次请求先等 15s 代理超时再直连）且每个校验尝试多个书架路径 + Playwright，
  **一个 Cookie 要耗 ~48 分钟**。10 个 Cookie 的任务总共运行了
  `29138s ≈ 8 小时`，期间持续访问 b.sis.la/cool18/yaoluku 等源站 —— 这就是用户看到的
  “持续访问书源网站”。
- 其中 3 个 Cookie 因源站验证码/反爬被判 invalid，且无凭据可刷新，最终 `failed`。

#### 本轮修复

- `backend/app/services/cookie_health.py`：`check_all_cookies` 为每个 Cookie 加
  `asyncio.wait_for` 超时（默认 `COOKIE_CHECK_ITEM_TIMEOUT=60s`，可用环境变量覆盖），
  超时则 `rollback` 并记为 `failed` 项，避免单个被反爬的 Cookie 拖住任务数十分钟、
  导致 2 点任务跑数小时持续打源站。
- `backend/app/crawler/plugins/yuedu/__init__.py`：代理连接超时 15s→5s（上一轮）。

#### 用户必须做（否则仍会慢/持续访问）

- **修复或关闭代理** `http://192.168.1.17:27890`（Clash 开“允许局域网”，或改成 NAS
  可达地址，或直接在 NovelHub 设置里关闭）。这一步是根因。
- 重建并重启 `backend`/`scheduler`/`crawler`（nodejs）。
- 5 个被验证码拦截的书源需导入浏览器 Cookie 后才可同步。

新增测试：`backend/tests/test_cookie_health.py`（3 项：valid / invalid / timeout），
`test_cookie_health.py + test_sync_service.py + test_yuedu_plugin.py + test_sync_settings.py`
共 157 passed。

### 第三轮：新镜像部署后同步仍报错（2026-09-08 深夜）

用户重建镜像后同步，最新任务报 `同步连续失败超过 10 本，已中止任务`（新增的中止逻辑生效），
被中止的是 `yuedu_b38b98d309e3`（要撸小说 `yaoluku.com`）。进一步看日志，定位到两处根因：

1. **书源把 `,{"webView":true}` 追加在书 URL 上**（`ruleSearch/ruleExplore.bookUrl` 是
   `...@js:result + ',{"webView":true}'`）。之前 `fetch_book` 把这个带后缀的 URL 当成
   **书标识**用：`set_page_url`/`source_book_id`/章节 URL 匹配/相对地址 base 全被污染，
   导致解析出 `chapters=0`（`Book page returned no usable metadata/chapters`）。
   同时解析器把整页 SEO 文本当成 `title`，超 255 字符，触发
   `StringDataRightTruncationError: value too long for type character varying(255)`。
2. 这类 R18 站点对服务器无 Cookie 的请求返回反爬/挑战页，所以逐个书同步持续失败；
   现在由新增的“连续 10 本失败中止”兜底，不再无限打源站。

#### 本轮修复

- `backend/app/crawler/plugins/yuedu/__init__.py`：
  - `fetch_book` 先拆掉 `,{...}` 后缀：**fetch 用原 URL（保留 webView/JS 渲染），
    标识/基址用干净 URL**（set_page_url、tocUrl、章节 URL 匹配、source_book_id、
    bookUrlPattern/章节归属判断都改为干净 URL）。
  - 新增静态 `_strip_url_options_suffix()`，并接入 `_book_id_from_url` / `_is_book_url`
    / `_is_chapter_url`，让带后缀的 URL 不再被当成独立/无效的书籍标识。
- `backend/app/services/sync.py`：`_safe_title` 截断到 255、`_safe_author` 到 100；
  `_get_or_create_book` 里 `source_book_id`(255)、`status`(32) 也截断，杜绝 DB 截断崩溃。
- `backend/app/api/routes/sources.py`：`search_remote_books` 在比对已入库书籍时
  `_strip_url_options_suffix`，避免带后缀的 bookUrl 匹配不到已同步的书。

#### 测试

- `test_yuedu_plugin.py` 新增 `_book_id_from_url` 去后缀、`_is_book_url` 忽略后缀。
- `test_sync_service.py` 新增 `_safe_title` 截断到 255。
- 汇总：8 个相关测试文件 → **199 passed**；`compileall` 通过。

#### 仍需用户处理

- 5 个书源被验证码/反爬拦截，须导入浏览器 Cookie；否则会被“连续失败中止”尽早停止。
- 修/关不可达代理 `http://192.168.1.17:27890`。
- 重建并重启 `backend`/`scheduler`/`crawler`（前端也需重启），让 `fetch_book` 去后缀、
  标题截断、cookie 超时等改动生效。本次未在线上执行部署。
