# 阅读路径性能（打开书籍 / 章节）

用户报的现象：**点开一本书或一部漫画要等很久，点进章节后正文/图片也要等很久**。
结论：不是网络慢，是后端有一个 12 秒的接口卡在整条链路上，再加上图片没有任何缓存头。

## 打开一次都要请求什么

书详情页（`BookDetailPage.vue`）：

| 请求 | 线上实测 | 说明 |
|---|---|---|
| `GET /api/books/{id}` | 0.03s | 书籍本身 |
| `GET /api/books/{id}/chapters` | 0.13s（2484 章时 675 KB） | 章节目录 |
| `GET /api/books/{id}/sources` | **10.3–12.3s**（已修） | 「其他书源的同名书」 |
| `GET /api/progress/{id}`、书签、自定义标签、分类… | 各 0.02s 左右 | 次要数据 |

阅读器（`ReaderPage.vue`）会再要 `GET /api/books/{id}/chapters` + `GET /api/chapters/{chapter_id}/content`
（正文，0.03s）+ 每张图一个 `GET /api/chapters/{chapter_id}/images/{filename}`（0.02s/张）。

## 根因 1：`/books/{id}/sources` 把全库读进 Python（已修）

```python
# 旧写法：把 24k 本书（含 tags/categories 的 eager load）全查出来再在 Python 里比标题
select(Book).options(selectinload(Book.author)).where(Book.id != book_id)
...
if _normalize_book_title(b.title) == normalized
```

线上 23,979 本书时**每次调用 10.3–12.3 秒**（独立进程复现：old 12.26s / new 0.127s）。
同一个 helper（`SyncService._find_same_title_books`）还在**每本全站书同步结束时**跑一次，
所以它同时在拖慢同步、把 crawler 的 CPU 打满。

修法：标题归一化下推到 SQL（`app/services/book_title.py` 的 `normalized_title_sql()`），
数据库只返回可能命中的那几行，Python 只做最后确认（它只会**删**行，不会凭空多出行）。

```python
where(normalized_title_sql(Book.title) == normalized)   # ~0.1s
```

两个引擎的差异只有一处：Python 的 `\s` 匹配 `\xa0`（NBSP），Postgres 的 `[[:space:]]` 不匹配。
线上 23,979 条书名里正好有 4 条含 NBSP，所以模式里把所有非 ASCII 空格**逐个列出**，
两边完全一致（已全库比对：**0 处不一致**）。

## 根因 2：次要数据挡在正文前面（已修）

`onMounted` 里是串行 `await`：章节正文排在 `/sources`（10s）后面，书详情页的
`loading=false` 也放在最后 → 整个页面/正文都被一个次要请求卡住。

- 阅读器：先加载章节正文，`loadAlternates()` / `loadBookmarks()` 改成后台跑（它们各自有 try/catch）。
- 书详情页：先渲染书籍 + 章节目录并结束 loading，「继续阅读」的进度、标签、分类、
  其他书源、书签全部移到后台异步块。

## 根因 3：图片没有缓存头，且 Starlette 不返回 304（已修）

漫画图片存储 47,300 张 / 31.5 GB（中位数 408 KB，最大 14.8 MB）。旧行为：

- 响应里**没有 `Cache-Control`**；
- `starlette.responses.FileResponse` 只处理 `Range`，**不处理 `If-None-Match`**，
  所以带条件头的请求照样回整个文件（实测 `If-None-Match` 命中仍返回 200 + 394,658 字节）。

于是每次重看一页 / 来回翻章节都在重新下载整张图。现在：

| 资源 | 响应头 | 理由 |
|---|---|---|
| 章节图片 `/api/chapters/{id}/images/{name}` | `private, max-age=604800, immutable` | 文件名是源 URL 的 hash，且 `save_chapter_image` **从不覆盖**已有文件 → 内容永不变 |
| 封面 `/api/books/{id}/cover` | `private, max-age=300` + 304 | 重新同步会覆盖同名封面文件，所以只能短缓存 |
| 其它 `FileResponse` | 未改 | 见下 |

