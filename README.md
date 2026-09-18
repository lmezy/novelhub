# NovelHub

Personal Novel Digital Library - self-hosted on NAS (UGREEN DX4600 Pro)

NovelHub 是一个自托管的个人小说数字资产平台：导入阅读（YueDu / Legado）书源，抓取书籍和章节，管理个人书架，并提供网页阅读、全文搜索、AI 辅助阅读等功能。

## Quick Start

```bash
cp .env.example .env
# Edit .env with your passwords
docker compose up -d --build
```

启动后访问 `http://localhost:8088`。

首次启动会自动创建超级管理员账号：

- 账号：`admin@novelhub.local`
- 默认密码：见 `backend/app/main.py`

请登录后立即在“个人设置”中修改密码。

## 使用说明

### 1. 导入阅读书源

NovelHub 内置阅读（Legado / YueDu）书源导入引擎，可以直接使用书源仓库中的链接或 JSON：

1. 打开书源仓库，例如 <https://www.yckceo.com/yuedu/shuyuan/index.html>；
2. 进入“设置 -> 阅读书源导入”；
3. 粘贴书源 URL，或直接粘贴书源 JSON；
4. 选择书源范围并导入。

“高级 JSON”框只用于粘贴书源 JSON。如果误把浏览器 Cookie 文本粘进去，系统会检测不到有效书源，并自动回退到上方填写的 URL 重新抓取。

导入成功后，书源会出现在“设置 -> 书源”列表中，可以继续配置 Cookie 和账号密码（见下文）。

`yuedu/` 目录保留了开源阅读（Legado）源码，用于规则格式兼容和本地阅读器参考，构建与使用说明见 `yuedu/README.md`。

### 2. 个人书源

个人书源只负责导入书源，不会立即同步。

导入后请前往“同步”页面：

- 勾选要处理的书源；
- 点击“导入书籍”：从书源发现/分类页面抓取书籍；
- 点击“导入个人书架”：同步当前书源的个人书架，并自动加入自己的书架。

个人书源同步出来的书籍、书架、进度和搜索记录默认只有本人和管理员可见。

个人书籍可以在书籍详情页选择“公开为全年龄书籍”：

- 公开前必须确认该书为全年龄内容；
- 确认后会标记 `all-ages`，其他用户才能看到；
- 可以随时“取消公开”。

### 3. 全站书源

普通用户选择“全站书源”时，不会直接创建书源，而是提交给管理员审批。

- 管理员在“设置 -> 待审批”中通过后才会创建全站书源；
- 管理员批准后会自动触发全站同步；
- 全站书源会记录提交用户；
- 提交时可以选择“公开我的贡献标签”或隐藏贡献者信息。

全站同步时，如果同一本书同时存在 R18 和非 R18 全站版本：

- 系统会以 R18 为主，把该组全站书籍标记为 R18；
- 同时生成“R18 冲突确认”待审批项；
- 管理员批准则保持 R18，拒绝则恢复冲突前的标记。

### 4. Cookie 与书源账号密码

Cookie 和书源账号密码已经合并到“设置 -> 书源”页面：

1. 先导入书源（见第 1 节）；书源列表为空时，“Cookie / 账号”入口不会显示；
2. 在书源列表中找到对应书源；
3. 点击“Cookie / 账号”展开；
4. 在 Cookie 区域粘贴浏览器 Cookie 并保存、测试；
5. 在账号区域填写书源登录用户名和密码，可用于自动登录刷新 Cookie。

同一个“编辑”表单里还可以给书源单独设置**同步间隔**（秒/请求）：像搬山人这类站点会公布
“一分钟只能拉一次”的拉取间隔，而书源 JSON 里写的 `concurrentRate` 往往比这快得多，被抓到就
直接返回验证码。填 `60` 即每分钟最多 1 次请求；**留空表示使用默认间隔**（书源自带的
`concurrentRate`，没有就 1.2 秒/请求），填 `0` 表示这个源不限速。间隔越大同步越慢——
60 秒/请求时一本 50 章的书约需 50 分钟，请只给确实需要的书源配置。
详见[全站同步](docs/full-site-sync.md)的“限速与并发”。

