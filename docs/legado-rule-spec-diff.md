# Legado 规则引擎 spec 差异表

以 `yuedu/` 里 Legado 的权威实现为**规范**，逐条对照 NovelHub 的
`backend/app/crawler/plugins/yuedu/{rule_engine.py,jsoup_shim.js,js_runtime.py}`。

| 项 | 说明 |
|---|---|
| 目的 | 把「靠线上报错才发现语义猜错」换成**有据可查的规范对照** |
| 方法 | 纯只读；每条差异必须同时给出参考实现与 NovelHub 两侧的 `文件:行` |
| 判定 | `确认不一致` / `尚未实现` / `已正确` / `无法判定` |
| 许可证 | `yuedu/` 是 **GPL-3.0**：**只作为规范阅读**，不要把它的代码复制进本仓库 |

## 0. 覆盖声明（重要，先读）

本表是**增量交付**，分两批：

| 批次 | 内容 | 状态 |
|---|---|---|
| 第 1 批 | 主代理亲自核实：结构性差异 + 取值语义 + `java.*` API 面覆盖 | ✅ 已完成（本文第 2～6 节） |
| 第 2 批 | 四个对照轴的完整逐条表：A 元素选择/切分、B 规则调度/URL、C `java.*` 语义、D JSONPath/正则/文本 | ✅ 全部并入（附录 A/B/C/D，共 97 条） |

四个轴各自的「未覆盖」清单已在附录文末列出；仍有未覆盖区域的例子：
`AnalyzeRule.kt`（837 行）的调度细节、`AnalyzeUrl.kt`（784 行）的 URL 选项、
`AnalyzeByXPath.kt`（142 行）的 XPath 语义、`##` 正则替换、JSONPath 过滤器语义、
以及 `java.*` 各成员的**语义**正确性。

**口径警告**：第 6 节的 API 表只判断「`jsoup_shim.js` 里是否出现过这个名字」，
**不代表语义正确**。

怎么把这些条目变成测试：NovelHub 已有 `backend/tests/test_rule_engine_legado.py`（约 22 KB），
每条的「建议用例」都可以直接加成该文件里的一个 `pytest` 用例。

---

## 1. 结论摘要

| 判定 | 条数 | 条目 |
|---|---|---|
| 尚未实现 | 1 | M-1（`{$.rule}` JSONPath 内嵌规则） |
| 确认不一致 | 5 | M-4、M-7、M-8、M-9、M-10 |
| 已正确 | 3 | M-2、M-3、M-6 |
| 待评估（架构级） | 1 | M-5（XPath 原生 vs 翻译） |
| 额外发现 | 1 | C-1（`java.*` API 面只覆盖 17/86） |

**最值得先做的三件事**（按收益/成本排序）：

1. **补 `{$.rule}`（M-1）** —— 纯缺失，JSON 书源整类受影响，实现成本低。
2. **补 `java.*` 的纯计算 API（C-1 里的 41 个）** —— 尤其 26 个加密函数（AES/DES/3DES/digest/HMAC）成体系缺失，而这些书源用来做接口签名；不需要 Android，纯实现。
3. **为 M-5（XPath）建差分测试** —— 不是单点 bug，是结构性降级，只有抽样对比才能定位。

**不要急着改的**：M-7 / M-8 / M-9 可能是**有意的改进**而非缺陷（见各条「影响」），需你裁决。

---

### 修复进度（本表条目的处理状态）

附录 A/B/C/D 是**评审时的原始记录**，按「逐字并入、未改写」的原则不再改动。
已处理与待处理的进度记在这里（每次修完一条就更新本节）。

| 条目 | 状态 | 提交 | 验证 |
|---|---|---|---|
| **A-3** `_split_tail` 丢中间片段 | ✅ 已修复 | `2df97cf` | 4 条新测试；撤掉修复 4 条全失败；746 → 750 passed |
| **A-5** 列表路径未剥 `##` 后缀 | ✅ 已修复 | `dac5e64` | 2 条新测试（含无后缀对照）；撤掉修复目标测试失败；750 → 752 |
| **A-4** 字段级 XPath 无 `&&`/`\|\|` 组合 | ✅ 已修复 | `da0f94b` | 4 条新测试（3 判伪 + 1 对照）；撤掉修复 3 条判伪失败；752 → 756 |
| M-7 / M-8 / M-9 / M-10、D-13、D-14 | ⏳ **待裁决**（可能是有意偏差，改了可能是倒退） | — | — |
| M-1 `{$.rule}`、C-22 编码族、C-23 加密族、C-18/C-19/C-20 请求侧 | ⏳ 待补（纯新增，无回归风险） | — | — |
| M-5（XPath 静默降级）、C-1（两套 `java.*` stub） | ⏳ 待评估（架构级，需差分 oracle） | — | — |

> **处置原则**：A-3 / A-4 / A-5 修的都是**无争议**项 —— Legado 的行为明确更正确
> （前者丢数据、后两者取空值），不涉及「哪种更好」的判断。
> 凡涉及判断的条目（`@text` 换行、`@html` 内层/外层、去重范围）一律**未动**，
> 等裁决后再改。

### 附：实测复核记录（真实引擎，非静态阅读）

`\.commandcode/probe_ad.py` 直接 `import YueduRuleEngine` 后运行：

| 输入 | 实测结果 | 结论 |
|---|---|---|
| `split_rule('a\|\|b[x="\|\|"]\|\|c')` | `['a', '', 'c']` | A-3 成立 |
| `split_rule('a\|\|b[x]\|\|c')` | `['a', '', 'c']` | **A-3 范围比报告更广**：括号组内**没有**分隔符也丢片段（应 `['a','b[x]','c']`） |
| `_split_element_steps('div.x@a[@href]/b')` | `['div.x', '/b']` | A-3 成立 → **§8 的修复不完整** |
| `//div[@class='i']`（元素节点，HTML 含 `LEAD<p>a</p>TAIL`） | `'LEAD'` | D-01 成立：`TAIL` 与子元素文本全丢 |
| 同上 `/text()` | `'LEAD\nTAIL'` | 旁证：只有显式 `/text()` 才正常 |
| `.body@class`（`class="body strikeout"`） | `None` | D-13 成立：多值属性使**整条字段空** |
| `span@text`（两个内容相同的 span） | `'same'` | D-14 成立 → **本条即 M-10 的更正依据** |
| `div@text`（`<div>a<br>b</div>`） | `'a\nb'` | M-7 / D-11 复现 |
| `.x@html`（`<div class='x'>hi</div>`） | `'hi'` | M-8 / D-10 复现（内层） |
| `div@ownText` 与 `div@textNodes`（同输入） | 都是 `'a\nb'` | M-9 / D-12 复现（两者实现雷同） |

**主代理判断失误记录**：`_split_tail`（`rule_engine.py:140-166`）我在评审原文时手工读过并判为
「忠实移植」，**判错了**。A-3 指出后我逐步推演 `a||b[x]||c` 才确认：括号跳过分支结束后
`pos` 被直接置为 `self._pos`，`q[pos:end]` **从不入列**。教训：静态阅读不足以判定移植忠实度。

---

## 2. 结构性差异（比零散 bug 更重要）

### M-5. XPath 是「翻译成 CSS」而不是「原生执行」 —— 待评估（架构级）

- **参考实现**：Legado 依赖 `libs.jsoup` **+ `libs.jsoupxpath`**（`yuedu/build.gradle`），
  并有**独立**的 `AnalyzeByXPath.kt`（142 行，`:57`、`:95`、`:134` 三处 `RuleAnalyzer`）；
  `AnalyzeByJSoup.kt:10` 还 `import org.seimicrawler.xpath.JXNode`，
  `parse()`（`:29-31`）能把 `JXNode` 直接当文档源。
  → **XPath 由 jsoupXpath 原生执行**。
- **NovelHub**：没有 XPath 引擎，把 XPath **翻译成 CSS**：
  `rule_engine.py:851 _xpath_list_rule_to_css`、`:912 _xpath_step_to_css`、`:1313 _eval_xpath`。
- **判定**：待评估（架构级，不是单点 bug）
- **差异说明**：任何落在翻译器覆盖范围之外的 jsoupXpath 特性都会**静默降级**
  （返回空或选错节点）而不是报错。这是「同一书源个别书失败、其余正常」这类症状的结构性来源。
- **影响**：与 `docs/codex-handoff.md` 第 9、13 节同族。
- **建议用例**：不是单条用例，而是**抽样差分**——取真书源里出现过的 XPath
  （含 `text()`、`contains()`、`following-sibling`、`normalize-space()`、多条件 `@class='x' and @id='y'`），
  逐条比对两侧命中数。

### C-1. `java.*` 是模拟层，而且有**两套**实现

- **参考实现**：`JsExtensions` 声明为 `interface JsExtensions : JsEncodeUtils`
  （`JsExtensions.kt:83`），因此脚本可见 API = `JsExtensions.kt`（1003 行）
  **+** `JsEncodeUtils.kt`（497 行），共 **86 个成员**。
- **NovelHub**：
  - `jsoup_shim.js:1064` `var java = { … }`，`:1251` `globalThis.java = java`
    （同时暴露 `source`/`cookie`/`cache`/`org`）。
  - **另有一套**：`js_runtime.py:535-695` 为**登录路径**单独写了 `java.post/get/ajax` stub
    与独立的 cache 块。
  → **同一个 `java` 命名空间有两处定义，覆盖面并不一致**：正常 `<js>` 规则路径与
  `loginUrl` 路径能调到的 API 不同。这本身是隐患（书源在登录脚本里能用的 API，
  在规则脚本里可能不可用，反之亦然）。
- **判定**：确认不一致（结构性）
- **建议**：把两套 stub 合并为单一来源，否则每次补 API 都要改两处。

---

## 3. 尚未实现

### M-1. JSONPath 的 `{$.rule}` 内嵌规则未实现 —— 尚未实现

- **参考实现**：`yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByJSonPath.kt:35-48`
  ```kotlin
  val ruleAnalyzes = RuleAnalyzer(rule, true) //设置平衡组为代码平衡
  val rules = ruleAnalyzes.splitRule("&&", "||")
  if (rules.size == 1) {
      ruleAnalyzes.reSetPos()
      result = ruleAnalyzes.innerRule("{$.") { getString(it) } //替换所有{$.rule...}
      if (result.isEmpty()) {        // st为空，表明无成功替换的内嵌规则
          val ob = ctx.read<Any>(rule)
          result = if (ob is List<*>) ob.joinToString("\n") else ob.toString()
      }
      return result
  }
  ```
  同一 `innerRule("{$.")` 也见 `:74-79`（`getStringList`）。
- **NovelHub**：`rule_engine.py:1545-1562`
  ```python
  if "{{" not in rule or "}}" not in rule:
      return rule
  ...
  return analyzer.inner_rule("{{", "}}", _resolve_template)
  ```
  `_eval_json`（`:1277-1311`）直接 `self._jsonpath(raw, rl)`；**全文没有任何 `{$` 的处理**
  （已 grep 确认）。
- **判定**：尚未实现
- **差异说明**：Legado 的 JSONPath 有**两层**——先做 `{$.xxx}` 内嵌替换（内嵌规则自身递归走
  `getString`），**替换失败则把整条规则当 JSONPath 直接读**。NovelHub 只有 `{{...}}`
  （那是 `AnalyzeUrl` 的另一条路径，见 M-2），完全没有 `{$.}` 这一层，也没有失败回退。
- **影响**：把 JSON 接口当书源的源，凡用 `{$.字段}` 跨字段引用者解析为空 →
  书名/作者为空、目录 0 章、报「书源未返回可同步的书籍」。与 `codex-handoff.md` 第 26 节同族。
- **建议用例**：规则 `{$.data.id}`，输入 `{"data":{"id":"42"}}` → 期望 `"42"`（当前为空）。

---

## 4. 确认不一致

### M-7. 取值模式 `text` 的换行语义

- **参考实现**：`AnalyzeByJSoup.kt:232-237`
  ```kotlin
  "text" -> for (element in elements) {
      val text = element.text()          // jsoup: 空白规范化，文本块之间用「空格」
      if (text.isNotEmpty()) textS.add(text)
  }
  ```
- **NovelHub**：`rule_engine.py:1258-1261`
  ```python
  if not attr or attr == "text":
      return el.get_text("\n", strip=True)   # 用「换行」
  ```
- **判定**：确认不一致
- **影响**：正文/简介等用 `@text` 的规则，换行结构不同。**也可能是 NovelHub 的有意改进**（`\n` 更利于阅读）——需裁决。另：Legado 跳过空文本，NovelHub 不跳。
- **建议用例**：HTML `<div>a<br>b</div>` + `@text` → Legado `"a b"`；NovelHub `"a\nb"`。

### M-8. 取值模式 `html` 返回内层，Legado 返回外层

- **参考实现**：`AnalyzeByJSoup.kt:260-267`
  ```kotlin
  "html" -> {
      elements.select("script").remove(); elements.select("style").remove()
      val html = elements.outerHtml()    // 外层：含元素自身标签
      ...
  }
  ```
- **NovelHub**：`rule_engine.py:1265-1268`
  ```python
  if attr == "html":
      for tag in el.find_all(["script", "style"]): tag.decompose()
      return el.decode_contents()        # 内层：不含元素自身标签
  ```
- **判定**：确认不一致
- **差异说明**：旁证——NovelHub 的 `"all"` 用 `str(el)`（外层、不删脚本），与 Legado 的
  `"all" = outerHtml()`（不删）一致。说明「删不删脚本」对齐了，但 `html` 的「内层/外层」没对齐。
- **建议用例**：`<div class="x">hi</div>` + `.x@html` → Legado `<div class="x">hi</div>`；NovelHub `hi`。

### M-9. 取值模式 `ownText` 用 `\n` 连接，jsoup 用空格

- **参考实现**：`AnalyzeByJSoup.kt:253-258` → `element.ownText()`（jsoup：仅直属文本节点，
  空白规范化后**用空格**连接）。
- **NovelHub**：`rule_engine.py:1262-1264`
  ```python
  if attr == "owntext":
      texts = [t.strip() for t in el.find_all(string=True, recursive=False) if t.strip()]
      return "\n".join(texts)
  ```
  注意：与 `"textnodes"` 分支（`:1259-1261`）**实现完全相同**。
- **判定**：确认不一致
- **差异说明**：Legado 的 `textNodes` 逐个 trim 后 `\n` 连接；`ownText` 是空格连接。NovelHub 把两者写成了一样。
- **建议用例**：`<div>a<br>b</div>` + `@ownText` → Legado `"a b"`；NovelHub `"a\nb"`。

### M-10. 去重作用域过宽（**本条已更正**，原判定写反了）

> **更正说明**：本条最初写作「默认属性分支**缺少**去重」。实测证明**写反了** ——
> NovelHub 有去重，但施加在**集合层面、对所有取值模式生效**；而 Legado 只对**属性**分支去重。
> 感谢 D 轴（D-14）指出，已用真实引擎实测确认。

- **参考实现**：`AnalyzeByJSoup.kt:270-277` —— 去重只发生在 `else`（属性）分支：
  ```kotlin
  else -> for (element in elements) {
      val url = element.attr(lastRule)
      if (url.isBlank() || textS.contains(url)) continue   // 只有属性分支去重
      textS.add(url)
  }
  ```
- **NovelHub**：`rule_engine.py:1237-1243` —— 去重发生在 `_eval_css` 的**结果集合**上，
  `@text`/`@html`/`@ownText` 等**所有模式**都会被去重：
  ```python
  seen: set[str] = set()
  unique: list[str] = []
  for val in results:
      if val not in seen:
          seen.add(val); unique.append(val)
  return unique
  ```
- **判定**：确认不一致
- **实测**（`probe_ad.py`，真实引擎）：`<div><span>same</span><span>same</span></div>`
  + `span@text` → NovelHub `'same'`（**只剩 1 条**）；Legado 会保留 2 条。
- **影响**：中。**合法重复内容被静默吞掉** —— 列表页重复出现的同名项、正文里重复的段落/图片
  都会少一条。比"多一条重复"更难发现。
- **建议用例**：两个内容相同的 `<span>` + `span@text` → 期望 2 条（当前 1 条）。

### M-4. JSONPath 分隔符多了一个 `%%`

- **参考实现**：`AnalyzeByJSonPath.kt:35` `splitRule("&&", "||")` —— **两参**；
  而 `getStringList`（`:75`）与 `getList`（`:133`）用三参 `("&&","||","%%")`。
  → Legado 的 JSON **取值**路径不支持 `%%`，只有列表路径支持。
- **NovelHub**：`rule_engine.py:1285`
  ```python
  separators = ("&&", "||", "%%") if "##" in rule else self.SEPARATORS
  ```
  `_eval_json` 一个函数同时承担取值与取列表，固定含 `%%`。
- **判定**：确认不一致
- **影响**：低。仅当 JSONPath 规则里真的含 `%%` 字面量时可见。
- **建议用例**：`a%%b` + `{"a":1,"b":2}` → Legado 视作单条规则；NovelHub 交错合并。

---

## 5. 已正确（这些是资产，改动时别弄坏）

### M-2. `{{...}}` 选对了 `innerRule` 重载

- **参考实现**：`RuleAnalyzer.kt` 有**两个** `innerRule` 重载，用途不同：
  - `:308-332` `innerRule(inner, startStep, endStep, fr)`，用 `chompCodeBalanced('{','}')`，
    **无匹配返回 `""`**（`:329`）。被 `AnalyzeByJSonPath.kt:41,79` 以 `innerRule("{$.")` 调用。
  - `:339-365` `innerRule(startStr, endStr, fr)`，用朴素 `consumeTo`，**无匹配返回原串**（`:362`）。
    被 `AnalyzeUrl.kt:186` 以 `innerRule("{{","}}")` 调用。
- **NovelHub**：`rule_engine.py:236-274` 移植的是**第二个**，且 `:270-271 if start_x == 0: return q`。
- **判定**：已正确（全仓 grep `innerRule` 只有那 3 个调用点，`{{...}}` 归 `AnalyzeUrl` 那个重载）

### M-3. 平衡模式（code balance）的划分选对了

- **参考实现**：全仓 `RuleAnalyzer(...)` 构造点中**只有** `AnalyzeByJSonPath.kt` 传 `code=true`
  （`:34`、`:74`、`:132`）；`AnalyzeByJSoup.kt:87,138,153,208`、`AnalyzeByXPath.kt:57,95,134`、
  `AnalyzeUrl.kt:184` 全用默认 `code=false`（`RuleAnalyzer.kt:4`）。
- **NovelHub**：`_RuleAnalyzer(` 共 5 处，只有 `:1284`（在 `_eval_json` 内）传 `code_balance=True`。
- **判定**：已正确。这条微妙且容易搞错。
- **另**：`_chomp_rule_balanced` 正确保留了 Legado 的「**引号内 `\` 不是转义符**」语义
  （`RuleAnalyzer.kt:129` 注释：「经过仔细测试 xpath 和 jsoup 中引号内转义字符无效」vs
  `rule_engine.py:189-193`）。

### M-6. `@CSS:` 的 `@` 切分

- **参考实现**：`AnalyzeByJSoup.kt:94-99` 用 `lastIndexOf('@')`；`isCss` 仅在 `@CSS:` 前缀时为真（`:514-522`）。
- **NovelHub**：`_split_css_attr`（`:1246-1255`）在括号深度 0 处按**第一个** `@` 切分。
- **判定**：已正确（常见写法 `div.x@text` 两者一致）；仅当**选择器自身含 `@`** 时分歧（罕见，低影响）。

---

## 6. `java.*` API 面覆盖表（机械对照）

参考实现 = `JsExtensions.kt`（1003 行，79 成员）+ `JsEncodeUtils.kt`（497 行，30 成员），
去重后 **86 个脚本可见成员**。NovelHub = `jsoup_shim.js`（1237 行）。

| 结果 | 个数 |
|---|---|
| 在 shim 中出现（**仅代表名字出现，语义未验证**） | 17 |
| **未出现** | **69** |

未出现的 69 个按所需运行时归类：

| 所需运行时 | 个数 | 说明 |
|---|---|---|
| **纯计算（可实现，没有任何借口）** | **39** | 26 个加密（AES/DES/3DES/digest/HMAC/md5Encode16）+ `timeFormat`/`timeFormatUTC`/`htmlFormat`/`t2s`/`s2t`/`toURL`/`toNumChapter`/`randomUUID`/`strToBytes`/`bytesToStr`/base64·hex 字节数组变体 |
| Android 专属（按设计无法实现） | 23 | 文件读写删、zip/rar/7z 解压、TTF 字体、`androidId`、`openUrl`、`logType` |
| 需要真实浏览器 | 5 | `webView`、`webViewGetSource`、`webViewGetOverrideUrl`、`startBrowser`、`importScript` |
| HTTP（可实现） | 2 | `getSource`、`ajaxAll` |

**缺失名称全表（69）**

纯计算（39）：
`strToBytes` `bytesToStr` `base64DecodeToByteArray` `hexDecodeToByteArray`
`hexEncodeToString` `timeFormatUTC` `timeFormat` `htmlFormat` `t2s` `s2t`
`toNumChapter` `toURL` `randomUUID` `md5Encode16` `createSymmetricCrypto`
`createAsymmetricCrypto` `createSign` `aesDecodeToByteArray` `aesDecodeToString`
`aesDecodeArgsBase64Str` `aesBase64DecodeToByteArray` `aesBase64DecodeToString`
`aesEncodeToByteArray` `aesEncodeToString` `aesEncodeToBase64ByteArray`
`aesEncodeToBase64String` `aesEncodeArgsBase64Str` `desDecodeToString`
`desBase64DecodeToString` `desEncodeToString` `desEncodeToBase64String`
`tripleDESDecodeStr` `tripleDESDecodeArgsBase64Str` `tripleDESEncodeBase64Str`
`tripleDESEncodeArgsBase64Str` `digestHex` `digestBase64Str` `HMacHex` `HMacBase64`

Android（23）：
`cacheFile` `downloadFile` `getFile` `readFile` `readTxtFile` `deleteFile`
`unzipFile` `un7zFile` `unrarFile` `unArchiveFile` `getTxtInFolder`
`getZipStringContent` `getRarStringContent` `get7zStringContent`
`getZipByteArrayContent` `getRarByteArrayContent` `get7zByteArrayContent`
`queryBase64TTF` `queryTTF` `replaceFont` `logType` `androidId` `openUrl`

浏览器（5）：`webView` `webViewGetSource` `webViewGetOverrideUrl` `startBrowser` `importScript`

HTTP（2）：`getSource` `ajaxAll`

在 shim 中出现（17）：
`ajax` `connect` `get` `post` `head` `getSource`※ `startBrowserAwait` `getVerificationCode`
`getCookie` `base64Decode` `base64Encode` `hexDecodeToString` `encodeURI`
`getWebViewUA` `toast` `longToast` `log`
（※ `getSource` 同时出现在两表属抽取边界问题，以「未出现」为准——已 grep 确认整个插件目录 0 命中。）

**验证**：以上 69 个名字在**整个 `plugins/yuedu` 目录**（`*.js` + `*.py`）中 grep 命中数均为 **0**，
即确实未在别处实现，不是抽取误差。

---

## 7. 待补：四个对照轴的完整表

| 轴 | 范围 | 状态 |
|---|---|---|
| A 元素选择/切分 | `AnalyzeByJSoup.kt` + `RuleAnalyzer.kt` + `AnalyzeByXPath.kt` ↔ `rule_engine.py` 的选择栈 | ✅ 22 条，见**附录 A** |
| B 规则调度/URL | `AnalyzeRule.kt` + `AnalyzeUrl.kt` ↔ `rule_engine.py` 的求值栈 | ✅ 20 条，见**附录 B** |
| C `java.*` 语义 | `JsExtensions.kt` + `JsEncodeUtils.kt` + `modules/rhino` ↔ `jsoup_shim.js` + `js_runtime.py` | ✅ 38 条，见**附录 A**（主代理已复核 5 条高影响项） |
| D JSONPath/正则/文本 | `AnalyzeByJSonPath.kt` + `AnalyzeByRegex.kt` + `getResultLast` ↔ `rule_engine.py` | ✅ 29 条，见**附录 D**（部分与 M-7~M-10 双重印证） |

---



---

## 附录 A. A 轴完整逐条表（元素选择 / 规则切分 / XPath）

**来源与可信度声明**：本附录由一次并行评审（与本文档同一套规范、同样的只读约束）产出，
**逐字并入、未改写**。该评审文件自身登记了全部引用锚点与未覆盖范围（其文末 G 节）。
采用任一条目前请自行核对它给出的双侧 `文件:行`。

**主代理已独立复核的条目**：

| 条目 | 复核方式 | 结论 |
|---|---|---|
| A-3 `_split_tail` 丢片段 | 手工逐步推演 `a||b[x="||"]||c` **+ 真实引擎实测** | ✅ 成立，且**范围更广**：`a||b[x]||c` 也丢（括号组内无分隔符时同样丢） |
| A-5 列表路径多一个 `\|` 且无 `##` 保护 | 读码确认 `rule_engine.py:281 SEPARATORS = ("&&","\|\|","\|","%%")` 且 `:664` 直接用 `*self.SEPARATORS`；字段路径 `:1153`/`:1285` **有** `if "##" in rule` 保护 | ✅ 前提成立 |
| A-1 / A-2 / A-4 | 采信其探针实测，未由主代理重跑 | ⚠️ 未独立复核 |
| A-3 影响面 | `_get_elements_from_root:664` 也用 `_RuleAnalyzer.split_rule` → **列表路径同样受影响** | ✅ 已补 |

**主代理判断失误**：`_split_tail` 我在评审原文时判为「忠实移植」，**判错**（见第 6 节附实测记录）。


## A 轴差异表：元素选择 / 规则切分（Legado ↔ NovelHub）

