# NovelHub 文档索引

`docs/` 只放长期有效的内容：能照着做的操作说明 + 会重复出现的故障结论。
历史排错过程不留在文档里（过长会拖慢每次阅读）。

## 项目与架构

| 文档 | 内容 |
|---|---|
| [NovelHub-AI-Development-Context.md](NovelHub-AI-Development-Context.md) | 项目定位、模块职责、规则驱动原则、改动红线 |
| [legado-rule-spec-diff.md](legado-rule-spec-diff.md) | **书源规则引擎 vs Legado 权威实现的差异表**（改规则引擎前先查这里；含 API 缺口与有意偏差的区分） |
| [js-http-request-side.md](js-http-request-side.md) | **方案（未实现）**：让 JS 发起的请求带上书源 `header` / 已导入 Cookie / 限速——改动点、分级风险与决策点 |
| [../README.md](../README.md) | 安装、启动、书源导入、书架、本地导入、阅读器 |
| [../yuedu/README.md](../yuedu/README.md) | 开源阅读（Legado）源码说明与书源规则格式参考 |

## 功能使用

| 文档 | 内容 |
|---|---|
| [cookie-guide.md](cookie-guide.md) | Cookie 获取/导入、哪些站点必须用 Cookie |
| [ai-assistant.md](ai-assistant.md) | AI 配置（设置 → AI）、问答/摘要/人物/时间线、划词、RAG 索引、**同步报错诊断**与排错 |
| [source-search.md](source-search.md) | 书源搜索 → 入库闭环与 API |
| [r18-access.md](r18-access.md) | R18 书源标记、书籍分级、可见性规则 |
| [crawl-queue.md](crawl-queue.md) | 爬取队列：暂停 / 继续 / 取消 / 置顶 |
| [reading-performance.md](reading-performance.md) | 阅读路径性能：打开书籍/章节慢的原因、图片缓存、别踩回去的坑 |

## 同步与排错（最常用）

| 文档 | 内容 |
|---|---|
| [full-site-sync.md](full-site-sync.md) | 全站同步机制、限速并发、**常见同步报错排查** |
| [codex-handoff.md](codex-handoff.md) | 交接记录：环境/红线、历史根因索引、线上排错速查、最新修复 |

### 按现象直接跳转

- 任务报 `书源未返回可同步的书籍` → [full-site-sync.md](full-site-sync.md)
  “任务报…但书源本身正常”
- 任务卡在“运行中” / `MissingGreenlet` → [full-site-sync.md](full-site-sync.md)
  “任务卡在运行中不动”
- 目录只同步一页就“完成” → [full-site-sync.md](full-site-sync.md)
  “目录只同步了一页”
- 一批书失败被判定“连续失败，已中止任务” → [full-site-sync.md](full-site-sync.md)
  “一批书同步失败后报…”
- 站点要求验证码 / 人机验证 / Cloudflare → [cookie-guide.md](cookie-guide.md)
- 章节报 `Chapter returned empty content` → [full-site-sync.md](full-site-sync.md)
  “章节报 Chapter returned empty content”
- 同一书源只有个别书失败、报 `maximum recursion depth exceeded` →
  [full-site-sync.md](full-site-sync.md)
  “同一书源大部分书正常，个别书报…”
- 日志一直刷 `JsRuntime JS error: Cannot read properties of null (reading '0')` →
  [full-site-sync.md](full-site-sync.md) “日志一直刷 JsRuntime JS error…”
- 漫画书章节报 `Chapter returned empty content` → [full-site-sync.md](full-site-sync.md)
  “漫画书章节报 Chapter returned empty content”
- 漫画书每个章节都只有 12 张图片（相册明明更长） → [full-site-sync.md](full-site-sync.md)
  “漫画书不管原本多少页，每个章节都只有 12 张图片”
- 一批书同步失败后报“同步连续失败超过 N 本”，日志里错误文本是空白 →
  [full-site-sync.md](full-site-sync.md) “一批书同步失败后报…”
- 漫画章节的图片打不开、请求返回 401 → [full-site-sync.md](full-site-sync.md)
  “漫画章节显示的是打不开的图片，图片请求返回 401”
- 正常页面被判成限流/反爬、整本书同步中止 → [full-site-sync.md](full-site-sync.md)
  “正常页面被判成“限流/反爬”，整本书同步中止”
- 正常页面被判成验证码页（正文里有“已被限制 / 身份验证 / 无人机”） →
  [full-site-sync.md](full-site-sync.md)
  “正常页面被判成验证码页：小说正文里出现了“已被限制 / 身份验证”这类词”
- 日志刷 `Failed to fetch content image …`、章节缺图 → [full-site-sync.md](full-site-sync.md)
  “日志刷 `Failed to fetch content image …`，章节里图片缺失”
- 日志刷 `Future exception was never retrieved` / `Task exception was never retrieved` →
  [full-site-sync.md](full-site-sync.md) “日志刷 `Future exception was never retrieved`…”
- 没配 Cookie 的书源却提示「书源已配置 Cookie 但仍被站点拦截」→
  [full-site-sync.md](full-site-sync.md) “没配 Cookie 的书源却提示…”
- 同步某本书报 `[Errno 36] File name too long` → [full-site-sync.md](full-site-sync.md)
  “个别书报 [Errno 36] File name too long”
- 想让小说和漫画分页显示 / 漫画书被算成小说 → [codex-handoff.md](codex-handoff.md) 第 16 节
- **小说页/漫画页搜索时两类结果混在一起** → [codex-handoff.md](codex-handoff.md) 第 23 节
  （搜索索引缺 `kind` 字段；首次启动会自动回填）