Cookie 获取方法见 [Cookie 获取指南](docs/cookie-guide.md)。

Cookie 粘贴整段浏览器 Cookie 即可（形如 `key1=value1; key2=value2`），
其中的 URL 编码值（例如 `lf_user_auth=think%3A%7B...%7D`）无需手动解码：
系统会按原文加密保存，并在请求时原样放入 `Cookie` 请求头，由站点自行解码。

点击“测试”会尝试用该 Cookie 抓取对应书源的书架。测试失败通常是以下原因：

- 站点当前不可达或域名已变化（部分书源如爱丽丝书屋使用动态站源分发）；
- Cookie 已过期、失效，或绑定了签发时的 IP / 浏览器指纹；
- 该书源不支持书架抓取，或书架地址规则已过时。

### 5. 个人设置

“设置 -> 个人设置”中支持：

- 阅读字体、字号、语言、主题；
- 昵称；
- 邮箱；
- 修改账号密码。

### 6. 同步任务

“同步”页面支持：

- 选择多个书源；
- 导入书籍或导入个人书架；
- 查看、暂停、恢复、取消同步任务；
- 管理员可配置自动同步时间。

更多说明：

- [文档索引](docs/README.md)
- [全站同步](docs/full-site-sync.md)
- [书源搜索](docs/source-search.md)

### 7. 本地 Markdown 导入

“设置 -> 本地 / 手动 -> 本地 Markdown 导入”：

1. 在服务器上准备书籍目录，章节为 `.md` 文件，可附带 `metadata.json`；
2. 点击“选择目录”浏览已配置的导入根目录，或手动输入容器内可见的路径；
3. 勾选书籍后点击“批量导入所选”，或点击“导入全部”。

容器部署时，服务器上的书籍目录需要挂载到容器内才能被读取。默认会把
`./data/imports` 挂载到 `/imports`。如果书籍在服务器其他目录，修改 `.env`：

```dotenv
LOCAL_IMPORT_VOLUME=/mnt/books
LOCAL_IMPORT_ROOTS=/imports,/app/storage/imports,/library
```

然后重建后端和爬虫容器：

```bash
docker compose up -d --build backend crawler
```

`LOCAL_IMPORT_ROOTS` 中列出的目录会显示在“选择目录”弹窗中，未列出的已挂载目录仍可手动输入路径。

目录格式示例：

```text
storage/imports/
  demo-book/
    metadata.json
    000001.md
    000002.md
```

`metadata.json` 可包含 `title`、`author`、`description`、`status`、`tags`。未提供时自动从目录名、父目录名和正文样本推断。

本地扫描和导入会自动补全简介、状态、标签和分类，并对书名、作者、简介、标签及正文样本做 R18 检测；扫描列表会显示 R18、分类和标签。

也可以通过 API 导入单个目录：

```bash
curl -X POST http://localhost:8088/api/sync/book \
  -H "Content-Type: application/json" \
  -d '{"source_id":"local_markdown","url":"file:///app/storage/imports/demo-book"}'
```

### 8. 手动上传

“设置 -> 本地 / 手动 -> 手动上传”中可以粘贴章节文本，或选择 `.txt / .md` 文件读取。

- 选择文件后会自动识别书名、作者、简介、状态、标签和 R18 结果；
- 也可以粘贴文本后点击“自动识别”；
- 保存时后端会再次执行 R18 校验与自动分类，即使没有预览也会补全书籍信息和标签。

### 9. 阅读

桌面端阅读器支持字号、字体、主题、目录、AI 侧栏和进度保存；
选中文字会浮出 AI 工具条（解释 / 翻译 / 润色 / 续写 / 问 AI）。

手机端会自动进入分页阅读模式：

- 点击屏幕左侧或向右滑动：上一页；
- 点击屏幕右侧或向左滑动：下一页；
- 点击屏幕中间：呼出阅读菜单；
- 章节末尾继续翻页会进入下一章；
- 阅读器内切换章节不会堆积历史记录，返回书籍后可以正常回到书库。