- 参考实现（权威规范）: `yuedu/`（GPL-3.0，**只读**，仅用于理解语义；本文档不复制其代码进 NovelHub）
- 对照实现: `backend/app/crawler/plugins/yuedu/rule_engine.py`
- 已读文件与行数、未覆盖范围见文末「阅读清单 / 未覆盖」。
- 判定口径：`确认不一致` = 两侧都能指出代码且行为不同；`尚未实现` = Legado 有能力、NovelHub 没有/只做了子集；`已正确` = 已对齐的资产。
- 排序：高影响（0 章 / 静默错书 / 整任务失败风险）在前。
- NovelHub 实际输出均在本机用 `python` 直接调用该模块实测（未改动任何产品代码，脚本写在系统临时目录）。

---

#### A-1. 列表规则里的 XPath 只翻译了子集，`contains()`/`position()`/轴/`text()` 等直接退回 0 元素
- **参考实现**: `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeRule.kt:385` — `Mode.XPath -> getAnalyzeByXPath(result).getElements(rule)`；
  `yuedu/.../AnalyzeByXPath.kt:52-61` — `internal fun getElements(xPath: String): List<JXNode>? { ... val rules = ruleAnalyzes.splitRule("&&", "||", "%%"); if (rules.size == 1) { return getResult(rules[0]) } ... }`（`getResult` = `JXDocument.selN(xPath)`，跑**完整 XPath 1.0**）
- **NovelHub**: `backend/app/crawler/plugins/yuedu/rule_engine.py:850-909`（`_xpath_list_rule_to_css`，注释自陈「Only the shape that book sources actually use ... is supported」）+ `911-939`（`_xpath_step_to_css`，谓词不是 `@attr`/纯数字就 `return None`）+ `835-848`（`_legado_before_elements`：翻译失败 → `el.select(normalize_css_selector(before))` 抛错 → `except: return _text_matching_elements(el, before)`）
- **判定**: 尚未实现
- **差异说明**: Legado 对 `/`/`@XPath:` 开头的 `bookList`/`chapterList` 直接交给 JXDocument 执行任意 XPath。NovelHub 只支持「子代/后代步 + `[@attr]`/`[@attr=值]` + 数字位置」这一小类；`contains(@class,'x')`、`position()>1`、`a/@href`、`text()`、多条件谓词 `[@a and @b]` 一律返回 `None` → 接着走 soupsieve（必然失败）→ 再走「文本包含整条规则字符串」的兜底 → **0 元素**。而这个 `before` 串本身是 XPath，不可能出现在页面文本里，所以兜底恒为空。
- **影响**: 用 XPath 写列表规则的源**整本站 0 本 / 0 章**（`Explore kind ... returned no books` / `书源未返回可同步的书籍`），与 `docs/codex-handoff.md` 第 3 节「目录解析 0 本」、第 26 节同症状，但触发条件从「组合规则」变成了「XPath 谓词超纲」，当前没有任何日志提示。
- **建议用例**: 输入 `bookList = //div[contains(@class,'book-list')]/ul/li` + HTML `<div class="book-list"><ul><li><a href="/b/1">b1</a></li><li><a href="/b/2">b2</a></li></ul></div>` → 期望 2 个元素（Legado 就是 2 个）；NovelHub 当前得到 `[]`（实测）。同页再放一个 `<ul><li>` 可同时验证 A-2。

#### A-2. `@XPath:` 前缀的列表规则会**静默丢掉首段选择器**（谓词被吞）
- **参考实现**: `yuedu/.../AnalyzeRule.kt:558-561` — `ruleStr.startsWith("@XPath:", true) -> { mode = Mode.XPath; ruleStr.substring(7) }`（前缀在进解析器前就被剥掉，整条 `//div[...]/ul/li` 作为完整 XPath 执行）
- **NovelHub**: `rule_engine.py:634` → `639-682`（`_get_elements_from_root` 用 `_split_element_steps(fragment)` 切 `@`）→ `698-715`（`_split_element_steps` = `_RuleAnalyzer(rule).split_rule("@")`）→ `140-166`（`_split_tail`，**159-162**）：
  `if self._pos > end: self._start = self._pos; self._split_tail(separators); return`（丢弃了 `q[pos:end]` 这段）
- **判定**: 确认不一致
- **差异说明**: NovelHub 没有识别 `@XPath:`（只在 `_eval_with_mode` 里识别字段级规则），于是 `@` 被当成「元素步骤分隔符」。`split_rule("@")` 扫到括号内的 `@class` 后 `_chomp_*` 跳过整组，`_pos` 越过 `end`，`_split_tail` 直接递归而**不把 `pos..end` 这段压进结果**，于是 `XPath://div[@class='book-list']` 被丢掉，只剩 `/ul/li`，再被 `_xpath_list_rule_to_css` 翻成 CSS `ul > li`——**页面上任何 `ul > li` 都会被选中**。
  实测：`_split_element_steps("@XPath://div[@class='b']/ul/li")` → `['/ul/li']`。
- **影响**: 比 A-1 更阴险：**不报错、有结果**，但混进无关元素 → 目录多出站内导航/其它列表项的书或章节（第 3 节「书页 0 章/错章」、第 31 节的目录污染是同类症状）。同一个 `_split_tail` 缺陷也作用于 `&&`/`||`/`%%`（见 A-3），所以影响面不止 `@XPath:`。
- **建议用例**: 输入 `bookList = @XPath://div[@class='book-list']/ul/li` + HTML `<ul class="other"><li>other1</li></ul><div class="book-list"><ul><li>b1</li><li>b2</li></ul></div>` → 期望 `["b1","b2"]`；NovelHub 当前得到 3 个元素 `["other1","b1","b2"]`（实测）。

#### A-3. `_RuleAnalyzer._split_tail` 在「分隔符出现在括号组内」时丢片段（A-2 的根因）
- **参考实现**: `yuedu/.../RuleAnalyzer.kt:263-280` — `if (st > end) { rule += arrayListOf(queue.substring(startX, end)); pos = end + step; while (consumeTo(elementsType) && pos < st) { rule += queue.substring(start, pos); pos += step }; return if (pos > st) { startX = start; splitRule() } else { rule += queue.substring(pos); rule } }`；`285-287` — 平衡组失败 `throw Error("...后未平衡")`；`228-230` 同
- **NovelHub**: `rule_engine.py:140-166`（`_split_tail`，**159-162 丢片段**；`147-164` 的循环里 `st < end` 时先 chomp，`self._pos > end` 就递归返回）
- **判定**: 确认不一致
- **差异说明**: Legado 在「分隔符在括号组里」时会把 `startX..end` 之前已确认的片段先 `rule +=` 压入，再继续扫描；NovelHub 递归前**没有压入** `q[pos:end]`，该片段永久丢失（且 `_start_x` 在 `_split_tail` 里根本没被用于切片）。
  实测：`_RuleAnalyzer('a||b[x="||"]||c').split_rule('&&','||','%%')` → `['a', '', 'c']`（Legado → `['a','b[x="||"]','c']`）；`_split_element_steps("div.x@a[@href]/b")` → `['div.x', '/b']`（Legado → `['div.x','a[@href]/b']`）。
- **影响**: 与 A-2 同一根因的通用缺陷：任何「括号组内含 `||`/`&&`/`|`/`%%`/`@`，且该括号组之前已经有分隔符」的规则都会丢一整个片段。最常见的就是 `@XPath:`/`@CSS:` 前缀 + XPath 属性谓词；其次是 `##` 正则里含 `|` 的列表规则。第 8 节（`@` 链要按括号配对切）修好了「按 `@` 蛮切」，但这条递归分支没修，属于**同一节的残留**。
- **建议用例**: 直接单测 `_RuleAnalyzer('a||b[x="||"]||c').split_rule('&&','||','%%')` → 期望 `['a','b[x="||"]','c']`，当前 `['a','','c']`。

#### A-4. 字段级 XPath 规则的 `||` / `&&` / `%%` 组合完全没实现
- **参考实现**: `yuedu/.../AnalyzeByXPath.kt:92-131` — `internal fun getStringList(xPath: String): List<String> { ... val rules = ruleAnalyzes.splitRule("&&", "||", "%%"); if (rules.size == 1) {...} else { for (rl in rules) { val temp = getStringList(rl); if (temp.isNotEmpty()) { results.add(temp); if (... elementsType == "||") break } } ... } }`（`getString` 同理，133-154 行）
- **NovelHub**: `rule_engine.py:1313-1351`（`_eval_xpath`：`selector, transform = self._split_xpath_transform(rule)` 后**整条**交给 `tree.xpath(selector)`；失败则 `soup.select(normalize_css_selector(selector))`，再失败 `return None`）
- **判定**: 确认不一致
- **差异说明**: Legado 先把 XPath 规则按 `&&`/`||`/`%%` 切成多段，逐段求值，`||` 取第一个非空、`&&`/无分隔符全部拼接、`%%` 交错。NovelHub 把带 `||` 的字符串整条塞给 lxml → `XPathEvalError`（XPath 1.0 没有 `||`）→ 再走 CSS 兜底（`//...` 不是合法 CSS）→ `None` → 字段为空。
- **影响**: 正文/封面/目录 URL 字段最常见的写法之一 `//div[@id="content"]||//div[@class="content"]` 直接取空 → 正文空 / `Chapter returned empty content` / 封面空；与第 25 节（正文只读到一部分）、第 11 节（`Chapter returned empty content`）同类收敛点。注意：`_get_elements_from_root`（列表规则）**有**组合切分（第 26 节已修），只有 XPath 字段路径漏了。
- **建议用例**: 输入 `content = //div[@id='a']||//div[@id='b']` + HTML `<div id="b">FALLBACK</div>` → 期望 `"FALLBACK"`；NovelHub 当前得到 `None`（实测；同页 `//div[@id='b']` 单段正常返回 `"FALLBACK"`）。

#### A-5. 列表规则里的 `##` 正则含 `|`（或空段）会被当成组合分隔符 → 0 元素
- **参考实现**: Legado 在进元素解析前先剥掉 `##` 后缀：`yuedu/.../AnalyzeRule.kt:374,376` — `sourceRule.makeUpRule(result)` … `val rule = sourceRule.rule`；`707-712` — `val ruleStrS = rule.split("##"); rule = ruleStrS[0].trim(); if (ruleStrS.size > 1) { replaceRegex = ruleStrS[1] }`；分隔符集合只有三个：`AnalyzeByJSoup.kt:139` / `RuleAnalyzer.kt:165` — `splitRule("&&", "||", "%%")`
- **NovelHub**: `rule_engine.py:279-281` — `SEPARATORS = ("&&", "||", "|", "%%")`；列表路径直接用它：`_get_elements_from_root:664` — `fragments = analyzer.split_rule(*self.SEPARATORS)`（**没有 `##` 保护**）；对比字段路径 `_eval_css:1153` — `separators = ("&&", "||", "%%") if "##" in rule else self.SEPARATORS`（这里有保护，列表路径没有）
- **判定**: 确认不一致
- **差异说明**: Legado 对 `bookList`/`chapterList` 会先把 `##` 之后的替换正则摘出去（`replaceRegex`），只拿选择器部分去选元素；于是正则里的 `|` 不影响元素选择。NovelHub 的列表路径把整条（含 `##…` 正则）交给 `_RuleAnalyzer`，`SEPARATORS` 里又多了个单 `|`，正则里的 `\s+|\s+` 就把规则切碎：第一段 `.list li##\s+` 交给 soupsieve 报错 → 文本兜底 → 空；`elements_type == "|"` 时还要等第一段为空再试第二段 `\s+`（同样选不到）→ **0 元素**。附带说明：单 `|` 本身也不在 Legado 的分隔符集合里（`a|b` 在 Legado 里是整条进 jsoup/soupsieve，`|` 只作命名空间语法）。
- **影响**: **高**。`##` 是书源里极常见的写法，正则里带 `|` 同样常见（`##\s+|\s+`、`##^第.+?章|正文`）；落在 `bookList`/`chapterList` 上就是**整个分类页 0 本 / 书页 0 章**，症状与 `docs/codex-handoff.md` 第 26 节（`||` 组合列表规则未切分 → `Explore kind … returned no books` / `书源未返回可同步的书籍`）完全一致，但触发点是「单 `|` 多切了一刀」，线上极难从日志看出。
- **建议用例**: 输入 `chapterList = ".list li##\s+|\s+"` + HTML `<ul class="list"><li>c1</li><li>c2</li></ul>` → 期望 2 个元素（Legado 会先剥掉 `##\s+|\s+` 再选 `.list li`）；NovelHub 当前得到 `[]`（实测；同页 `chapterList = ".list li"` 正常得到 2 个，说明差异就出在 `##` + `|`）。
  第二个更轻的用例（只验证单 `|`）：`bookList = ".a|.b"` → NovelHub 只取第一段命中的元素，Legado 是把 `a|b` 当一条选择器。

#### A-6. `%%` 交错的长度上界：Legado 以**第一个片段**的长度为界，NovelHub 取最长
- **参考实现**: `yuedu/.../AnalyzeByJSoup.kt:180-187` — `if ("%%" == ruleAnalyzes.elementsType) { for (i in 0 until elementsList[0].size) { for (es in elementsList) { if (i < es.size) { elements.add(es[i]) } } } }`
- **NovelHub**: `rule_engine.py:688-695` — `longest = max(len(group) for group in groups); for index in range(longest): for group in groups: if index < len(group): combined.append(group[index])`（字段路径 `_eval_css` 的 `1187-1194` 用同样的 `max_len`）
- **判定**: 确认不一致
- **差异说明**: Legado 的外层循环上界是 `elementsList[0].size`（而且 `getElements` 的 CSS/非 CSS 两支都**无条件**把每段加进 `elementsList`，包括空段），所以第一段比后面短时，**后面的尾巴会被丢掉**，第一段为空时结果就是空。NovelHub 用 `max`，把后面片段多出来的元素全部追加。
  实测：规则 `.a%%p`、HTML `<div class="a"><p>p1</p></div><div class="b"><p>p2</p><p>p3</p></div>` → NovelHub 4 个元素（文本 `['p1','p1','p2','p3']`，即 `div.a` + 3 个 `p` 交错）；Legado 上界 = `elementsList[0].size` = 1 → 只有 2 个（`div.a` 与 `p1`）。
- **影响**: 低-中。`%%` 在线上暂无书源用例（第 26 节自陈），但一旦有源用它做「列表+标题」并行取值，NovelHub 会**多解析出条目**（图书/章节数虚高、条目与来源不匹配），而不是 Legado 的截断行为。
- **建议用例**: 输入 `chapterList = ".a%%p"` + 上述 HTML → 期望 2 个元素（Legado）；NovelHub 当前 4 个（实测，走 `_get_elements` 列表路径；字段路径因为纯 CSS 规则要求带 `@属性` 本身取不到值，未构造对照）。

#### A-7. 以 `./` 开头的规则被当成 XPath（Legado 只有 `/` 开头才是 XPath）
- **参考实现**: `yuedu/.../AnalyzeRule.kt:573-576` — `ruleStr.startsWith("/") -> { mode = Mode.XPath; ruleStr }`（**只有** `/`，没有 `./`）；`@XPath:` 见 `558-561`
- **NovelHub**: `rule_engine.py:746-753` — `if rule.endswith("]"): if "//" in rule or rule.startswith(("/", ".//", "./")): return rule, " ", []`；`835-836` — `if before.startswith(("/", ".")) or "//" in before: css = _xpath_list_rule_to_css(before)`；`867` — `text = re.sub(r"^\.?//?", "", text, count=1)`
- **判定**: 确认不一致
- **差异说明**: 在 NovelHub 里 `./div` 被翻译成**后代** CSS `div`（`^\.?//?` 把 `./` 直接剥掉），于是 `./div` 会选中所有后代 `div`；在 Legado 里 `./div` 走 `AnalyzeByJSoup`（mode Default），`beforeRule` 进 `AnalyzeByJSoup.kt:320 else -> temp.select(beforeRule)`，`./div` 不是合法 jsoup CSS → 抛 `SelectorParseException`（外层按规则失败/取空处理）。语义上 XPath `./div` 也应是**直接子元素**，NovelHub 用后代选择器同样不对。
  实测：`_get_elements(html, './div')` 在 `<div class="wrap"><div class="item">outer</div><div><div class="item">inner</div></div></div>` 上返回 4 个 `div`。
- **影响**: 低-中。`./` 开头的源极少；一旦出现，NovelHub 会**静默多选**（列表规则里表现为目录混入更多元素），而 Legado 是明确失败。
- **建议用例**: 输入 `chapterList = ./div` + 上述嵌套 HTML → Legado/正确 XPath 语义应为 2 个直接子 `div`；NovelHub 当前 4 个后代 `div`（实测）。

#### A-8. 老式（非 `[]`）索引写法只支持**单个**索引；区间/多索引与区间负步长语义不同
- **参考实现**: `yuedu/.../AnalyzeByJSoup.kt:482-506`（非 `[]` 写法：`if (rl == '!' || rl == '.' || rl == ':') { indexDefault.add(...) ... }`，`:` 与 `,` 一样只做数字分隔，**可以有多个**）+ `331-335`（逐个取 `indexDefault`）；区间写法 `337-370`：`val step = if (stepX > 0) stepX else if (-stepX < len) stepX + len else 1`、`if ((start < 0 && end < 0) || (start >= len && end >= len)) continue`（两侧同向越界→整条区间作废）
- **NovelHub**: `rule_engine.py:800-802` — `m = re.match(r"^(.*?)([.!:])(-?\d+)$", rule); if m: return m.group(1).rstrip(), m.group(2), [int(m.group(3))]`（**只能一个尾随数字**）；区间展开 `982-999` — `if step < 0: step = -step`（取绝对值，不做 `stepX + len`），且 `989-990` 把起止 `clamp` 进 `[0, len-1]` 后**总会产出元素**
- **判定**: 确认不一致
- **差异说明**: 老式写法里 `div.item.0:2` 在 Legado = 第 0 与第 2 个元素（`: ` 只是分隔符）；NovelHub 的正则把它切在最后一个 `:` 上，`before = "div.item.0"`，于是 `find_all("div.item.0")` → 空。区间越界时 Legado 丢弃该区间（可能得到空列表），NovelHub 夹取后仍返回元素；负步长时 Legado 用 `stepX + len`（近似「从头再数一位」的用法），NovelHub 直接反转成正向步长。
  实测：`div.item.0:2` + 3 个 `<div class="item">` → NovelHub `[]`（Legado → 第 0、2 个）；`div.item[0:2]` → NovelHub 3 个（Legado 的 `Triple` 语义 = 0..2 → 也是 3 个，此例一致）。
- **影响**: 低。老式多索引/区间写法在现代书源里少见；出现时表现为「该字段取空」（不报错）。
- **建议用例**: 输入 `bookList = div.item.0:2` + `<div class="item">0</div><div class="item">1</div><div class="item">2</div>` → 期望 `["0","2"]`；NovelHub 当前 `[]`（实测）。

#### A-9. `_parse_legado_index` 的 XPath 判定过宽：CSS 选择器里只要出现 `//` 就丢掉 Legado 索引
- **参考实现**: `yuedu/.../AnalyzeByJSoup.kt:416-481`（`findIndexSet`：只要规则以 `]` 结尾且括号体是索引，就先切出 `beforeRule`，与规则里是否含 `/` 无关）
- **NovelHub**: `rule_engine.py:752-753` — `if "//" in rule or rule.startswith(("/", ".//", "./")): return rule, " ", []`
- **判定**: 确认不一致
- **差异说明**: 判据用的是「整条规则里含 `//`」，而不是「这条规则是 XPath 模式」。属性值里带 `//` 的 CSS 规则（`a[href^='//x'][1]`）会整条进 soupsieve，`[1]` 被它当畸形属性选择器 → 抛错 → 文本兜底 → 0 元素。Legado 会切出 `beforeRule = "a[href^='//x']"` + `indexes=[1]` → 第二个 `a`。
  实测：`a[href^='//x'][1]` → `[]`、`a[href^='//x'][0]` → `[]`（Legado → `"two"` / `"one"`）。
- **影响**: 低-中（取决于源是否用 `//` 出现在属性选择器里、同时还要抓元素索引）；表现为该字段静默取空。
- **建议用例**: 输入 `chapterList = a[href^='//x'][0]` + `<a href="//x/1">one</a><a href="//x/2">two</a>` → 期望 `["one"]`；当前 `[]`（实测）。

#### A-10. 括号不平衡：NovelHub 抛 `RuleUnbalancedError`（已对齐 Legado 的 `throw`），但**调用方吞掉后按单片段继续求值**
- **参考实现**: `yuedu/.../RuleAnalyzer.kt:228-230` — `if (!chompBalanced(queue[pos], next)) throw Error(queue.substring(0, start) + "后未平衡")`
- **NovelHub**: `rule_engine.py:44-52`（`RuleUnbalancedError`）、`128`/`158`（抛出点）、`663-672`（`_get_elements_from_root` catch 后 `fragments = [rule]`）、`1154-1168`（`_eval_css` catch 后 `rules = [rule]`）
- **判定**: 已正确（就第 9 节的核心诉求：不再原地递归/`RecursionError`）
- **差异说明**: 两侧都在不平衡时报错；NovelHub 额外在调用方兜底成「整条当一段」，Legado 是让异常冒到上层（字段级失败）。这是有意的降级（第 9 节：字段为空而不是整本书失败），行为不等价但方向更安全——如果之后要严格对齐，需要显式决定「字段为空 vs 抛错」。
- **影响**: 无新风险；仅记录为与 Legado 的**有意偏离**。
- **建议用例**: 输入 `content = 'div[0@text'`（未闭合 `[`）+ 任意 HTML → 期望不出现 `RecursionError`/死循环，NovelHub 返回空而不是抛到任务层（现状即期望）。

---

#### 已正确（对齐的资产，供回归保护）

| 编号 | 行为 | 参考实现 | NovelHub | 说明 |
|---|---|---|---|---|
| C-1 | 未加引号的属性选择器归一化 `a[href*=/post/]` | jsoup 原生容忍（`AnalyzeByJSoup.kt:320 temp.select(beforeRule)`） | `normalize_css_selector` `rule_engine.py:55-71` | soupsieve 会抛，归一化后与 jsoup 行为一致；第 3 节「目录解析 0 本」的修复点。 |
| C-2 | `@` 链按括号/引号配对切分（不是 `split("@")`） | `RuleAnalyzer.kt:165-237`（`splitRule("@")`） | `_split_element_steps` `rule_engine.py:698-715` | `//div[@class='gallary_wrap']/ul/li` 能正确切；第 8 节修复点（但见 A-3 的残留缺陷）。实测该规则能选出 2 个 `li`。 |
| C-3 | 列表规则里 `@CSS:` 前缀剥离 | `AnalyzeByJSoup.kt:514-522`（`isCss = true`，`elementsRule` 去掉前缀） | `_get_elements` `rule_engine.py:627-628` | 一致。 |
| C-4 | `@@` 前缀剥离后当纯 CSS | `AnalyzeRule.kt:553-556`（`@@` → substring(2)） | `_select_elements_chain` `rule_engine.py:722-727`、`_eval_with_mode:1093-1094` | 一致。 |
| C-5 | `&&` 拼接 / `\|\|` 首个非空即停（列表规则） | `AnalyzeByJSoup.kt:139-149,179-193` | `_get_elements_from_root` `rule_engine.py:662-684`（`||` break 在 `683`） | 第 26 节的核心修复，实测 `h3 a\|\|.post-title a` 切成两段、`.a\|\|.b` 生效。 |
| C-6 | `/`、`//` 开头的列表规则按 XPath 语义的**位置谓词 1-based** | `AnalyzeByXPath` + JXDocument（`//ul/li[1]` = 第一个 li） | `_parse_legado_index:752`（XPath 不做 Legado 索引）+ `_xpath_step_to_css:932-937`（`li[1]` → `:nth-of-type(1)`） | 第 13 节修复点。实测 `//ul/li[1]` → 第一个 `li`（修好前会选第二个）。 |
| C-7 | CSS/元素规则的 Legado 索引是 **0-based**（`div.item[1]` = 第 2 个） | `AnalyzeByJSoup.kt:376-377` | `_parse_legado_index:765-798` + `_apply_legado_indexes:976-981` | 实测 `div.item.2` → 第 3 个、`div.item[0,2]` → 第 1、3 个。 |
| C-8 | `!` 排除 / `.` 选取 语义 | `AnalyzeByJSoup.kt:386-400` | `_apply_legado_indexes:1001-1004` | `div.item[!0,3]` 解析与排除顺序一致。 |
| C-9 | 索引声明顺序保留（不是反向） | `AnalyzeByJSoup.kt:325-381`（`lastIndexes downTo 0` 还原声明序） | `_apply_legado_indexes:973-999`（`ordered` 按声明序） | 一致。 |
| C-10 | 前置规则为空时按 `children()`；`children`/`class.`/`tag.`/`id.`/`text.` 前缀 | `AnalyzeByJSoup.kt:311-321` | `_select_elements_chain:735`、`_legado_before_elements:806-828` | 一致。 |
| C-11 | 括号不平衡不再原地递归（第 9 节） | `RuleAnalyzer.kt:228-230` throw | `rule_engine.py:44-52,128,158,663-672,1154-1168` | 见 A-10。 |
| C-12 | 元素规则 + `@js:` 步骤混用（列表规则） | Legado 由 `AnalyzeRule.splitSourceRule` 把 `@js:` 段派给 Js 模式（**本次未细读该函数**，见未覆盖） | `_eval_list_rule:575-602`（`@js:` 之后保留元素、JS 只做 `java.put` 时返回原元素） | 第 8 节的修复点，行为上更宽容；严格对齐未核实，故列在「已正确」的边界，需 B/C 轴复核。 |

---

### 阅读清单 / 未覆盖

