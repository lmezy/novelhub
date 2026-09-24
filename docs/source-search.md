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

## API

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

搜索的**匹配、过滤**由 Meilisearch 在**全库**上完成（33.5k 本书 / 116.7k 章），
但中文的「整串/逐字」语义引擎给不了（charabia 把 `铃铛` 切成 `铃`/`铛`，
`matchingStrategy: last` 只要求最后一个字命中），所以**相关性判断与排序在 Python 里做**。
代价是「取回多少条候选」有窗口：

| 条件 | 候选窗口 | 说明 |
|---|---|---|
| 单条件（搜索框，最常见） | 元数据 **10000** / `content` **300 起、随页码增长到 2000** | 精确与模糊都走「扫一次候选 → 打分 → 缓存排名 → 按 id 补水本页」 |
| 多条件（高级搜索 AND/OR） | books 元数据 **10000** / chapters 元数据 1000 / `content` 300 | 跨字段组合压不成一条引擎查询 |
| **同一字段上的多条件 AND** | **1000**（引擎联合查询） | 例如两个 `content` 条件：走 `matchingStrategy: "all"` 的联合查询，见下 |

- **精确** = 连续子串必须出现；**模糊** = 查询里的**每个字都要出现**（可乱序、可断开），
  再按「整串 > 最长连续片段 > 引擎排序分」排。搜索「铃铛」只会返回真含「铃铛」的结果。
- **正文搜索会随页码变深**（2026-09-24 起）：窗口不再是死的 300 条，而是
  「够这一页用」向上取整到 300 的倍数，最多 `CONTENT_WINDOW_MAX=2000`。第 1~8 页
  仍是一次 300 候选的扫描（<1s），第 9 页才扫 600，第 34 页 1500……每次翻页只多付
  一档；超过 2000 的页从最后一档里出（列表会自然变短/为空）。改之前第 9 页根本不存在，
  用户看到的就是「搜索只有 300 条」。宽窗口按 1000 行分块取（Meilisearch 不会一次
  给你 2000 章正文）。
- **同一字段上的多条件 AND**（如正文「铃」+ 正文「仙」）不再「每个条件各取一段候选再求交集」——
  正文「铃」和「仙」各命中近万章，两个前 300 名几乎不重叠，那样的交集恒为空。改成把各条件值
  空格连接后交给引擎（`matchingStrategy: "all"`，只返回每个词都出现的文档），再对**当前页**
  逐条复核（要求每个条件都命中）。线上实测「铃 仙」= 1 299 章，可一直翻到第 25 页。
- 候选窗口之所以不能无限大：`content` 要取回正文打分（线上实测 10000 章 = 118 MB / 105 s）；
  chapters 索引在 10000 条宽度上也要几十秒到几分钟。窗口与页数上限的关系见
  [codex-handoff.md](codex-handoff.md) 第 28 节「检索到底搜了多少库」。
  想让正文搜索真正做到「全库、任意深」应该走**索引期 n-gram + filter 子串匹配**
  （第 28 节一直记为「未做」的方案），不是继续放大窗口。

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
