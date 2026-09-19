# JS 侧 HTTP 的请求上下文：改造方案（C-18 / C-19 / C-20 / C-17）

> 状态：**方案，未实现**。本文件只描述改动与风险，不动代码。
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