**实际读过（只读，未修改任何产品代码）**
- `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByJSoup.kt`：全文 524 行。
- `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/RuleAnalyzer.kt`：全文 378 行。
- `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByXPath.kt`：全文 155 行。
- `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeRule.kt`：读了 60-259、330-449、520-808 行（约 610 行），用于确认 mode 判定（`/`→XPath、`@XPath:`、`@CSS:`、`@@`）与 `getElements`/`getElement` 的分派。
- `backend/app/crawler/plugins/yuedu/rule_engine.py`：读了 1-330、440-534、533-1006、1007-1257、1258-1279、1310-1369 行（约 1100 行）。
- `docs/codex-handoff.md`: 第 3 节（91-153）、第 8 节（231-264）、第 9 节（265-281）、第 13 节（342-368）、第 26 节（655-692）。
- 只读验证：在系统临时目录写了 4 个探针脚本（`%TEMP%\diffA_probe*.py`）直接 `import` 该模块调用被测函数；未写入仓库、未跑仓库测试、未改产品代码。

**本次没来得及看（不在这份表里的结论请不要当作已核实）**
- `AnalyzeRule.kt:475-519 splitSourceRule`（Legado 如何在进解析器前拆 `<js>`/`@js:`/`{{}}`/`@get:`）——因此 C-12（元素规则 + `@js:`）的 Legado 侧语义只做到「推断」，未逐行核对；`@js:` 步骤在 Legado 列表规则里的确切求值上下文（`result` vs 当前元素）也留给 B/C 轴。
- `AnalyzeRule.kt:260-330`（`getString(ruleList,...)` 的 `Mode.Regex`/`AnalyzeByRegex` 分支）与 `RuleData.kt` / `RuleDataInterface.kt` / `SourceRule`（`.../SourceRule.kt` 未找到独立文件，`SourceRule` 是 `AnalyzeRule` 的内部类）。
- `getString`/`getStringList`/`getResultLast`/`getResultList` 的取值/文本抽取语义（明确不属于本轴；顺带发现：`_eval_css_single:1213 rule.split("@")` 是**不按括号配对**的蛮切，且 `@`-less 的纯 CSS 规则（`div.two`、`#content`）在两侧都取不到值，未列为差异）。
- `##` 正则后缀、`{{}}` 模板、`@js:` 调度、`jsoup_shim.js` / `js_runtime.py`（别人负责）。
- `_eval_json` / `_jsonpath` 的 `&&`/`||` 切分（JSON 轴，不属本轴）。
- A-6 的 `%%` 在**字段路径**（`_eval_css`）的行为：因为纯 CSS 规则本身取不到值（见上一条），未构造出有意义的对照用例。

---


---

## 附录 B. B 轴完整逐条表（规则调度 / 字段求值 / URL 模板）

**来源与可信度声明**：本附录由一次并行评审（与本文档同一套规范、同样的只读约束）产出，
**逐字并入、未改写**。该评审文件自身登记了全部引用锚点与未覆盖范围（其文末 G 节）。
采用任一条目前请自行核对它给出的双侧 `文件:行`。

**主代理已独立复核的条目**：

| 条目 | 复核方式 | 结论 |
|---|---|---|
| B-1 `_try_eval_js` 仍原样返回输入 | 读 `rule_engine.py:1456-1457`，注释与代码字面即 `# Last resort: return input as-is if it's a string` / `return None if not isinstance(raw, str) else raw` | ✅ 成立 → **§9 的根因只消除了一半**（只改了 `_try_eval_js_value`） |
| B-2 JSON 默认模式只在列表路径置位 | `_is_json_context` 定义 `:296`、仅在 `_eval_list_rule` 内置位（`:565`/`:572`/`:574`）、读取 `_eval_with_mode:1102`，**其它路径从不复位** | ✅ 成立 → 且是**跨调用残留状态**，行为取决于求值顺序（与 §9「上下文串味」同型） |
| B-4 `sourceRegex` 被误用为正文正则 | 未独立复核 | ⚠️ 影响大（正文被截成子串），建议优先核对 |


## B — 规则分派 / 字段求值 / URL 模板 对照（Legado 为权威规范）

范围：`rule_engine.py` 的 `_extract_*` / `_eval_*` / `_try_eval_js*` / `_substitute*` / `_split_*` /
`parse_*` / `build_*_url` / `get_next_*` / `get_variable`/`put_variable`，对照
`yuedu/.../analyzeRule/AnalyzeRule.kt`（907 行）与 `AnalyzeUrl.kt`（851 行）。

标记：`参考实现` 一律为 `yuedu/` 下相对路径；少量“补充依据”来自 Legado 的调用方
（`BookInfo.kt`/`BookContent.kt`/`BookList.kt`/`BackstageWebView.kt`/`RhinoScriptEngine.kt`/`AppPattern.kt`），
用于确定“谁把什么内容交给 AnalyzeRule”，已明确标注。未读到的部分统一放在最后一节，不倒推结论。
`yuedu/` 为 GPL-3.0：以下只引用片段说明语义，不含任何可复制进 NovelHub 的实现建议。

编号说明：按影响排序，编号不严格连续——`确认不一致`/`尚未实现` 为 B-1…B-12 与 B-20（低影响项排在最后），
`已正确` 为 B-13…B-19。

---

#### B-1. JS 规则求值为 `undefined` 时，NovelHub 把“输入”当结果返回（整页 HTML 变字段值）
- **参考实现**: `yuedu/app/src/main/java/com/script/rhino/RhinoScriptEngine.kt:315`（`AnalyzeRule.evalJS` → `script.eval` 的返回值，见 `AnalyzeRule.kt:801-803`）
  ```kotlin
  return if (result1 is Undefined) null else result1
  ```
  `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeRule.kt:287-312`
  ```kotlin
  287: for (sourceRule in ruleList) {
  288:     putRule(sourceRule.putMap)
  289:     sourceRule.makeUpRule(result)
  290:     result ?: continue          // 上一步为 null → 后续片段全部跳过
  ...
  312: if (result == null) result = ""
  ```
- **NovelHub**: `backend/app/crawler/plugins/yuedu/rule_engine.py:1451-1457`
  ```python
  if result is not None:
      return result
  except Exception:
      pass
  # Last resort: return input as-is if it's a string
  return None if not isinstance(raw, str) else raw
  ```
  （成因在 `js_runtime.py:357`：`return __codex_out__===undefined?result:__codex_out__;`）
- **判定**: 确认不一致
- **差异说明**: Legado 的 JS 求值为 `undefined` → `null` → 链上后续片段被跳过（`result ?: continue`），`getString` 最终返回 `""`。NovelHub 在 JS 求值为 `undefined`、脚本抛错、或 Node 运行时不可用（1453-1454 吞掉异常）时，返回**规则输入本身**（`ruleBookInfo` 时是整页 HTML，列表字段时是列表项 HTML），并以此继续求值后续片段。
- **影响**: 与 `docs/codex-handoff.md` 第 9 节同源——第 9 节只修了模板路径 `_try_eval_js_value:1594-1629`，`_try_eval_js` 的“原样返回输入”仍在。症状：`ruleBookInfo.name`/`ruleSearch.*` 是 JS 规则而脚本失败时字段变成整页 HTML，再被当 CSS/JSON 规则求值 → 字段空、超长书名入库、或解析异常。第 9 节 / 第 26 节。
- **建议用例**: 输入规则 `@js:var x = 1;` + 上下文 `<html><body><p>x</p></body></html>` → 期望 `""`（Legado：undefined→null→空）。NovelHub 当前得到整段 `<html>...</html>` 字符串。

#### B-2. 内容为 JSON 时 `isJSON → Mode.Json` 的默认模式分派缺失
- **参考实现**: `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeRule.kt:87-90`
  ```kotlin
  isJSON = when (content) { is Node -> false; else -> content.toString().isJson() }
  ```
  `AnalyzeRule.kt:568-571`
  ```kotlin
  isJSON || ruleStr.startsWith("$.") || ruleStr.startsWith("$[") -> {
      mode = Mode.Json
      ruleStr
  }
  ```
- **NovelHub**: `backend/app/crawler/plugins/yuedu/rule_engine.py:1102-1108`
  ```python
  if self._is_json_context or isinstance(raw, (dict, list)):
      ...
      return self._jsonpath(raw, rule)
  ...
  return self._eval_css(raw, rule)
  ```
  `_is_json_context` 只在 `_eval_list_rule` 内部置位：`rule_engine.py:565-574`
  ```python
  565: self._is_json_context = True
  ...
  572: self._is_json_context = False
  574: self._is_json_context = False
  ```
- **判定**: 确认不一致
- **差异说明**: Legado 在 `setContent` 时按**内容**判定一次 JSON，之后所有无模式前缀的 SourceRule 都走 JSONPath（`name: "data.bookName"` 直接可用）。NovelHub 的 `_is_json_context` 只在列表规则求值期间为真，而 `_extract_book_info:479` / `_extract_content:501` 解析 JSON 响应时它为 `False`，`raw` 又是 `str`（不是 dict/list）→ 落到 `_eval_css`，把 JSON 文本当 HTML 选择器 → 恒为空。搜索/目录因列表项是 dict（`isinstance(raw,(dict,list))`）而幸免。
- **影响**: JSON API 类书源的 `ruleBookInfo.*`、`ruleContent.content`（写成 `data.xxx` 这种不带 `$.` 的路径）全部解析为空 → 书名/作者/正文空，`no usable metadata` 类失败。第 26 节。
- **建议用例**: `raw = '{"data":{"name":"书名"}}'`，规则 `data.name` → 期望 `书名`。NovelHub 当前得到 `""`。

#### B-3. `ruleBookInfo.init` 的结果被转成字符串，DOM 根丢失
- **参考实现**: `yuedu/app/src/main/java/io/legado/app/model/webBook/BookInfo.kt:58-64`（补充依据）
  ```kotlin
  infoRule.init?.let {
      if (it.isNotBlank()) {
          analyzeRule.setContent(analyzeRule.getElement(it))
  ```
  `AnalyzeRule.kt:332-361`（`getElement` 返回 Element，之后所有字段规则继续在这棵子树上选择）
- **NovelHub**: `backend/app/crawler/plugins/yuedu/rule_engine.py:478-484`
  ```python
  init_rule = rules.get("init", "")
  if init_rule:
      narrowed = self._eval_field(raw, init_rule)
      if narrowed:
          raw = str(narrowed) if not isinstance(narrowed, str) else narrowed
  ```
- **判定**: 确认不一致
- **差异说明**: `_eval_field` 的 CSS 通道最终由 `_eval_css_single:1200-1243` + `_extract_css_value:1258-1275` 产出**文本**（`el.get_text("\n", strip=True)`），`str()` 之后被当新 HTML 重新 `BeautifulSoup` 解析——标签结构没了。Legado 的解析根仍是 Element，后续 `h1@text`、`@css:`、`//` 规则依旧能在子树里选元素。
- **影响**: 书源 `ruleBookInfo.init` 指容器、后续字段用子选择器时全部为空（书名/作者/简介空，第 26 节 "no usable metadata" 同类）。仅当 init 用元素选择器且后续字段依赖结构时命中。
- **建议用例**: `ruleBookInfo = {"init": "div.info", "name": "h1@text"}` + `<div class="info"><h1>书名</h1></div>` → 期望 `书名`。NovelHub 当前得到 `""`（init 先求值成文本 `书名`，`h1` 已不存在）。

#### B-4. `ruleContent.sourceRegex` 被当正文正则（Legado 是 WebView 资源 URL 匹配器）
- **参考实现**: `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeUrl.kt:402/428/438`（`sourceRegex` 只被转发给 `BackstageWebView`）；`yuedu/app/src/main/java/io/legado/app/help/http/BackstageWebView.kt:283-293`
  ```kotlin
  override fun onLoadResource(view: WebView, resUrl: String) {
      sourceRegex?.let {
          if (resUrl.matches(it.toRegex())) {
              val response = StrResponse(url!!, resUrl)
  ```
- **NovelHub**: `backend/app/crawler/plugins/yuedu/rule_engine.py:520-527`
  ```python
  source_regex = rules.get("sourceRegex", "")
  if source_regex:
      try:
          m = re.search(source_regex, content)
          if m:
              content = m.group(0)
  ```
- **判定**: 确认不一致
- **差异说明**: Legado 的 `sourceRegex` 作用于浏览器加载的**资源 URL**（`resUrl.matches(...)`），且只在 webView 分支存在，从不作用于正文文本；NovelHub 把它当正文正则，命中后**用匹配串替换整段正文**。
- **影响**: 声明了 `ruleContent.sourceRegex` 的书源，正文被替换成一个子串（内容被截断/破坏）；Legado 下该规则对正文无副作用。属“正文被破坏”级。
- **建议用例**: `ruleContent = {"content": ".c@text", "sourceRegex": "\\d+"}` + `<div class="c">第1章 内容</div>` → 期望正文 `第1章 内容`。NovelHub 当前得到 `1`。

#### B-5. `@get:{key}` 变量读取未实现
- **参考实现**: `AnalyzeRule.kt:881-882`
  ```kotlin
  private val evalPattern =
      Pattern.compile("@get:\\{[^}]+?\\}|\\{\\{[\\w\\W]*?\\}\\}", Pattern.CASE_INSENSITIVE)
  ```
  `AnalyzeRule.kt:598-605`
  ```kotlin
  tmp.startsWith("@get:", true) -> {
      ruleType.add(getRuleType)
      ruleParam.add(tmp.substring(6, tmp.lastIndex))
  }
  ```
  `AnalyzeRule.kt:698-700` — `regType == getRuleType -> { infoVal.insert(0, get(ruleParam[index])) }`（`get` 见 754-769）
- **NovelHub**: `rule_engine.py:1082-1108`（`_eval_with_mode` 只识别 `@js:`/`@put:`/`@xpath:`/`@json:`/`@css:`/`@@`/`/`/`$.`/`$[`）；`_substitute_inner_rules:1545-1562` 只处理 `{{...}}`；`_split_put:1524-1543` 只处理 `@put:`
- **判定**: 尚未实现
- **差异说明**: Legado 会把规则里的 `@get:{key}` 就地替换成 `get(key)`（chapter/book/ruleData 变量或书源自身的 key）；NovelHub 完全不认识 `@get:`，残留文本会被当普通片段继续走 `_eval_css`/`_eval_xpath`。
- **影响**: 用 `@get` 拼变量的书源（如 `ruleSearch.bookUrl: "@get:{baseUrl}@js:..."`）整段规则解析为空 → bookUrl/目录 URL 空。第 9 节同类。
- **建议用例**: `engine.set_page_url("https://a/")`（写入 `_variables["baseUrl"]`），规则 `@get:{baseUrl}` + 任意上下文 → 期望 `https://a/`。NovelHub 当前得到 `""`（或把 `@get:{baseUrl}` 当选择器后为空）。

#### B-6. `ruleContent.replaceRegex` 被当正则替换（Legado 当规则求值 + 逐行 trim/缩进）
- **参考实现**: `yuedu/app/src/main/java/io/legado/app/model/webBook/BookContent.kt:139-144`（补充依据）
  ```kotlin
  val replaceRegex = contentRule.replaceRegex
  if (!replaceRegex.isNullOrEmpty()) {
      contentStr = contentStr.split(AppPattern.LFRegex).joinToString("\n") { it.trim() }
      contentStr = analyzeRule.getString(replaceRegex, contentStr)
      contentStr = contentStr.split(AppPattern.LFRegex).joinToString("\n") { "　　$it" }
  ```
  （`analyzeRule.getString(replaceRegex, contentStr)` 见 `AnalyzeRule.kt:250-327`，替换规则本身也走 SourceRule 解析，`##a##b` 由 `AnalyzeRule.kt:436-460` 处理）
- **NovelHub**: `rule_engine.py:516-518` — `content = self._apply_replace_regex(content, replace_regex)`；实现是 `re.sub`：`rule_engine.py:1362-1390`。同一份规则在 `chapter.py:181-183` 又跑一次。
- **判定**: 确认不一致
- **差异说明**: Legado 把 `replaceRegex` 当**规则字符串**求值（可以是 `@js:`、CSS 选择器，`##` 形式也只是其中一种），并在替换前后逐行 `trim()` / 加 `　　` 缩进；NovelHub 只有 `##pattern##replacement` 的 `re.sub` 路径，没有规则求值，也没有逐行 trim/缩进。
- **影响**: `replaceRegex` 写成规则的书源，正文替换（去广告/水印）失效；末尾"　　"缩进与逐行 trim 的排版差异。第 25/29 节正文类问题相关。
- **建议用例**: `ruleContent = {"content": ".c@html", "replaceRegex": "@js:result.replace('广告','')"}` + `<div class="c">正文广告</div>` → 期望 `正文`。NovelHub 当前把整条 `@js:...` 当正则（编译失败被吞）→ 正文仍含“广告”。

#### B-7. `{{...}}` 内嵌规则的求值时机（每片段一次 vs 链前一次）
- **参考实现**: `AnalyzeRule.kt:287-289`（`makeUpRule` 在链的每一步都用**当前** `result` 调用）；`AnalyzeRule.kt:659-706`
  ```kotlin
  677: regType == jsRuleType -> {
  678:     if (isRule(ruleParam[index])) {
  679:         val ruleList = getOrCreateSingleSourceRule(ruleParam[index])
  680:         getString(ruleList).let { infoVal.insert(0, it) }
  683:     } else {
  684:         val jsEval: Any? = evalJS(ruleParam[index], result)
  ```
- **NovelHub**: `rule_engine.py:1067`（链开始前一次性替换）
  ```python
  rule = self._substitute_inner_rules(rule, raw)
  ```
  `_substitute_inner_rules:1545-1562` / `_resolve_template:1549-1560`（`self._try_eval_js_value(inner, raw)` 传的是原始 `raw`）
- **判定**: 确认不一致
- **差异说明**: Legado 在链上**每个片段**替换一次 `{{...}}`，非规则内嵌内容拿到的是**该片段之前的结果**（`evalJS(inner, result)`）；NovelHub 在链开始前用**原始输入** `raw` 替换一次。单片段规则（绝大多数 `{{book.name}}` 写法）两者等价；多片段规则（`A@B@js:{{result}}`）不等价。另外 `{{规则}}` 形式 Legado 用 `getString(ruleList)`（不传 mContent → `this.content`），与 NovelHub 传 `raw` 在字段级一致。
- **影响**: 中低。把上一步结果再交给 JS 的规则（`p@text@js:{{result}}.toUpperCase()` 这类）会拿到整页而不是上一步结果。第 9 节相关。
- **建议用例**: 规则 `p@text@js:{{result}}.toUpperCase()` + 上下文 `<p>abc</p>` → 期望 `ABC`（Legado：第三片段的 `{{result}}` = 上一步结果 `abc`）。NovelHub 在链前用 raw 替换 → 变成 `p@text@js:.toUpperCase()`，得不到 `ABC`。

#### B-8. `ruleBookInfo.tocUrl` 为空时不回落书页，而是先去“猜”一个目录页
- **参考实现**: `AnalyzeRule.kt:319-324`
  ```kotlin
  if (isUrl) {
      return if (str.isBlank()) {
          baseUrl ?: ""
      } else {
          NetworkUtils.getAbsoluteURL(redirectUrl, str)
      }
  }
  ```
  `BookInfo.kt:150-154`（补充依据）
  ```kotlin
  book.tocUrl = analyzeRule.getString(infoRule.tocUrl, isUrl = true)
  if (book.tocUrl.isEmpty()) book.tocUrl = baseUrl
  if (book.tocUrl == baseUrl) { book.tocHtml = body }
  ```
- **NovelHub**: `rule_engine.py:1024-1040`（`_eval_rule_first` 空则返回 `""`，既不回落 `baseUrl` 也不做绝对化）
  ```python
  result = self._eval_field(raw, rule)
  if result is None:
      return ""
  ...
  return next((line.strip() for line in str(result).splitlines() if line.strip()), "")
  ```
  `_extract_book_info:494-495` 用它取 `tocUrl`；下游 `book.py:99-104` 先猜目录页：`if not toc_url: toc_url = self._find_toc_url(html, identity_url)`
- **判定**: 确认不一致
- **差异说明**: Legado 的 tocUrl 规则求值为空 → 直接等于 `baseUrl`（书页），并把书页 HTML 当 `tocHtml`，**不猜**。NovelHub 引擎层返回 `""`，`book.py` 先在页面上找一个像"目录"的链接当目录页。第 31 节已加兜底（猜出来的页 0 章或全是自引用条目时回书页重跑 ruleToc，`book.py:146-176`），但“先猜一次”本身是 Legado 没有的行为。URL 绝对化差异由调用方兜底（`book.py:101-102`、`explore.py:670`）。
- **影响**: 无 `ruleBookInfo.tocUrl` 的书源会多发一次请求、并可能在猜中的站内索引页上短暂解析出错误章节。第 31 节（xbookcn 0 章）。
- **建议用例**: 书源无 `tocUrl`，书页含 `<a href="/search/label/目录索引">目录</a>` → Legado 直接在书页上跑 ruleToc；NovelHub 当前先请求 `/search/label/目录索引`。

#### B-9. `ruleContent.title` 的求值结果被丢弃
- **参考实现**: `BookContent.kt:66-78`（补充依据）
  ```kotlin
  val titleRule = contentRule.title
  if (!titleRule.isNullOrBlank()) {
      val title = analyzeRule.runCatching { getString(titleRule) }...
      if (!title.isNullOrBlank()) {
          bookChapter.title = title
          bookChapter.titleMD5 = null
          appDb.bookChapterDao.update(bookChapter)
  ```
- **NovelHub**: `rule_engine.py:503-507`
  ```python
  title_rule = rules.get("title", "")
  if title_rule:
      extracted = self._eval_rule_str(raw, title_rule)
      if extracted:
          self._variables["contentTitle"] = extracted
  ```
  （全目录检索确认：`contentTitle` 只在此处写入，包内无任何读取点；`get_variable` 亦无调用者）
- **判定**: 确认不一致
- **差异说明**: Legado 在正文规则里求出的标题**直接更新 `bookChapter.title` 并写库**；NovelHub 只把它塞进引擎实例的 `_variables["contentTitle"]`，没有任何调用方读取 → 结果被丢弃。
- **影响**: 目录标题不可靠、真标题只在正文页的书源（`ruleContent.title` 常见于此类站），章节标题保持目录里的值（常是"正文"/"第X页"）。第 23 节空章节名相关问题域。
- **建议用例**: `ruleContent = {"title": "h1@text", "content": ".c@text"}` + `<h1>真正的标题</h1><div class="c">正文</div>` → 期望章节标题更新为 `真正的标题`。NovelHub 当前不更新（只写 `_variables["contentTitle"]`）。

#### B-10. `@put` 变量的作用域/生命周期不同（跨请求丢失）
- **参考实现**: `AnalyzeRule.kt:740-749`
  ```kotlin
  fun put(key: String, value: String): String {
      ...
      chapter?.putVariable(key, value)
          ?: book?.putVariable(key, value)
          ?: ruleData?.putVariable(key, value)
          ?: source?.put(key, value)
      return value
  }
  ```
  （读取侧 `AnalyzeRule.kt:754-769` 依次查 chapter → book → ruleData → source）
- **NovelHub**: `rule_engine.py:1063-1066`
  ```python
  rule, put_map = self._split_put(rule)
  for k, v in put_map.items():
      val = self._eval_field(raw, v)
      self._variables[k] = str(val) if val is not None else ""
  ```
  读写只有一层实例字典：`rule_engine.py:1631-1635`（`get_variable`/`put_variable` 只碰 `self._variables`），`set_book:327-333` 只写 `self._variables["book"]`，没有把变量回写进 book 对象的路径。
- **判定**: 确认不一致
- **差异说明**: Legado 的 `@put` 写进 Book/BookChapter 的 `variableMap`（随书持久化，之后任何请求/任何一次同步都能 `@get`/`{{}}` 读到）；NovelHub 只写引擎实例的 `_variables`，而章节正文用独立引擎（`chapter.py:37` 注释即说明 baseUrl/bookUrl 这类页面级变量是分开存的；`docs/codex-handoff.md` 第 3 节亦记「每章独立引擎」）。同一次 `fetch_book` 内（`book.py:87` 解析详情 → `book.py:122` 解析目录，同一 engine）仍可见。
- **影响**: 「详情页 `@put` 一个 id，正文/目录请求再取」的写法失效（取到空串）→ 目录/正文 URL 拼不出来。中高，取决于书源是否跨请求传变量。
- **建议用例**: 详情页规则 `ruleBookInfo.intro: "@put:{aid:'42'}@css:.x@text"`，随后用**新引擎**跑 `ruleContent.content: "{{aid}}"` → Legado（同一本书的变量表）得到 `42`；NovelHub 得到 `""`。

#### B-11. `name` 为空的搜索结果条目：Legado 直接丢弃，NovelHub 照常返回
- **参考实现**: `yuedu/app/src/main/java/io/legado/app/model/webBook/BookList.kt:220-289`（补充依据）
  ```kotlin
  220: searchBook.name = BookHelp.formatBookName(analyzeRule.getString(ruleName))
  222: if (searchBook.name.isNotEmpty()) {
  ...   （author/kind/wordCount/intro/cover/bookUrl 全部在 if 内求值）
  286:     return searchBook
  287: }
  288: return null
  ```
- **NovelHub**: `rule_engine.py:455-476`
  ```python
  for item in items:
      entry: dict[str, Any] = {}
      self._js_content = item
      for field in field_names:
          rule = rules.get(field, "")
          if rule:
              ...
      results.append(entry)          # 不判断 name 是否为空
  ```
