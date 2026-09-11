# NovelHub 文档索引

`docs/` 目录收录了 NovelHub 的使用说明、排错笔记和开发交接记录。
按“我要做什么”挑一篇读即可；遇到同步报错先看第 3 组。

## 1. 项目与架构

| 文档 | 内容 | 什么时候看 |
|---|---|---|
| [NovelHub-AI-Development-Context.md](NovelHub-AI-Development-Context.md) | 项目定位、模块职责、Source Engine 架构目标、修改红线 | 想了解“这个项目要做什么/怎么改动” |
| [../README.md](../README.md) | 安装、启动、书源导入、书架、全站书源、本地导入 | 第一次部署或日常使用 |
| [../yuedu/README.md](../yuedu/README.md) | 开源阅读（Legado）源码说明，书源规则格式参考 | 要对照 Legado 规则实现 |

## 2. 功能使用

| 文档 | 内容 |
|---|---|
| [cookie-guide.md](cookie-guide.md) | 各站点 Cookie 获取方法（浏览器 DevTools / 扩展导出） |
| [source-search.md](source-search.md) | 书源搜索 → 入库的完整闭环与 API |
| [r18-access.md](r18-access.md) | R18 书源标记、书籍分级、可见性规则 |
| [crawl-queue.md](crawl-queue.md) | 爬取队列：暂停 / 继续 / 取消 / 置顶，多任务并行 |

## 3. 同步与排错（最常用）

| 文档 | 内容 |
|---|---|
| [full-site-sync.md](full-site-sync.md) | 全站同步机制、限速、`crawl_tasks` 状态、**常见同步报错排查** |
| [source-sync-fix-202608.md](source-sync-fix-202608.md) | 2026-08 书源同步修复说明（按书源浏览、菠萝包 WAF、UAA JS 规则、爱丽丝 DNS 污染） |
| [codex-handoff.md](codex-handoff.md) | 按日期追加的开发交接记录：每次线上定位到的根因、改动文件、验证结果、用户需要做的事 |

### 按现象直接跳转

- 任务一直显示“运行中”/报 `MissingGreenlet: greenlet_spawn has not been called`
  → [full-site-sync.md](full-site-sync.md) “任务报 MissingGreenlet…”，
  最新一次修复见 [codex-handoff.md](codex-handoff.md) 第 19 节
- 目录只同步了一页（几百本）就“完成”（風月文學網 h528 等）
  → [full-site-sync.md](full-site-sync.md) “目录只同步了一页…”，第 19 节
- 站点要求验证码 / 人机验证 / Cloudflare “Just a moment”
  → [cookie-guide.md](cookie-guide.md)；GoEdge、Cloudflare 挑战无法服务端绕过
- 章节报 “Chapter returned empty content” / 书籍被禁用
  → [full-site-sync.md](full-site-sync.md)、[codex-handoff.md](codex-handoff.md) 第 18 节
- 一批书同步失败被判定为“连续失败，已中止任务”
  → [full-site-sync.md](full-site-sync.md) “一批书同步失败…”，第 19 节

## 4. 文档维护约定

- **每次线上定位/修复**：在 [codex-handoff.md](codex-handoff.md) 末尾追加一节，
  格式为“现象 → 根因 → 改动文件 → 验证 → 用户需要做什么”，不要改写历史小节。
- **可复用的排查结论**：沉淀到 [full-site-sync.md](full-site-sync.md) 的排错清单里
  （面向“用户看到什么报错该怎么办”）。
- **架构或需求变化**：更新
  [NovelHub-AI-Development-Context.md](NovelHub-AI-Development-Context.md)，
  并在本索引里补上入口。
- 所有文档使用中文正文、Markdown、相对路径链接，命令统一用 `docker compose`。