### 10. 搜索与 AI

- 搜索页支持书籍、章节全文搜索；
- 搜索**精确**模式要求整串连续出现，**模糊**模式要求查询里的**每个字都出现**（可乱序、
  可断开）——搜「铃铛」不会再返回只有「铃」或只有「铛」的结果；高级搜索的多条件
  （AND/OR）组合同样按这个口径；
- 阅读器侧栏的 **AI 助手**有四个标签页：**问答 / 摘要 / 人物 / 时间线**；
- 选中正文任意一段会浮出工具条：**解释 / 翻译 / 润色 / 续写 / 问 AI**，结果流式显示，
  翻译目标语言可切换；
- 配置向量模型后可以给整本书建 **RAG 索引**：跨章节提问会先做语义检索，回答里带上章节出处。

#### 配置（设置 → AI，管理员）

不用改环境变量、不用重建容器：

1. 选 **提供方**（OpenAI / DeepSeek / 通义千问 / Kimi / 智谱 / SiliconFlow / Ollama / Claude / 自定义）；
2. 填 **Base URL** 与 **模型**（留空用提供方默认值）；
3. 填 **API Key**（加密入库，页面只显示掩码；本地 Ollama 可以留空）；
4. 国内网络访问 OpenAI / Anthropic 时打开 **使用代理**，可复用爬虫代理
   （设置 → 代理里配置的 mihomo 地址），也可以单独填一个；
5. 点 **测试连接**，会分别探测对话接口与向量接口，并显示实际请求地址、是否走代理、耗时。

字段说明：

| 字段 | 作用 |
|---|---|
| `Temperature` / `最大输出 token` / `超时` | 生成参数 |
| `上下文上限（字符）` | 每次问答送给模型的原文上限 |
| `向量提供方 / 向量模型 / 向量接口地址 / 向量 API Key` | RAG 用的 embedding 服务，可与对话模型不同 |
| `问答时使用语义检索` / `检索片段数` | 是否用 RAG 检索、检索几个片段 |

环境变量仍然可用（`AI_PROVIDER` / `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` /
`AI_EMBEDDING_MODEL` / `AI_USE_PROXY` …），只有在后台没有填写对应字段时才作为兜底。

#### 问答的上下文怎么取

- 阅读器提问时会带上**当前章节**，默认取前后各若干章（`context_chapters`）；
- 如果请求了语义检索（`mode=rag`）或该书已建索引，会优先用 RAG 命中片段，
  并在回答下方标出「第 N 章」出处；
- 没有阅读位置时（例如从别处调用接口）回退到书首若干章，并在回答里说明。

#### RAG 索引

**设置 → AI → RAG 索引管理**里输入书籍 ID 建索引；列表里可以查看每本书的片段数并删除。

```bash
# 建索引（管理员）；force=true 强制重建，max_chunks 限制片段数
curl -X POST "http://localhost:8088/api/rag/index/<BOOK_ID>?force=true" \
  -H "Authorization: Bearer $TOKEN"
# 索引状态（普通用户，需可见该书）
curl "http://localhost:8088/api/rag/status/<BOOK_ID>" -H "Authorization: Bearer $TOKEN"
# 语义检索
curl -X POST "http://localhost:8088/api/rag/search" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"book_id":"<BOOK_ID>","query":"主角第一次见到谁","top_k":6}'
```

章节多的大书建索引会调用很多次向量接口，耗时较长；受 `max_chunks`（默认 2000 片段）保护。

#### 流式接口

- `POST /api/ai/chat/stream`：问答，SSE 事件 `sources` / `delta` / `done` / `error`；
- `POST /api/ai/transform/stream`：划词操作，事件 `start` / `delta` / `done` / `error`。

响应头带 `X-Accel-Buffering: no`，仓库里的 nginx 配置同时关掉了 `/api` 的
`proxy_buffering`，否则网关会把整个流缓冲到结束才吐给浏览器。

#### 同步报错诊断（AI 只提建议，不自动改配置）

