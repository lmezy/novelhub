# AI 助手（问答 / 摘要 / 人物 / 时间线 / 划词 / RAG / 同步排错）

面向使用与排错。架构与历史根因见 [codex-handoff.md](codex-handoff.md) 第 18、20 节。

## 1. 配置（设置 → AI）

AI 配置存在数据库里（`app_settings`，键名 `ai_*`），改完立即生效，**不需要重建容器**。
环境变量只在后台没有填写对应字段时才作为兜底。

| 字段 | 说明 |
|---|---|
| 启用 AI 功能 | 总开关。OpenAI 兼容且填了 Key 时默认开启 |
| 提供方 | OpenAI / DeepSeek / 通义千问 / Kimi / 智谱 / SiliconFlow / Ollama / Hermes / Claude / 自定义 |
| 模型 | 留空用提供方默认值（如 `deepseek-flash`、`qwen-plus`、`claude-3-5-haiku-latest`）；旁边的 **「拉取模型」** 会调 `{base}/models` 列出服务端真正提供的模型，点一下即填入 |
| 接口地址 | 留空用提供方默认值。OpenAI 兼容端点写到 `/v1` 即可，会自动补 `/chat/completions`；Claude 会自动补 `/v1/messages` |
| API Key | AES-GCM 加密入库，接口只回掩码；本地 Ollama 这类服务可以留空 |
| Temperature / 最大输出 token / 超时 | 生成参数 |
| 上下文上限（字符） | 每次问答拼给模型的原文上限 |
| 使用代理 | 走代理发请求。勾选后留空表示**复用爬虫代理**（设置 → 代理里的地址） |
| 向量提供方 / 向量模型 / 向量接口地址 / 向量 API Key | RAG 用的 embedding 服务，可以与对话模型不同 |
| 问答时使用语义检索 / 检索片段数 | 是否用 RAG、检索几个片段 |

**测试连接**会分别探测对话接口和向量接口，并显示：解析出的请求地址、是否走代理、耗时、
返回的模型名。报错信息会直接说明原因（Key 无效 / 404 模型名或路径错 / 429 限流 / 超时与代理）。

### 常见提供方

| 提供方 | Base URL | 对话模型 | 向量模型 |
|---|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-flash`（另有 `deepseek-v4-pro`） | 无（要 RAG 需另配向量提供方） |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | `text-embedding-v3` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | `embedding-3` |
| SiliconFlow | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` | `BAAI/bge-m3` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | `text-embedding-3-small` |
| Claude | `https://api.anthropic.com` | `claude-3-5-haiku-latest` | 无 |
| Ollama（本地） | `http://localhost:11434/v1` | `qwen2.5:7b` | `nomic-embed-text` |

## 2. 阅读器里的用法

- 侧栏 **AI 助手**：四个标签页 —— 问答 / 摘要 / 人物 / 时间线。
  - 问答是流式的，边生成边显示；回答下方会标出用到的章节（RAG 模式标「语义检索」，
    否则标「当前章节附近」）；
  - 摘要可以选择章节范围、详细/简要、最多章节数；章节很多时会**等距抽样**并在结果里说明；
  - 人物 / 时间线返回结构化卡片；模型没吐 JSON 时会保留原始文本，不会丢结果。
- **选中正文**会浮出工具条：解释 / 翻译 / 润色 / 续写 / 问 AI。翻译可以在结果卡片里切换
  目标语言并重新生成；「问 AI」会把选中的文字带进侧栏提问。

提问的上下文优先取「你当前读到的章节」附近若干章；如果这本书建过 RAG 索引，
会先做语义检索，这样问很早之前的伏笔也能找到原文。

## 3. RAG 索引

**设置 → AI → RAG 索引管理**：输入书籍 ID → 建立索引；下方列出已建索引的书（片段数 / 章节数）
并可删除。已经建过且章节数没变的书会直接跳过，要强制重建加 `force=true`。

