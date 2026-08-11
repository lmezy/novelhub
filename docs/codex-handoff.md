# NovelHub 完整交接记录

> 最后更新：2026-08-11
> 状态：本地代码已完成本轮增强，待提交
> 分支：`develop`
> HEAD：以 git log 为准

## 0. 本轮修改（2026-08-11）

### 0.1 修复 Cookie 保存后仍显示“保存 Cookie”

- 根因：`POST /api/cookies`、`PUT /api/cookies/{id}`、`DELETE /api/cookies/{id}` 只 `flush` 未 `commit`，请求结束后事务回滚，刷新后 Cookie 丢失。
- `backend/app/api/routes/cookies.py`：创建/更新/删除 Cookie 均补充 `commit`。
- `frontend/src/pages/AdminPage.vue`：已有 Cookie 时保存按钮显示“更新 Cookie”并走 PUT，避免重复创建报 409；恢复“导入并同步全部”区域缺失的 Cookie 输入框、发现开关和按钮。

### 0.2 书籍页面支持添加分类

- `frontend/src/pages/BooksPage.vue`：书库页分类筛选旁新增管理员“添加分类”输入（名称 + R18 开关）。
- `frontend/src/pages/BookDetailPage.vue`：编辑模式下的“设置分类”区域新增“添加分类”。
- 后端沿用已有 `POST /api/categories`，无需数据库迁移。

### 0.3 本地导入/手动上传支持选择全年龄

- `backend/app/schemas/sync.py`、`backend/app/schemas/book.py`：`LocalImportRequest`、`LocalScanRequest`、`ManualBookCreate`、`ManualAnalyzeRequest` 增加 `is_r18: bool | None`。
- `backend/app/services/book_enrichment.py`：`enrich_book_metadata` 支持 `is_r18` 覆盖自动检测，分类也按覆盖后的结果计算。
- `backend/app/services/sync.py`：`sync_book` 支持 `is_r18_override`，覆盖后同步写入对应 `is_r18` 与 `r18`/`all-ages` 标签。
- `backend/app/services/local_library.py`、`backend/app/services/manual_import.py` 与对应路由透传覆盖值。
- `frontend/src/pages/AdminPage.vue`：本地导入和手动上传均新增“自动检测 / 全年龄 / R18”下拉，扫描预览、导入、手动保存都会带上选择。

### 0.4 书籍详情增加编辑模式

- `frontend/src/pages/BookDetailPage.vue`：新增“编辑 / 完成编辑”按钮；普通查看时标签、分类、作者可点击跳转搜索，删除按钮不再直接暴露；编辑模式下才显示来源标签删除、自定义标签移除和分类管理。

### 0.5 标签/分类/作者点击跳转搜索

- `frontend/src/pages/BookDetailPage.vue`、`BooksPage.vue`、`HomePage.vue`：作者、标签、分类点击后跳转 `/search?field=...&q=...`。
- `frontend/src/pages/SearchPage.vue`：读取 URL 查询参数自动填充搜索条件并执行；作者/书名用模糊匹配，标签/分类用精确匹配。

### 0.6 测试

- 新增 `backend/tests/test_cookies.py`，验证 Cookie 创建/更新/删除会提交事务。
- `test_book_enrichment.py`、`test_local_library.py`、`test_manual_import.py` 增加全年龄/R18 覆盖测试。
- 完整 `pytest`：237 passed；`npm run typecheck`、`npm run build` 均通过。

### 0.7 书库按书源筛选与批量删除

- `backend/app/api/routes/books.py`：新增 `POST /api/books/batch-delete-by-source`（管理员），按 `source_id` 查出该书源同步的全部书籍并复用 `delete_books` 清理章节、标签、分类、收藏、阅读进度与搜索索引。
- `frontend/src/pages/BooksPage.vue`：书库页新增“书源”筛选下拉；选中书源后显示“删除该书源书籍 (N)”按钮，确认后删除该书源同步的全部书籍；卡片底部同时展示该书来源名称。
- 测试：`test_books.py` 新增按书源删除接口测试（删除数量、书源不存在返回 404）。

## 1. 原始需求

### 1.1 阅读体验

- 手机访问时不能只靠整页上下滑动，需要像阅读（YueDu / Legado）一样分页阅读。
- 手机进入阅读模式：点按左右区域翻页、横滑翻页、中间呼出菜单。
- 修复返回层级问题：书库 -> 书籍 -> 章节阅读 -> 返回书籍 -> 返回书库，不能出现“返回章节阅读”的闭环。

### 1.2 阅读书源导入

