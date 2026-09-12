# NovelHub 项目上下文与改动红线

## 1. 定位

NovelHub 是**长期运行、自托管、可扩展的个人小说数字资产平台**（不是下载脚本、单站爬虫
或简单阅读器）。目标链路：

```
书源 → 采集 → 本地永久保存 → 元数据管理 → 全文搜索 → 网页阅读 → 阅读记录 → AI/RAG
```

已完成：Docker 环境、Backend(API/用户/书架/进度/搜索/AI)、Frontend(阅读/搜索/管理)、
PostgreSQL、Storage 抽象、Meilisearch、Celery 定时任务、插件框架，
以及 **Source Engine（书源规则引擎）**。当前阶段是**验收、调试、稳定性修复**，
不是新建架构。

## 2. 架构与模块职责

```
Frontend(Vue) → Backend API(FastAPI) → Service → Repository → PostgreSQL
                                    ↘ Meilisearch（全文/向量索引）
Crawler 容器：队列 worker + Celery worker → Source Engine（书源规则）→ 站点
Scheduler 容器：Celery Beat（定时同步、cookie 健康检查）
```

| 模块 | 负责 | 禁止 |
|---|---|---|
| `backend/` | API、用户、阅读进度、收藏、标签、搜索接口、AI 接口 | 不写站点解析与 HTTP 采集逻辑 |
| Source Engine（代码在 `backend/app/crawler/`，容器是 `crawler`） | 加载书源规则、搜索、书籍信息、目录、正文、Cookie/自动登录、请求管理 | 不暴露 Web API、不写 UI 逻辑 |
| `scheduler/` | 定时同步、任务投递、重试、队列 | — |
| `frontend/` | 阅读、搜索、管理界面 | 不写业务逻辑 |
| Storage 抽象 | `save_book/save_chapter/save_cover/read/delete/exists`；当前本地磁盘，未来可接 S3/WebDAV | 业务层不直接 `open()/write()` |

## 3. 规则驱动（核心原则）

书源（YueDu / Legado JSON）是唯一入口：**不要为单个网站写死逻辑**，也不要再加“一个网站
一个 crawler 插件”的实现。规则执行层支持：

- search / explore / bookInfo / toc / content 五段规则，含 `{{key}}`、`{{page}}`、
  算式占位符、`,{...}` URL 选项后缀（webView / method / POST body / headers / charset）；
- Legado CSS 简写（`tag.x`、`class.x`、`id.x`、`text.x`、`children.x`、`@` 链、
  `-`/`+` 列表前缀、索引选择器、`##` 正则后缀、`|` 回退、未加引号的属性选择器）；
- `<js>` / `@js:` 规则（Node 子进程 shim 模拟 `java.*`、`source`、`book` 等 Legado API）。
  依赖完整 Android 运行时的脚本（如 UAA）无法执行，必须给出明确错误而不是静默 0 本。

## 4. 数据与存储原则

- 数据库只存元数据：Book（标题/作者/来源/分类/状态）、Chapter（标题/顺序/URL/Hash）、
  Source（规则）、Reading（进度）。**正文不入库**。
- 正文以 Markdown 落 Storage：`storage/books/{author}/{title}/000001.md` + `metadata.json`。
- 更新必须是增量：拉目录 → 按章节 URL/Hash 比对 → 只下载新增 → 更新索引；
  支持断点恢复与失败重试。
- 结构变更必须走 Alembic 迁移。

## 5. 修改代码时的约束

1. 先读现有代码与 `docs/`，不推倒重写、不替换技术栈、不新建独立项目。
2. 不破坏已有模块与接口，不修改数据库语义，不删除已有 API。
3. 优先复用已有实现（`SyncService`、Source Engine、Storage、Repository）。
4. 保持 Docker 可运行，提供迁移与测试。
5. 不改 `yuedu/`（开源阅读源码，仅作规则格式参考）；不为单站点写死逻辑。
6. 不绕过验证码 / WAF / 登录限制；不提交任何凭据。
7. 每处修复都要能说清「现象 → 根因 → 改动文件 → 验证」，并补可复现的测试。

## 6. 后续可做（都不是阻塞项）

- 失效书籍自动标记（源站已删除的书目前只报明确原因）；
- `crawl_tasks` 列表接口在超大任务量下的 cursor 分页；
- 真实站点样本回归（脱敏后入库，禁止保存 Cookie/密码）；
- AI/RAG 只做消费端，不参与爬取/解析/下载。