```bash
TOKEN=<你的 JWT>
# 建索引（管理员）
curl -X POST "http://localhost:8088/api/rag/index/<BOOK_ID>?force=true" \
  -H "Authorization: Bearer $TOKEN"
# 状态（普通用户，需可见该书）
curl "http://localhost:8088/api/rag/status/<BOOK_ID>" -H "Authorization: Bearer $TOKEN"
# 语义检索
curl -X POST "http://localhost:8088/api/rag/search" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"book_id":"<BOOK_ID>","query":"第一次见到她是在哪里","top_k":6}'
# 已建索引的书
curl "http://localhost:8088/api/rag/index?limit=50" -H "Authorization: Bearer $TOKEN"
```

建索引是同步请求，会反复调用向量接口：一本几百章的书可能要一两分钟
（网关已配 3600s 超时）。片段总数上限 2000（`max_chunks` 可调），超出会截断并在结果里标注。

## 4. 同步报错诊断

失败的时候不用自己啃日志：**同步页 → 选中那个失败的任务 → 「AI 分析这次报错」**。
任务以 `failed` 或 `completed_with_errors` 结束时也会自动分析一次（**设置 → AI → 同步失败后自动
AI 分析**可以关；每个任务最多分析一次，手动「重新分析」才会重跑）。

它会基于这些证据给结论：任务级报错、逐书/逐章失败明细（按错误文本分组带样本）、该书源最近几次
任务、以及**书源规则 JSON 全文**。

输出四块：

| 输出 | 说明 |
|---|---|
| 分类 | 站点侧问题 / Cookie 问题 / 站点限速 / 代理与网络 / 书源配置问题 / 源站已删书 / 无法确定 |
| 结论 + 判断依据 | 一到两句结论，加上从证据到结论的推理 |
| 建议下一步 | 具体动作，例如「稍后重试」「去浏览器过验证后导入 Cookie」「检查代理」 |
| 建议修改书源配置 | 字段路径、当前值、建议值、理由、风险等级；**只有当分类是配置类问题时才有** |

### AI 能改配置吗？—— 只能提，不能改

- AI **从不直接写** `sources.config`。它提出的是补丁提案，必须点「**提交为待审批提案**」，
  然后到 **设置 → 审批** 里逐条看 diff（字段 / 当前值 / 建议值 / 理由 / 风险）后点「批准」，
  才会真正写入书源。「拒绝」则什么都不发生。
- 分类为**站点侧 / 代理 / 源站删书**时，服务端会**强制丢弃**模型给出的规则修改建议
  （即使模型硬要提），因为改规则解决不了这些故障，只会把好源改坏。
- 补丁只能落在 `config.*`（书源规则，深度合并，不会清掉其它规则）和
  `enabled` / `url` / `name` / `plugin_name` / `is_r18` / `sync_interval_seconds`
  这几个字段上；其它键一律忽略。
- 明确不做：自动导入或改写 Cookie、任何绕过验证码 / WAF / 登录限制的操作、自动改解析规则。

> 「站点限速」分类最常见的正确处置**不是**改规则，而是给这个书源配一个「同步间隔」
> （设置 → 书源 → 编辑 → 同步间隔，单位秒/请求；搬山人这类站点按站点说明填 60）。
> AI 也可以直接建议 `sync_interval_seconds` 的值，同样要你批准才生效。见
> [codex-handoff.md](codex-handoff.md) 第 21 节。

### 接口

```bash
TOKEN=<管理员 JWT>
# 分析（force=true 重跑）
curl -X POST "http://localhost:8088/api/ai/diagnose/<TASK_ID>" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"force":true}'
# 读取已存结果（404 = 还没分析过）
curl "http://localhost:8088/api/ai/diagnose/<TASK_ID>" -H "Authorization: Bearer $TOKEN"
# 把建议转成待审批提案
curl -X POST "http://localhost:8088/api/ai/diagnose/<TASK_ID>/propose" \
  -H "Authorization: Bearer $TOKEN"
```