- 个人书源只导入，不立即同步；之后到同步页选择“导入书籍”或“导入个人书架”。
- 个人书源导入的书籍和书架只有本人可见。
- 全站书源提交管理员审批，管理员批准后才创建并触发全站同步。
- 全站书源记录提交用户，可选择公开或隐藏贡献标签。
- 阅读书源导入里的账号密码移到个人设置。
- Cookie 页面和账号密码页面合并到书源页面。

### 1.3 R18 与公开策略

- 书源修改是否 R18 后，该书源已经同步的书籍也要一起修改。
- 全站同步中如果同一本书同时存在 R18 和非 R18 版本，以 R18 为主，并提交管理员确认。
- 个人同步书籍默认只有自己可见；除非选择公开。
- 公开之前需要确认是否为全年龄书籍，并进行标记。

### 1.4 文档

- 整理使用说明并更新 `README.md`。
- 当前任务暂停时生成完整交接记录。

## 2. 已完成步骤

1. 实现手机分页阅读模式：
   - 自动进入手机阅读模式；
   - 左侧/右滑上一页，右侧/左滑下一页，中间呼出菜单；
   - 章节末尾自动进入下一章；
   - 阅读器内章节切换使用 `replace`，不堆积历史。
2. 修复返回链路：
   - 阅读器返回书籍；
   - 书籍详情返回书库；
   - 不再出现返回章节阅读的闭环。
3. 改造阅读书源导入：
   - 个人书源只导入；
   - 同步页新增“导入书籍”和“导入个人书架”；
   - 个人书架同步后自动加入自己的书架。
4. 实现全站书源审批：
   - 普通用户提交全站书源时生成 `SourceChange`；
   - 管理员批准后创建全站书源并触发全站同步。
5. 实现贡献者标签：
   - `Source` 增加 `submitter_id`、`show_contributor`；
   - 可选择公开或隐藏贡献者。
6. 合并 Cookie 与书源账号密码到书源页面。
7. 将账号密码、昵称、邮箱入口移入个人设置。
8. 实现个人书籍可见性：
   - `Book` 增加 `owner_id`；
   - 个人书籍默认仅本人和管理员可见；
   - 增加公开为全年龄书籍功能，确认后标记 `all-ages`。
9. 实现 R18 联动：
   - 书源 R18 变化同步更新该书源全部书籍和搜索索引。
10. 实现全站 R18 冲突审批：
    - 同名书籍同时存在 R18/非 R18 全站版本时统一标记 R18；
    - 生成 `confirm_r18` 待审批；
    - 管理员批准保持 R18，拒绝恢复原标记。
11. 调整调度器：
    - 自动同步和每日同步只处理全站书源。
12. 重写 `README.md` 使用说明。
13. 生成 `docs/codex-handoff.md` 交接记录。
14. 本地 Markdown 导入自动补全书籍信息、分类和标签，并从正文样本执行 R18 检测。
15. 手动上传支持自动识别书名、作者、简介、状态、标签和 R18，保存时再次校验并自动分类。
16. 前端本地扫描/直接读取和手动上传结果展示 R18、分类、标签。
17. 修复后端应用加载、迁移 ID 长度和本地测试基线，完整 `pytest` 已跑通。
18. 本地 Markdown 导入支持配置的服务器目录挂载，并提供前端目录浏览/选择。

## 3. 已修改文件及主要变化

### 3.1 数据库迁移

| 文件 | 主要变化 |
|------|----------|
| `backend/alembic/versions/0027_book_owner_source_contributor.py` | `books.owner_id`、`sources.submitter_id`、`sources.show_contributor`，并回填个人书源书籍归属。 |
| `backend/alembic/versions/0028_book_public_all_ages.py` | `books.is_public`、`books.all_ages_confirmed`。 |

### 3.2 后端模型与 Schema

| 文件 | 主要变化 |
|------|----------|
| `backend/app/models/book.py` | 增加 `owner_id`、`is_public`、`all_ages_confirmed`。 |
| `backend/app/models/source.py` | 增加 `submitter_id`、`show_contributor`、提交人关系。 |
| `backend/app/models/source_change.py` | 增加提交人用户名关系。 |
| `backend/app/schemas/book.py` | `BookOut` 增加归属和公开字段。 |
| `backend/app/schemas/source.py` | `SourceOut` 增加贡献者字段。 |
| `backend/app/schemas/source_change.py` | `SourceChangeOut` 增加提交人用户名。 |

### 3.3 后端 API

