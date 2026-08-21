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