实现见 `backend/app/api/file_response.py`（`cached_file_response()`），
它给 `FileResponse` 传 `stat_result`，从而在构造时就拿到 etag/last-modified。

## 改动这些地方时别踩回去

1. **不要在 Python 里按标题过滤整库**。任何「同名书 / 同名章节」类查询都要用
   `normalized_title_sql()` 落到 SQL；`tests/test_book_title.py`、
   `tests/test_books.py::test_list_book_sources_calls_unique_before_all`、
   `tests/test_sync_service.py::test_find_same_title_books_filters_normalized_title`
   会检查语句里确实有 `regexp_replace`。
2. **正文/首屏不能被次要请求 await 住**。新增的次要数据要么后台跑，要么放进已有的
   后台块里。
3. **给文件响应加缓存头时想清楚能不能变**：内容寻址（hash 文件名、从不覆盖）才能
   `immutable`；会被覆盖的资源只能用短 `max-age` + 304。

## 超长章节（一章几十万字）怎么读

书库里 153 个章节文件 >300 KB，最大的 1.38 MB（约 46 万汉字）——有些源是「一本书 = 一章」。
正文按 20 万字符分块传输（`GET /api/chapters/{id}/content?offset=&limit=`），
`GET /api/chapters/{id}/content/meta` 先给长度和内容摘要（hash），前端据此决定缓存是否还有效。

以前有三个坑，2026-09-18 修（见 [codex-handoff.md](codex-handoff.md) 第 25 节）：

1. **没有可见入口**：剩余内容只靠「滚到距底部 900px 内」这一个隐形触发点加载；
   单章书的页脚还会在正文只加载了一半时显示「结束」。现在首屏之后**后台自动续传**
   （最多 8 块），另有「继续加载剩余 N 字」按钮与「已加载 X / Y 字」提示，失败可重试。
2. **刷新/切后台就退回第一块**：追加过的内容只存在内存里，重载后 `loadChapter` 从
   offset 0 重新开始（旧代码还从 IndexedDB 直接读回旧块，所以连请求都不发），
   手机上就是「翻到第 318 页，一刷新回到第 1 页」。现在重载会自动续传到完整长度。
3. **分块缓存只按 offset 缓存**：章节被重新同步变长后，旧的 `next_offset: null` 会永久
   卡住整章。现在缓存键里带内容摘要（`chunk:{id}:{hash}:{offset}:{limit}`），
   没有 hash 的老数据用 `len{总长}` 兜底，并且这种情况下 offset=0 强制不走缓存。

另外 `frontend/public/sw.js` 原来对**所有**请求做 cache-first（缓存里只有 `/`、
`/index.html`，却永不更新、没有 activate 清理），浏览器可能一直跑旧 app shell。
现在导航请求网络优先（离线才回退缓存）、`/assets/*` 缓存优先、`/api/*` 完全不拦截。

## 已知仍然慢的地方

- **单张图很大**：47,300 张里 8,465 张 >1 MB（18.8 GB），571 张 >5 MB，最大 14.8 MB。
  一章 20 张图在中等带宽下就是几十秒——这是**上传带宽**问题，改查询没用。
  可选做法：读取时按需缩放/重编码并缓存（需要 Pillow，且要考虑漫画清晰度取舍），
  目前没做。
- `GET /api/books/home` 约 0.85s：首页会并发请求多个书源的 `browse`，属于聚合查询，
  比单本书慢是正常的。
- 章节目录一次返回全部章节（2484 章 = 675 KB）。目前可接受；若出现上万章的源，
  再考虑分页或按需懒加载。
- 后台续传上限 8 块（160 万字符）。更长的章节（目前库里没有）需要点按钮继续，
  不会无限占用带宽。
