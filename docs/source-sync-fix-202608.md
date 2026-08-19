# NovelHub 书源同步修复说明（2026-08）

本说明汇总对以下三个问题的修复：首页按书源显示书籍、菠萝包/UAA 书源同步失败、爱丽丝书屋被限制。

---

## 一、问题根因与修复

### 1. 首页"按书源显示书籍"不见了

**根因**：服务器上部署的前端容器是旧版源码构建的（`npm run dev` 运行的是构建镜像时 COPY 的旧代码），
缺少"按书源浏览"功能（该功能在最新提交 820dde7 中才加入 BooksPage.vue）。且真正的首页
（`/`，HomePage.vue）此前从未有该功能。

**修复**（`frontend/src/pages/HomePage.vue`）：
- 首页新增"按书源浏览"区块：并行加载每个书源最近 6 本书（`/books/browse?source_id=X&limit=6`），
  按书源分组展示，点击"查看全部"进入 `/books?source=xxx`。
- 已构建新的 `frontend/dist`。

### 2. 菠萝包（boluomao.com）同步失败

**根因**：网站部署了 **GoEdge WAF**，服务器直连时返回"身份验证"图形验证码页面（HTTP 200），
插件未识别该页面，静默解析为 0 本书。

**修复**（`backend/app/crawler/plugins/yuedu/__init__.py`）：
- 扩展 `STRONG_BLOCK_MARKERS`：新增 `goedge_waf`、`身份验证`、`请输入上面的验证码`、
  `访问被拒绝`、`已被限制` 等标记，现在会识别验证码页面并抛出明确错误：
  `Site returned an anti-bot/captcha page (网站要求验证码/人机验证，请在浏览器中访问该网站通过验证后，把 Cookie 导入书源再同步)`
- `fetch_explore` 在所有分类都被拦截时向上抛出错误，crawl 任务会显示真实原因，
  不再出现误导性的"0 本书"。

**注意**：菠萝猫的 WAF 无法在服务器端自动绕过。请在浏览器（或阅读 App）中访问
`https://www.boluomao.com` 通过验证码后，把浏览器 Cookie 导入 NovelHub（设置 → Cookie 管理，
或书源导入时填写 Cookie），再执行同步。

### 3. UAA 书源同步失败

**根因**：UAA 书源的 `exploreUrl` / `searchUrl` / `ruleToc` / `ruleContent` / `header`
全部是 `eval(String(Reload('https://qyyuapi.com/qt/js/UAA小说/xxx.js')))` 形式的远程 JS 规则，
脚本是重度混淆的，依赖完整 Legado Android 运行时（`source`、`cache`、`java.importScript`、
`window` 等）和登录 token。NovelHub 的 Node.js shim 无法完整模拟，多次尝试补充 API
（md5Encode16、importScript、cache_api 等）后仍无法执行。

**修复**：
- 现在会抛出明确的中文错误：
  `该书源的发现规则是 Legado JS 脚本（<js>/@js:），当前环境无法执行；请在 Legado 中搜索书籍后通过书源搜索/手动链接同步，或更换该网站的其他书源。`
- 不再静默返回 0 本书。

**建议**：UAA 属于"写源"作者的专用书源（需要登录、签名、JS 环境），建议在 yckceo 书源库
（https://www.yckceo.com/yuedu/shuyuan/index.html）寻找 UAA 小说的其他实现，或删除该书源。

### 4. 爱丽丝书屋（alicesw.com）报被限制

**根因**：**DNS 污染**。`www.alicesw.com` 在服务器和本地的系统 DNS（包括 8.8.8.8、
114.114.114.114）都解析到 `127.0.0.1`（GFW 污染），连接被拒绝。8/11 曾成功同步 488 本，
之后域名被污染。通过 DoH 查询得到真实 IP：**38.46.217.34**。

**修复**（`backend/app/crawler/plugins/yuedu/__init__.py`）：
- 新增 **DNS 污染自动绕过**：当系统 DNS 解析为回环地址（127.0.0.1/0.0.0.0/::1）或连接失败时，
  自动通过 DoH（腾讯 doh.pub → Cloudflare → Google）解析真实 IP，改用 `IP + Host 头` 直连，
  结果缓存 300 秒。完全透明，无需配置。
- 已验证：本地测试 `plugin._get("https://www.alicesw.com/lists/65.html")` 成功返回 42KB 页面，
  `fetch_explore` 从"乱伦"分类抓到 49 本书。

**注意**：书源配置里的 url 保持 `https://www.alicesw.com` 不变，无需修改。

---

## 二、修改文件清单

| 文件 | 修改内容 |
|---|---|
| `frontend/src/pages/HomePage.vue` | 首页新增"按书源浏览"区块（+82 行） |
| `backend/app/crawler/plugins/yuedu/__init__.py` | GoEdge 拦截识别、DoH DNS 污染绕过、JS 书源明确错误、双语错误信息（+215 行） |
| `backend/tests/test_yuedu_plugin.py` | 新增 7 个测试：GoEdge 识别、DNS 污染、DoH 重写/缓存、错误传播（+120 行） |
| `frontend/dist/*` | 重新构建的前端产物 |

未改动的部分：数据库结构（无迁移）、API 接口、其他插件。

---

## 三、部署步骤

### 后端

```bash
cd /path/to/novelhub   # 服务器上的部署目录
# 拉取新代码后重新构建后端镜像
docker compose build backend
docker compose up -d backend
```

### 前端

当前 Dockerfile 是 `npm run dev`（开发模式，构建时 COPY 源码）。要部署新前端：

```bash
docker compose build frontend
docker compose up -d frontend
```

或者改用生产模式：修改 `frontend/Dockerfile` 使用构建产物 + nginx：

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json .
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

并更新 `nginx/nginx.conf` 把前端 upstream 指向 `frontend:80`（或直接由 nginx 提供静态文件）。

---

## 四、测试

```bash
cd backend
python -m pytest tests/test_yuedu_plugin.py tests/test_yuedu_import.py -q
# 111 passed
```

新增测试覆盖：
- GoEdge WAF 验证码页面识别
- 普通页面不误报
- 污染 DNS 检测（回环地址）
- DoH 解析 + 缓存 + 跳过污染应答
- URL 重写（IP + Host 头）
- fetch_explore 拦截错误传播
- JS exploreUrl 无结果时抛明确错误

---

## 五、已知限制

1. **菠萝猫**：WAF 验证码无法自动绕过，必须导入浏览器 Cookie 后同步。
2. **UAA**：规则依赖完整 Legado JS 运行时 + 登录 token，NovelHub 无法执行，建议换书源。
3. **爱丽丝书屋**：依赖 DoH 直连（dns.pub 等），若所有 DoH 端点都不可达则无法绕过；
   可在服务器 /etc/hosts 中手动添加 `38.46.217.34 www.alicesw.com` 作为双保险。
