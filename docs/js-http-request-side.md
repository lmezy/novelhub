# JS 侧 HTTP 的请求上下文：改造方案（C-18 / C-19 / C-20 / C-17）

> 状态：**第 1 级已实现**（`13542b2`）；**第 2 级已完成** —— 注入已求值的 `header`
> （`47f66ca`）与 `java.*` 的 Referer 默认值（`a75158e`）；第 3 级仍是**方案**。
> 本文件保留当时的分析，实施结果在各节标注。

> **实施第 1 级时新发现两处问题**（见 §6），其中 6.1 已补、6.2 已修、6.3 待评估。
> 背景条目见 [legado-rule-spec-diff.md](legado-rule-spec-diff.md) 附录 C 的 C-17～C-20。

## 1. 问题：JS 发起的请求是"裸奔"的

书源里的 `<js>` / `@js:` 脚本调 `java.ajax(url)` / `java.connect(url)` 时，请求走的是
Node 侧 shim 的 `curl`（`jsoup_shim.js` 的 `__nhCurlRaw`），而 shim 手里几乎什么都没有：

| 项 | 现状 | 后果 |
|---|---|---|
| 书源 `header` 规则 | `jsoup_shim.js:844` 会 `merge(__nhSourceConfig.header)`，但 `header` **从未被写入** —— `_build_js_context`（`rule_engine.py:383-398`）只注入 `baseUrl`/`bookUrl`/`sourceUrl`/`url`/`book`/`chapter` | 声明了 UA/Referer 的书源，JS 请求全丢；站点按默认 UA 返回手机版/拒绝 |
| 已导入的 Cookie | `jsoup_shim.js:796` `__nhCookieJar = []`，只有脚本自己调 `setCookie` 才会写入；**没有任何地方把用户导入的 Cookie 灌进去** | Cookie 鉴权的书源，规则用 JS 取页时拿不到内容 |
| `source.*` 属性 | `jsoup_shim.js` 用 `__nhSourceConfig[key]` 取值，而该对象只有 `_build_js_context` 给的键 | `source.bookSourceName` / `bookSourceGroup` / `bookSourceType` / `bookUrlPattern` / `loginUrl` 全返回 `''` |
| 限速 | JS 请求绕过 `CRAWL_DELAY_MS` / 书源 `concurrentRate` / 「同步间隔」——这三者只在 Python 侧的 `transport.py` 生效 | 第 21 节给搬山人配的「同步间隔」在 JS 路径**不成立** |

> 这正是 `docs/codex-handoff.md` 第 8 / 19 节的同一类症状，而当初的修复**只覆盖了 Python 路径**。

## 2. 为什么不能简单"把 config 塞进去"

三个陷阱，决定了改造要分级而不是一把梭：

1. **`header` 规则可能本身是 JS**。`@js:JSON.stringify({...})` 形式的 `header`（绅士漫画就是这样）必须先求值成
   **普通对象**才能 merge；直接注入原始字符串会让 shim 把一段 JS 源码当 header 名。
   而求值 `header` 又需要 JS 运行时 —— **存在递归风险**。
   可行的做法是复用 Python 侧**已经存在**的求值路径（`transport.py` 的
   `_build_headers` / `_parse_header_rule` + `_header_rule_cache`），在进入 JS 之前求好、
   缓存好，只把结果 dict 传进去。

2. **Cookie 有两个来源，不能混**。`_configured_cookie`（用户导入的，`auth.py:19` /
   `bookshelf.py:34` 写入）与站点 `Set-Cookie` 下发的会话 cookie 是两回事 ——
   第 17 节的教训就是把两者混在一起，导致「书源没配 Cookie 却提示 Cookie 已过期」。
   注入时应当**替换**"配置来源"那部分，而保留脚本自己 `setCookie` 的追加语义。

3. **限速没有回传通道**。shim 是一次性 Node 子进程 + 同步协议，没法回调 Python。
   要在 JS 路径限速，只能把 delay 传进 shim、在发起请求前自己等。
   这意味着**在 Node 里阻塞**，代价必须提前讲清楚（见 §4）。

## 3. 分级方案

### 第 1 级：补齐 `source.*` 键（**低风险，建议先做**）

- **改动点**：`rule_engine.py::_build_js_context` 增加
  `bookSourceName` / `bookSourceGroup` / `bookSourceType` / `bookUrlPattern` /
  `loginUrl` / `customOrder` 等键（取自 `self.config`）。
- **为什么低风险**：纯**新增**键。现在这些属性返回 `''`，补上后返回真值 ——
  对"本来就没用到"的书源无影响，对"用到但拿到空串"的书源只会变好。
