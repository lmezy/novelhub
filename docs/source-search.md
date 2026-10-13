# Source Search

Source search completes the P0 loop: import book source -> search novel ->
fetch chapters -> download content -> storage/database/search -> web reader.

## How it works

1. Import a YueDu (Legado) book source through Admin -> Book Source Import.
2. Open the Search page and switch to Book Sources mode.
3. Pick a source, enter a keyword, and search.
4. Results are parsed with the source's `searchUrl` and `ruleSearch`.
5. Each result is checked against the local library (`in_library`).
6. Admin users can click Sync to download the book into NovelHub.

## Searching every source at once

Guessing which of one's sources carries a book does not scale, so the Book
Sources tab defaults to **全部书源**: one keyword goes to every enabled source the
caller may see, concurrently (Legado's behaviour). The page is a Server-Sent
Event stream, so each site's hits appear as soon as that site answers rather than
after the slowest one; sources still running are counted, and sites that returned
nothing or failed are kept in a collapsed list with the reason.

```bash
# aggregated JSON (waits for every source)
curl -X GET "http://localhost:8088/api/sources/search?q=keyword&limit=20" \
  -H "Authorization: Bearer $TOKEN"

# streamed (one event per source, in completion order)
curl -N -X GET "http://localhost:8088/api/sources/search/stream?q=keyword" \
  -H "Authorization: Bearer $TOKEN"
```

Both accept `sources=` (comma-separated ids; the visibility gate still applies, so
naming a source cannot reach one the caller may not use) and `limit=` (results
**per source**, default `SEARCH_FANOUT_RESULTS_PER_SOURCE`, max 100).

Behaviour worth knowing:

- Each source keeps its own `concurrentRate` / per-source 拉取间隔 limiter, so a
  fan-out does not raise the request rate any single site sees.
  `SEARCH_FANOUT_CONCURRENCY` only bounds how many sites are in flight.
- `SEARCH_FANOUT_SOURCE_TIMEOUT_SECONDS` times each source out on its own: one dead
  host cannot hold the stream. A slow source still contributes if it answers
  later, and a failure is reported per source instead of shrinking the result set.
- Each source is queried at **page 1** only. The per-source cap multiplies by the
  number of sources; paging deeper *within* one source is not implemented yet.

### Single source (unchanged)

```bash
curl -X GET "http://localhost:8088/api/sources/yuedu_xxx/search?q=keyword&page=1&limit=30" \
  -H "Authorization: Bearer $TOKEN"
```

Response: `{source_id, source_name, query, page, total, results[]}`，其中每个结果含
`name / author / url / cover_url / intro / kind / latest_chapter / word_count /
in_library / book_id`（`book_id` 非空表示已入库）。

**本地搜索**（`POST /api/search/advanced`）的每条命中额外带 `cover` / `cover_url`
（两者同值，去掉一个前先查前端）：封面来自 `books` 表，**不进搜索索引**——用户自选封面
（`PUT /books/{id}/cover`）与重新同步都只写库，索引副本必然过期。规则与书库一致：
用户自选封面 > 书源封面；远程 URL 原样返回，本地文件走 `/api/books/{id}/cover`；
**没有封面时字段直接不存在**（前端显示标题占位块，不要指向必然 404 的 `<img>`）。
章节命中用 `book_id` 取所属书的封面，所以正文搜索的结果也有书封。

Then sync a remote result:

```bash
curl -X POST "http://localhost:8088/api/sync/book" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_id":"yuedu_xxx","url":"https://example.com/novel/123.html"}'
```

## Library search (novels vs comics)

`/novels`、`/comics` 与书库页共用 `POST /api/search/advanced`。请求体可以带
`"kind": "novel" | "comic"`，只返回该类型的结果（前端在两个页面自动带上）。
`GET /api/search?kind=` 同样支持。

`books.kind` 会写进 Meilisearch 的 `books` 与 `chapters` 两个索引；老索引缺这个字段时，
backend 启动会后台自动回填一次（`POST /api/search/index/kinds` 可手动触发，
管理员「设置 → 索引 → 小说 / 漫画识别 → 重新识别」也会顺带刷新索引）。

## Search performance

搜索的**匹配、过滤**由 Meilisearch 在**全库**上完成，但中文的「整串/逐字」语义引擎给不了
（charabia 把 `铃铛` 切成 `铃`/`铛`，`matchingStrategy: last` 只要求最后一个字命中），所以
**相关性判断与排序在 Python 里做**。曾经代价是「取回多少条候选」有窗口，现在**元数据**已经没有这个窗口了：

| 条件 | 候选窗口 | 说明 |
|---|---|---|
| **单条件元数据（搜索框，最常见）** | **无窗口** | 搜索框对 title/author 发的是 **fuzzy**；`title` / `author` / 章节标题 / 所属书名 / 所属书作者都走索引期 gram + filter（见下），精确与模糊都能到引擎上限 |
| 多条件元数据（高级搜索 AND/OR） | 同上 | 这些字段上的条件同样被 filter 收敛 |
| `description` / `book_description` | books 10000 / chapters 1000 | 长文本不进 gram（token 爆炸） |
| `tags` / `category_names` | books 10000 / chapters 1000 | 精确枚举，窗口足够 |
| **正文 `content`** | **最多 10000** | 按 1000 条分块扫描；正文是唯一真正受限的字段 |
| **同一字段上的多条件 AND** | **最多 10000** | 引擎联合查询（`matchingStrategy: "all"`），只取章节 ID，正文在结果页复核 |

判定的语义（所有条件都走这一套）：

- **精确** = 连续子串必须出现；**模糊** = 查询里的**每个字都要出现**（可乱序、可断开），
  再按「整串 > 最长连续片段 > 引擎排序分」排。搜索「铃铛」只会返回真含「铃铛」的结果。
- **同一字段上的多条件 AND**（如正文「铃」+ 正文「仙」）不是「每个条件各取一段候选再求交集」——
  正文「铃」和「仙」各命中近万章，两个前 300 名几乎不重叠，那样的交集恒为空。改成把各条件值
  空格连接后交给引擎（`matchingStrategy: "all"`）缩小候选集。CJK 引擎即使收到带引号的短语也可能
  返回字符散落的正文，因此精确 AND 还要复核实际正文；完整且不超过 200 条的结果集会先筛除假命中并
  校正总数，较大的集合逐页复核。线上实测「铃 仙」= 1 299 章，可一直翻到第 25 页。

### 元数据为什么没有窗口了

引擎的 `pagination.maxTotalHits` 是**硬顶**：`limit=30000` 也只回 `maxTotalHits` 条，所以
窗口再大也没用（实测复现见 [codex-handoff.md](codex-handoff.md) 第 53 节）。做法是给
书名/作者/章节标题/所属书名/所属书作者在**索引期**生成一个 filterable 的 gram 字段
（`services/search_ngrams.py`），查询直接用引擎 filter 表达：

```
精确 铃铛    -> title_grams = "铃铛"
精确 白骨精  -> (title_grams = "白骨") AND (title_grams = "骨精")   # 超集，Python 复核连续子串
精确 的      -> title_grams = "的"                                     # 单字走 1-gram
模糊 铃铛    -> (title_grams = "铃") AND (title_grams = "铛")          # 与模糊判定严格等价
```

- **两种模式都能收敛，且模糊不是近似**：模糊的判定就是「查询里每个字都出现（任意顺序、可断开）」，
  而字段含 1-gram，所以「各字 1-gram 的 AND」与 `_condition_score` 的模糊门**完全等价**；
  精确模式对超过一字的查询是**超集**，仍需 Python 复核连续子串。搜索框对 title/author 发的是模糊，
  只收敛精确模式等于没帮到最常用的那条路。
- **入库后才会启用**：`sync_book_grams()` 在启动时后台回填，**校验过「没有任何文档缺字段」**才切换到
  filter 路径；没回填完就启用会把未回填的书藏起来。字段缺失期间自动走原来的窗口路径，不会搜不到。
- `POST /api/search/index/grams` 是手动回填入口；`GET /indexes/books/settings/pagination`
  可确认 `maxTotalHits`（默认 200000，`MEILI_MAX_TOTAL_HITS` 可覆盖）是否已生效——它是一个索引
  **设置**，不需要重建索引，但在写入积压的实例上可能排队（第 51 节）。
- 章节侧有个**按索引区分的扫描预算**（`SCAN_BUDGET_BOOKS` / `SCAN_BUDGET_CHAPTERS`）：线上
  chapters 索引 666k 文档、只取 id 的 10 000 行窗口要 514-631s（第 51 节），所以章节停在 10 000，
  不像 books 那样放开到天花板。
- **正文搜索仍受引擎上限约束**，且线下实测 10 000 章约 118 MB / 105 s；线上（666k 章 / 94GB 索引 /
  12GB 容器）更慢。真正做到「全库、任意深」的正文检索需要另建一套子串索引，
  见 [codex-handoff.md](codex-handoff.md) 第 51 节。

前端另外把已取回的搜索页放进 `sessionStorage`，翻页与返回不再重跑查询。

结果列表（本地搜索 / 高级搜索 / 书库浏览 / `/search` 页）每页 40 条（`/search` 页 30 条），
都带「跳至 __ 页」输入框可以直接跳页，不必一页页点「下一页」；正文命中的行右侧另有
「书籍」按钮，直接去 `/books/{id}` 看目录与书籍信息（点正文本身仍然进阅读器那一章）。
旧的快照里如果存的是 40 条/页之前的结果，翻页会重新查询一次，属正常。

> 高级搜索的分页缓存按「条件 + offset」分页存，恢复视图时优先取 **URL 里 offset 对应的那一页**。
> 2026-09-19 修：以前只把“最后访问的那一页”存进快照，而路由 offset 一变就会从快照重画，
> 于是点「上一页」页码变了、列表却还是当前页。

## Rule compatibility

The search implementation follows Legado behavior:

- `{{key}}` / `{{page}}` and legacy `searchKey` / `searchPage` placeholders.
- Page arithmetic expressions such as `{{(page-1)*12}}`.
- URL option suffix such as `,{"method":"POST","body":{...}}`.
- POST JSON bodies, extra headers, cookies, retries, and proxy fallback.
- Legado CSS shorthand selectors: `tag.xxx`, `class.xxx`, `id.xxx`,
  `text.xxx`, `children.xxx`, `@` chains, `-`/`+` list prefixes, index
  selectors (`option.0`, `option!0`, `option[-1]`), and `##` regex suffixes.
- `bookUrlPattern` filtering so category/search/navigation links are not
  returned as novels.
- Generic list parsing fallback when the configured `ruleSearch` misses the
  live page markup.

## Verification

1. `docker compose up -d --build`
2. Log in as admin.
3. Admin -> Book Source Import -> paste a source URL or JSON.
4. Search page -> Book Sources -> search a keyword.
5. Sync a result and open it in the reader.