- **判定**: 确认不一致
- **差异说明**: Legado 把“name 为空”视为该条无效：不仅丢弃条目，而且**不再求值后续字段**（省掉 N 次解析）。NovelHub 一律求值全部字段并返回条目，空名条目继续流向上层。
- **影响**: 空名条目是否入库取决于上层是否再过滤（本次未核 `explore.py::_normalize_search_items` 对 name 的处理）——未过滤则出现空书名书、`no usable metadata`。相对地 NovelHub 每条目多做最多 12 次字段求值（性能）。
- **建议用例**: `ruleSearch = {"bookList": ".row", "name": ".title@text", "author": ".au@text"}` + `<div class="row"><span class="au">作者</span></div>`（无 `.title`）→ Legado 返回 0 条；NovelHub 当前返回 1 条（`name=""`、`author="作者"`）。

#### B-12. `kind` 多值分隔符：Legado `,`，NovelHub `\n`
- **参考实现**: `BookInfo.kt:85-88`（补充依据）
  ```kotlin
  analyzeRule.getStringList(infoRule.kind)?.joinToString(",")?.let { if (it.isNotEmpty()) book.kind = it }
  ```
  （搜索侧同：`BookList.kt:233`）`getStringList` 见 `AnalyzeRule.kt:170-244`
- **NovelHub**: `rule_engine.py:487-497`（`_extract_book_info` 的 `kind` 走 `_eval_field`）→ `_eval_css:1195-1198`
  ```python
  all_r: list[str] = []
  for r in results:
      all_r.extend(r)
  return "\n".join(all_r) if all_r else None
  ```
- **判定**: 确认不一致
- **差异说明**: 同一字段多值合并符不同：Legado 用 `getStringList(...)?.joinToString(",")`（逗号），NovelHub 的 `_eval_field`/`_eval_css` 用换行拼接。`getStringList` 与 `getString` 在 Legado 里也分别联动 `@put`/`isUrl` 处理，这里只报可确证的分隔符差异。
- **影响**: 低-中：`kind` 变成多行文本，前端标签/分类解析与 Legado 不一致。
- **建议用例**: `ruleBookInfo = {"kind": "p.k@text"}` + `<p class="k">都市</p><p class="k">言情</p>` → 期望 `都市,言情`；NovelHub 当前得到 `都市\n言情`。

#### B-20. `@put:` 非规范 JSON（`{k:v}`）的宽松解析缺失
- **参考实现**: `AnalyzeRule.kt:414-428`
  ```kotlin
  val putJson = GSONStrict.fromJsonObject<Map<String, String>>(putJsonStr).getOrNull()
  if (putJson != null) { putMap.putAll(putJson); continue }
  GSON.fromJsonObject<Map<String, String>>(putJsonStr).getOrNull()?.let {
      if (!loggedNonStandardJSON) { Debug.log("≡@put 规则 JSON 格式不规范，请改为规范格式") }
      putMap.putAll(it)
  }
  ```
  （`GSONStrict` 用 `Strictness.STRICT`，`GSON` 是默认宽松解析：`yuedu/app/src/main/java/io/legado/app/utils/GsonExtensions.kt:42-57`）
- **NovelHub**: `rule_engine.py:1533-1541`
  ```python
  except json.JSONDecodeError:
      try:
          cleaned = re.sub(r"(\w+):", r'"\1":', json_str)
          d = json.loads(cleaned)
  ```
- **判定**: 确认不一致（低影响）
- **差异说明**: NovelHub 的兜底只给**键**补引号，`{"k":v}`、`{k:v}` 这类值不带引号的写法仍然 `JSONDecodeError` → 静默丢弃；Legado 第二遍用宽松 GSON 解析成功并存入变量（只打一条不规范提示）。
- **影响**: 用非规范 `@put` 的书源变量丢失 → 依赖该变量的 `@get`/`{{}}` 为空（与 B-5 叠加）。
- **建议用例**: 规则 `@put:{aid:42}@css:.x@text` → 期望 `_variables["aid"] == "42"`。NovelHub 当前 `aid` 不存在。

---

### 已正确（两侧代码均已核对）

#### B-13. `{{book.name}}` / `{{chapter.title}}` 无上下文 → 空串（不是整页 HTML）
- **参考实现**: `AnalyzeRule.kt:776-787`（`bindings["book"] = book`；按 URL 打开书页时 `ruleData` 里没有书名）+ `AnalyzeRule.kt:684`（非规则内嵌内容 `evalJS(inner, result)`）
- **NovelHub**: `rule_engine.py:1577-1580`
  ```python
  elif rest and name in _CONTEXT_OBJECT_NAMES:
      # ``book.name`` before any book is known: stay empty.
      return ""
  ```
  以及 `_try_eval_js_value:1603-1629`（`if isinstance(raw, str) and result == raw: return None`，不再原样返回输入）
- **判定**: 已正确
- **差异说明**: NovelHub 返回 `""`，与第 9 节要求的语义一致；Legado 这一路在 `book` 为 null 时会给 `book.name` 抛 TypeError（错误路径），两者都不会把整页 HTML 当模板值。已有回归测试 `backend/tests/test_rule_engine_legado.py:334-347`。
- **建议用例**: `engine._substitute_inner_rules("{{book.name}}", "<p>x</p>")` → 期望 `""`。

#### B-14. `<...>` 页数占位符
- **参考实现**: `AnalyzeUrl.kt:670` — `private val pagePattern = Pattern.compile("<(.*?)>")`；`AnalyzeUrl.kt:197-207`
  ```kotlin
  ruleUrl = if (page < pages.size) { ruleUrl.replace(matcher.group(), pages[page - 1].trim()) }
            else { ruleUrl.replace(matcher.group(), pages.last().trim()) }
  ```
- **NovelHub**: `rule_engine.py:1686-1696`
  ```python
  pages = [p.strip() for p in m.group(1).split(",")]
  page_num = int(page_str)
  if 1 <= page_num <= len(pages): return pages[page_num - 1]
  return pages[-1]
  ```
- **判定**: 已正确（1-based 取页、超界取 last、逗号切分均一致；且与 Legado 一样在内嵌 `{{}}` 替换之后处理）
- **建议用例**: `_substitute("https://x/<1,2,3>.html", page="9")` → `https://x/3.html`。

#### B-15. `,{...}` URL 选项后缀（method/POST body/headers/charset/webView）
- **参考实现**: `AnalyzeUrl.kt:669` — `val paramPattern: Pattern = Pattern.compile("\\s*,\\s*(?=\\{)")`；解析见 `AnalyzeUrl.kt:213-254`；`UrlOption` 见 `AnalyzeUrl.kt:682-834`
- **NovelHub**: `rule_engine.py:336-352`（`build_search_url`/`build_explore_url`/`build_book_url` 只做 `_substitute`，**不**解析后缀）；真正解析在 `urls.py:305-360`（`_parse_url_options`/`_split_options_suffix`）、`transport.py:666-677`、`transport.py:869-895`、`explore.py:511`、`explore.py:743`
- **判定**: 已正确（在 rule_engine 之外实现并生效，勿当成缺口）
- **建议用例**: `searchUrl` 末尾 `,{"method":"POST","body":"k={{key}}"}` → 期望发出 POST + 该 body（由 explore.py/transport.py 生效）。

#### B-16. 模式前缀分派（`@xpath:` / `@json:` / `@css:` / `@@` / `$[` / `/`）
- **参考实现**: `AnalyzeRule.kt:546-579`
  ```kotlin
  548: ruleStr.startsWith("@CSS:", true) -> { mode = Mode.Default; ruleStr }
  553: ruleStr.startsWith("@@") -> { mode = Mode.Default; ruleStr.substring(2) }
  558: ruleStr.startsWith("@XPath:", true) -> { mode = Mode.XPath; ruleStr.substring(7) }
  563: ruleStr.startsWith("@Json:", true) -> { mode = Mode.Json; ruleStr.substring(6) }
  573: ruleStr.startsWith("/") -> { mode = Mode.XPath; ruleStr }
  ```
- **NovelHub**: `rule_engine.py:1083-1101`（`@xpath:`→`rule[7:]`、`@json:`→`rule[6:]`、`@@`→`rule[2:]`、`/`→XPath、`$.`/`$[`→JSONPath）
- **判定**: 已正确（前缀长度逐一核对一致；`@CSS:` 见未覆盖）
- **建议用例**: `_eval_field(html, "@json:$.a")`、`_eval_field(html, "@@div")` 与 Legado 同结果。

#### B-17. `@js:` / `<js></js>` 在规则链里的切分
- **参考实现**: `yuedu/app/src/main/java/io/legado/app/constant/AppPattern.kt:8` — `Pattern.compile("<js>([\\w\\W]*?)</js>|@js:([\\w\\W]*)", CASE_INSENSITIVE)`（`@js:` 分支贪心到串尾）；使用见 `AnalyzeRule.kt:499-516`
- **NovelHub**: `rule_engine.py:1120-1146`
  ```python
  js_start = rule.find("@js:")
  if js_start >= 0:
      before_js = rule[:js_start].strip()
      js_fragment = rule[js_start:].strip()
      return ([before_js] if before_js else []) + [js_fragment]
  ```
- **判定**: 已正确（`@js:` 之后整段作为一个 JS 片段，与 Legado 的贪心 `[\w\W]*` 等价；`<js>…</js>` 用非贪婪，亦与 Legado 一致）。第 8 节「元素规则 + `@js:`」场景已有测试 `test_rule_engine_legado.py:258-289`。
- **建议用例**: 规则 `//ul/li@js:java.put('a','1');result` 的片段切分为 `["//ul/li", "@js:java.put('a','1');result"]`。

#### B-18. `@put:{...}` 合法 JSON 形式
- **参考实现**: `AnalyzeRule.kt:880` — `Pattern.compile("@put:(\\{[^}]+?\\})", CASE_INSENSITIVE)`；`AnalyzeRule.kt:408-431`（`splitPutRule`）+ `AnalyzeRule.kt:399-403`（`put(key, getString(value))`，值本身也按规则求值）
- **NovelHub**: `rule_engine.py:1524-1543`（`re.sub(r"@put:\s*(\{[^}]+\})", ...)` + `json.loads`）+ `rule_engine.py:1063-1066`（`self._eval_field(raw, v)`，值同样按规则求值）
- **判定**: 已正确（大小写不敏感、从规则串里摘除 `@put:`、值按规则求值，三者一致）；非规范 JSON 见 B-19
- **建议用例**: 规则 `@put:{"a":"b"}@css:.x@text` → 变量 `a=b` 且规则退化为 `@css:.x@text`。

#### B-19. `nextContentUrl` / `nextTocUrl` 多值 + 绝对化
- **参考实现**: `AnalyzeRule.kt:226-241`
  ```kotlin
  if (result is String) { result = result.split("\n") }
  if (isUrl) { ... val absoluteURL = NetworkUtils.getAbsoluteURL(redirectUrl, url.toString())
              if (absoluteURL.isNotEmpty() && !urlList.contains(absoluteURL)) urlList.add(absoluteURL) }
  ```
- **NovelHub**: `rule_engine.py:1042-1058`
  ```python
  values = [line.strip() for line in str(result).splitlines() if line.strip()]
  if is_url:
      return [urljoin(base_url or self.base_url, v) for v in values]
  ```
  调用方 `get_next_content_urls:390-404` / `get_next_toc_urls:414-428`
- **判定**: 已正确（按行拆分、逐条绝对化一致；Legado 额外去重，NovelHub 不去重但 `chapter.py` 用 `seen_content_urls` 去重，未见行为差异）
- **建议用例**: 规则返回 `"/p2.html\n/p2.html"` + `current_url="https://a/p1.html"` → 期望 `https://a/p2.html`（重复项由调用方去重）。

---

### 未覆盖 / 无法判定（本次未核，需后续确认；不倒推结论）

1. **`formatJs`**：NovelHub `_extract_list:471-473` 只对 `chapterName` 调用 `_try_format_js`。Legado 侧 `TocRule.kt:14` 有 `formatJs` 字段，但它在 `BookChapterList.kt` 里作用于哪些字段、传入的 JS 上下文（`result` 是整条 entry 还是单个值）**本次未读** → 无法判定。
2. **`@CSS:` 前缀**：Legado 保留整串（`AnalyzeRule.kt:548-551`），NovelHub 剥离（`rule_engine.py:1091-1092`）。是否等价取决于 `AnalyzeByJSoup` 是否自己剥前缀（属他人范围，未读）→ 无法判定。
3. **`is_book_detail_url` / `bookUrlPattern` 匹配方式**：NovelHub 用 `re.search`（`rule_engine.py:354-362`），另有 `urls.py:223` 一份实现。Legado 侧 `bookUrlPattern` 的匹配点（`matches` vs `find`）未找到调用处读取 → 无法判定。
4. **`get_variable`/`put_variable` 与 JS 侧 `java.get`/`java.put` 的绑定关系**：`jsoup_shim.js:728-732` 的 `get`/`put` 走 shim 自己的 `_cache`，与引擎 `_variables` 是否互通未核（`jsoup_shim.js`/`js_runtime.py` 属他人范围）→ 无法判定。
5. **`ruleContent.title` 之外**：`_extract_content` 未实现 Legado `getString(contentRule.content, unescape=false)` + `HtmlFormatter.formatKeepImg` + 实体反转义（`BookContent.kt:179-183`）这一链；文本抽取/反转义属他人范围 → 未判定。
6. **`{{...}}` 里任意 JS**（如 URL 模板 `{{randomUUID()}}`）：Legado `AnalyzeUrl.replaceKeyPageJs:183-195` 用 `evalJS` 求值任意表达式；NovelHub `_substitute:1729-1734` 只替换已知变量名，否则原样保留 → 差异方向明确，但影响面（真实书源是否这样写）未核 → 未判定。
7. 本次**未读**：`AnalyzeByJSoup.kt`（文本抽取/getString0 细节）、`AnalyzeByXPath.kt`、`AnalyzeByJSonPath.kt`、`BookChapterList.kt`、`WebBook.kt`、`RuleDataInterface.kt`、`rule_engine.py` 的 `_get_elements*`/`_select_elements_chain`/`_parse_legado_index`/`_xpath_*`（他人范围）。

---


---

## 附录 D. D 轴完整逐条表（JSONPath / 正则 / 文本抽取）

**来源与可信度声明**：本附录由一次并行评审（与本文档同一套规范、同样的只读约束）产出，
**逐字并入、未改写**。该评审文件自身登记了全部引用锚点与未覆盖范围（其文末 G 节）。
采用任一条目前请自行核对它给出的双侧 `文件:行`。

**主代理已独立复核的条目**：

| 条目 | 复核方式 | 结论 |
|---|---|---|
| D-01 XPath 元素节点只返回首个直接文本 | 真实引擎实测：`//div[@class='i']` → `'LEAD'`（`TAIL` 与子元素文本全丢），`/text()` 才 `'LEAD\nTAIL'` | ✅ 成立 |
| D-13 多值属性使整条字段空 | 真实引擎实测：`.body@class` → `None` | ✅ 成立 |
| D-14 去重作用域过宽 | 真实引擎实测：两个内容相同的 span + `span@text` → `'same'` | ✅ 成立，**并据此更正了本文档的 M-10** |
| D-10 / D-11 / D-12 | 与主代理 M-8 / M-7 / M-9 **各自独立得出同一结论**，且都实测复现 | ✅ 双重印证 |
| D-02 / D-03 / D-04 / D-07 / D-08 / D-20 / D-28 | 采信其探针实测，未由主代理重跑 | ⚠️ 未独立复核 |


## D. JSONPath / 正则替换 / 从元素取值 —— Legado(权威) ↔ NovelHub 差异表

- 范围：`yuedu/` 为规范（GPL-3.0，**只读**，未复制任何代码），`backend/app/crawler/plugins/yuedu/rule_engine.py` 为被测实现。
- 不在本表范围（他人负责）：`||`/`&&`/`%%` 列表切分与 XPath 位置谓词总流程、`AnalyzeRule` 规则调度、`AnalyzeUrl`、`jsoup_shim.js`/`js_runtime.py`。
- NovelHub 侧条目均先在**本机真实运行引擎**（`python -` 直接调用 `YueduRuleEngine`，未改任何产品代码）复核过"当前得到"，下方"当前"即为实测输出。
- 路径缩写：
  - `JSoup` = `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByJSoup.kt`（实际 524 行）
  - `JSonPath` = `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByJSonPath.kt`（172 行）
  - `XPath` = `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByXPath.kt`（155 行，超出指定清单，为判定 XPath 取值后缀所必需）
  - `Rule` = `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeRule.kt`（907 行）
  - `BookContent` = `yuedu/app/src/main/java/io/legado/app/model/webBook/BookContent.kt`
  - `Engine` = `backend/app/crawler/plugins/yuedu/rule_engine.py`（1949 行）

---

#### D-01. XPath 字段规则取"元素节点"时只返回**首个直接文本**（正文/简介为空）
- **参考实现**: `XPath:133-140` — `getResult(rule)?.let { return TextUtils.join("\n", it) }`（元素节点交给 `JXNode` 序列化，是"整个节点"的内容）；`XPath:99-101` 列表分支用 `it.asString()`
- **NovelHub**: `Engine:1331-1337` — `if isinstance(el, str): texts.append(el.strip())` / `elif hasattr(el,"text_content"): ...` / `else: texts.append((el.text or "").strip())`
- **判定**: 确认不一致
- **差异说明**: 实测 `lxml.etree.HTML(...).xpath(...)` 返回的是 `lxml.etree._Element`，**没有** `text_content`（实测 `hasattr(el,"text_content") == False`），于是永远走 `el.text` —— lxml 的 `.text` 只是"第一个子元素之前的文本"。`<div>LEAD<p>a</p>TAIL<p>b</p></div>` + 规则 `//div[@class='i']` 实测得到 `'LEAD'`：**tail 文本与全部子节点内容被丢弃**。Legado 侧 JXNode 对元素节点的序列化（outerHtml 还是全文本）因 `seimicrawler` 未 vendor 无法逐字核对，但它不可能是"只有首个直接文本节点"——否则所有写 `//div[@id='content']`（不带 `/text()`）的源在 Legado 里也会拿不到正文。
- **影响**: **正文为空/只剩一句** 的头号来源：`ruleContent.content = //div[@id='xxx']`、`ruleBookInfo.intro = //div[@class='intro']`、`name`/`author` 用 XPath 选元素（不写 `/text()`）时，值被截成元素开头那点文字；嵌套型简介（`<p>` 段落）直接得到空串。关联 handoff §3（`//li/div[@class='info']/…/text()`）。
- **建议用例**: 输入 `<div class="i">LEAD<p>a</p>TAIL<p>b</p></div>` + 规则 `//div[@class='i']` → 期望（Legado）至少含 `a`、`b` 的完整内容；NovelHub 当前得到 `LEAD`（`//div[@class='i']//text()` 才得到 `LEAD\na\nTAIL\nb`）

#### D-02. JSONPath 规则后的 `##` 正则替换**完全不生效**（且 pattern 含 `.` 时规则被切碎）
- **参考实现**: `Rule:708-718` — `val ruleStrS = rule.split("##"); rule = ruleStrS[0].trim(); if (ruleStrS.size > 1) replaceRegex = ruleStrS[1]; if (ruleStrS.size > 2) replacement = ruleStrS[2]`；`Rule:306-308` — `if (result != null && sourceRule.replaceRegex.isNotEmpty()) result = replaceRegex(result.toString(), sourceRule)`（**模式分派之前**就把 `##` 段剥掉了）
- **NovelHub**: `Engine:1795-1798` — `for rp in regex_parts[1:]: if rp.strip(): current = self._apply_replace_regex_simple(current, rp.strip())`；`Engine:1941-1948` — `if "##" in transform: ... return text`
- **判定**: 确认不一致
- **差异说明**: `_jsonpath` 已用 `split("##")` 把 `##` 当分隔符拆掉，再把**不含 `##` 的 pattern** 传给 `_apply_replace_regex_simple`，而后者要求 `transform` 里含 `"##"`，否则 `return text` —— 于是 JSON 规则的所有 `##` 替换（2 段、3 段、4 段）都是空操作。实测 `{"data":{"name":"A&amp;B"}}` + `$.data.name##A##B` → `A&amp;B`（Legado → `B&amp;B`）。另外 `_jsonpath` 用 `re.split(r"(?<!\\)\.", path)`（`Engine:1756`）切段，pattern 里出现**未转义的 `.`**（如 `##第(.)章##`）会把规则切碎。
- **影响**: 所有 JSON 接口源里 `$.x##…##…` 形式的书名/作者/章节名/分类清洗全部失效 → 书名带 `《》`/后缀、章节名带 `正文` 前导、`kind` 带噪声；handoff §3 表格"JSONPath 语义差异"行、§8(2)。
- **建议用例**: 输入 `{"d":{"t":".x."}}` + 规则 `$.d.t##\.##` → 期望 `x`（NovelHub 当前得到 `.x.`）

#### D-03. JSONPath 取到数组/对象时返回 Python repr（`['p', 'q']`）而不是按行拼接
- **参考实现**: `JSonPath:44-50` — `val ob = ctx.read<Any>(rule); result = if (ob is List<*>) ob.joinToString("\n") else ob.toString()`
- **NovelHub**: `Engine:1304` — `resolved = str(val) if val is not None else ""`（对 list/dict 直接 `str()`）
- **判定**: 确认不一致
- **差异说明**: Legado 对列表逐项用 `"\n"` 拼接（→ `p\nq`）；NovelHub 得到 Python 字面量 `['p', 'q']`（`{'a': 1}`）。`_eval_json` 全程把结果压成字符串（`Engine:1298-1311`），列表语义一并丢失。
- **影响**: 值为数组的字段（tags/kind/多作者/封面列表）写进"书名/作者/分类"里会带方括号和引号；`$.data.list[*].name` 这类继续下钻的写法才不会中招。
- **建议用例**: 输入 `{"data":{"tags":["p","q"]}}` + 规则 `$.data.tags` → 期望 `p\nq`（当前 `['p', 'q']`）

#### D-04. JSONPath 负下标 `[-1]` 被静默忽略 → 字段为空
- **参考实现**: `JSonPath:45` — `ctx.read<Any>(rule)`（Jayway JsonPath 原生支持 `[-1]` 取末元素）；`Rule:568` — `isJSON || ruleStr.startsWith("$.")` 决定 Mode.Json
- **NovelHub**: `Engine:1764-1777` — `array_match = re.match(r"^(\w+)?\[(\*|\d+|[:,\d]+)\]$", seg)` … `elif idx.isdigit(): current = current[i] if i < len(current) else None`
- **判定**: 确认不一致
- **差异说明**: 该字符类只有 `*`/`\d+`/`[:,\d]+`，**不含 `-`**，`list[-1]` 整体匹配失败 → 退化成 `dict.get("list[-1]")` → `None`；NovelHub 既不报错也不回退。实测 `{"data":{"list":[{"t":"x"},{"t":"y"}]}}` + `$.data.list[-1].t` → 空（期望 `y`）。
- **影响**: 书源常用 `$.data.list[-1].url` 取"最新章节/目录地址/最后一页"，字段全空 → 目录 0 章、tocUrl 空（handoff §31 的"目录猜错"会更容易触发）。关联 handoff §13 的位置谓词类问题。
- **建议用例**: 输入 `{"list":[{"u":"a"},{"u":"b"}]}` + 规则 `$.list[-1].u` → 期望 `b`（当前 空）

#### D-05. JSONPath 数组切片 `[0:2]` / `[1:]` 未实现（静默返回整个数组）
- **参考实现**: `JSonPath:45` — Jayway 支持 `[start:end:step]`
- **NovelHub**: `Engine:1764-1777` — 正则能匹配 `[0:2]`（`[:,\d]+`），但分支里只处理 `idx == "*"` 与 `idx.isdigit()`，切片直接 `continue`，`current` 仍是整个数组
- **判定**: 尚未实现
- **差异说明**: 切片被"忽略"而不是"报错"，随后要么被下一段按列表 fan-out，要么被 `str()` 成 repr，得到错误值。
- **影响**: `$.data.list[0:10]`（取前 10 章/前 10 本）得到全部或 repr；影响章节列表数量与书名列表。
- **建议用例**: 输入 `{"tags":["p","q","r"]}` + 规则 `$.tags[0:2]` → 期望 `p\nq`（当前 `['p', 'q', 'r']`）

#### D-06. JSONPath 递归下降 `$..x` 未实现
- **参考实现**: `JSonPath:45` — Jayway 支持 `$..field`
- **NovelHub**: `Engine:1752-1756` — `if path.startswith("$."): path = path[2:]` 后 `re.split(r"(?<!\\)\.", path)`
- **判定**: 尚未实现
- **差异说明**: `$..t` → `path[2:] == ".t"` → 段为 `["", "t"]` → `current.get("")` → `None` → 返回 None（无任何提示）。
- **影响**: 用 `$..title`/`$..url` 的 JSON 源取书名/章节地址全部为空（整本书 0 章）。
- **建议用例**: 输入 `{"a":{"t":"x"},"b":{"t":"y"}}` + 规则 `$..t` → 期望 `x\ny`（当前 空）

#### D-07. JSONPath 过滤器：只能作为整条路径、只支持 `&&`+单层键；非列表时返回字面量 `"[]"`
- **参考实现**: `JSonPath:34` + `JSonPath:44-45` — `RuleAnalyzer(rule, true)` 后 `ctx.read<Any>(rule)`（Jayway 过滤器可出现在任意层级，支持 `||`、`in`/`nin`、嵌套路径 `@.a.b`、`=~`）
- **NovelHub**: `Engine:1744-1749` — `filter_match = re.match(r"^[.$]?\s*\[\?\s*\((.*)\)\s*\]\s*$", path.strip())` … `if isinstance(obj, list): return [...] ` / `return []`；`Engine:1808-1814` — 只按 `re.split(r"\s*&&\s*", expr)` 拆原子；`Engine:1816-1822` — 只认 `@.key` / `@['k']` 单层
- **判定**: 确认不一致（其中 `||`、嵌套键、`in`、`=~` 属**尚未实现**）
- **差异说明**: ① 过滤器后面还有段（`$.data.list[?(@.vip==true)].name`）时整条不再命中 `filter_match`，并且 `re.split(r"(?<!\\)\.")` 会把 `@.vip` 的 `.` 当路径分隔符切碎 → 返回空；② 过滤器结果经 `Engine:1304` `str()` 变 repr；③ `obj` 不是列表时返回 `[]`，经 `str()` 得到**非空字符串 `"[]"`**（`Engine:1305` 认为非空），字段值真的会变成 `[]`。
- **影响**: 用过滤器筛 VIP/最新卷的书源，书名与章节列表整段为空；或者书名/作者被写成字面量 `[]`。
- **建议用例**: (a) `{"data":{"list":[{"n":"a","vip":true}]}}` + `$.data.list[?(@.vip==true)].n` → 期望 `a`（当前 空）；(b) `{"t":1}` + `$[?(@.t==1)]` → 期望 空（当前 `[]`）

