# Cookie 获取指南

NovelHub 通过浏览器 Cookie 来登录小说网站。本指南介绍如何获取各网站的 Cookie。

---

## 通用方法（适用于所有网站）

### 方法 A：浏览器 DevTools 手动复制

1. 用 **Chrome** 或 **Edge** 打开目标小说网站
2. 正常登录你的账号
3. 按 `F12` 打开开发者工具
4. 切换到 **Application**（应用程序）标签
5. 左侧找到 **Cookies** → 点击网站域名
6. 你会看到一列 Name/Value 的 cookie 条目
7. 按以下格式拼接所有 cookie（用 `; ` 分隔）：

```
name1=value1; name2=value2; name3=value3
```

8. 复制整串文本，粘贴到 NovelHub 管理后台的 Cookie 输入框

### 方法 B：浏览器扩展一键导出（推荐）

安装 **EditThisCookie**（Chrome/Edge 扩展商店均有）：

1. 登录网站后，点击扩展图标
2. 点击导出按钮（Export）
3. 格式选择 **Netscape** 或 **Header String**
4. 复制导出内容，粘贴到 NovelHub

---

## AliceSW（爱丽丝书屋）

**网站域名可能变化。** 常见域名包括：

- `www.alicesw.com`
- `www.alicesw.me`
- `www.alicesw.org`

如果默认域名 (`www.alicesw.com`) 无法访问，请自行搜索最新域名，并在创建 Source 时修改 URL。

### 获取步骤

1. 打开 AliceSW 网站并登录
2. 登录后按 F12 → Application → Cookies
3. 复制所有 cookie
4. 在 NovelHub Admin → Cookies → Add Cookie：
   - **Source**: 填 `alicesw`
   - **Cookie Data**: 粘贴 cookie 字符串
   - **Expired At**: 留空或设置过期日期

### 关键 Cookie

通常需要以下 cookie 才能正常工作：

- 会话 cookie（如 `PHPSESSID`、`token`、`user`）
- 登录态 cookie（如 `remember_me`、`auth`）

### 测试

保存后在 Admin → Sync 页面，输入 `alicesw` 作为 Source ID，选择"书架同步"测试是否成功。

---

## Qidian（起点中文网）

网站：`https://www.qidian.com`

### 获取步骤

1. 打开 qidian.com 并登录（可用 QQ/微信/手机号）
2. F12 → Application → Cookies → `www.qidian.com`
3. 复制所有 cookie
4. 在 NovelHub Admin → Cookies → Add Cookie：
   - **Source**: 填 `qidian`
   - **Cookie Data**: 粘贴 cookie 字符串

### 关键 Cookie

- `_csrfToken` — CSRF 令牌
- `qd_uid` / `qd_theme` — 用户标识
- 登录后自动生成的会话 cookie

### 注意

- 起点部分内容有字体加密，正文抓取可能需要额外处理
- Cookie 有效期通常较长（数周到数月）
- 如果开启了两步验证，需要保持浏览器会话活跃

---

## Fanqie（番茄小说）

网站：`https://fanqienovel.com`

### 获取步骤

1. 打开 fanqienovel.com 并用手机号登录
2. F12 → Application → Cookies → `fanqienovel.com`
3. 复制所有 cookie
4. 在 NovelHub Admin → Cookies → Add Cookie：
   - **Source**: 填 `fanqie`
   - **Cookie Data**: 粘贴 cookie 字符串

### 关键 Cookie

- `novel_web_id` — 设备标识
- 登录后的 session token

### 注意

- 番茄小说的页面大量使用 JavaScript 渲染，NovelHub 使用 Playwright 浏览器来处理
- 首次同步可能较慢，因为需要启动 headless 浏览器

---

## 常见问题

### Cookie 失效了怎么办？

Cookie 通常有有效期。失效后需要重新登录网站并获取新 cookie，然后在 NovelHub 中更新。

### Cookie 字符串太长粘贴不了？

后台的 cookie 输入框是 textarea，可以容纳很长的内容。如果是从 EditThisCookie 导出的 JSON 格式，可以用 [EditThisCookie JSON to Header String](https://www.convertsimple.com/convert-json-to-cookie-string/) 转换。

### 同步时提示 "No cookie found"

确认 Cookie 的 Source 字段与 Source 的 ID 完全一致（大小写敏感）。

- AliceSW → Source ID: `alicesw`
- Qidian → Source ID: `qidian`
- Fanqie → Source ID: `fanqie`

### 盗版站域名变了怎么办？

1. 找到新域名
2. 在 Admin → Sources 中编辑对应 Source 的 URL
3. 用新域名登录并获取新 Cookie
4. 更新 Cookie

---

## 安全提醒

- Cookie 包含你的登录凭证，**不要分享给他人**
- NovelHub 使用 AES-256-GCM 加密存储所有 Cookie
- 建议使用小号或专用账号，避免主账号风险