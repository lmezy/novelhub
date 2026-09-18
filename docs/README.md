# NovelHub 文档索引

`docs/` 只放长期有效的内容：能照着做的操作说明 + 会重复出现的故障结论。
历史排错过程不留在文档里（过长会拖慢每次阅读）。

## 项目与架构

| 文档 | 内容 |
|---|---|
| [NovelHub-AI-Development-Context.md](NovelHub-AI-Development-Context.md) | 项目定位、模块职责、规则驱动原则、改动红线 |
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
  [codex-handoff.md](codex-handoff.md) 第 24 节
- **crawler 日志大量 `Chapter returned empty content`** → [full-site-sync.md](full-site-sync.md)
  “日志大量 `Chapter returned empty content`（同一批书）”
- AI 侧栏提示未配置 / 回答总从第 1 章说起 / 回答不出字（流式被缓冲）→
  [ai-assistant.md](ai-assistant.md) “排错”
- AI 测试连接报 400 `The supported API model names are …`（模型名不对）→
  [ai-assistant.md](ai-assistant.md) “排错” + [codex-handoff.md](codex-handoff.md) 第 19 节
- 同步失败想让 AI 说明原因 / AI 能不能自动改书源 → [ai-assistant.md](ai-assistant.md)
  “同步报错诊断” + [codex-handoff.md](codex-handoff.md) 第 20 节
- **点开书籍/漫画要等很久、点进章节正文或图片也要等很久** →
  [reading-performance.md](reading-performance.md) + [codex-handoff.md](codex-handoff.md) 第 22 节
- 想连服务器看真实任务与日志 → [codex-handoff.md](codex-handoff.md) 第 1、5 节

## 文档维护约定

- **每次线上定位/修复**：在 [codex-handoff.md](codex-handoff.md) **文末追加一节**，
  只写「现象 → 根因 → 改动文件 → 验证」；不要粘长日志、逐条命令输出或历史测试次数。
- **可复用的排查结论**：写进 [full-site-sync.md](full-site-sync.md) 的排错清单
  （面向“用户看到什么报错该怎么办”）。
- **架构或需求变化**：更新 [NovelHub-AI-Development-Context.md](NovelHub-AI-Development-Context.md)，
  并在本索引补入口。
- 正文用中文、Markdown、相对路径链接；命令统一用 `docker compose`。