#### D-08. 四段式 `##regex##replacement###` 的 replaceFirst 语义不同（Legado 只保留首个匹配）
- **参考实现**: `Rule:441-452` — `if (matcher.find()) { matcher.group(0)!!.replaceFirst(regex, replacement) } else { "" }`；`Rule:716-718` — `if (ruleStrS.size > 3) replaceFirst = true`
- **NovelHub**: `Engine:1370-1379` — `replace_first = len(parts) > 3` … `return re.sub(pattern, replacement, text, count=1)`
- **判定**: 确认不一致
- **差异说明**: 触发条件一致（注意 `##a##b##` 也会被 `split("##")` 成 4 段），但操作不同：Legado 返回的是"**首个匹配片段本身**被替换后的结果"（其余文本丢弃、无匹配返回空串），NovelHub 返回"整段文本，只替换第一处"。
- **影响**: 用四段式清洗**正文**或书名时两侧文本完全不同：Legado 会把整章截成一小段（书源作者本意），NovelHub 保留全文；反之依赖"截取首个匹配"的规则（如 `##第(\d+)章(.+)###`）在 NovelHub 下拿到整行。（handoff §3"正则替换语义"行）
- **建议用例**: 输入 `abc123def456` + 规则 `##\d+##X####` → 期望 `X`（当前 `abcXdef456`）

#### D-09. 替换串里的 `$1` 不会被展开
- **参考实现**: `Rule:455-458` — `return result.replace(regex, replacement)`（Kotlin `Regex.replace`，replacement 中 `$1` 展开为捕获组）；`Rule:626-654`/`Rule:666-675` — 规则里 `$1` 还能作为"上一步列表元素"记号（`regType > defaultRuleType` → 取 `result[regType]`）
- **NovelHub**: `Engine:1374-1377` — `re.sub(pattern, replacement, text)`（Python 用 `\1`/`\g<1>`，`$1` 是普通字符）；全文件 grep 无 `\$n` 处理，`_split_rule_chain`（`Engine:1110-1146`）也不拆 `$n`
- **判定**: 尚未实现
- **差异说明**: Legado 两种 `$1` 语义（替换串反向引用、正则模式下的捕获组选择）在 NovelHub 都没有。
- **影响**: 用 `$1` 拼接 URL/章节名的规则会输出字面量 `$1`（字段值可见地错）；用 `:regex` + `$1` 的 Mode.Regex 链路整体缺失。
- **建议用例**: 输入 `ch12` + 规则 `##ch(\d+)##[$1]##` → 期望 `[12]`（当前 `[$1]`）

#### D-10. `@html` 取**内层** HTML，Legado 取 `outerHtml()`
- **参考实现**: `JSoup:260-267` — `elements.select("script").remove(); elements.select("style").remove(); val html = elements.outerHtml()`
- **NovelHub**: `Engine:1268-1271` — `for tag in el.find_all(["script","style"]): tag.decompose()`；`return el.decode_contents()`
- **判定**: 确认不一致
- **影响**: 简介/正文用 `@html` 时最外层元素与其 class/style 丢失 → 阅读器里样式与图片包裹层级不同；若下游按容器选择器再处理会失配。
- **建议用例**: 输入 `<div class="b">A:<a>B</a>(C)</div>` + 规则 `div@html` → 期望 `<div class="b">A:<a>B</a>(C)</div>`（当前 `A:<a>B</a>(C)`）

#### D-11. `@text` 用换行拼接，Legado 用空格归一化
- **参考实现**: `JSoup:232-237` — `element.text()`（jsoup 归一化：内联子节点之间不插分隔符，块级/`<br>` 处补一个空格）
- **NovelHub**: `Engine:1260-1261` — `return el.get_text("\n", strip=True)`
- **判定**: 确认不一致
- **影响**: 书名/作者/章节名里被塞进换行（`A:<a>B</a>` → `A:\nB`）；走 `_eval_rule_first` 的 URL 字段（`Engine:1034-1036` 只取第一个非空行）会**被截断**成半截书名/半截地址（handoff §3"从元素取值"行）。
- **建议用例**: 输入 `<div>A:<a>B</a></div>` + 规则 `div@text` → 期望 `A:B`（当前 `A:\nB`）

#### D-12. `@ownText` 与 `@textNodes` 实现雷同（应为拼接后的**单个**字符串）
- **参考实现**: `JSoup:253-258` — `element.ownText()`；`JSoup:239-251` — `element.textNodes()` 才逐节点 `trim` 后用 `"\n"` 拼接
- **NovelHub**: `Engine:1265-1267`（ownText）与 `Engine:1262-1264`（textNodes）**代码完全相同**：`[t.strip() for t in el.find_all(string=True, recursive=False) if t.strip()]` + `"\n".join`
- **判定**: 确认不一致
- **差异说明**: 实测 `<div>A:<a>B</a>(C)</div>` 的 `@ownText` 与 `@textNodes` 都得到 `A:\n(C)`；Legado `ownText()` = `A:(C)`（同一空白归一化规则）。
- **影响**: 用 `@ownText` 取书名/章节名（避开子元素噪声的常见写法）会多出换行，配合 D-11 的 `_eval_rule_first` 截断问题。
- **建议用例**: 输入 `<div>A:<a>B</a>(C)</div>` + 规则 `div@ownText` → 期望 `A:(C)`（当前 `A:\n(C)`）

#### D-13. `@class` 等多值属性返回 list → 整条规则取值失败（字段为空）
- **参考实现**: `JSoup:270-277` — `val url = element.attr(lastRule); if (url.isBlank() || textS.contains(url)) continue; textS.add(url)`（jsoup `attr` 对 `class` 返回 `"a b"` 字符串）
- **NovelHub**: `Engine:1274-1275` — `val = el.get(attr); return val if val else ""`（bs4 对 `class`/`rel` 等多值属性返回 **list**）→ `Engine:1230`/`Engine:1237-1243` 里 `val not in seen` 抛 `TypeError: unhashable type: 'list'` → 被 `Engine:1179-1180` 的 `except Exception` 吞掉 → 整条 `None`
- **判定**: 确认不一致
- **差异说明**: 实测 `<div class="body strikeout">…</div>` + `div@class` → NovelHub **空**，Legado `body strikeout`。
- **影响**: 用 `@class` 取分类/标签/状态（漫画/连载标记）的字段全空；handoff §16（小说/漫画判定）依赖 kind/tags，源里用 `@class` 时判定会退化。
- **建议用例**: 输入 `<div class="body strikeout">x</div>` + 规则 `div@class` → 期望 `body strikeout`（当前 空）

#### D-14. 去重范围过宽：NovelHub 对 `@text` 也去重
- **参考实现**: `JSoup:274` — `if (url.isBlank() || textS.contains(url)) continue`（**只**在属性分支去重）；`JSoup:232-267` 的 text/textNodes/ownText/html 分支不去重
- **NovelHub**: `Engine:1237-1243` — 对 `results` 里所有值（含 text/html）做 `seen` 去重
- **判定**: 确认不一致
- **影响**: 重复的章节目录项、`%%` 交错取值、需要保留重复项的列表被压成一条（`<i>a</i><i>a</i>` → Legado 两条，NovelHub 一条）；对"章节数"类统计有直接影响。
- **建议用例**: 输入 `<i>a</i><i>a</i>` + 规则 `i@text` → 期望 `a\na`（当前 `a`）

#### D-15. `##` 替换的作用对象不同：逐元素 vs 拼接后的整串
- **参考实现**: `Rule:306-308`（`getString`：先取值、把列表 join 成整串，再整体 `replaceRegex`）；`Rule:214-222`（`getStringList`：对列表**每一项**替换）
- **NovelHub**: `Engine:1231-1232` — 在 `_eval_css_single` 的元素循环里**逐元素**先替换，之后才 `Engine:1198` `"\n".join`
- **判定**: 确认不一致
- **差异说明**: `^`/`$`/跨行模式差异明显：`##^a##X##` 在 Legado 只改整串开头一处，NovelHub 每个元素各改一次。
- **影响**: 章节名/标签批量清洗在 NovelHub 下多改几处（或反之），两侧文本不一致；单元素场景无差异。
- **建议用例**: 输入 `<i>a1</i><i>a2</i>` + 规则 `i@text##^a##X` → 期望 `X1\na2`（当前 `X1\nX2`）

#### D-16. `@all` 多元素时用 `\n` 连接（Legado 直接串接 `outerHtml`）
- **参考实现**: `JSoup:269` — `"all" -> textS.add(elements.outerHtml())`
- **NovelHub**: `Engine:1272-1273` — 每个元素 `str(el)` 各出一个值，再由 `Engine:1198` 用 `\n` 连接
- **判定**: 确认不一致
- **影响**: `@all` 常用于取一整块 HTML（正文/简介），插入的换行会让外层 HTML 出现空白文本节点（一般无害，但影响折叠/比较）。
- **建议用例**: 输入 `<i>a</i><i>b</i>` + 规则 `i@all` → 期望 `<i>a</i><i>b</i>`（当前 `<i>a</i>\n<i>b</i>`）

#### D-17. 缺少 HTML 实体反转义（`getString` 的 unescape）
- **参考实现**: `Rule:314-318` — `val str = if (unescape && resultStr.indexOf('&') > -1) StringEscapeUtils.unescapeHtml4(resultStr) else resultStr`
- **NovelHub**: `Engine:1007-1022`（`_eval_rule_str`）/`Engine:1060-1080`（`_eval_field`）直接返回，**无 unescape**；全规则引擎 grep 无 `unescape`（`__init__.py:22` 的 `html_unescape` 只在 `explore.py` 用）
- **判定**: 尚未实现
- **差异说明**: HTML 源里 bs4 解析时已解码实体，差别小；**JSON/JS 取值里的实体不会还原**。
- **影响**: JSON 接口源的书名/作者/简介出现 `&amp;`、`&#39;`、`&nbsp;` 原样入库。
- **建议用例**: 输入 `{"name":"A &amp; B"}` + 规则 `$.name` → 期望 `A & B`（当前 `A &amp; B`）

#### D-18. 正文 `replaceRegex` 管道缺少"按行 trim + 全角缩进"（边界条目）
- **参考实现**: `BookContent:139-144` — `contentStr = contentStr.split(LFRegex).joinToString("\n"){ it.trim() }` → `analyzeRule.getString(replaceRegex, contentStr)` → `... { "　　$it" }`
- **NovelHub**: `Engine:516-518` — `content = self._apply_replace_regex(content, replace_regex)`（章节路径对应 `chapter.py:183`，**不在我的主范围**，一并列出）
- **判定**: 确认不一致
- **差异说明**: 只要书源配了 `replaceRegex`，Legado 就会先逐行 trim、再把每行前置全角双空格；NovelHub 只做替换。另外 Legado 的 `replaceRegex` 是当**规则**求值的（走 `getString`），`##` 语义同 D-08。
- **影响**: 正文段落缩进/首尾空白不同（阅读观感、Markdown 渲染、正文 hash 都会变）。
- **建议用例**: 正文 `"  l1\n广告l2"` + `replaceRegex: "##广告##"` → 期望 `"　　l1\n　　l2"`；NovelHub 当前 `"  l1\nl2"`

#### D-19. 旧式 `{$.rule}` 包装与 JSONPath 内嵌规则未实现
- **参考实现**: `JSonPath:41` — `result = ruleAnalyzes.innerRule("{$.") { getString(it) }`（`{$..}` 形式先按内嵌规则递归求值，为空才走 `ctx.read`）
- **NovelHub**: `Engine:1545-1562` — `_substitute_inner_rules` 只处理 `{{...}}`；`Engine:1082-1108`（`_eval_with_mode`）没有 `{$.` 分支 → 整条落到 `_eval_css`
- **判定**: 尚未实现（低优先级，旧式写法）
- **影响**: 仍写 `{$.data.name}` 的老书源字段为空；仓库/测试里没有该写法的样例（grep `\{\$\.` 只命中 `{{$.slug}}` 这种 JS 模板，那条路径是好的）。
- **建议用例**: 输入 `{"n":"x"}` + 规则 `{$.n}` → 期望 `x`（当前 空）

#### D-20. `_eval_css_single` 用裸 `split("@")` 切规则，括号感知的 `_split_css_attr` 是死代码
- **参考实现**: `JSoup:212` — `val rules = rule.splitRule("@")`（`RuleAnalyzer`，`[]`/`()`/引号内的 `@` 不切）；`JSoup:200-224` 非 `@CSS:` 规则同样走这条
- **NovelHub**: `Engine:1213` — `parts = rule.split("@")`；`Engine:1245-1255` 的 `_split_css_attr`（括号感知、默认 `text`）**没有任何调用点**（全仓 grep 只命中定义处）
- **判定**: 确认不一致
- **差异说明**: `@` 出现在属性选择器内部时规则被切碎。实测 `<a href="mailto:x@y">m</a>` + `a[href*='@']@href` → NovelHub 空（`parts` 变成 `["a[href*='", "']", "href"]`）。
- **影响**: 选择器/取值里含 `@` 的字段规则（邮件链接、含 `@` 的 URL 值）全部取不到值；现成的正确工具函数却未接线。
- **建议用例**: 输入 `<a href="mailto:x@y">m</a>` + 规则 `a[href*='@']@href` → 期望 `mailto:x@y`（当前 空）

#### D-21. `_eval_css_single` 自创"无 `@` 且含括号"的正则回退 —— NovelHub 会返回 Legado 不会返回的值
- **参考实现**: `JSoup:200-224` + `JSoup:229-280`：无 `@` 的 jsoup 规则，最后一段被当作**属性名**（`element.attr(rule)`），不会做正则匹配
- **NovelHub**: `Engine:1206-1212` — `if "@" not in rule and re.search(r"\([^()]*[.+*?][^()]*\)", rule): match = re.search(rule, soup.get_text(" ", strip=True)); ... return [match.group(1) if match.lastindex else match.group(0)]`
- **判定**: 确认不一致（NovelHub 多出来的行为，不是缺失）
- **差异说明**: 实测页面 `<p>作者：张三</p>` + 规则 `作者：(.*)`（无 `@`）→ NovelHub 返回 `张三`；Legado 走属性名分支得到空。
- **影响**: 书源里写错的规则（本该 `##` 却直接写正则）在 NovelHub 下"看起来能用"，返回的值可能与 Legado 不同 → 用 Legado 调试正常/异常会互相矛盾，排错时容易误判。
- **建议用例**: 输入 `<p>作者：张三</p>` + 规则 `作者：(.*)` → 期望 空（当前 `张三`）

#### D-22. `coverDecodeJs` / `imageDecode` 的 `src`、`result` 绑定
- **参考实现**: `ImageUtils.kt:42-54` — `source?.evalJS(ruleJs) { put("book", book); put("result", inputStream); put("src", src) } as ByteArray`
- **NovelHub**: `Engine:1849-1872`（`decode_cover`）— `runtime.eval_bytes_sync(code, image_bytes)`；`Engine:1874-1889`（`decode_content_image`）同形
- **判定**: 无法判定
- **差异说明**: 调用点只传了 `code` 与图片字节，**没有** `src`（图片 URL）/`book`/`result` 的显式绑定；Legado 明确注入 `src` 与 `result`(InputStream)。是否由 `eval_bytes_sync` 内部补齐无法确认——`js_runtime.py` 明确不在我的范围，我没有读它。
- **影响**: 若运行时不注入 `src`，用 `src` 生成密钥/切片的封面解密规则会失败 → 封面为空（其余解密逻辑不受影响）。
- **建议用例**: 书源 `coverDecodeJs: "java.ajax(src).…"` 或任何读 `src` 的脚本 + 一张真实封面 → 期望解出图片；需先读 `eval_bytes_sync` 的上下文注入才能定论。

---

#### D-23. `@textNodes` 【已正确】
- **参考实现**: `JSoup:239-251` — 逐 `element.textNodes()`、每个 `trim { it <= ' ' }`、非空才收、`"\n"` 拼接、整元素结果非空才入列
- **NovelHub**: `Engine:1262-1264` — `[t.strip() for t in el.find_all(string=True, recursive=False) if t.strip()]` → `"\n".join`
- **判定**: 已正确（语义一致：只取直接子文本节点、trim、`"\n"`、跳过空）
- **建议用例**: `<div>A:<a>B</a>(C)</div>` + `div@textNodes` → 两侧都是 `A:\n(C)`

#### D-24. 两段式 `##regex##`（replaceAll 成空串）【已正确】
- **参考实现**: `Rule:710-715` + `Rule:455-458` — `replaceRegex = ruleStrS[1]`、`replacement = ""`（size==3）、`result.replace(regex, "")`
- **NovelHub**: `Engine:1380-1384` — `re.sub(parts[1], "", text)`
- **判定**: 已正确（实测 `ch12` + `##\d##` → 两侧都是 `ch`）
- **建议用例**: 输入 `ch12` + 规则 `##\d##` → `ch`

#### D-25. 属性缺失/空值跳过 + 属性值去重【已正确】
- **参考实现**: `JSoup:272-276` — `val url = element.attr(lastRule); if (url.isBlank() || textS.contains(url)) continue`
- **NovelHub**: `Engine:1233`（`if val: results.append(val)`）+ `Engine:1237-1243`（`seen`）
- **判定**: 已正确（属性分支的"空跳过 + 去重"与 Legado 一致；超范围去重见 D-14）
- **建议用例**: `<i>x</i>` + `i@href` → 两侧都空；`<i href="x"></i><i href="x"></i>` + `i@href` → 两侧都只有一条 `x`

#### D-26. XPath 列表规则的 `/` 开头 + 1-based 位置谓词（handoff §13 修复点）【已正确】
- **参考实现**: `Rule:573-576` — `ruleStr.startsWith("/")` → `Mode.XPath`；XPath 位置谓词天然 1-based；`JSoup:311` 的 Legado 索引是另一套 0-based 语法
- **NovelHub**: `Engine:752-753` — `if "//" in rule or rule.startswith(("/", ".//", "./")): return rule, " ", []`（不当 Legado 索引解析）
- **判定**: 已正确（实测 `_get_elements(html, "//div[@class='gallary_wrap tb']/ul/li[1]")` → 第一个 `li`，`_eval_list_rule` 同）
- **建议用例**: `<div class='gallary_wrap tb'><ul><li>L1</li><li>L2</li></ul></div>` + `chapterList: //div[@class='gallary_wrap tb']/ul/li[1]` → 只出 `L1`（相册封面不再丢）

#### D-27. `children`/`class.`/`tag.`/`id.` 简写与 `[n]` 0-based 索引【已正确】
- **参考实现**: `JSoup:311-321` — `children` → `temp.children()`、`class` → `getElementsByClass`、`tag` → `getElementsByTag`、`id` → `Evaluator.Id`；`JSoup:331-335`/`JSoup:372-379` — 正索引直接查 `0 until len`，负索引 `it + len`
- **NovelHub**: `Engine:806-828`（`_legado_before_elements`）+ `Engine:976-1005`（`_apply_legado_indexes`）
- **判定**: 已正确（实测 `class.gallary_wrap tb@text` → `L1\nL2`；`li[0]@text` → `L1`）
- **建议用例**: `<div><i>x</i><i>y</i></div>` + `i[0]@text` → `x`；`children` 与 `class.xxx` 均能取到

#### D-28. `text.x` 简写的匹配依据不同（ownText 拼接 vs 单个文本节点）
- **参考实现**: `JSoup:319` — `"text" -> temp.getElementsContainingOwnText(rules[1])`（元素的 **ownText**（多个直接文本节点拼接后）包含关键字即命中）
- **NovelHub**: `Engine:824-828` — `[node.parent for node in el.find_all(string=True) if value in str(node)]`（单个文本节点包含关键字，取父元素）
- **判定**: 确认不一致
- **差异说明**: 关键字跨越两个直接文本节点时 Legado 命中（ownText 已拼接），NovelHub 不命中；反之同一元素内多个文本节点各自命中时 NovelHub 会产出重复元素（随后被 D-14 的去重掩盖）。
- **影响**: `text.作者：xxx@text` 这类"按标签文字定位"的规则在带内联标签的页面上会取不到值（低概率）。
- **建议用例**: 输入 `<div>A<span>x</span>B</div>` + 规则 `text.AB@text` → 期望（Legado）命中 div；当前 NovelHub 空

#### D-29. 无 `@` 的 jsoup 规则最终按属性名取值（两侧都为空）【已正确】
- **参考实现**: `JSoup:200-224` — `rules = rule.splitRule("@")` 只有 1 段时 `last = 0`，循环不执行，`elements` 仍是 `[页面]` → `getResultLast(elements, rules[0])` 落到属性分支 `element.attr("div#content")`
- **NovelHub**: `Engine:1216-1222` — `parts` 只有一段时 `attr_suffix = 整条规则`、`elements = [soup]` → `Engine:1274` `soup.get("div#content")` → `None` → 空
- **判定**: 已正确（两边都把裸选择器当属性名，结果都是空 —— 看着像 bug，其实与 Legado 一致，不要"顺手修"）
- **建议用例**: 输入 `<div id="content">x</div>` + 规则 `div#content` → 两侧都空；写 `div#content@text` 才有值

---

### 未覆盖 / 未核实（不要当结论用）

1. **`seimicrawler` JXNode 的序列化**：仓库未 vendor（`yuedu/app/cronetlib/*.jar` 里没有），`XPath:99-101 asString()` 与 `XPath:137-139 TextUtils.join(..., it)`（调 `toString()`）对元素节点分别返回什么（outerHtml？全文本？）无法核实 → D-01/D-22 的"Legado 期望值"按上述保守表述。
2. **jsoup DOM vs lxml DOM 的树差异**：Legado 的 XPath 建在 jsoup 树上（`JXDocument.create(html)`，含 `strToJXDocument` 的 `<td>/<tr>` 补壳），NovelHub 用 `lxml.etree.HTML`（`Engine:1326`）→ 畸形 HTML、表格、`<br>` 的处理可能不同；本表未逐条比对。
3. **`rule_engine.py:121-275`**（`_RuleAnalyzer._split_head/_split_tail/_chomp_*` 内部实现）未读；本轮所有 `##`/`@`/分隔符结论都基于对调用方与实测行为的判定，未逐行核对该解析器。
4. **`AnalyzeRule.kt` 的规则调度总流程、`AnalyzeUrl.kt`、`AnalyzeByRegex.kt`（Mode.Regex 的 `getElement/getElements` 调用链）** 只读了 `##`/取值相关部分，`AnalyzeByRegex` 只有 D-09 一条间接结论。
5. **`isJson()`（`io.legado.app.utils`）与 `Engine:1647-1659 _try_parse_json` 的判定边界**（是否允许前后有空白/`]` 开头/非 JSON 的 HTML）未比对。
6. **`||`/`&&`/`%%` 的组合与交错、XPath 位置谓词总流程**：按任务约定不归我，未复核。
7. **`js_runtime.py` / `jsoup_shim.js`**：明确不在范围，未读（D-22 因此只能是"无法判定"）。
8. **`docs/codex-handoff.md`**：只读到第 579 行（§1–§22）；§23 之后未读，可能还有与本表相关的事故记录。
9. **`_extract_css_value` 的 `@html` 副作用**：`tag.decompose()` 会修改共享 soup（Legado `elements.select("script").remove()` 同样会改），是否在后续字段解析中互相污染未验证。
10. **`decode_cover` 的 base64 兜底**（`Engine:1863-1870`）对非加密图片是否可能误判：只做了代码阅读，未用真实图片字节跑过。

### 本轮实际读过的文件

| 文件 | 行数 |
|---|---|
| `docs/codex-handoff.md` | 1–579（§1–§22） |
| `yuedu/.../analyzeRule/AnalyzeByJSonPath.kt` | 全 172 行 |
| `yuedu/.../analyzeRule/AnalyzeByRegex.kt` | 全 60 行 |
| `yuedu/.../analyzeRule/AnalyzeByJSoup.kt` | 全 524 行 |
| `yuedu/.../analyzeRule/AnalyzeRule.kt` | 全 907 行 |
| `yuedu/.../analyzeRule/AnalyzeByXPath.kt` | 全 155 行（超出指定清单，为判定 XPath 取值后缀） |
| `yuedu/.../analyzeRule/RuleAnalyzer.kt` | 300–369（`innerRule`） |
| `yuedu/.../webBook/BookContent.kt` | 125–197 |
| `yuedu/.../utils/ImageUtils.kt` | 40–74 |
| `backend/app/crawler/plugins/yuedu/rule_engine.py` | 1–120、276–1949（**121–275 未读**） |

另外用 `python -` 直接调用 `YueduRuleEngine` 跑了 5 批合成输入（HTML/JSON 共约 50 个断言）复核"当前得到"，未修改任何产品代码、未 git 操作。

---


---

## 附录 C. C 轴完整逐条表（`java.*` / jsoup shim 语义，38 条）

**来源与可信度声明**：本附录由一次并行评审（与本文档同一套规范、同样的只读约束）产出，
**逐字并入、未改写**。该评审文件自身登记了全部引用锚点与未覆盖范围（其文末 G 节）。
采用任一条目前请自行核对它给出的双侧 `文件:行`。

