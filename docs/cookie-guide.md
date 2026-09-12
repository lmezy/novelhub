# Cookie 获取与使用

NovelHub 用浏览器 Cookie 登录书源站点。**绝大多数书源不需要 Cookie**；只有站点要求
验证码 / 人机验证 / Cloudflare 挑战，或需要登录才能看书架时，才需要导入。

## 1. 获取 Cookie

**方法 A：DevTools 手动复制**

1. 用 Chrome / Edge 打开站点并完成登录（或通过验证码 / Cloudflare 验证）；
2. `F12` → **Application（应用程序）** → 左侧 **Cookies** → 选站点域名；
3. 把 Name=Value 拼成一整串，用 `; ` 分隔：

```
name1=value1; name2=value2; name3=value3
```

**方法 B：扩展导出（推荐）**：EditThisCookie → Export → 选 Netscape 或 Header String。

## 2. 导入 NovelHub

「设置 → 书源」→ 找到该书源 → 点“Cookie / 账号”展开 → 粘贴 Cookie → 保存 → 点“测试”。

同一处还能填书源账号密码，用于自动登录刷新失效的 Cookie。Cookie 以 AES-256-GCM 加密
存储，请求时原样放进 `Cookie` 头（URL 编码的值不用手工解码）。

## 3. 什么时候必须用 Cookie

| 站点 | 防护 | 处理 |
|---|---|---|
| SiS文學網 `b.sis.la` | Cloudflare 挑战页 | 浏览器过验证后导入 Cookie |
| 御宅屋 `yswhub.cc` | Cloudflare 挑战页 | 同上 |
| 第一版主 `banzhu…net` | Cloudflare 挑战页 | 同上 |
| 菠萝猫 `boluomao.com` | GoEdge 图形验证码 | 同上 |
| 搬山人小说网 `banshanren.com` | 浏览器也会被挑战 | 同上 |
| 爱丽丝书屋 `alicesw.com` | 无（曾 DNS 污染，插件已用 DoH 绕过） | 一般不需要 |
| 禁忌书屋 `cool18.com` | 无（页面里的“请稍后再试”是正常文案） | 一般不需要 |

**关键**：浏览器过验证所用的出口 IP 要和 NovelHub 的代理节点一致，否则 Cookie 与 IP
不匹配，导入后仍会被拦。

## 4. 常见问题

- **测试失败**：站点不可达 / 域名变了 / Cookie 过期 / Cookie 绑定了签发时的 IP 或浏览器
  指纹 / 该书源不支持书架抓取。先确认浏览器里还能正常打开书架页。
- **提示 No cookie found**：Cookie 的 `source` 必须与书源 ID 完全一致（大小写敏感），
  例如 `yuedu_b38b98d309e3`。
- **Cookie 太长**：输入框是 textarea，可以贴整段；JSON 格式可用在线工具转成 Header String。
- **盗版站换域名**：在「设置 → 书源」编辑书源 URL，重新登录取 Cookie 再更新。
- **Cookie 失效**：重新登录取新 Cookie 覆盖；填了账号密码的会由定时任务尝试自动刷新。

## 5. 安全提醒

Cookie 等于登录凭证：不要分享、不要提交到仓库或粘贴进聊天记录。建议用专用小号。