| 文件 | 主要变化 |
|------|----------|
| `backend/app/api/routes/yuedu.py` | 个人书源只导入；普通用户全站书源生成待审批；全站书源记录贡献者。 |
| `backend/app/api/routes/sources.py` | 普通用户可查看全站书源；个人书源可编辑；隐藏贡献者时对普通用户隐藏用户名。 |
| `backend/app/api/routes/source_changes.py` | 批准全站书源后触发全站同步；支持 R18 冲突确认的批准/拒绝。 |
| `backend/app/api/routes/sync.py` | `/sync/book`、`/sync/bookshelf`、`/sync/discover` 开放给个人书源所有者；本地导入仍限管理员。 |
| `backend/app/api/routes/books.py` | 列表/详情/搜索按归属过滤；新增个人书籍公开与取消公开接口。 |
| `backend/app/api/routes/credentials.py` | 书源账号密码开放给个人书源所有者。 |
| `backend/app/api/routes/manual_login.py` | 手动登录前校验书源归属。 |
| `backend/app/api/routes/progress.py` | 进度按归属和公开状态过滤。 |
| `backend/app/api/routes/search.py` | 搜索结果按归属和公开状态过滤。 |

### 3.4 后端服务

| 文件 | 主要变化 |
|------|----------|
| `backend/app/services/sync.py` | 书籍写入 `owner_id`；个人书架同步自动收藏；全站 R18 冲突检测与确认记录。 |
| `backend/app/services/visibility.py` | 个人书籍默认仅本人可见，已公开书籍对所有人可见。 |
| `scheduler/app/tasks.py` | 自动/每日同步只处理全站书源。 |

### 3.5 前端

| 文件 | 主要变化 |
|------|----------|
| `frontend/src/pages/ReaderPage.vue` | 手机分页阅读、点按/横滑翻页、阅读菜单、章节切换不堆历史。 |
| `frontend/src/pages/BookDetailPage.vue` | 返回回退保护；个人书籍公开/取消公开及全年龄确认。 |
| `frontend/src/pages/AdminPage.vue` | 书源页整合 Cookie/账号；移除独立 Cookie/账号页签；阅读书源导入新流程；待审批支持 R18 冲突确认；账号密码移入个人设置。 |
| `frontend/src/pages/SyncPage.vue` | 新增“导入书籍”和“导入个人书架”，普通用户只选择自己的个人书源。 |
| `frontend/src/router/index.ts` | 修复 `/admin` 路由类型问题。 |
| `frontend/src/assets/main.css` | 手机阅读模式锁定页面滚动。 |
| `frontend/src/stores/books.ts` | `Book` 类型增加归属和公开字段。 |
| `frontend/src/stores/i18n.ts` | 新增相关中文/英文文案。 |

### 3.6 文档

| 文件 | 主要变化 |
|------|----------|
| `README.md` | 按新流程重写使用说明。 |
| `docs/codex-handoff.md` | 本次交接记录。 |

### 3.7 本轮本地增强

| 文件 | 主要变化 |
|------|----------|
| `backend/app/services/book_enrichment.py` | 新增本地/手动导入的元数据提取、标签/分类建议和正文 R18 检测。 |
| `backend/app/services/local_library.py` | 扫描和直接读取返回 `is_r18`、`categories` 及补全后的书籍信息。 |
| `backend/app/services/manual_import.py` | 保存时合并自动识别结果，内容参与 R18 检测并自动分类。 |
| `backend/app/crawler/plugins/local_markdown/__init__.py` | 目录和单文件导入均使用自动补全并返回 `is_r18`。 |
| `backend/app/api/routes/books.py` | 新增 `POST /api/books/manual/analyze`。 |
| `backend/app/api/routes/sync.py` | 新增 `/sync/local/roots` 和 `/sync/local/list` 目录选择接口。 |
| `backend/app/core/config.py` | 新增 `LOCAL_IMPORT_ROOTS`。 |
| `docker-compose.yml` | 将 `${LOCAL_IMPORT_VOLUME:-./data/imports}` 挂载到 `/imports:ro`。 |
| `frontend/src/pages/AdminPage.vue` | 本地扫描/手动上传展示 R18、分类、标签，并支持自动识别。 |
| `docker-compose.yml` | backend/crawler/scheduler 从 `MEILI_MASTER_KEY` 注入 `MEILI_KEY`。 |
| `.env.example` | 新增 `LOCAL_IMPORT_VOLUME` 和 `LOCAL_IMPORT_ROOTS`。 |

## 4. 当前 Git 状态

```text
分支：develop
HEAD：d3927d3
```

最近提交：

```text
4b8cdf9 update_backend   # 本地导入/手动上传自动补全与 R18
d3927d3 update_backend   # README 更新
1ea1d21 update_backend   # 书源/权限/R18/公开/迁移等主要功能
29177fb update_backend   # 手机阅读器与返回修复
```

