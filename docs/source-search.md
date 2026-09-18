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

搜索走两条路，取决于条件的数量：

**单条件（搜索框那种，最常见）** —— 匹配、排序、分页全部交给 Meilisearch：

- 模糊模式：引擎原生 `page`/`hitsPerPage`，`total` 用引擎的 `totalHits`，
  所以页数只受 `maxTotalHits`（10000，即 250 页）限制；线上实测第 250 页 **0.007s**，
  第 1 页 0.2s 左右。章节结果用 `attributesToCrop` 只取匹配附近的正文做摘要，
  不会把整章 10 万字拉回来。
- 精确模式：Meilisearch 对中文只能分词，给不出「整串出现」的语义（实测 `白骨精` 会命中
  `穿成白骨肿么破` 的「破」），所以子串判断仍在 Python 里做。引擎只负责扫描：
  只取 `id` + 被搜字段，10000 条窗口 0.02–0.7s，排好序的结果缓存 120 秒，
  翻页时按 id 取回**本页 40 条**的完整字段。非正文字段最多 250 页；`content` 字段因为
  打分必须取回正文，窗口仍是 300（8 页）。

**多条件（高级搜索的 AND/OR）** —— 仍用候选窗口（1000，`content` 300）：跨字段组合
压不成一条引擎查询，所以页数以窗口为上限。

前端另外把已取回的搜索页放进 `sessionStorage`，翻页与返回不再重跑查询。

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