同步任务失败时，**同步页 → 选中任务 → 「AI 分析这次报错」**（任务以失败/部分失败结束时
也会自动分析一次，可在 设置 → AI 关掉）。AI 读任务报错、逐书/逐章失败明细、
**该书源的登录状态（有没有 Cookie、Cookie 名字与过期时间——值不会发给模型）**与书源规则，
给出分类（站点侧 / Cookie / 限速 / 代理 / 书源配置 / 源站删书）、判断依据、建议下一步，
以及（**仅在判定为配置问题时**）具体到字段的修改建议。
已经分析过的任务不会自动重跑，改动后再看结论请点「重新分析」。

建议不会自动生效：点「提交为待审批提案」，再到 **设置 → 审批** 看 diff 后批准才写入书源。
判定为站点侧/代理/Cookie/删书时，服务端会强制丢弃规则修改建议 —— 改配置解决不了那些故障。
详细说明与接口见 [docs/ai-assistant.md](docs/ai-assistant.md)。

### 11. 小说与漫画分页

书库把「文字小说」和「图片漫画」分开显示：

- 顶部导航的 **小说**（`/novels`）与 **漫画**（`/comics`）各占一页，**书库**（`/books`）
  是包含两者的全部列表，页头也有「全部 / 小说 / 漫画」切换；
- 个人书架可选「全部 / 小说 / 漫画」筛选，选择会记住；
- 一本书属于哪一类由系统判定：书源自报图片类型（Legado `bookSourceType: 2`）、
  标签/分类含「漫画 / 图集 / 写真」等关键词，或章节正文只有图片没有文字；
- 判定在每次同步时自动更新；导入新书源后如果分类不对，用管理员接口重算：

也可以直接在网页里点：**设置 → 索引 → 小说 / 漫画识别 → 重新识别**：

- 勾选「同时读取章节正文」会逐本读第一章，能识别把自己声明成文本源的漫画书（书多时较慢）；
- 勾选「严格重算」会按规则重新判定，把误判成漫画的书改回小说；默认只把小说升级成漫画，
  不会反向改动。只靠正文识别的漫画，严格重算时请同时勾选读正文。

接口版本：

```bash
curl -X POST "http://localhost:8088/api/books/reclassify?scan_content=true" \
  -H "Authorization: Bearer $TOKEN"
# 只重算一个书源（更快）：
curl -X POST "http://localhost:8088/api/books/reclassify?scan_content=true&source_id=yuedu_xxx" \
  -H "Authorization: Bearer $TOKEN"
# 严格重算（允许把误判的漫画改回小说）：
curl -X POST "http://localhost:8088/api/books/reclassify?scan_content=true&force=true" \
  -H "Authorization: Bearer $TOKEN"
```

`scan_content=true` 会读取每本书的第一章正文，用来识别「自报为文本源、正文其实是图集」
的漫画书；书多时耗时较长，可先不加该参数。

## Services

| Service | Port | Notes |
|---------|------|-------|
| Nginx gateway | 8088 | Unified entry |
| AI (optional) | -- | Configure in 设置 → AI (or `AI_*` env) |
| Backend API | 8000 | FastAPI |
| Frontend | 5173 | Vue 3 + Vite |
| PostgreSQL | 5432 | Database |
| Redis | 6379 | Cache / queue |
| Meilisearch | 7700 | Full-text search |

## 升级后迁移

如果从旧版本升级，需要先重建服务并执行数据库迁移：

```bash
docker compose up -d --build backend crawler scheduler frontend
docker compose exec backend alembic upgrade head
```

## Storage Layout

```text
storage/
  books/
    {author}/
      {title}/
        metadata.json
        000001.md
        000002.md
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2.x, Alembic |
| Database | PostgreSQL 17 |
| Cache/Queue | Redis 7 |
| Search | Meilisearch |
| Tasks | Celery |
| Frontend | Vue 3, TypeScript, Vite, Pinia |
| Crawler | requests, BeautifulSoup4, Playwright |
| Crawler (JS) | Playwright (Chromium headless) |
| Deploy | Docker Compose, Nginx |
