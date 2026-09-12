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
| [source-search.md](source-search.md) | 书源搜索 → 入库闭环与 API |
| [r18-access.md](r18-access.md) | R18 书源标记、书籍分级、可见性规则 |
| [crawl-queue.md](crawl-queue.md) | 爬取队列：暂停 / 继续 / 取消 / 置顶 |

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
- 想连服务器看真实任务与日志 → [codex-handoff.md](codex-handoff.md) 第 1、5 节

## 文档维护约定

- **每次线上定位/修复**：在 [codex-handoff.md](codex-handoff.md) **文末追加一节**，
  只写「现象 → 根因 → 改动文件 → 验证」；不要粘长日志、逐条命令输出或历史测试次数。
- **可复用的排查结论**：写进 [full-site-sync.md](full-site-sync.md) 的排错清单
  （面向“用户看到什么报错该怎么办”）。
- **架构或需求变化**：更新 [NovelHub-AI-Development-Context.md](NovelHub-AI-Development-Context.md)，
  并在本索引补入口。
- 正文用中文、Markdown、相对路径链接；命令统一用 `docker compose`。