- **验证**：`java` 里断言 `source.bookSourceName` 不再是空串；现有 792 条测试回归。
- **回滚**：删掉新增键即可。

### 第 2 级：注入已求值的 `header`（**中风险**）

- **改动点**：`_build_js_context` 增加 `header` 键，值为 Python 侧已求值、已缓存的
  header **dict**（复用 `transport.py::_build_headers` 的结果）。
- **风险**：**改变现有行为** —— JS 请求会开始带上书源声明的 UA/Referer。
  对"依赖默认 UA 反而能用"的极少数书源，可能出现行为变化（换 UA 后被站点区别对待）。
  第 8 节的实例说明**不换 UA 就是拿到手机版页面**，所以净收益明确为正。
- **必须先解决**：递归防护 —— 求值 `header` 时不能再次进入同一个 JS 求值入口。
  现有 `_header_rule_cache` 已经是按 `base_url + rule` 记忆化的，可直接复用。
- **验证**：用 `test_java_connect_returns_a_response_with_get_body` 的既有手法
  （stub 掉 `__nhCurlRaw`，检查收到的 headers）断言 UA 已注入；再跑全量回归。
- **回滚**：从 `_build_js_context` 里去掉 `header` 键，行为回到现状。

### 第 3 级：Cookie 注入 + JS 侧限速（**高风险，建议单独一批**）

**3a Cookie 注入**

- **改动点**：shim 增加 `__nhSetConfiguredCookie(value)`（**替换**语义）与
  `__nhSetSessionCookies`（追加语义）两个入口；`_build_js_context` 传
  `_configured_cookie`。发起请求时按"配置 Cookie + 会话 Cookie"拼接。
- **风险**：JS 请求开始带 Cookie —— 这正是它该做的，但会改变现有请求特征。
- **顺带修**：`getCookie(tag)` 目前**无参数**（`jsoup_shim.js:1568`），
  Legado 是 `getCookie(tag[, key])` 查命名存储。要不要支持 `tag` 需先确认书源用法。
- **验证**：stub `__nhCurlRaw` 断请求带上了 Cookie；断言 `java.getCookie()` 非空；
  回归全量。

**3b JS 侧限速**

- **改动点**：`_build_js_context` 传 `requestDelayMs`；`__nhCurlRaw` 在发请求前等待。
- **⚠️ 主要代价（必须知情）**：shim 是**同步**的，等待只能阻塞在 Node 子进程里。
  给搬山人那种配了 60s 间隔的源，**每一次 JS 发起的请求都要在 Node 里阻塞 60s**，
  而 Python 侧 `eval_js_sync` 会一直等它。需要先确认这不会拖住其它并发同步
  （`js_runtime.py` 用独立事件循环线程驱动子进程，但同一次 JS 求值期间是串行的）。
- **替代方案（更保守）**：只做"**至少不并行轰炸**"—— 给 JS 请求串行化 + 一个小的固定
  间隔，而不是完整复刻 `concurrentRate` 语义。这样风险可控，但和 Python 侧仍不完全一致。
- **建议**：先做 3a，观察真实站点反馈，再决定 3b 是否值得。

## 4. 需要你决策的点

| 决策 | 选项 | 我的建议 |
|---|---|---|
| 是否做第 1 级 | 做 / 不做 | **做**：纯新增、零回归风险、直接修掉一批空属性 |
| 是否做第 2 级 | 做 / 不做 | **做**：净收益明确（不换 UA 会拿到手机版页面）；但要接受"JS 请求特征会变" |
| 第 3a Cookie | 做 / 不做 / 先确认 `getCookie(tag)` 用法 | 倾向**做**，但请先确认你的书源里是否有用 `tag` 的写法 |
| 第 3b 限速 | 完整复刻 / 保守串行化 / 不做 | **先不做**。代价（Node 内阻塞 60s）需要实测确认不会拖累并发同步 |
| 顺序 | 逐级提交 / 一次做完 | **逐级**：每级一个提交，便于一旦出现站点行为变化时精确回滚 |

## 5. 无论做哪一级，都必须遵守的既有约束

- 不改 API 契约、不改数据库语义。
- **不把 Cookie 值写进日志或文档**（第 27 节的教训）。
- 每级都要有**能判伪的测试**（撤掉改动必须失败），并跑全量 `pytest`。
- 站点行为可能因"请求特征变了"而变化 —— 上线后需对照 crawler 日志的
  失败率，而不是只看测试是否通过。

## 6. 实施第 1 级时新发现的两处问题（留给第 2 级）

### 6.1 `java.*` 的 HTTP 路径**根本不设 Referer** —— ✅ 已补（`a75158e`）