**主代理已独立复核的条目**：

| 条目 | 复核路径 | 结论 |
|---|---|---|
| C-14 / C-19 书源 `header` 进不了 JS HTTP | `rule_engine.py:310-325 _build_js_context` 只注入 `baseUrl/bookUrl/sourceUrl/url/book/chapter` → `js_runtime.py:334 __nhSetSourceConfig(ctx)` → `jsoup_shim.js:844 merge(__nhSourceConfig.header)` **恒空操作** | ✅ 成立 |
| C-17 已导入 Cookie 进不了 shim | `jsoup_shim.js:796 __nhCookieJar = []`，仅 `setCookie` 追加；全仓无任何地方用已导入 Cookie 调用它；`:1117 getCookie()` **无参数**（Legado 为 `getCookie(tag[,key])`） | ✅ 成立 |
| C-27 `java.log` 返回 `null` 且不输出 | `jsoup_shim.js:1152 log: function (msg) { return null; }`（Legado 返回 msg，书源有 `x=java.log(x)` 链式写法） | ✅ 成立 |
| C-30 `Connection.execute()` 状态码写死 200、响应头为空 | `jsoup_shim.js:780 return new __nhResponse(this.url, text, 200, {})` —— 字面 200 + 空 headers（故 `Set-Cookie` 永不解析） | ✅ 成立 |
| C-23 加密套件缺失 | 26 个加密函数在整个 `plugins/yuedu`（`*.js`+`*.py`）grep **0 命中** | ✅ 成立 |


## C. Legado `java.*` / `org.jsoup` JS API 规范对照（只读）

**参考实现（权威）**：`yuedu/`（GPL-3.0，只读、只用于理解语义，未复制任何代码到 NovelHub）
**被对照实现**：`backend/app/crawler/plugins/yuedu/jsoup_shim.js`（Node 子进程内的 shim）、
`backend/app/crawler/plugins/yuedu/js_runtime.py`、`rule_engine.py`

**绑定事实（先读这一条，后面很多判定依赖它）**：

- Legado 在 JS 里绑定的 `java` 不是 `JsExtensions` 这个接口，而是两个宿主对象：
  - 规则求值时 `java` = `AnalyzeRule` 实例（`yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeRule.kt:776`，
    `bindings["java"] = this`），`AnalyzeRule : JsExtensions`（同文件 `:56`）。
  - URL 规则求值时 `java` = `AnalyzeUrl` 实例（`AnalyzeUrl.kt:350`，`AnalyzeUrl : JsExtensions` 同文件 `:92`）。
  因此 `java.*` 的可用方法 = `JsExtensions`（`JsExtensions.kt:83`）+ `JsEncodeUtils`（`JsEncodeUtils.kt:18`）
  + `AnalyzeRule` 自己的 `get/put/getString/evalJS/…`。
- 绑定变量（`AnalyzeRule.kt:775-788`）：`java / cookie / cache / source / book / result / baseUrl /
  chapter / title / src / nextChapterUrl / rssArticle`；`AnalyzeUrl.kt:349-360` 另有 `page / key /
  speakText / speakSpeed`，**没有 `chapter`**。
- `src` = 当前解析内容（`AnalyzeRule.kt:785`），`Element`/`Elements` 来自 jsoup（`org.jsoup.*`）。

---

### A. 历史事故条目（`docs/codex-handoff.md` 第 3/8/10/26 节）逐条复核

#### C-1. `java.getString` 支持 XPath（不只 CSS）
- **参考实现**: `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeRule.kt:250,296` — `fun getString(ruleStr: String?, ...)`；`Mode.XPath -> getAnalyzeByXPath(result).getString(rule)`；`SourceRule` 在 `:573` 用「以 `/` 开头即 XPath」判定（`ruleStr.startsWith("/") -> mode = Mode.XPath`）
- **NovelHub**: `jsoup_shim.js:1000-1026`（`__nhGetString`）→ `:1022` `if (selector.charAt(0) === '/' || selector.indexOf('./') === 0) { var css = __nhXPathToCss(selector); ... }`，翻译器在 `:933-998`
- **判定**: 已正确
- **类别**: 可实现
- **差异说明**: 前导 `/` 判定为 XPath 与 Legado 一致；`__nhXPathToCss` 支持 `tag`、`*`、`[@attr]`、`[@attr='v']`、`[n]`（`nth-of-type`）、`//`→后代、`/`→子。Legado 侧是 JsoupXpath 的完整 XPath 1.0，shim 只是子集（`[last()]`、`[contains(@class,'x')]`、`//text()`、`/@href` 作为独立步骤等不在子集内；`/a/@href` 能工作是因为 `:1017` 的尾部分离正则先剥掉了 `/@href`）。**XPath 主路径已对齐，复杂谓词仍不行**。
- **影响**: 只用 `//div[@class='x']/ul/li`、`//li[1]`、`/…/text()`、`/…/@href` 的书源已可用（第 10 节第 1 条已修）；用 `contains()`/`last()`/`following-sibling::` 的书源仍会 `return ''`（`:1024`），进而让书源自带 `try/catch` 把空串当结果（第 10 节）。
- **建议用例**: 输入 `<div class="box"><a href="/b/1.html">第一本</a><a href="/b/2.html">第二本</a></div>`，脚本 `java.getString("//div[@class='box']/a/@href")`
  → 期望 `"/b/1.html"`（与 `backend/tests/test_rule_engine_legado.py:596-604` 的断言一致，NovelHub 当前得到 `"/b/1.html"`）；
  同一输入 `java.getString("//div[@class='box']/a[last()]/@href")`
  → 期望 `"/b/2.html"`，NovelHub 当前得到 `""`（`__nhXPathStepToCss` 在 `:952` 对未识别谓词返回 null → `:1024 return ''`）。
  同类可见症状（`:1028-1029`）：`var current = current.select(selector).first(); if (!current) return '';`
  → 选择器不匹配时**返回空串**，书源里常见的 `java.getString('…').match(/(\d+)/)[0]` 随即抛
  `Cannot read properties of null (reading '0')`（即 `docs/codex-handoff.md` 第 10 节记录的那条日志）。

#### C-2. `java.getString` 支持 `##regex##replacement`
- **参考实现**: `AnalyzeRule.kt:708-718`（`makeUpRule` 拆 `##`）与 `:436-460`（`replaceRegex`：`##match##replace` 全替换、**4 段 `##match##replace###` 是「取第一个匹配子串再替换」**）
- **NovelHub**: `jsoup_shim.js:1003-1012`（拆 `##`）+ `:1054-1060`（`value.replace(new RegExp(transform[0],'g'), transform[1])`）
- **判定**: 确认不一致（4 段语义）
- **类别**: 可实现
- **差异说明**: 2 段形式一致（全量替换）。Legado 的 4 段形式 `##regex##repl###` 是「先 `matcher.find()` 取出 group(0)，只对这一段做替换，没命中返回 `""`」；shim 只取 2 段，第 3 段被丢弃，退化成「全量替换」（`:1007` 只取 `tParts[0]`/`tParts[1]`）。Python 侧同样不含该语义（`rule_engine.py:1370-1377` 的 `replace_first` 用 `count=1`，返回的是**整串**而非命中的 group(0)）。
- **影响**: 用 `###` 形式从长文本里「截一段再清洗」的书源会得到整段文本而非截出的片段（章节标题/作者字段被污染）。第 10 节只提到 2 段形式。
- **建议用例**: `java.getString("//div[@class='box']/a/text()##第一##第1")` → 期望 `"第1本"`（已正确）；`java.getString("//div[@class='box']/a/text()##(第.{1})##[$1]###")` → 期望 `"[第一]"`，NovelHub 当前得到 `"[第1]本"` 形态（全量替换）。

#### C-3. `java.get(key)` 单参数 = 变量存取，双参数才是 HTTP
- **参考实现**: `AnalyzeRule.kt:754-769` — `fun get(key: String): String`（依次查 `chapter/book/ruleData/source` 变量）；HTTP 重载在 `AnalyzeUrl.kt:93`（`AnalyzeUrl.ajax`）/`JsExtensions.kt:378`（`get(urlStr, headers)` 是 **2 参数**，且**只在 `AnalyzeUrl` 上有**）
- **NovelHub**: `jsoup_shim.js:1108-1113` — `get: function (key, headers) { var isUrl = arguments.length >= 2 || /^(https?:)?\/\//.test(String(key)); if (isUrl) return java.httpGet(key, headers); var stored = __nhCacheGet(String(key)); ... }`
- **判定**: 已正确
- **类别**: 可实现
- **差异说明**: 单参数走 `__nhCacheGet`（`java.put` 的存储，`:1103`），双参数走 HTTP，与第 10 节第 4 条的修复一致。次要差异：Legado 的 `get(key)` 还能取 `bookName`/`title` 及 book/chapter 变量（`AnalyzeRule.kt:756-767`），shim 只查 `java.put` 的 store，**取不到 book/chapter 上的变量**。
- **影响**: 只依赖 `java.put`/`java.get` 往返的规则（绅士漫画 `imgInfoList`）已修好（第 10 节）；`java.get('title')`/`java.get('bookName')` 这类会拿到 `""`。
- **建议用例**: `java.put('x','1'); java.get('x')` → 期望 `"1"`（NovelHub 当前 `"1"`）；`java.get('title')`（chapter 上下文存在时）→ 期望章节名，NovelHub 当前 `""`。

#### C-4. 求值内容要对「当前解析内容」（列表项元素 / 页面）求值，不是「上一步结果」
- **参考实现**: `AnalyzeRule.kt:263-270` — `fun getString(ruleList, mContent: Any? = null, ...)` / `val content = mContent ?: this.content`，而 `this.content` 由 `setContent()`（`:84`）在解析每一步前设置；JS 内 `java.getString` 因此总是对**当前 content**求值，`result` 只是「上一步结果」
- **NovelHub**: `rule_engine.py:456-469`（`for item in items:` → `self._js_content = item` 后逐字段求值）与 `:1445-1450`（`runtime.eval_js_sync(code, raw, context=..., content=self._js_content)`）；`js_runtime.py:337-349`（`var src=<content>; __nhSetContent(src)`）；shim `:1027-1028`（`var current = new __nhDocument(__nhContent); current = current.select(selector).first()`）
- **判定**: 已正确
- **类别**: 可实现
- **差异说明**: 列表解析时 `_js_content` 被显式设为**列表项元素**（`:462`），页面级解析时设为整页（`:479`/`:501`/`:539`），与 Legado「对当前解析内容求值」一致；`src` 也绑定为同一值（`js_runtime.py:348`），与 `AnalyzeRule.kt:785` 的 `bindings["src"] = content` 语义一致。残留差异：Legado 的 `content` 是 jsoup `Element`（相对选择），shim 里 `__nhContent` 是**外层 HTML 字符串**再重新解析成 `#document`（`:1027`），所以 `java.getString("> a")` 这类相对选择器、以及 `:root` 语义会不同。
- **影响**: 第 10 节第 2 条的主症状（拿错求值内容）已修；仍可能出问题的是「用相对/绝对选择器对准列表项自身」的写法。
- **建议用例**: 列表项 `item = '<li><div class="info"><span class="p">49P</span></div></li>'`，字段规则 `<js>java.getString("//div[@class='info']/span/text()")</js>`
  → 期望 `"49P"`（NovelHub 当前 `"49P"`）。

#### C-5. 属性选择器 `[class='info']` 的 `=` 全系列
- **参考实现**: `yuedu` 侧交给 jsoup 的 `Evaluator`（`AnalyzeByJSoup.kt` 的 select 路径），语义见 `SourceRule`（`AnalyzeRule.kt:573`）之外的 CSS 直通；`[class=x]` 在 jsoup 表示「属性值等于」
- **NovelHub**: `jsoup_shim.js:236` — `var am = inner.match(/^([\w:.-]+)(?:\s*(~=|\^=|\$=|\*=|\|=|=)\s*(.*?))?$/);` 与 `:244-249`（`=`/`~=`/`|=`/`^=`/`$=`/`*=` 六种全部实现）
- **判定**: 已正确
- **类别**: 可实现
- **差异说明**: `=` 已在操作符表内，不再退化成「有 class 就算命中」；无引号值（Legado 允许 `a[href*=/post/]`，见 `codex-handoff.md` 第 3 节第一条同类问题）也能匹配：`__nhTokenizeSimple`（`:215`）把整个 `[...]` 当 token，`:249` 的 `*=` 用 `indexOf`。
- **影响**: 第 10 节第 3 条已修；第 3 节表格里「`a[href*=/post/]` 无引号」在 shim 路径也成立。
- **建议用例**: `a[href*=/post/]` 对 `<a href="/post/1.html">x</a>` → 期望命中；`div[class='info']` 对 `<div class="other">` → 期望不命中（NovelHub 当前均符合）。

#### C-6. `java.getWebViewUA()` 缺失
- **参考实现**: `JsExtensions.kt:555-557` — `fun getWebViewUA(): String { return WebSettings.getDefaultUserAgent(appCtx) }`
- **NovelHub**: `jsoup_shim.js:1124-1126`（返回硬编码 Android 14 / Chrome 120 UA）；`js_runtime.py:705-707`（登录脚本里同样硬编码）
- **判定**: 已正确（有）/ 确认不一致（值）
- **类别**: 可实现
- **差异说明**: 函数存在（第 10 节第 5 条已修），调用不再抛 TypeError。但 Legado 返回的是**运行设备的 WebView UA**（随 Android/WebView 版本变化），shim 返回固定串；站点按 UA 分流（手机版/桌面版/不同模板）时行为会与真机不同。
- **影响**: 要撸小说这类用 UA 构造 `header` 的源不会再因缺函数失败（第 10 节）；若站点对特定 UA 返回不同 DOM，规则命中率会有偏差。
- **建议用例**: `java.getWebViewUA().indexOf('Mozilla')===0` → 期望 `true`（NovelHub 当前 `true`）；`java.getWebViewUA()` 与真机 UA 一致性 → 无法判定（取决于设备）。

#### C-7. `java.connect(url).getBody()` 缺失
- **参考实现**: `JsExtensions.kt:129-143` — `fun connect(urlStr: String): StrResponse`（真正发请求）；`StrResponse.kt:60` — `fun body() = body`（**没有 `getBody()`**，源码里 `body()` 是函数；`getBody()` 是 jsoup `Connection.Response` 风格的历史写法，Legado 侧靠 Rhino 的 JavaBean 属性映射）
- **NovelHub**: `jsoup_shim.js:1076-1080`（`connect` 真发请求）+ `:855`（`__nhResponse.prototype.body`）+ `:860`（`__nhResponse.prototype.getBody`）+ `:861`（`string`）
- **判定**: 已正确
- **类别**: 可实现
- **差异说明**: 语义是「发请求并返回带 body 的响应对象」，与 `JsExtensions.connect` 一致；`getBody()` 与 `body()` 都给（比 Legado 更宽容，不构成差异）。Legado 的 Java 方法名是 `body()`（`StrResponse.kt:60`），`getBody()` 在 Legado 侧能工作是因为 Rhino 对 JavaBean 的属性访问映射；NovelHub 两条路径都提供。**`java.connect` 没有**应用书源 `header`（见 C-14/C-19）。
- **影响**: 第 26 节 Icu 的 `java.connect(url).getBody()` 已可用（`backend/tests/test_yuedu_plugin.py:4461` 亦覆盖）。
- **建议用例**: 见 `backend/tests/test_rule_engine_legado.py:552-575`。

#### C-8. `Element.selectFirst` 缺失
- **参考实现**: jsoup `Element.selectFirst(String)`；Legado 直接把 jsoup 对象交给 Rhino
- **NovelHub**: `jsoup_shim.js:376-379`（`__nhEl.prototype.selectFirst`）+ `:193-199`（`__nhElements.selectFirst`）
- **判定**: 已正确
- **类别**: 可实现
- **差异说明**: 返回首个匹配或 `null`，与 jsoup 一致。
- **影响**: 第 26 节 Icu 的 `if (span)` 守卫不再因缺方法整体变成错误串。
- **建议用例**: 见 `backend/tests/test_rule_engine_legado.py:579-592`。

#### C-9. `Element.equals` 缺失
- **参考实现**: jsoup `Node.equals(Object)`（同文档内按位置/身份判等；跨文档为 `false`）
- **NovelHub**: `jsoup_shim.js:567-570` — `__nhEl.prototype.equals = function (other) { return other === this; }`（另有 `is()`）
- **判定**: 已正确
- **类别**: 可实现
- **差异说明**: shim 的所有元素来自同一棵手写 DOM 树，身份比较与 jsoup「同一文档同位置」等价。
- **影响**: 第 26 节 Icu 的 `while (el && !el.equals(stopEl))` 可用。
- **建议用例**: 见 `backend/tests/test_rule_engine_legado.py:579-592`。

#### C-10. `JSON.stringify` / `json_safe`：输入是非 JSON 可序列化对象（bs4 `Tag`）时不能抛错
- **参考文档**: `codex-handoff.md` 第 26 节第 3 条 — `json.dumps(Tag)` 抛 `Object of type Tag is not JSON serializable`，脚本没跑就失败
- **NovelHub**: `js_runtime.py:38-61`（`json_safe`：dict/list 递归、不可序列化 `return str(value)`）；`:320`（`input_json = json.dumps(json_safe(input_value))`）；`:324`（`safe_context = json_safe(context)`）；`:339`（`content` 走 `str(content)`）
- **判定**: 已正确
- **类别**: 可实现
- **差异说明**: 进入 JS 前所有输入都被降级为字符串（`Tag` → `str(Tag)` = 外层 HTML），与第 26 节修复意图一致。备注：`json_safe` 只作用在 `input_value`/`context`，`content` 是在 `:339` 直接 `str()`（效果相同）。
- **影响**: 纯 JS 字段规则（Icu 的 `ruleSearch.kind`）不再因序列化失败而整条丢掉。
- **建议用例**: 输入 `<div class="t">x</div>`，脚本 `JSON.stringify(typeof result)` → 期望 `'"string"'`（NovelHub 当前即 `string`）。

#### C-11. 依赖完整 Android 运行时的 API（`Reload(...)`、`java.importScript`、`java.startBrowserAwait`、`java.webView`）无法执行 —— 错误提示是否诚实
- **参考实现**: `JsExtensions.kt:170-183`（`webView`，依赖 `BackstageWebView`=Android WebView）、`:188-202`（`webViewGetSource`）、`:207-226`（`webViewGetOverrideUrl`）、`:233-236`（`startBrowser`）、`:241-251`（`startBrowserAwait`，依赖 `SourceVerificationHelp` + 前台浏览器）、`:256-259`（`getVerificationCode`）、`:264-271`（`importScript`，可读网络/本地文件并返回脚本源码）、`:985-1001`（`openUrl`）
- **NovelHub**: `jsoup_shim.js:1127-1129`（`startBrowserAwait` **抛** `Error('startBrowserAwait: 页面需要浏览器验证/输入验证码，无法自动处理: …')`）、`:1130-1132`（`getVerificationCode` 同样抛）；`webView`/`webViewGetSource`/`webViewGetOverrideUrl`/`startBrowser`/`importScript`/`openUrl` **完全没有定义**；`Reload` 在 shim `:1224-1231` 被实现成一次裸 curl（**注意：Legado 的 yuedu 源码里没有内置 `Reload`/`Get`/`Put`/`sleep` 这些全局，它们是书源自己定义的**，所以这条不是「缺 API」而是「来源不同的近似实现」——见 C-41）
- **判定**: 尚未实现（4 个 webView 族 + `startBrowser` + `importScript` + `openUrl`）/ 已正确（2 个「明确抛错」的）
- **类别**: `startBrowserAwait`、`getVerificationCode`、`webView`、`webViewGetSource`、`webViewGetOverrideUrl`、`startBrowser`、`importScript`（`http`/本地文件变体）、`openUrl` = **需要 Android 运行时（无法实现）**；若未来愿意用 Playwright 顶替 `webView` 家族，可归入 **需要真实浏览器**
- **差异说明**: 缺失的 7 个 API 在 JS 里是 `undefined` → 调用抛 `TypeError: java.webView is not a function`，书源自带 `try/catch` 会把它变成普通字符串结果（**错误不可见**，与第 26 节记录的现象同型）。已实现的 2 个抛错型 API 文案诚实（明说需要浏览器验证），但只覆盖 `startBrowserAwait`/`getVerificationCode` 两种写法。
- **影响**: UAA、禁漫天堂这类源（`codex-handoff.md` 第 26/30 节）注定跑不了；任何用 `java.webView`（**没有** try/catch）的源会在 `JsRuntime JS error` 里留下 TypeError。
- **错误提示诚实度（结论）**:
  - **诚实**：`jsoup_shim.js:1127-1132` 的两种抛错文案；`js_runtime.py:779` 把 JS 异常打成 `JsRuntime JS error: …`（不过**书源 try/catch 会吞掉**）。
  - **不诚实**：`explore.py:280-293`。只要 `exploreUrl` 是 `<js>`/`@js:` 且最终 0 个分类，就统一报
    `"该书源的发现规则是 Legado JS 脚本（<js>/@js:），当前环境无法执行；…"`。而同一段 JS 可能刚刚成功跑过（第 30 节 Icu：9 小时同步 244 本，只是翻到下一页时站点要人机验证）——此时真正原因是「站点改版/人机验证/限流」，文案却把责任推给「环境无法执行」。
- **建议用例**: `java.webView("", "https://x", "")` → 期望得到可区分的错误（例如 `unimplemented: java.webView (needs Android WebView)`），NovelHub 当前得到 `TypeError: java.webView is not a function`；Icu 式 `exploreUrl` 返回 `[]` 且站点返 403 验证页 → 期望文案指出「站点要求人机验证」，NovelHub 当前得到「当前环境无法执行」。

---

### B. 请求侧（`java.ajax` / `connect` / `get` / `post` / cookie）

#### C-12. `java.ajax(url)` / `java.ajax(url, {…})`
- **参考实现**: `JsExtensions.kt:93-108` — `fun ajax(url: Any): String?`（返回 **body 字符串**；`url` 可以是 `List`，取第一个；内部走 `AnalyzeUrl`，**自动带书源 header/cookie/限速/重试**）
- **NovelHub**: `jsoup_shim.js:1082-1098` — 字符串形式用正则 `^(\S+?)\s*,\s*(\{.*\})\s*$` 抽出 `,{json}`，其余走 `__nhCurlRaw`（裸 curl，**不带书源 header / cookie，且不走限速**）- **判定**: 确认不一致
- **类别**: 可实现
- **差异说明**: ① 不注入书源 `header`（Legado 的 `AnalyzeUrl` 会），也不注入已导入的 Cookie（`jsoup_shim.js:1066-1098` 与 `:747-781` 都没有把 `__nhCookieJar` 或 `__nhSourceConfig.header` 合进去，除 `connect` 显式调 `__nhSourceHeaders`）；② 返回类型本该是 `String`（body），shim 返回 `__nhResponse` 对象——多数书源把结果直接当字符串用（JS 会调 `toString`→`__nhResponse.prototype.toString` 返回正文，`:877`），所以多数情况能跑，但 `JSON.parse(java.ajax(u))` 传对象会抛；③ 选项对象只认 `method/body/headers`，不认 Legado 的 `charset`/`webView`/`webJs`/`retry`。
- **影响**: 依赖书源 header 才能拿到正确版本/接口的 `java.ajax` 源会拿到默认 UA 的响应（与第 8 节第 2 条同类坑，只是换到了 JS 路径）。
- **建议用例**: 书源 `header={"User-Agent":"UA-1"}`，脚本 `typeof java.ajax(baseUrl)` → 期望 `"string"`（NovelHub 当前 `"object"`）；服务端回显 UA 时 → 期望 `UA-1`，NovelHub 当前得到 Node 默认（`curl` 自带 UA，非 `UA-1`）。

#### C-13. `java.ajaxAll(urlList)`
- **参考实现**: `JsExtensions.kt:113-124` — `fun ajaxAll(urlList: Array<String>): Array<StrResponse>`
- **NovelHub**: 未定义（`jsoup_shim.js` 全文无 `ajaxAll`）
- **判定**: 尚未实现
- **类别**: 可实现
- **差异说明**: Legado 并发拉取并返回 `StrResponse[]`（用 `AppConfig.threadCount`）。
- **影响**: 调用它的源抛 `TypeError`（多被 try/catch 吞掉，结果静默为空）。
- **建议用例**: `java.ajaxAll(['https://a','https://b']).length` → 期望 `2`，NovelHub 当前抛 TypeError。

#### C-14. `java.connect(url, headerJson)` 的 header 与返回对象
- **参考实现**: `JsExtensions.kt:145-161`（第 2 参数是 **JSON 字符串**，解析成 `headerMapF`）+ `StrResponse.kt`（`body()/url()/code()/message()/headers()/isSuccessful()/errorBody()`，`url` 同时是 **属性** `:58`）
- **NovelHub**: `jsoup_shim.js:1076-1080` + `:831-847`（`__nhSourceHeaders(header)`：把 `__nhSourceConfig.header` 与传入 JSON 合并） + `:855-877`（`body/getBody/string/url()/code()/statusCode()/isSuccess()/header(name)/headers()/json()/cookie()`）
- **判定**: 确认不一致（部分）
- **类别**: 可实现
- **差异说明**: ① `__nhSourceHeaders` 读的是 `__nhSourceConfig.header`，**生产中没有任何地方给它写入 `header`**（唯一写入点是 `js_runtime.py:334` 的 `__nhSetSourceConfig(<JS 上下文>)`，里面只有 `baseUrl/bookUrl/sourceUrl/url/book`）→ 书源 `header` 实际从未生效（测试里靠手工调用 `__nhSetSourceConfig({header})` 造出来，见 `backend/tests/test_rule_engine_legado.py:568`）。② shim 缺 `message()`/`isSuccessful()`/`raw()`；③ Legado 的 `StrResponse.url` 是**属性**，shim 是**方法** `url()`，书源写 `.url` 会拿到函数对象（拼接时变成函数源码）。
- **影响**: 用 `java.connect(url).url` 或 `.isSuccessful()` 的源行为异常；书源 header 失效（见 C-19）。
- **建议用例**: `java.connect('https://x').url` → 期望 URL 字符串，NovelHub 当前得到函数源码；`java.connect('https://x', '{"X-A":"1"}').getBody()` → header 应带上 `X-A`。