- **搜索翻页/搜索本身要等几十秒** → [codex-handoff.md](codex-handoff.md) 第 23 节
- **搜索结果只有几十页（想要几百页）/ 为什么别人翻页那么快** →
  [codex-handoff.md](codex-handoff.md) 第 24 节 + 第 28 节「检索到底搜了多少库」
- **一章几十万字只读到一部分 / 后面读不到 / 刷新后回到开头** →
  [codex-handoff.md](codex-handoff.md) 第 25 节 + [reading-performance.md](reading-performance.md)
  “超长章节（一章几十万字）怎么读”
- **crawler 日志大量 `Chapter returned empty content`** → [full-site-sync.md](full-site-sync.md)
  “日志大量 `Chapter returned empty content`（同一批书）”
- **分类页「明明有书」却报 0 本 / 任务报「书源未返回可同步的书籍」** →
  [full-site-sync.md](full-site-sync.md) “分类页明明有书，却报 `returned no books`” +
  [codex-handoff.md](codex-handoff.md) 第 26 节
- **书源 `exploreUrl` 是 `<js>`，报「发现规则是 Legado JS 脚本，当前环境无法执行」** →
  [full-site-sync.md](full-site-sync.md) “书源发现规则是 `<js>` / `@js:` 脚本，同步报无法执行”
  + [codex-handoff.md](codex-handoff.md) 第 26 节
- **搜索/发现结果被过滤成 0 本，只入库一个首页链接；书页报 `no usable metadata`** →
  [codex-handoff.md](codex-handoff.md) 第 26 节
- **AI 分析说「书源未配置 Cookie」，但书源页显示「已保存 Cookie」** →
  [codex-handoff.md](codex-handoff.md) 第 27 节 + [ai-assistant.md](ai-assistant.md) “排错”
- **搜索结果和搜索词没关系（搜「铃铛」出「铃木」）** → [codex-handoff.md](codex-handoff.md) 第 28 节
- **搜索结果只有 300 条 / 正文搜索翻到第 8 页就到头** → [codex-handoff.md](codex-handoff.md) 第 45 节
  + [source-search.md](source-search.md)「Search performance」
- **搜索结果里看不到书封 / 正文搜索点进去回不到书籍页 / 只能一页页点「下一页」** →
  [codex-handoff.md](codex-handoff.md) 第 44 节 + [source-search.md](source-search.md)
- **高级搜索（多条件）怎么填都是 0 条** → [codex-handoff.md](codex-handoff.md) 第 28 节
- **问「检索是不是全库检索」/ 结果为什么会少** → [codex-handoff.md](codex-handoff.md) 第 28 节
  「检索到底搜了多少库」
- AI 侧栏提示未配置 / 回答总从第 1 章说起 / 回答不出字（流式被缓冲）→
  [ai-assistant.md](ai-assistant.md) “排错”
- AI 测试连接报 400 `The supported API model names are …`（模型名不对）→
  [ai-assistant.md](ai-assistant.md) “排错” + [codex-handoff.md](codex-handoff.md) 第 19 节
- 同步失败想让 AI 说明原因 / AI 能不能自动改书源 → [ai-assistant.md](ai-assistant.md)
  “同步报错诊断” + [codex-handoff.md](codex-handoff.md) 第 20 节
- **点开书籍/漫画要等很久、点进章节正文或图片也要等很久** →
  [reading-performance.md](reading-performance.md) + [codex-handoff.md](codex-handoff.md) 第 22 节
- 想连服务器看真实任务与日志 → [codex-handoff.md](codex-handoff.md) 第 1、5 节
- **书源规则被"切碎"/取空（整源 0 本、书页 0 章、`{$.x}` 取不到值）** →
  [codex-handoff.md](codex-handoff.md) 第 33 节 + [legado-rule-spec-diff.md](legado-rule-spec-diff.md)
- **JS 书源拿不到内容（书源 header / Cookie / Referer 不生效）** →
  [js-http-request-side.md](js-http-request-side.md)（现状、分级方案与已修部分）
- **要改书源插件的代码、找不到某个方法在哪个文件** →
  [NovelHub-AI-Development-Context.md](NovelHub-AI-Development-Context.md) §2 +
  [codex-handoff.md](codex-handoff.md) 第 32 节（插件已按职责拆成 18 个模块）
- 时间戳又对不上 / 差 8 小时 → [codex-handoff.md](codex-handoff.md) 第 32 节
  （约定是 `core/clock.py::naive_now()`；`deleted_accounts.deleted_at` 是唯一 aware 列）
- **「一个帖子 = 一本单章书」的书只读到开头（合集帖 1-23 只显示 1-2）** →
  [codex-handoff.md](codex-handoff.md) 第 46 节（正文里的分帖链接会接成后续章节，需重新同步一次）

## 文档维护约定

- **每次线上定位/修复**：在 [codex-handoff.md](codex-handoff.md) **文末追加一节**，
  只写「现象 → 根因 → 改动文件 → 验证」；不要粘长日志、逐条命令输出或历史测试次数。
- **沉淀过的小节要持续压缩**：现象一句、根因要点、改动落点（文件/函数）、一条仍然有效的提醒；
  细节去读代码。2026-09-19 做过一次（143KB → 66KB，1540 → 780 行），别让它再涨回去。
- **可复用的排查结论**：写进 [full-site-sync.md](full-site-sync.md) 的排错清单
  （面向“用户看到什么报错该怎么办”）。
- **架构或需求变化**：更新 [NovelHub-AI-Development-Context.md](NovelHub-AI-Development-Context.md)，
  并在本索引补入口。
- 正文用中文、Markdown、相对路径链接；命令统一用 `docker compose`。
- 提交前确认没把 `.env`、`*.pyc`、`.pytest_cache/` 带进去（`.gitignore` 已覆盖）。