第 1 级原以为"注入 `bookSourceUrl` 就顺带修好了 JS 请求的 Referer"。实测否定了这个判断：

- `jsoup_shim.js` 的 `if (__nhSourceConfig.bookSourceUrl) headers['Referer'] = …`
  位于 **`Reload(url)`**（Legado 的全局函数，UAA 类书源用）里；
- `java.ajax` / `java.connect` / `java.get` / `java.post` 这些路径
  **完全没有 Referer 逻辑** —— 只有 `java.connect` 走 `__nhSourceHeaders`，
  其余把调用方 headers 原样透传。

**已修**：新增 `__nhFinalHeaders`，在 `__nhCurlRaw` 顶部调用 ——
那是**所有 JS 请求的唯一入口**，比在四个调用点各加一遍更不易漏。
调用方自己传的 Referer 优先。

**更新（2026-09-19）：已实现，且落法与下面两个选项都不同（`47f66ca`）**

原先的两条路都有死结——**在 Python 侧求值 header 再传进来**时：

1. `_build_js_context` **先于** JS 求值运行；若在那里求值 `@js:` 形式的 header，
   会再次进入同一个求值 → **无限递归**。
2. 求值路径是 **async**，而 `_build_js_context` 是 **sync**：要么改 5 个调用点，
   要么接受"首次请求前没有 header"的时序缺口。

**实际落法**：注入**原始规则**，由 **shim 自己在 JS 环境里求值**
（新增 `__nhParseHeaders`）。shim 本来就是 JS 环境，所以：

- 没有嵌套递归（同一个求值上下文里的普通函数调用）；
- 不需要 async→sync 接线；
- 没有时序缺口；
- 改动全部局限在 shim 内。

支持三种形式：纯 JSON、`@js:`、`<js>…</js>`。用 `new Function` 而非裸 `eval`，
以便显式绑定 `java`/`source`/`cookie`/`cache`/`baseUrl`/`book`/`chapter` 等名字 ——
模块级函数看不到引导层在用户闭包里声明的 `var baseUrl`。
求值失败返回 `{}`，坏 header 不会让请求失败。

副作用（正向）：`source.header` 现在返回**原始规则**而非空串，与 Legado 的源字段一致。

### 6.2 source config 会**跨求值泄漏**（与第 9 节的"上下文串味"同型）—— ✅ 已修（`a4be164`）

- Node 子进程是**常驻**的（`js_runtime.py` 的既定设计，为降低延迟）；
- 而 `__nhSetSourceConfig` / `__nhSetVars` 原本是**合并**语义：
  只覆盖传入的键，**不清空旧的**。

后果：上一次求值留下的键会残留到下一次。这在测试里已经真实发生过 ——
既有测试设过 `header`，泄漏进了后来新增的边界测试，导致**全量跑失败而单跑通过**。

**生产侧真正有风险的是 `chapter`**：`_build_js_context` 只在
`if self._chapter_context:` 为真时才注入 `chapter`，所以一个"没有章节上下文"的求值
会**读到上一章的 title/url**。这正是 `docs/codex-handoff.md` 第 9 节的
「上一本书的上下文串味」同一类问题。

**实际修法**（已实施）：新增 `__nhContextKeys`（引导层拥有的上下文键），
两个 setter 在合并前**只清空这些键**。
关键是**不能一刀切** —— `__nhVars` 同时承载上下文变量与 `Put()`/`source.put` 的
持久变量，整体替换会直接破坏 `@put` 持久化。测试同时钉住两件事：
"旧 chapter 读不到"与"Put 变量仍在"。

### 6.3 变量存储有**三处**且互不相通（新发现，未修）

实施 6.2 时发现，`java.*` 与 `source.*` 读写的是**不同的存储**：

| 存储 | 谁在用 |
|---|---|
| `__nhVars` | `source.get`/`source.put`、全局 `Get`/`Put`、**引导层注入的上下文** |
| `__nhCache` | `java.get`/`java.put`（`jsoup_shim.js:1554`、`:1562`） |
| 引擎 `_variables`（Python） | 规则里的 `@put:{k:v}`（`_get_elements_from_root` 之外的字段路径） |

Legado 里这些是**同一个** ruleData 变量存储。所以：

- 规则里 `@put:{token:abc}`，脚本里 `java.get('token')` → **取不到**；
- 脚本里 `java.put('x',1)`，规则里 `@get:{x}` → 也取不到。

这与 `legado-rule-spec-diff.md` 的 B-10（`@put` 只写引擎实例、跨引擎读不到）
是同一族问题的另一面。**修它需要先把三处收敛成一处**，属独立规模，
建议单独评估，不要和请求侧混在一起改。