#### C-15. `java.get(url, headers)` 的 header 处理
- **参考实现**: `JsExtensions.kt:378-394` — `fun get(urlStr: String, headers: Map<String, String>): Connection.Response`；`followRedirects(false)`（**不自动跟随跳转**，配合书源自己读 `Location`）、`ignoreContentType(true)`、`sslSocketFactory(unsafe)`；返回 jsoup `Connection.Response`
- **NovelHub**: `jsoup_shim.js:1108-1116` — `isUrl` 时 `return java.httpGet(key, headers)`；`httpGet` 直接 `__nhCurlRaw(url,'GET',null,headers)`（**不合并书源 header**）
- **判定**: 确认不一致
- **类别**: 可实现
- **差异说明**: 与 `connect` 不同，`httpGet` 没走 `__nhSourceHeaders`；另外 shim 的 curl 带 `-L`（`jsoup_shim.js:818`），**会自动跟随 302**，而 Legado 的 `java.get` 刻意不跟随（书源常靠 `code()==302` + `header('Location')` 取跳转地址）。返回对象也缺 `statusMessage()`/`cookies()`。
- **影响**: 用 `java.get(url, header).header('Location')` 做重定向拦截/多次跳转（`JsExtensions.kt:376` 注释「js实现重定向拦截」）的源拿到的是最终页而不是 302。
- **建议用例**: 对返回 302 的 URL 执行 `java.get(u, {}).code()` → 期望 `302` 且 `header('Location')` 有值，NovelHub 当前得到 `200`（curl -L 已跟到底）。

#### C-16. `java.head(url, headers)` / `java.post(url, body, headers)`
- **参考实现**: `JsExtensions.kt:399-415`（`head`，`followRedirects(false)`，不返 body）、`:420-437`（`post(urlStr, body: String, headers: Map)`；`requestBody(body)`）
- **NovelHub**: `post` 在 `jsoup_shim.js:1069-1071`（`__nhCurlRaw(url,'POST',body,headers)`，返回 `__nhResponse`）；`head` **未定义**
- **判定**: `post` 确认不一致 / `head` 尚未实现
- **类别**: 可实现
- **差异说明**: `post` 的 body 直接当 `--data` 发（`__nhCurlRaw` `:820`），Legado 走 `requestBody`（同时受 `header` 里的 `Content-Type` 影响），差异在于 **shim 不合并书源 header**、且返回对象缺 `statusMessage()`。`head` 完全没有。
- **影响**: 用 `java.head` 探活/取大小（第 3 节表格里 `head` 属于「网络访问」）的源抛 TypeError。
- **建议用例**: `java.head(u, {}).code()` → 期望 `200`，NovelHub 当前抛 `TypeError: java.head is not a function`。

#### C-17. `java.getCookie(tag[, key])` / `java.setCookie`
- **参考实现**: `JsExtensions.kt:305-315` — `getCookie(tag)` / `getCookie(tag, key)` 走 `CookieStore`（`setCookie` 由外部导入写入）；绑定层另有 `cookie = CookieStore`（`AnalyzeRule.kt:777`），`CookieStore` 暴露 `getCookie(url)`/`setCookie` 等
- **NovelHub**: `jsoup_shim.js:1117-1119`（`getCookie: function () { return __nhCookieJar.join('; '); }`（**忽略参数**）、`setCookie` 往 `__nhCookieJar` push、`getCookies()` 返回数组）；`:1179-1183`（`cookie` 对象同款）；`cookie` 变量由 `js_runtime.py` 注入（shim 全局）
- **判定**: 确认不一致
- **类别**: 可实现
- **差异说明**: ① 签名不符：Legado 的 `getCookie(tag)` 是「按 tag 取整条 Cookie（含已导入的登录 Cookie）」，shim 无参数、永远返回本地 jar；**已导入的书源 Cookie 从不进 shim**（`__nhCookieJar` 初始为空数组，`:796`），所以 `java.getCookie('...')`/`cookie.getCookie()` 恒为 `''`。② `setCookie` 只进 jar，**后续 `java.get/connect/ajax` 都不会带它**（`:747-781`、`:1066-1098` 均未把 jar 合进 headers）→ 书源内「登录后把 Cookie 存起来，后续请求自动携带」的写法完全失效。
- **影响**: 依赖脚本内 Cookie 往返的源（登录型/分页型）拿不到鉴权；`codex-handoff.md` 第 4 节建议的「浏览器过验证后导入 Cookie」在 **Python 侧 HTTP 请求**有效，但 **JS 发起的请求无效**。
- **建议用例**: `java.setCookie('a=1'); java.getCookie('')` → 期望包含 `a=1`（NovelHub 当前 `''`）；`java.setCookie('a=1'); java.connect(u).getBody()` 且服务端回显 Cookie → 期望含 `a=1`，NovelHub 当前不含。

#### C-18. `source.getCookie()` / `source.put/get/remove` / `source.getLoginInfo*`
- **参考实现**: `source` 绑定 = `BaseSource`（`AnalyzeRule.kt:779`）；`BookSource` 上是**字段属性**（`bookSourceUrl`/`bookSourceName`/`header`/`loginUrl`/`bookUrlPattern`/…，见 `yuedu/app/src/main/java/io/legado/app/data/entities/BookSource.kt`），**没有** `source.getCookie()`/`put` 这类方法
- **NovelHub**: `jsoup_shim.js:1161-1177` — 造了 `source` 对象：`getVariable()/put/get/remove/getLoginInfo/getLoginInfoMap/getCookie/setCookie` + 用 `Object.defineProperty` 暴露 `bookSourceUrl/bookSourceName/bookSourceGroup/bookSourceType/bookUrlPattern/customOrder/loginUrl/header/searchUrl`
- **判定**: 确认不一致（补充实现 + 字段值错位）
- **类别**: 可实现
- **差异说明**: shim 比 Legado 多出 `source.put/get/remove`（Legado 这些在 `java` 上，`AnalyzeRule.kt:740/754`），不构成失败；真正的问题是**属性取值**：`__nhSourceConfig` 只被塞进 JS 上下文（`js_runtime.py:334` 传的是 `baseUrl/bookUrl/sourceUrl/url/book`），所以 `source.bookSourceUrl`/`source.bookSourceName`/`source.header`/`source.loginUrl`/`source.bookUrlPattern` **全部返回 `''`**，只有 `source.sourceUrl` 因为上下文里有同名键才「歪打正着」有值。
- **影响**: 任何用 `source.bookSourceUrl` 拼 URL、用 `source.header` 判断/转发的源会拿到空串（静默错值，比抛错更难查）。
- **建议用例**: `source.bookSourceUrl`（配置为 `https://a.com`）→ 期望 `"https://a.com"`，NovelHub 当前 `""`。

#### C-19. 书源 `header` 规则在 JS 侧 HTTP 中生效
- **参考实现**: Legado 的 `java.*` HTTP 全部经 `AnalyzeUrl`（`JsExtensions.kt:99`、`:130-134`、`:147-152`、`:324`、`:736` 等），`AnalyzeUrl` 自身解析并附加书源 `header`（`AnalyzeUrl.kt:111-114`、`headerMapF` 参数 `:150`），并套用 `concurrentRateLimiter`
- **NovelHub**: `jsoup_shim.js:831-847`（`__nhSourceHeaders` 只读 `__nhSourceConfig.header`）+ `:1076-1079`（只有 `connect` 调它）；`transport.py:212-264`（Python 侧自己解析 `header` 规则：`_custom_headers`/`_parse_header_rule`，**完全不进 shim**）
- **判定**: 确认不一致
- **类别**: 可实现
- **差异说明**: Python 侧 HTTP 请求有 `header`（第 8 节已修）；**JS 侧（`java.connect`/`ajax`/`get`/`post`）没有**，且 `__nhSourceConfig.header` 无人写入（见 C-13/C-18）。
- **影响**: 绅士漫画式 `header`（声明 UA/Referer）在 `@js:` 规则的 HTTP 调用里失效 → 站点返回手机版/DOM 不同 → 规则解析 0 元素（第 8 节第 2 条的同型问题在 JS 路径仍未修）。
- **建议用例**: 书源 `header='{"User-Agent":"UA-1"}'`，脚本 `java.connect(u).getBody()`（服务端回显 UA）→ 期望 `UA-1`，NovelHub 当前得到 curl 默认 UA。

#### C-20. `java.ajax` / `java.get` 的限速与 Cookie 会话
- **参考实现**: `AnalyzeUrl.setCookie()`（`AnalyzeUrl.kt:409`）在每个请求前注入 CookieStore，`concurrentRateLimiter.withLimit`（`:408`）统一限速；`enabledCookieJar`（`:111`、`:612`）决定是否走 cookieJar
- **NovelHub**: `jsoup_shim.js:802-827`（`__nhCurlRaw` 直接 `execSync`，**无任何限速**）、`transport.py:896`（`await self._sleep_rate_limit()` 只在 Python `_get` 里）
- **判定**: 确认不一致
- **类别**: 可实现
- **差异说明**: shim 里由 JS 发起的请求绕过 `CRAWL_DELAY_MS` / `concurrentRate` / 手配「同步间隔」。
- **影响**: 用 JS 请求打站点的源会突破限速（第 21 节搬山人这类站点会被打），且 `sync_interval_seconds` 的承诺在 JS 路径不成立。
- **建议用例**: 连续两次 `java.ajax(u)` → 期望间隔 ≥ 配置的同步间隔，NovelHub 当前为 0。

---

### C. 文件 / 编码 / 加解密 / 字体工具（`JnExtensions`+`JsEncodeUtils` 全覆盖清点）

> 统计口径：`JsExtensions.kt` 与 `JsEncodeUtils.kt` 中所有 `fun` 名（含重载），逐个在
> `jsoup_shim.js`/`js_runtime.py` 里找同名实现。

#### C-21. 文件操作族（全部缺失）
- **参考实现**: `JsExtensions.kt:566`（`getFile`）、`:581`（`readFile`）、`:589`/`:598`（`readTxtFile`）、`:609`（`deleteFile`）、`:619`/`:628`/`:637`/`:646`（`unzipFile`/`un7zFile`/`unrarFile`/`unArchiveFile`）、`:659`（`getTxtInFolder`）、`:683`/`:689`/`:700`/`:706`/`:717`/`:723`（`getZipStringContent`/`getRarStringContent`/`get7zStringContent`）、`:734`/`:762`/`:780`（`getZipByteArrayContent`/`getRarByteArrayContent`/`get7zByteArrayContent`）、`:133`/`:141`（`getStrResponse` 系列带）、`:278`/`:286`（`cacheFile`）、`:322`/`:357`（`downloadFile`）
- **NovelHub**: `jsoup_shim.js` 中**均未定义**（grep `readTxtFile|getZip|unrar|downloadFile|cacheFile|deleteFile|getFile` 无命中）
- **判定**: 尚未实现
- **类别**: `getFile/readFile/readTxtFile/deleteFile/getTxtInFolder` = **可实现**（但 Legado 限制在 App 私有 cache 目录内，`JsExtensions.kt:566-579` 有 `SecurityException("非法路径")` 沙箱；NovelHub 若实现需要等价的路径沙箱，不能直接映射到容器 FS）；`unzipFile`/`un7zFile`/`unrarFile`/`getZip*`/`getRar*`/`get7z*` = **可实现**（zip 可纯 Python；rar/7z 需要额外 native 库）；`downloadFile`/`cacheFile` = **可实现**
- **差异说明**: 这类 API 在 Legado 里是「脚本能落盘/读盘」，NovelHub 是无状态子进程，既没有实现也没有目录约定。
- **影响**: 用 `downloadFile` + `unrarFile` + `readTxtFile` 做「下载压缩包书源」的源（部分 TXT/EPUB 合集源）完全不可用；`java.importScript` 依赖 `readTxtFile`（见 C-11）。
- **建议用例**: `java.readTxtFile('x.txt')` → 期望文件内容或 `""`，NovelHub 当前抛 `TypeError: java.readTxtFile is not a function`。

#### C-22. `java.getWebViewUA()` 以外的编码/摘要工具
- **参考实现**: `JsExtensions.kt:440`/`:444`（`strToBytes`）、`:449`/`:453`（`bytesToStr`）、`:460`/`:464`/`:468`（`base64Decode` 3 重载）、`:472`/`:479`（`base64DecodeToByteArray`）、`:486`/`:490`（`base64Encode`）、`:495`/`:500`/`:505`（`hexDecodeToByteArray`/`hexDecodeToString`/`hexEncodeToString`）、`JsEncodeUtils.kt:20`/`:24`（`md5Encode`/`md5Encode16`）、`:438`/`:452`（`digestHex`/`digestBase64Str`）、`:468`/`:485`（`HMacHex`/`HMacBase64`）
- **NovelHub**: `jsoup_shim.js:1133-1146`（`stringToBase64`/`base64ToString`/`base64Encode`/`base64Decode`/`hexDecodeToString`/`stringToHex`/`md5Encode`/`md5`）；`:1137-1143`（`hexDecodeToString` 顺带处理奇数长度）；`:1144`（`stringToHex`）；`:1145-1146`（`md5Encode`/`md5`）
- **判定**: 已正确（`base64Encode`、`base64Decode`、`hexDecodeToString`、`md5Encode` 4 个）/ 尚未实现（`strToBytes`、`bytesToStr`、`base64DecodeToByteArray`、`hexDecodeToByteArray`、`hexEncodeToString`、`md5Encode16`、`digestHex`、`digestBase64Str`、`HMacHex`、`HMacBase64`、`base64Decode(str, flags)`）
- **类别**: 可实现
- **差异说明**: 已实现 4 个的**名字**与 Legado 相同（`base64Encode`/`base64Decode`/`hexDecodeToString`/`md5Encode`）。shim 另有 `stringToBase64`/`base64ToString`/`stringToHex` 是 **Android `android.util.Base64`/`HexUtil` 风格**的额外赠品，不构成差异。注意方向：`hexDecodeToString`（hex→utf8）**有**，方向相反的 `hexEncodeToString`（utf8→hex）**没有**；缺 `md5Encode16`（取 16 位）与 `digestHex('MD5', …)`、`HMacHex` 在签名算法源里很常见。
- **影响**: 用 `java.md5Encode16`/`java.digestHex`/`java.HMacHex` 计算签名的源抛 TypeError（多被 try/catch 变成空串 → 请求 403/签名错，日志看不到原因）。
- **建议用例**: `java.md5Encode16('abc')` → 期望 16 位 MD5，NovelHub 当前抛 TypeError；`java.base64Decode('aGk=')` → 期望 `hi`（NovelHub 当前 `hi`）。

#### C-23. 对称/非对称加密与签名（`createSymmetricCrypto` / `createAsymmetricCrypto` / `createSign` + 全部 `aes*`/`des*`/`tripleDES*`）
- **参考实现**: `JsEncodeUtils.kt:42-73`（`createSymmetricCrypto` 4 重载）、`:77-81`（`createAsymmetricCrypto`）、`:84-87`（`createSign`）、`:103-427`（`aesDecodeToByteArray`/`aesDecodeToString`/`aesDecodeArgsBase64Str`/`aesBase64DecodeToByteArray`/`aesBase64DecodeToString`/`aesEncodeToByteArray`/`aesEncodeToString`/`aesEncodeToBase64ByteArray`/`aesEncodeToBase64String`/`aesEncodeArgsBase64Str`/`desDecodeToString`/`desBase64DecodeToString`/`desEncodeToString`/`desEncodeToBase64String`/`tripleDESDecodeStr`/`tripleDESDecodeArgsBase64Str`/`tripleDESEncodeBase64Str`/`tripleDESEncodeArgsBase64Str`）
- **NovelHub**: 未定义；`rule_engine.py:1417-1426` 只对「`Packages.javax.crypto` + `AES/CBC/PKCS5Padding`」把封面规则**原样返回**（不做解密），实际解密在 `fetch_cover`（Python 侧）
- **判定**: 尚未实现
- **类别**: 可判定：**可实现**（AES/DES/3DES/HMAC/RSA 都有纯 JVM/纯 Python 等价物；Legado 用的是 hutool + Android `Base64`）
- **差异说明**: 这是书源里**最常用的一类 Android-only 写法**：`java.createSymmetricCrypto("AES/CBC/PKCS5Padding", key, iv).decryptStr(data)`。NovelHub 只在「正文 AES-CBC」这一条窄路径上做了等价处理，脚本里的通用调用没有。
- **影响**: 正文/目录/封面需要脚本解密的源（大量 R18 / 论坛源）会抛 TypeError 或被 try/catch 吞成空串 → 空正文（第 10 节「漫画书章节报 Chapter returned empty content」的另一条潜在来源）。
- **建议用例**: `java.createSymmetricCrypto('AES/ECB/PKCS5Padding','0123456789abcdef').decryptStr(<base64>)` → 期望明文，NovelHub 当前抛 TypeError。

#### C-24. 字体（`queryTTF` / `replaceFont` / `queryBase64TTF`）
- **参考实现**: `JsExtensions.kt:802`（`queryBase64TTF`）、`:813`/`:857`（`queryTTF`）、`:867`/`:904`（`replaceFont`）；`QueryTTF` 见 `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/QueryTTF.java`
- **NovelHub**: 未定义（grep `queryTTF|replaceFont` 无命中）
- **判定**: 尚未实现
- **类别**: 可判定：**可实现**（TTF 解析是纯逻辑，可用 fontTools/Python；但工作量大）
- **差异说明**: 阅文/起点系书源常用「自定义字体反爬」：`queryTTF(data)` 拿到字体对象，`replaceFont(text, errorTTF, correctTTF)` 把错字映射回真字。
- **影响**: 用字体混淆的站点会存下乱码正文（**静默错数据**，比失败更糟）。
- **建议用例**: `java.queryTTF(<base64 ttf>)` → 期望非 null 对象，NovelHub 当前抛 TypeError。

#### C-25. 时间与本地化
- **参考实现**: `JsExtensions.kt:512-518`（`timeFormatUTC(time, format, sh)`）、`:523-525`（`timeFormat(time)`，用 `AppConst.dateFormat`）、`:527-541`（`encodeURI(str[, enc])`，`URLEncoder.encode`）、`:543-545`（`htmlFormat`，`HtmlFormatter.formatKeepImg`）、`:547-553`（`t2s`/`s2t` 繁简转换）、`:916-924`（`toNumChapter`，「第N章」数字规范化）
- **NovelHub**: `jsoup_shim.js:1147-1149`（`encodeURI` = `encodeURIComponent`）；`timeFormat`/`timeFormatUTC`/`htmlFormat`/`t2s`/`s2t`/`toNumChapter` **未定义**
- **判定**: `encodeURI` 确认不一致 / 其余尚未实现
- **类别**: 可实现（繁简表需要数据；`timeFormat`/`timeFormatUTC`/`htmlFormat`/`toNumChapter` 纯逻辑）
- **差异说明**: ① `encodeURI` 语义不同：Java 的 `URLEncoder.encode` 是 **form 编码**（空格→`+`、`*`→`%2A`、`~`→`%7E`），JS 的 `encodeURIComponent` 空格→`%20`、不编码 `~`；Legado 的 `JsExtensions.encodeURI` 用的是前者。② `htmlFormat`（把纯文本转成保留 `<img>` 的 HTML）在正文兜底里被用；③ `t2s`/`s2t` 用于繁体站点。
- **影响**: 用 `java.encodeURI` 拼搜索词/参数的源，遇到空格或 `~` 时请求串与站点预期不符（`codex-handoff.md` 第 8 节同类：编码差异导致的 0 结果）。
- **建议用例**: `java.encodeURI('a b~c')` → Legado 期望 `"a+b%7Ec"`，NovelHub 当前得到 `"a%20b~c"`（不一致）；`java.timeFormat(1700000000000)` → 期望 `yyyy-MM-dd HH:mm` 形式，NovelHub 当前抛 TypeError。

#### C-26. `java.toURL(url[, baseUrl])` → `JsURL`
- **参考实现**: `JsExtensions.kt:927-933` — 返回 `JsURL`（`yuedu/app/src/main/java/io/legado/app/utils/JsURL.kt`），提供 `href/path/query/…` 等解析能力
- **NovelHub**: 未定义
- **判定**: 尚未实现
- **类别**: 可实现
- **差异说明**: 书源用它做 URL 归一化/取参数。
- **影响**: 调用抛 TypeError。
- **建议用例**: `String(java.toURL('/a/b?c=1','https://x.com'))` → 期望绝对 URL，NovelHub 当前抛 TypeError。

#### C-27. `java.log` / `java.logType` / `java.toast` / `java.longToast` / `java.startBrowser` / `java.openUrl`
- **参考实现**: `JsExtensions.kt:938-941`（`toast`）、`:946-949`（`longToast`）、`:954-961`（`log`，写入 `Debug.log` 并 **返回入参**）、`:966-972`（`logType`）、`:985-1001`（`openUrl`）、`:233-236`（`startBrowser`）
- **NovelHub**: `jsoup_shim.js:1150-1153` — `longToast: function(){return null;}` / `toast: …` / `log: function(msg){ return null; }` / `showDialog: …`；`logType`/`startBrowser`/`openUrl` **未定义**
- **判定**: 确认不一致（`log` 返回值）/ 尚未实现（`logType`、`startBrowser`、`openUrl`）
- **类别**: `log`/`logType` = 可实现（应写进 crawler 日志）；`toast`/`longToast` = 需要 Android 运行时（UI）；`startBrowser` = 需要真实浏览器/Android；`openUrl` = 需要 Android 运行时
- **差异说明**: Legado 的 `log(msg)` **返回 msg**（`:960 return msg`），书源常见 `x = java.log(x)` 的链式写法；shim 返回 `null` → 后续对 `x` 取值全空。另外 shim 的 `log` 完全**不输出**，调试信息丢失（第 26 节抱怨「日志里看不到 JS 报错」的同类问题：书源自己的 `java.log` 也看不到）。
- **影响**: 用 `java.log` 做链式/调试的源静默错值；排障时看不到书源自己的日志。
- **建议用例**: `java.log('x')` → 期望 `"x"`，NovelHub 当前 `null`。

#### C-28. `java.refreshTocUrl()` / `java.reGetBook()`
- **参考实现**: `AnalyzeRule.kt:867-877`（`refreshTocUrl`，仅 `preUpdateJs` 可调）、`:845-862`（`reGetBook`）
- **NovelHub**: `jsoup_shim.js:1154` — `refreshTocUrl: function () { return null; }`；`reGetBook` **未定义**
- **判定**: 已正确（语义上等价于 Legado 在非 `preUpdateJs` 时抛 `NoStackTraceException` 被吞）/ 尚未实现（`reGetBook`）
- **类别**: `refreshTocUrl` = **需要 Android 运行时**（Legado 的语义是「重新拉书页更新 tocUrl」，需要完整 WebBook 流程）；`reGetBook` 同上
- **差异说明**: Legado 的 `refreshTocUrl` 只在 `preUpdateJs` 上下文可用，NovelHub 不执行 `preUpdateJs`（第 26 节：`preUpdateJs` 属于「需要完整运行时」一族），所以 no-op 不会比 Legado 更糟；`reGetBook` 同理。
- **影响**: `preUpdateJs` 规则整体不生效（已在第 26 节记录）。
- **建议用例**: `java.refreshTocUrl()` → NovelHub 与 Legado（非 preUpdateJs）均不产生副作用。

#### C-29. `java.getLoginInfo()` / `java.getLoginInfoMap()` / `source.getLoginInfo*`
- **参考实现**: 绑定对象上由 `BaseSource`/登录体系提供（见 `yuedu/app/src/main/java/io/legado/app/ui/login/SourceLoginViewModel.kt` 与 `SourceLoginDialog.kt` 的登录信息流）
- **NovelHub**: `jsoup_shim.js:1120-1121`（`getLoginInfo: function(){return null;}` / `getLoginInfoMap: …null`）、`:1166-1167`（`source.*` 同款）；`js_runtime.py:644`（**登录脚本**里才把 `_loginInfo` 塞进 cache）
- **判定**: 确认不一致
- **类别**: 可实现
- **差异说明**: 规则执行上下文里登录信息恒为 `null`，而 Legado 会把用户在「设置 → 书源 → 登录」里填的账号密码交给脚本（`loginUrl` 的脚本正是靠 `java.getLoginInfo()` 取值）。
- **影响**: 用 `loginUrl` 自动登录的源在规则阶段拿不到凭据（登录动作本身由 `js_runtime._build_login_script` 处理，但该脚本的 `getLoginInfo` 是**自己**的 cache，与规则阶段不一致）。
- **建议用例**: 配置账号后 `JSON.stringify(java.getLoginInfo())` → 期望含 `username`，NovelHub 当前 `"null"`。

---

### D. jsoup 兼容层（`org.jsoup.*`）

