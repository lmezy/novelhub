# NovelHub 完整交接记录

更新日期：2026-08-19
工作区：D:/git/novelhub
远程环境：用户提供了 192.168.48.76:10022，本轮只修改本地代码，未部署。

## 1. 原始需求

用户提供项目上下文文档、yuedu 开源阅读代码和书源仓库，要求按文档继续开发。本轮问题：

1. 首页/书库原有的按书源显示相关书籍功能不见了。
2. 按书源批量删除书籍的功能不见了。
3. 同步页选择 UAA、搬山人、爱丽丝、菠萝包时，UAA 和菠萝包很快完成但没有同步；搬山人和爱丽丝只同步书名和图片，章节与正文为空。
4. 后台同步页面只能看到单个任务的当前状态，不能看到所有任务的成功书籍和失败书籍。
5. 只能修改本地代码，不修改 yuedu 开源目录，不进行未经确认的远程部署。

参考文件：docs/NovelHub-AI-Development-Context.md、docs/source-sync-fix-202608.md、yuedu/。
书源仓库：https://www.yckceo.com/yuedu/shuyuan/index.html

## 2. 已完成步骤

### 2.1 书源筛选和删除兼容

- BooksPage.vue 同时兼容 URL 查询参数 source 和旧版 source_id。
- 归一化后继续使用 source_id 请求后端，因此已有按书源展示和批量删除接口可以继续使用。
- 未修改现有书源批量删除 API。

### 2.2 空发现结果处理

- 全站同步第一轮没有发现任何书籍时会抛出明确错误，不再返回完成、0 本。
- 依赖 Legado JS 的发现规则会提示当前环境未能执行。
- 其他空结果会提示检查书源规则、Cookie 或站点验证。
- 不支持 discover_books 的插件会直接失败，不再被标记为成功。

### 2.3 同步任务状态和明细

- 有书籍失败或章节失败时，任务状态为 completed_with_errors。
- 每个任务都可展开查看该任务全部书籍明细。
- 明细区分成功、失败、部分失败、已过滤，并显示错误、过滤原因和章节失败数量。
- 当前任务卡片和任务列表统一显示完成但有错误状态。

### 2.4 yuedu 章节和正文解析

- 通用正文提取增加 main、.post-content、.entry-content、.article-content、.read-main、#read-content、.book-content、.text-content、.novel-content 等常见容器。
- 多页章节后续页面和第一页一样，规则失败或返回脚本诊断文本时回退到通用正文提取。
- 保留验证码页面检测，不把验证码页面写入正文。

## 3. 已修改文件及主要变化

| 文件 | 主要变化 |
|---|---|
| backend/app/services/sync.py | 空发现结果报错；无发现能力插件报错；保留成功、失败、过滤和章节失败明细。 |
| backend/app/services/crawl_runner.py | 有书籍或章节失败时把任务标记为 completed_with_errors。 |
| backend/app/crawler/plugins/yuedu/__init__.py | 扩充正文选择器；多页章节继续页增加解析异常和诊断文本回退。未修改 yuedu/ 开源目录。 |
| frontend/src/pages/BooksPage.vue | 书源筛选兼容 source 和 source_id。 |
| frontend/src/pages/SyncPage.vue | 展示所有任务的书籍结果、失败原因、过滤原因和章节失败数量。 |
| docs/codex-handoff.md | 本交接记录。 |

前置阶段的权限、R18、个人书源、Cookie、图片存储、分页阅读器、书源审批、书源批量删除等改动保留，以仓库现有提交和历史交接记录为准。

## 4. 当前 Git 状态

本会话尝试读取工作区状态时，运行环境无法启动 PowerShell，错误为：

spawn C:/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe ENOENT

因此无法可靠取得最新分支、HEAD、暂存区和未跟踪文件列表。可以确认本轮至少修改了第 3 节列出的业务文件和本交接文档，预计存在未提交改动，但未能由命令确认。新会话第一步执行：

- git status --short
- git branch --show-current
- git log -1 --oneline
- git diff --stat

不要根据旧交接文档中的 HEAD 或分支信息推断当前状态。

## 5. 已运行的测试及结果

### 本轮完成