诊断接口都是管理员权限：回答里会引用书源规则原文。

**怎么读结论**：如果分类是站点侧/Cookie/限速/代理，按「建议下一步」处理，不要改规则；
如果分类是「书源配置问题」且建议里带具体字段，先看「当前值」那一栏——如果标了
「当前值与 AI 的说法不一致」，说明模型记错了原文，以你看到的当前值为准。

## 5. 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ai/status` | 是否可用、用哪个模型、不可用原因 |
| POST | `/api/ai/chat` | 一次性问答（带 `sources`） |
| POST | `/api/ai/chat/stream` | 流式问答，SSE：`sources` / `delta` / `done` / `error` |
| POST | `/api/ai/summary` | 章节范围摘要（map-reduce） |
| POST | `/api/ai/person` | 人物识别 |
| POST | `/api/ai/timeline` | 时间线 |
| POST | `/api/ai/transform` | 划词：解释 / 翻译 / 润色 / 续写 / 自定义 |
| POST | `/api/ai/transform/stream` | 同上，流式（`start` / `delta` / `done` / `error`） |
| GET/PUT | `/api/admin/ai` | 读取 / 保存 AI 配置（管理员，不回显 Key） |
| POST | `/api/admin/ai/test` | 连通性测试（管理员） |
| POST | `/api/admin/ai/models` | 拉取服务端可用模型列表（管理员） |
| GET | `/api/ai/diagnose/{task_id}` | 读取某次同步任务的 AI 诊断（管理员） |
| POST | `/api/ai/diagnose/{task_id}` | 分析该任务（管理员，`force` 重跑） |
| POST | `/api/ai/diagnose/{task_id}/propose` | 把建议转成待审批的书源修改提案（管理员） |

问答请求可以带 `chapter_number`（当前章）、`mode`（`auto` / `rag` / `window`）和 `history`
（最近 10 轮，用于追问）。

## 6. 排错

| 现象 | 原因 / 处理 |
|---|---|
| 侧栏显示「AI 还没有配置好」 | 设置 → AI 里启用并填 Key；点「测试连接」看具体原因 |
| HTTP 401 / 403 | Key 不对、没该模型权限，或填的是别家的 Key |
| HTTP 404 | Base URL 路径或模型名不对；对比「测试连接」里显示的请求地址 |
| `无法连接 AI 服务` | 国内直连 OpenAI / Anthropic 需要打开「使用代理」；代理地址填 mihomo 的混合端口 |
| HTTP 400 `The supported API model names are …` | **模型名写错了**。服务端会把自己接受的模型名列出来，前端会把这些名字做成按钮，点一下填进「模型」→ 保存 → 重新测试。也可以点「拉取模型」让服务列出全部可用模型。 |
| HTTP 429 | 限流或余额/配额不足，稍后重试 |
| 回答里说「给出的片段里没有提到」 | 该书没建 RAG 索引，只带了当前章节附近；建索引后再问 |
| RAG 相关报「需要一个支持向量化的服务」 | 向量提供方/模型没配；DeepSeek 不提供 embedding，需另选一个向量提供方 |
| 摘要只覆盖了一部分章节 | 超出「最多章节数」上限，结果里会标注「已等距抽样」 |
| 回答半天不出字 | 网关是否缓冲了 SSE：响应需要 `X-Accel-Buffering: no`，nginx 的 `/api` 需要 `proxy_buffering off`（仓库配置已带） |
| 同步页看不到「AI 分析这次报错」 | 该入口只对管理员显示；任务状态要是 `failed` 或部分失败 |
| 诊断结果里没有「建议修改书源配置」 | 正常：AI 判定为站点侧/代理/Cookie/删书类问题，改配置解决不了（服务端也会强制丢弃这类建议） |
| 诊断报「AI 功能未启用」 | 先去 设置 → AI 配好并「测试连接」通过 |