当前工作区：

```text
本轮业务代码、测试和文档已修改，待提交
```

除本交接文档外，上一阶段业务代码均已提交；本轮本地增强尚未提交。

## 5. 已运行的测试及结果

| 测试 | 结果 |
|------|------|
| `python -m py_compile`（所有改动后端文件） | 通过 |
| `npm run typecheck` | 通过 |
| `npm run build` | 通过 |
| Playwright：桌面返回链路 `history-ok` | 通过 |
| Playwright：手机分页 `tap-next-ok`、`tap-prev-ok`、`swipe-next-ok`、`menu-ok` | 通过 |
| Playwright：书源导入/同步页 `admin-flow-ok`、`sync-flow-ok` | 通过 |
| Playwright：个人书籍公开/取消公开 `publish-flow-ok` | 通过 |
| 完整 `pytest` | 通过，216 passed |
| `npm run typecheck` | 通过 |
| `npm run build` | 通过 |

## 6. 尚未解决的问题

1. 本机 `.env` 仍缺 `MEILI_KEY`；已通过 `docker-compose.yml` 为 backend/crawler/scheduler 注入 `MEILI_MASTER_KEY`，本机直接运行 `pytest` 时仍需临时设置 `MEILI_KEY` 等环境变量。
2. 远程 `192.168.48.76` 上的容器仍是旧构建，未执行 `0027`、`0028` 迁移。
3. 搜索接口按可见性过滤后，`total` 当前按过滤后的当前页结果计算，不等于 Meilisearch 的真实总量。
4. `GET /api/books/{book_id}/cover` 没有用户权限校验，封面 URL 被猜到时仍可访问。
5. R18 冲突审批按书名去重：管理员拒绝后，再次同步同一书名不会自动重新提交确认。
6. 尚未在真实环境验证：全站书源审批、自动全站同步、R18 冲突确认、个人书籍公开后的跨用户可见性。
7. 尚未补充针对新功能的自动化测试。

## 7. 下一阶段的执行顺序

1. 阅读本交接记录和优先文件，确认当前代码状态。
2. 修正 `.env` 或改用容器环境，运行完整后端测试。
3. 在服务器 `/root/2026/novelhub` 执行部署：

```bash
docker compose up -d --build backend crawler scheduler frontend
docker compose exec backend alembic upgrade head
```

4. 验证迁移结果：
   - `books.owner_id`、`is_public`、`all_ages_confirmed`；
   - `sources.submitter_id`、`show_contributor`。
5. 真实环境验证：
   - 普通用户导入个人书源 -> 同步页导入书籍/书架；
   - 普通用户提交全站书源 -> 管理员审批 -> 自动全站同步；
   - 全站同步触发 R18/非 R18 冲突 -> 管理员批准/拒绝；
   - 个人书籍公开为全年龄后，其他用户可见。
6. 评估并修复搜索 `total` 精度。
7. 评估封面接口权限，同时保证 `<img>` 正常加载。
8. 根据验证结果补充自动化测试。
9. 在具备 PostgreSQL/Redis/Meilisearch 的本地或容器环境启动完整服务，验证本地导入和手动上传页面流程。

## 8. 不能修改的内容

- 禁止重写 NovelHub 后端或前端整体架构。
- 禁止修改 `yuedu/` 目录下的开源阅读源码。
- 禁止把章节正文写入数据库，正文必须继续走 Storage 文件存储。
- 禁止删除已有 API 接口，除非用户明确要求。
- 禁止不通过 Alembic migration 直接修改数据库表结构。
- 禁止新增大量重复 Crawler 或为单一网站写死逻辑。
- 禁止在未确认用户意图的情况下回滚已提交代码。
- 禁止在未执行迁移前重启新的后端容器。
- 在用户确认前不进行远程部署或数据库迁移。

## 9. 新会话开始时需要优先读取的文件

按顺序优先阅读：

1. `docs/codex-handoff.md`
2. `README.md`
3. `docs/NovelHub-AI-Development-Context.md`
4. `backend/app/api/routes/yuedu.py`
5. `backend/app/api/routes/source_changes.py`
6. `backend/app/services/sync.py`
7. `backend/app/api/routes/books.py`
8. `frontend/src/pages/AdminPage.vue`
9. `frontend/src/pages/SyncPage.vue`
10. `frontend/src/pages/BookDetailPage.vue`
11. `backend/alembic/versions/0027_book_owner_source_contributor.py`
12. `backend/alembic/versions/0028_book_public_all_ages.py`