- 代码检索确认 completed_with_errors 已被后端路由、任务执行器和前端状态映射共同支持。
- 代码检索确认书源筛选最终请求使用 source_id。
- 代码检索确认多页章节继续页包含通用正文回退。
- 一次只读代码审查发现的问题已修正。
- 早期审查报告称后端 Python compileall 通过。

### 本轮未能运行

- 前端 typecheck：当前执行环境没有可用的 PowerShell/pnpm。
- 前端构建：未运行。
- 完整 pytest：本轮未重新运行。
- Playwright 和真实浏览器验证：未运行。
- 四个真实书源同步：未运行。
- Docker 构建、数据库迁移和远程容器验证：未运行。

历史交接记录中的测试结果不能替代本轮改动后的完整构建和测试。

## 6. 尚未解决的问题

1. UAA 的发现、目录和正文规则包含复杂的远程 Legado JS、运行时依赖和登录 token；本地引擎仍可能无法执行。本轮修复的是明确失败并展示原因，不是绕过这些依赖。
2. 菠萝包可能返回 GoEdge WAF/验证码页面；没有有效 Cookie 或浏览器验证结果时，服务器端无法保证自动同步成功。
3. 搬山人和爱丽丝的真实目录、正文 HTML 结构未在本地真实请求验证；通用解析已增强但仍需真实样本验证。
4. 尚未构建 frontend/dist，运行中的容器不会自动获得前端改动。
5. 尚未部署远程容器，也未执行 Alembic migration。
6. 当前环境无法读取最新 Git 状态。
7. 大量书籍时任务明细全部展开可能需要分页或进一步折叠性能优化。
8. 尚未新增针对空发现失败、completed_with_errors、多页正文回退的自动化测试。

## 7. 下一阶段执行顺序

1. 新会话读取本文件、项目上下文和本轮业务文件。
2. 执行 git status --short、git diff --stat、git diff --check，确认没有覆盖用户已有改动。
3. 运行 python -m compileall -q backend/app，并运行实际存在的同步服务和任务测试。
4. 安装或确认前端依赖后运行项目实际的 typecheck 和 build 命令。
5. 增加并运行 yuedu 固定 HTML/规则样本测试，覆盖首页回退、多页回退、验证码拦截和空发现失败。
6. 在本地服务环境验证 UAA、搬山人、爱丽丝、菠萝包的发现、目录、正文、Cookie/WAF 结果。
7. 重新构建前端和后端镜像，确认 API 和前端产物包含本轮改动。
8. 用户明确确认后，连接远程服务器执行部署、迁移和浏览器验证。
9. 部署后检查所有同步任务都能看到成功、失败、过滤和章节失败历史。

## 8. 不能修改的内容

- 不修改 yuedu/ 目录下的开源阅读源码。
- 不修改用户未要求的远程服务器、容器、数据库、Cookie 或账号信息。
- 未确认前不执行远程部署、数据库迁移或删除远程数据。
- 不通过手工 SQL 绕过 Alembic 修改数据库结构。
- 不删除已有 API 或改变既有权限边界。
- 不将章节正文改为数据库存储，继续使用项目现有 Storage 文件存储。
- 不用硬编码绕过单个站点的验证码、WAF 或登录限制。
- 不回滚用户已有未提交代码，遇到冲突先读取并兼容。
- 不提交账号密码、Cookie、SSH 私钥或其他凭据。
- 不把远程书源仓库代码复制进项目作为不可维护的站点特例。

## 9. 新会话开始时需要优先读取的文件

按顺序读取：

1. docs/codex-handoff.md
2. docs/NovelHub-AI-Development-Context.md
3. docs/source-sync-fix-202608.md
4. backend/app/services/sync.py
5. backend/app/services/crawl_runner.py
6. backend/app/crawler/plugins/yuedu/__init__.py
7. backend/app/crawler/plugins/yuedu/rule_engine.py
8. frontend/src/pages/BooksPage.vue
9. frontend/src/pages/SyncPage.vue
10. backend/app/api/routes/crawl.py
11. backend/tests/test_sync_service.py
12. frontend/package.json

远程部署前还需读取实际的 docker-compose.yml、环境变量说明和 Alembic 最新迁移文件。