#### C-30. `Jsoup.parse` / `Jsoup.connect` 及 `Connection` 链式 API
- **参考实现**: 完整 jsoup（`org.jsoup.Jsoup`、`Connection`：`url/header/cookie/timeout/method/data/userAgent/ignoreContentType/followRedirects/execute/…`、`Response`：`body/statusCode/statusMessage/header/headers/cookies/charset`）
- **NovelHub**: `jsoup_shim.js:783-792`（只有 `Jsoup.parse` 与 `Jsoup.connect`）；`__nhConnection` 方法：`:756-762`（`ignoreContentType/header/cookie/timeout/method/data/userAgent`）、`:763-781`（`execute`，硬编码 `followRedirects` 由 curl `-L` 决定、响应码恒 `200`）；`__nhResponse` 见 `:849-877`
- **判定**: 确认不一致
- **类别**: 可实现（除 `Connection.response()`/`execute()` 的 SSL/代理细节）
- **差异说明**: ① 缺 `followRedirects(false)`、`ignoreHttpErrors`、`referrer`、`validateTLSCertificates`、`sslSocketFactory`、`data(Map)`、`parser`、`postDataCharset`、`requestBody`；② `execute()` 的 `__nhResponse(this.url, text, 200, {})` **把状态码写死 200**（`:780`），书源读 `.statusCode()` 永远 200，且**不解析 Set-Cookie**；③ `Jsoup.parseBodyFragment`、`Jsoup.clean`、`Jsoup.isValid` 缺。
- **影响**: 用 `Jsoup.connect(...).followRedirects(false).execute().statusCode()`/`.cookies()` 的源拿不到真实状态码与 Set-Cookie。
- **建议用例**: `org.jsoup.Jsoup.connect(u).method(org.jsoup.Connection.Method.HEAD).execute().statusCode()` → 期望真实码（如 302/404），NovelHub 当前恒 `200`。

#### C-31. `Element`/`Elements` 方法覆盖
- **参考实现**: jsoup `Element` 全量（`select/selectFirst/selectXpath`、`attr/attributes/hasAttr/removeAttr`、`text/ownText/textNodes/wholeText/data`、`html/outerHtml/val`、`children/child/parent/parents/siblingElements/nextElementSibling/previousElementSibling/nextElementSiblings/previousElementSiblings`、`getElementsByTag/Class/Attribute/AttributeValue/ContainingText/ContainingOwnText`、`getAllElements`、`addClass/removeClass/hasClass/className/classNames`、`id/tagName/nodeName`、`append/prepend/appendChild/remove/unwrap`、`equals/hashCode`、`firstElementChild/lastElementChild/elementSiblingIndex`）；`Elements`（`size/get/first/last/isEmpty/eachAttr/eachText/text/html/outerHtml/remove/addClass/…`）
- **NovelHub**: `jsoup_shim.js:168-210`（`__nhElements`：`size/get/first/last/isEmpty/eachAttr/eachText/select/selectFirst/remove/addClass/removeClass/hasClass/text/html/outerHtml/toString/toArray`）；`:417-697`（`__nhEl`：见 C-1/C-5/C-8/C-9 已核对的属性选择器/selectFirst/equals，另含 `attr/hasAttr/removeAttr/absUrl/attributes/text/ownText/ownTexts/textNodes/ownTextNodes/data/html/outerHtml/parent/children/childNodeSize/firstElementChild/lastElementChild/nextElementSibling/previousElementSibling/siblingElements/addClass/removeClass/hasClass/is/className/classNames/id/tagName/nodeName/val/remove/append/appendChild/prepend/getElementsByTag/getElementsByClass/getElementById/getElementsByAttribute/getElementsContainingText/toJSON`）
- **判定**: 确认不一致（**子集**）
- **类别**: 可实现（大部分）
- **差异说明**: 缺 `Element.select(String, int)`（带索引重载）、`selectXpath`、`wholeText`、`parents`、`nextElementSiblings`/`previousElementSiblings`（复数）、`getElementsByAttributeValue`（及 `Starting/Ending/Containing`）、`getAllElements`、`elementSiblingIndex`、`unwrap`、`hashCode`（`equals` 有但对象作 Map key 会不同）、`Elements.attr(name)`/`Elements.val()`/`Elements.forms()`/`Elements.not()`/`Elements.eq()`/`Elements.select(String,int)`、`Element.tag()`（返回 `Tag`）、`Element.ownerDocument()`、`Element.charset()`。
- **影响**: 书源里 `el.nextElementSiblings()`、`doc.select("a", 1)`、`el.tag()` 等写法抛 TypeError（被 try/catch 吞掉 → 空值）。
- **建议用例**: `org.jsoup.Jsoup.parse('<div><i>a</i><i>b</i></div>').selectFirst('i').nextElementSiblings().size()` → 期望 `1`，NovelHub 当前抛 TypeError。

#### C-32. HTML 解析保真度（手写解析器 vs jsoup）
- **参考实现**: jsoup 的 HTML5 容错解析（隐式 `<p>`/`<li>` 闭合、`<table>` 修复、实体解码、`<title>` 在 head）
- **NovelHub**: `jsoup_shim.js:110-165`（`__nhParse`：栈式、只处理显式闭合、`script/style` 特判 `:151-159`、void 标签表 `:80-83`、注释/doctype 跳过 `:119-128`）；属性解析 `:84-92`；序列化 `:495-508`
- **判定**: 确认不一致
- **类别**: 可实现（但完整 HTML5 解析成本高）
- **差异说明**: ① 不闭合标签靠 `</...>` 出栈（`:132-141`），遇到缺失闭合标签的页面会**层级错位**（jsoup 会按 HTML5 规则补全）；② 不做 HTML 实体解码（`&amp;` 原样留在 text 里，jsoup 会解成 `&`）；③ `__nhSerialize`（`:495-508`）会丢掉属性引号/实体，`outerHtml` 与 jsoup 不一致；④ 不处理 `<template>`、`<svg>` 命名空间。
- **影响**: 结构不规范的老站点（`codex-handoff.md` 第 8/26 节的 xbookcn/绅士漫画）可能选择器命中层级与 Legado 不同；实体不解码会让书名/正文带 `&amp;` 这类残留。注意 **NovelHub 的 CSS/XPath 主解析走的是 Python `BeautifulSoup(lxml)`**（`rule_engine.py:1638-1645`），shim 的解析器只影响 **JS 内**的 `org.jsoup.Jsoup.parse(...)` 与 `java.getString`（`:1027`）。
- **建议用例**: 输入 `<div><p>a<p>b</div>`，脚本 `org.jsoup.Jsoup.parse(<html>).select('p').size()` → jsoup 期望 `2`，NovelHub 当前 `1`（第二个 `<p>` 成为第一个的子节点）。

#### C-33. `Element.text()` 的空白语义
- **参考实现**: jsoup `Element.text()`：拼接文本节点后 `normaliseWhitespace`，**保留节点间一个空格**（`<b>a</b><i>b</i>` → `"a b"`），`ownText()` 同理只看直接文本子节点
- **NovelHub**: `jsoup_shim.js:437-445`（`collect` 后 `parts.join('').replace(/\s+/g,' ').trim()`）、`:446-452`（`ownText` 同款）；对比 Python 侧 `rule_engine._extract_css_value`（`:1258-1261`）用 `el.get_text("\n", strip=True)`
- **判定**: 确认不一致
- **类别**: 可实现
- **差异说明**: shim 的 `text()` **不插入节点分隔符**：`<b>a</b><i>b</i>` → `"ab"`，jsoup → `"a b"`；Python 主路径用 `\n` 分隔，三者各不相同。含行内标签的标题/作者拼接结果会退化成连在一起的字符串（影响书名匹配、章节标题去重）。
- **影响**: 用 `书名<b>（完）</b>` 这类结构的源，JS 路径得到 `书名（完）` 或粘连形式，Python 路径得到换行形式；同名书匹配（第 22 节按标题匹配）可能因此对不上。
- **建议用例**: `org.jsoup.Jsoup.parse('<div><b>第一章</b><i>标题</i></div>').selectFirst('div').text()` → 期望 `"第一章 标题"`，NovelHub 当前 `"第一章标题"`。

---

### E. 宿主/全局环境

#### C-34. 全局 `Get`/`Put`/`Set`/`sleep`/`Rate`/`Reload`/`Url`
- **参考实现**: 在**本仓库的 `yuedu/` 源码里找不到这些全局的定义**（grep `function Get(`/`Reload(`/`Rate(` 在 `yuedu/` 无命中）；实际书源（UAA 等）是**在书源自己的 JS 里定义/覆盖** `Reload(url)`、`Url()`、`Rate()` 等辅助函数
- **NovelHub**: `jsoup_shim.js:1204-1234` — `Get/Put/Set`（走 `__nhVars`）、`sleep(ms)`（`Atomics.wait` 忙等，`:1211-1222`）、`Rate()` 返回固定 `800`、`Reload(url)` 用裸 curl（**带写死的 Android UA + Referer=bookSourceUrl**，`:1224-1231`）、`Url()` 返回 `baseUrl` 或 `__nhVars.baseUrl`（`:1232-1234`）；`:1245-1262` 挂到 `globalThis`
- **判定**: 无法判定（以 `yuedu/` 为权威无法确认这些全局的规范定义；shim 提供的是近似实现）
- **类别**: `Get/Put/Set/sleep/Url` = 可实现；`Reload` = **需要真实浏览器/Android**（UAA 的 `Reload` 语义是「用浏览器重新加载页面」）
- **差异说明**: shim 的 `Reload` 是普通 HTTP GET，与 UAA 期望的「浏览器环境重载」不同；`Rate()` 固定 800 与 Legado 的 `concurrentRate` 无关；`sleep` 在 Node 单线程里忙等会**阻塞整个 JS 子进程**（含其它并发书源的 eval，因为只有一个 Node 进程 + 一把 `_session_lock`，`js_runtime.py:309`）。
- **影响**: 用 `sleep` 的书源会串行阻塞所有书源的 JS 求值（性能/超时风险）；`Reload` 的源行为与 Legado 不同。
- **建议用例**: `Url()`（baseUrl=`https://a/`）→ 期望 `"https://a/"`（NovelHub 当前 `"https://a/"`）；`Rate()` → 无法在 Legado 侧确定期望值。

#### C-35. JS 引擎能力与沙箱
- **参考定义**: Rhino（`yuedu/modules/rhino/**`：`RhinoScriptEngine.kt`、`RhinoClassShutter.kt` 等），ES 版本对应 Rhino 1.x（`let/const`/箭头函数可用，见 `JsTest.kt:52-69`、`:115`）；`RhinoClassShutter.kt:50-73,111-113` **屏蔽** `java.lang.Class`/`Runtime`/`ProcessBuilder`/`java.io.File*`/`java.nio.file.*`/反射等
- **NovelHub**: Node.js（`js_runtime.py:218-231`，`node --no-warnings` + `eval`），**无沙箱**：shim 里可直接 `require('child_process').execSync`（`:803`、`:888`），书源 JS 也能 `require`/`process`/`fetch`
- **判定**: 确认不一致
- **类别**: 可实现（收紧）
- **差异说明**: ① 语言特性上 Node ≥ Rhino，书源不会因语法不可用而失败（比 Legado 宽松）；② **安全边界完全不同**：Legado 用 `RhinoClassShutter` 挡掉文件/进程/反射，NovelHub 的 Node 子进程里书源脚本可读写容器文件、起进程；③ `typeof`/`String()` 等边界语义一致（`JsTest.kt:124-133` 的 `typeof String()` 用例在 Node 下同为 `"string"`）。
- **影响**: 不是「书源失败」问题，而是**信任边界**问题：导入第三方书源 JSON 等于允许其脚本在 crawler 容器内执行任意代码（NovelHub 当前无对应限制）。
- **建议用例**: `typeof require` → Node 侧 `"function"`，Legado 侧不存在 `require`。**这条不涉及书源兼容性，但涉及安全**。

#### C-36. `String.prototype` 的 Java 语义补丁
- **参考定义**: Rhino 里书源字符串是 Java `String`（或 JS string），书源常用 `replaceAll`/`replaceFirst`/`matches`/`split`（Java 语义：`replaceAll` 第一个参数是**正则**、`split` 参数是正则）
- **NovelHub**: `jsoup_shim.js:33-77` — 补了 `replaceAll`（`:42-54`）、`replaceFirst`（`:55-61`）、`matches`（`:62-68`，要求整体匹配 `m[0]===s`）、**重写 `split`**（`:69-77`，当分隔符含正则元字符时按正则切）
- **判定**: 已正确（意图与实现基本对齐）/ 无法判定（覆盖不全）
- **类别**: 可实现
- **差异说明**: Java 的 `"a|b".split("|")` 在 Java 里 `|` 是正则（切每个字符），JS 原生 `split` 是字面量；shim 用 `/[\^$.*+?()[\]{}|]/` 判定后按正则切，方向正确。已知缺口：`:35` 对 `RegExp` 参数做了 global 化；`:72` 的元字符表漏了 `-`（`[a-z]` 场景）、`\` 已含；`replaceAll` 的 replacement 里 Java 的 `\1`/`$1` 语义与 JS 不同（未转换）。
- **影响**: 用 `s.replaceAll("(\\d+)","[$1]")`（Java 风格）的源在 shim 下会得到字面 `[$1]` 而不是捕获组。
- **建议用例**: `"a1b2".replaceAll("(\\d+)", "[$1]")` → Java/Rhino 期望 `"a[1]b[2]"`，NovelHub 当前 `"a[$1]b[$2]"`。

#### C-37. `eval` 包装方式与「最后一句表达式」语义
- **参考定义**: Rhino `CompiledScript.eval` 返回**脚本最后一条表达式的值**（`JsTest.kt:29-41`、`:80-94`：`"$=result;id=$.id;id"`、`result.get("id")`、`s[2].substr(0,n);` 都靠这个语义）
- **NovelHub**: `js_runtime.py:344-358` — `try{__codex_out__=eval(__codex_src__);}catch(e){<user_code>; __codex_out__=result;}` + `return __codex_out__===undefined?result:__codex_out__`
- **判定**: 已正确（主语义）
- **类别**: 可实现
- **差异说明**: ① `eval(code)` 能返回最后一句表达式的值（与 Rhino 一致），且失败时退回「把代码当语句执行、返回入参」；② 隐患：`catch` 分支里把整段 `user_code` **再执行一次**（`:354`），若脚本有副作用（`java.put`/`java.setCookie`/网络）会**执行两遍**；③ `eval` 在 Node 里无法拿到 `let/const` 声明的跨语句可见性差异（`eval("let x=1"); x` 会 ReferenceError → 走 catch 分支再执行一次）。
- **影响**: 用 `let/const` 的脚本（`JsTest.kt:52-69` 那种写法在书源里也存在）可能被**执行两次**，导致重复请求/重复 put。
- **建议用例**: `var n=0; java.put('n', String((parseInt(java.get('n')||'0',10))+1))` 连续求值两次 → 期望每次 +1；NovelHub 里若首次 `eval` 抛错会一次 +2。

#### C-38. Rhino `JSAdapter` 语义
- **参考定义**: `yuedu/modules/rhino/src/main/java/com/script/rhino/JSAdapter.kt:72-306`（`__get__`/`__put__`/`__has__`/`__delete__`/`__getIds__` 钩子，全局注册在 `:295-301` `defineProperty(scope,"JSAdapter",obj,2)`）
- **NovelHub**: Node 侧**没有 `JSAdapter` 全局**
- **判定**: 尚未实现
- **类别**: 可实现（用 `Proxy` 等价实现）
- **差异说明**: 书源里极少直接用 `JSAdapter`，但它在 Rhino 里是全局可用的。shim 未注册 → 调用抛 `ReferenceError`。
- **影响**: 低（仅在书源显式使用 `new JSAdapter({...})` 时失败）。
- **建议用例**: `new JSAdapter({__get__: function(n){return 'x'+n;}}).foo` → 期望 `"xfoo"`，NovelHub 当前抛 ReferenceError。

---

### F. 总表：需要 Android 运行时 / 真实浏览器 ⇒ **永远无法实现**

| API | 参考位置 | 为什么无法实现 |
|---|---|---|
| `startBrowser` | `JsExtensions.kt:233-236` | 依赖前台 `OpenUrlConfirmActivity`/`SourceVerificationHelp` 人工过验证 |
| `startBrowserAwait(url,title[,refetch])` | `:241-251` | 同上，且等待用户交互（shim 已抛诚实错误 `jsoup_shim.js:1127-1129`） |
| `getVerificationCode(imageUrl)` | `:256-259` | 需要向用户展示图片验证码并等待输入（shim 已抛诚实错误 `:1130-1132`） |
| `webView(html,url,js)` | `:170-183` | 需要 Android WebView 渲染 + 执行页面 JS |
| `webViewGetSource(...)` | `:188-202` | 同上 |
| `webViewGetOverrideUrl(...)` | `:207-226` | 同上 |
| `openUrl(url[,mimeType])` | `:985-1001` | 需要 Android `Intent`/Activity |
| `importScript(path)`（本地相对路径分支） | `:264-271` | 依赖 App 私有 cache 目录；**http 分支可实现**（下方备注） |
| `downloadFile`/`cacheFile`/`getFile`/`readFile`/`readTxtFile`/`deleteFile`/`unzipFile`/`un7zFile`/`unrarFile`/`getTxtInFolder`/`getZip*`/`getRar*`/`get7z*` | `:278-790` | 属于 App 私有目录 + `SecurityException("非法路径")` 沙箱语义（`:566-579`）；**纯逻辑上可实现**，但要么放弃沙箱、要么重新定义目录约定 |
| `toast`/`longToast` | `:938-949` | 需要 Android UI（shim 已 no-op） |
| `refreshTocUrl`（`preUpdateJs` 上下文） | `AnalyzeRule.kt:867-877` | 需要完整 `WebBook` 刷新流程 |
| `reGetBook` | `AnalyzeRule.kt:845-862` | 需要完整 `WebBook` 精确搜索流程 |

> **可替代说明（不算「无法实现」但需要真实浏览器）**：`webView`/`webViewGetSource`/`webViewGetOverrideUrl` 在 NovelHub 已用 Playwright（`render.py`，见 `codex-handoff.md` 第 32 节）覆盖了「用浏览器渲染页面」的**大部分**用途；但**书源脚本内**调用 `java.webView(...)` 目前不会路由到 Playwright，而是 `undefined`。
>
> **可实现但未实现**（应补）：`java.ajaxAll`、`java.head`、`java.md5Encode16`、`java.digestHex`、`java.digestBase64Str`、`java.HMacHex`、`java.HMacBase64`、`java.strToBytes`、`java.bytesToStr`、`java.hexDecodeToByteArray`、`java.hexEncodeToString`、`java.base64DecodeToByteArray`、`java.createSymmetricCrypto`(+AES/DES/3DES 系列)、`java.createAsymmetricCrypto`、`java.createSign`、`java.timeFormat`、`java.timeFormatUTC`、`java.htmlFormat`、`java.t2s`/`java.s2t`、`java.toNumChapter`、`java.toURL`、`java.logType`、`java.queryTTF`/`java.replaceFont`、`java.importScript`(http)、`source.bookSourceUrl` 等属性、书源 `header` 注入 JS HTTP、JS 侧限速与 Cookie 会话。

---

### G. 复核方法与未覆盖范围

- **本仓库 `yuedu/` 的 `Reload`/`Get`/`Put`/`Rate`/`Url`**：在 `yuedu/` 里 grep 不到定义（见 C-34），故这组全局**无法以本仓库为权威判定**，一律标 `无法判定`。
- **`AnalyzeByJSoup`/`AnalyzeByXPath` 的 CSS/XPath 细节**：本轮只核到「模式选择」（`AnalyzeRule.kt:573`/`:568`）与 `splitRule`（`AnalyzeByJSoup.kt:88,139,157,212`），**没有逐行对照** `AnalyzeByJSoup.kt` 的 `getString/getStringList` 与 shim 的 CSS 子集（C-31/C-32 的结论来自 jsoup 文档语义 + shim 代码，未逐条跑 jsoup 实测）。
- **`JsURL.kt`、`QueryTTF.java`、`AnalyzeByJSonPath.kt`、`AnalyzeByRegex.kt`**：本轮只读了签名/调用点，**没有**逐一比对实现语义（`java.toURL`、`queryTTF`、JSONPath 已按「未定义/未实现」结论给出，但 JSONPath 的 Python 实现 `rule_engine._jsonpath` 未做完整差异比对）。
- **`js_enabled`/`RhinoClassShutter` 黑名单**：只读了清单（`RhinoClassShutter.kt:50-73,111-113`），没有验证 shim 侧对应约束（C-35 的结论方向明确：Node 侧无约束）。
- **未验证的推断（明确标注）**：C-25 中 `java.encodeURI` 与 `encodeURIComponent` 的差异来自两处实现（`JsExtensions.kt:527-541` vs `jsoup_shim.js:1147`）的代码语义对比，**未在真机/真站实测**。
- **未做的运行时实测**：本轮**没有**运行 Node 去实际执行任何 JS 用例（`backend/tests/test_rule_engine_legado.py` 里已有 4 个 shim 用例被引用为「现有覆盖」，但未重跑）；因此「NovelHub 当前得到 X」一栏中，凡未直接引用既有断言的，都是**按代码路径推断**的预期值，不是实测输出。

#### G-1. 抽查记录（本次实际核对过 file:line 的锚点）

| 文件 | 已核对行 |
|---|---|
| `backend/app/crawler/plugins/yuedu/jsoup_shim.js` | 236, 244, 567, 831, 860, 1000, 1003, 1007, 1022, 1024, 1028, 1054, 1067, 1076, 1108, 1114, 1117-1118, 1124, 1127, 1130, 1133-1146, 1154, 1180-1181, 1204, 1224, 1236 |
| `backend/app/crawler/plugins/yuedu/js_runtime.py` | 38, 54, 58, 295, 320, 324, 329, 334, 337, 339, 344, 349, 354, 357, 375, 392, 779, 860, 863 |
| `backend/app/crawler/plugins/yuedu/rule_engine.py` | 462, 501, 539, 1007, 1060, 1082, 1148, 1200, 1206, 1227, 1231, 1277, 1313, 1338, 1362, 1370, 1392, 1417, 1428, 1445, 1449, 1457, 1524, 1545, 1564, 1631, 1638, 1714, 1722, 1737 |
| `backend/app/crawler/plugins/yuedu/transport.py` | 212-226, 251-264, 280, 855-859, 897 |
| `backend/app/crawler/plugins/yuedu/explore.py` | 60-126, 255-293 |
| `yuedu/app/src/main/java/io/legado/app/help/JsExtensions.kt` | 129, 145, 170, 188, 207, 233, 241, 249, 256, 264, 278, 305, 322, 357, 378, 399, 420, 440-505, 512-557, 566, 581, 589, 598, 609, 619-646, 659-790, 802, 813, 857, 867, 904, 916, 927, 938, 954, 966, 977, 985, 990 |
| `yuedu/app/src/main/java/io/legado/app/help/JsEncodeUtils.kt` | 20, 24, 42, 51, 58, 65, 77, 84, 103-427（逐签名）, 438, 452, 468, 485 |
| `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeRule.kt` | 84, 98, 163, 250, 256, 263, 270, 296, 332, 367, 399, 408, 436, 462, 475, 530, 545-579, 590, 627, 659, 708, 721, 740, 754, 774, 785, 806, 819, 845, 867, 880 |
| `yuedu/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeUrl.kt` | 77-88, 92, 111, 145-152, 348-371, 373, 382, 400, 408 |
| `yuedu/app/src/main/java/io/legado/app/help/http/StrResponse.kt` | 16-83（全文） |
| `yuedu/modules/rhino/src/main/java/com/script/rhino/JSAdapter.kt` | 72, 81, 99, 119, 179, 287-301 |
| `yuedu/modules/rhino/src/main/java/com/script/ScriptBindings.kt` | 7-45（全文） |
| `yuedu/modules/rhino/src/main/java/com/script/rhino/RhinoClassShutter.kt` | 50-73, 111-113 |
| `yuedu/app/src/test/java/io/legado/app/JsTest.kt` | 28-133（全文） |
| `docs/codex-handoff.md` | 第 3 节表格全表、第 8、10、26、30 节 |

#### G-2. 统计

- 条目总数：**38**（C-1 … C-38）
- 判定分布：`已正确` **13**（C-1、C-3、C-4、C-5、C-6(有)、C-7、C-8、C-9、C-10、C-22(4 个)、C-28、C-36、C-37）
- `确认不一致` **12**（C-2、C-11(部分)、C-12、C-14、C-15、C-16(post)、C-17、C-18、C-19、C-20、C-25(encodeURI)、C-27(log)、C-30、C-31、C-32、C-33、C-35 —— 部分条目为混合判定，故合计大于 12）
- `尚未实现` **11**（C-11 的 webView 族、C-13、C-16(head)、C-21、C-22 缺项、C-23、C-24、C-25 缺项、C-26、C-28(reGetBook)、C-29、C-38）
- `无法判定` **2**（C-34、C-36 的覆盖度）


---

## 8. 建议的下一步

1. **把本表变成测试**：每条「建议用例」加进 `backend/tests/test_rule_engine_legado.py`。
   对 M-7/M-8/M-9/M-10 这类**可能是有意偏差**的条目，测试应当**记录当前行为**并注明
   「与 Legado 规范不一致，待裁决」，而不是直接断言规范值 —— 否则会把有意的改进判成失败。
2. **建差分 oracle**：`yuedu/app/src/test/java/io/legado/app/JsTest.kt` 是**纯 JVM 单测**
   （`org.junit.Test`，无 Robolectric / Android runner），直接驱动 `RhinoScriptEngine` +
   `ScriptBindings`。说明**真 Legado 的 JS 引擎能在普通 JVM 上跑**，因此可以用 Gradle
   JVM harness 对 `{规则 JSON, HTML, URL}` 取 ground truth，与 NovelHub 输出逐条 diff ——
   这是定位 M-5（XPath 静默降级）与 C-1（API 缺口）的唯一系统性办法。
3. **合并两套 `java.*` stub**（C-1）：`jsoup_shim.js:1064` 与 `js_runtime.py:535-695`。
4. **裁决 M-7/M-8/M-9**：决定是「对齐 Legado」还是「记为有意偏差并写注释」。
