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

候选窗口决定单次搜索的耗时，也是翻页耗时的上限：

- 一般字段（标题/作者/标签/分类/描述/章节名）候选窗口 1000，且候选查询**不取回正文**
  （旧版会把每个候选章节的完整正文拉回来：一次请求 313 MB、约 38 秒）；
- `content` 字段必须把正文取回来打分，所以单独用 300 的窗口（1000 个候选 ≈ 96 MB、
  每次 18–25 秒）。

也就是说单个查询最多给 25 页（`content` 8 页）结果、`total` 以窗口为上限。前端还会把已取回的
搜索页放进 `sessionStorage`，翻页与返回不再重跑查询。

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
