# 社媒采集模块接口文档

## 概述

`japan_peace_social.py` 是日本反战情报系统的社媒采集核心模块。本模块负责从 Twitter/X、Instagram、Reddit、Mastodon、YouTube、RSSHub 等平台采集公开内容。

> **2026-05-15 重大更新 v2**：
> - Twitter/X: twikit → **Nitter RSS**（无需登录，无封号风险）
> - Instagram: Instagrapi → **Playwright 浏览器模拟**（首选，封号风险低）
> - 新增 `ig_playwright.py`：模拟真实浏览器行为，前端实时显示滚动进度
> - 流式采集改为串行逐平台执行，前端实时显示每个平台的搜索结果

---

## 平台可用性状态

| 平台 | 方法 | 需要登录 | 封号风险 | 状态 |
|------|------|---------|---------|------|
| **Twitter/X** | Nitter RSS (+ FxTwitter metadata) | ❌ 不需要 | 零 | ✅ 可用 |
| **Instagram** | Playwright 浏览器模拟 → Instagrapi 回退 | Playwright: 需首次人工登录 | **低** (Playwright) / 高 (Instagrapi) | ✅ 可用 |
| **Reddit** | 原生 RSS + HTML 抓取 | ❌ 不需要 | 零 | ✅ 可用 |
| **Mastodon** | ActivityPub REST API + RSS | ❌ 不需要 | 零 | ✅ 可用 |
| **YouTube** | 公开 RSS Feed | ❌ 不需要 | 零 | ✅ 可用 |
| **RSSHub** | 公开 RSSHub 路由 | ❌ 不需要 | 零 | ✅ 可用 |

---

## 环境变量

### 新增/变更 (Instagram)

```bash
# Instagram 凭据 (必填 — Instagram 现在所有内容都需要登录)
INSTAGRAM_USERNAME=hong06234
INSTAGRAM_PASSWORD=+Lvs,*>2_Nu4s>#

# 代理配置 (按优先级)
PROXY_URL=http://127.0.0.1:7897            # 优先级1：显式代理
OUTBOUND_HTTP_PROXY=http://172.19.240.1:17897  # 优先级2：Docker 桥接代理
HTTP_PROXY=http://127.0.0.1:7897            # 优先级3：标准环境变量
HTTPS_PROXY=http://127.0.0.1:7897           # 优先级4：标准环境变量
```

### 代理读取逻辑

```python
proxy = (
    os.environ.get("PROXY_URL")
    or os.environ.get("OUTBOUND_HTTP_PROXY")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or ""
)
```

---

## 核心函数 API

### 1. Twitter/X — 账号时间线

```python
async def fetch_twitter_timeline(
    handle: str,           # Twitter 用户名 (不带 @)
    start_date: date,      # 开始日期
    end_date: date,        # 结束日期
    max_posts: int = 20,   # 最大帖子数
) -> tuple[list[dict], dict]  # (帖子列表, 诊断信息)
```

**实现**: Nitter RSS (`nitter.net/{user}/rss`) → xcancel.com HTML 回退  
**认证**: 无需登录  
**限流**: 无 (Nitter 是公开代理)

**返回的帖子字段**:
```json
{
    "platform": "twitter",
    "handle": "@ryukyushimpo",
    "title": "帖子文本前200字符",
    "url": "https://nitter.net/ryukyushimpo/status/...",
    "published_at": "2026-05-15T06:51:09+00:00",
    "raw_text": "完整帖子文本(前2000字符)",
    "fetched_at": "...",
    "source_method": "nitter_rss(nitter.net)"
}
```

**诊断信息**:
```json
{
    "platform": "twitter",
    "ok": true,
    "post_count": 10,
    "nitter_instance": "https://nitter.net",
    "profile": {
        "name": "琉球新報",
        "screen_name": "ryukyushimpo",
        "followers": 12345,
        "following": 100,
        "description": "...",
        "joined": "...",
        "location": "Okinawa, Japan"
    }
}
```

### 2. Twitter/X — 关键词搜索

```python
async def search_twitter_keywords(
    keywords: list[str],     # 搜索关键词列表
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict], dict]
```

**实现**: xcancel.com 搜索 HTML → Bing RSS 回退  
**注意**: Nitter 实例不支持关键词搜索 RSS，因此搜索使用 xcancel.com + Bing 回退

### 3. Instagram — 账号资料+帖子

```python
async def fetch_instagram_profile(
    handle: str,           # Instagram 用户名 (不带 @)
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict], dict]
```

**实现**: Instagrapi `client.user_medias()`  
**认证**: 需要 `INSTAGRAM_USERNAME`/`INSTAGRAM_PASSWORD`  
**限流**: 3-8秒延迟 (`cl.delay_range = [3, 8]`)  
**会话**: 持久化到 `data/instagram_session.json`

**返回的帖子字段**:
```json
{
    "platform": "instagram",
    "handle": "@peace_boat",
    "title": "标题前120字符",
    "url": "https://www.instagram.com/p/DXrPaXVD6lb/",
    "published_at": "2026-04-28T12:40:47+00:00",
    "raw_text": "完整说明文字",
    "engagement_count": 40,
    "likes": 40,
    "comments": 0,
    "media_type": 1,
    "fetched_at": "...",
    "source_method": "instagrapi"
}
```

### 4. Instagram — Hashtag 搜索

```python
async def search_instagram_keywords(
    keywords: list[str],     # 搜索关键词 (自动转hashtag)
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict], dict]
```

**实现**: Instagrapi `client.hashtag_medias_recent()` → Bing RSS 回退  
**注意**: 关键词自动去除 `#` 前缀后作为 hashtag 搜索

### 5. Reddit — Subreddit 帖子

```python
async def fetch_reddit_subreddit_posts(
    subreddit: str,        # 社区名称 (不带 r/)
    start_date: date,
    end_date: date,
    max_posts: int = 20,
    sort: str = "new",
) -> tuple[list[dict], dict]
```

**实现**: Reddit 原生 RSS (`reddit.com/r/{sub}/new/.rss`) → old.reddit.com HTML 回退

### 6. Mastodon — 账号状态

```python
async def fetch_mastodon_account(
    instance_url: str,     # Mastodon 实例 URL
    username: str,         # 用户名
    start_date: date,
    end_date: date,
    max_posts: int = 20,
) -> tuple[list[dict], dict]
```

**实现**: ActivityPub REST API → RSS 回退

### 7. YouTube — 频道 RSS

```python
async def fetch_youtube_channel_rss(
    channel_id: str,       # UC... 频道ID 或 @handle
    start_date: date,
    end_date: date,
    max_videos: int = 20,
) -> tuple[list[dict], dict]
```

### 8. 跨平台关键词搜索

```python
async def search_social_by_keywords(
    keywords: list[str],
    start_date: date,
    end_date: date,
    platforms: list[str] | None = None,  # 默认 ["reddit","twitter","instagram","mastodon","youtube"]
    max_posts_per_platform: int = 20,
) -> tuple[list[dict], list[dict]]       # (所有帖子, 诊断信息列表)
```

**并行执行**: 所有平台的搜索同时进行 (`asyncio.gather`)

### 9. 所有已追踪账号的帖子

```python
async def fetch_all_tracked_account_posts(
    start_date: date,
    end_date: date,
    max_posts_per_account: int = 20,
) -> tuple[list[dict], list[dict]]
```

从 `japan_social_accounts` 数据库表中读取 `verified`/`seed` 状态的账号并获取帖子。

### 10. 流式采集 (API端点用)

```python
async def fetch_japan_social_stream(
    start_date: date,
    end_date: date,
    platform: str | None = None,
    platforms: list[str] | None = None,
    account_ids: list[int] | None = None,
    include_verified_accounts: bool = False,
    include_candidate_accounts: bool = False,
    max_posts: int = 50,
    keywords: list[str] | None = None,
) -> AsyncGenerator[dict, None]
```

Streaming generator，逐条 yield 帖子。用于 `/japan/social/fetch/stream` 端点。

---

## Instagrapi 客户端管理

### 单例模式

```python
_ig_client = None           # 全局单例
_ig_client_lock = asyncio.Lock()  # 初始化锁
_ig_available = None        # None=未检查, True=可用, False=不可用
```

### 初始化流程

```
1. 检查 _ig_available (False = 快速失败)
2. 获取 _ig_client_lock
3. 在 thread pool 中运行同步 instagrapi 代码
4. 尝试加载 session 文件 → 成功则登录
5. 失败则重新登录 → 保存新 session
6. 设置 _ig_available = True/False
```

### Session 持久化

- 存储位置: `{project}/data/instagram_session.json`
- 自动创建目录
- 避免重复登录触发限流 (429)

---

## Playwright 浏览器模拟（Instagram 首选方案）

### 为什么用 Playwright

| | Instagrapi | Playwright |
|---|---|---|
| 原理 | 调用 Instagram 私有 API | 模拟真实浏览器行为 |
| 被检测为 bot | ✅ 很容易 | ❌ 极难 |
| 封号风险 | **高** | **低** |
| 速度 | 快 | 慢（模拟人类滚动） |
| 依赖 | Python 库 | Chrome/Chromium 浏览器 (~300MB) |
| 首次配置 | 设置凭据 | 人工登录一次 |

### 首次登录

```bash
cd analysis-worker
pip install playwright
playwright install chromium
python ig_login.py
```

弹出浏览器窗口 → 手动登录 Instagram → 关闭窗口。状态保存到 `data/ig_browser_profile/`。

### 工作流程

```
首次: 打开可视化浏览器 → 人工登录 → 保存 cookies 到磁盘
后续: 加载 cookies → headless 启动 → 导航到 profile/hashtag
       → 模拟人类滚动 (2-5秒随机延迟)
       → 提取帖子链接 → 逐条推送到前端
       → 前端实时显示: "peace_boat: 已找到 3 条帖子（滚动 1/5）…"
```

### 进度事件类型

Playwright 采集通过以下事件流式推送到前端：

| 事件类型 | 含义 | 前端显示 |
|---------|------|---------|
| `ig_progress` → 转为 `progress` | 浏览器正在滚动采集 | 进度条 "peace_boat: 已找到 3 条帖子…" |
| `ig_post` → 转为 `post` | 每条帖子即时推送 | `📄 帖子内容…` |
| `ig_error` → 转为 `rate_limited` | 登录失效/被拦截 | `🚫 instagram — 登录态失效` |

### 回退机制

如果 `playwright` 或 `chromium` 未安装：
- `fetch_instagram_profile` → 自动回退到 Instagrapi
- `fetch_japan_social_stream` → 自动回退到 Instagrapi
- 前端日志会显示 `source_method: "instagrapi"` 或 `"playwright"`

### Docker 部署

Docker 镜像默认**不包含** Chromium（节省 500MB）。如需在 Docker 中使用 Playwright：

1. 取消注释 `Dockerfile` 中的 `RUN playwright install chromium --with-deps`
2. 首次运行时进入容器执行 `python ig_login.py`（需要交互式终端）

或在 Windows 宿主机上独立运行 Playwright，通过 API 推送结果。

---

## 限流与错误处理

### Twitter/X (Nitter RSS)
- **限流风险**: 低 (Nitter 是公开代理，由 Nitter 实例处理 X 的限流)
- **错误处理**: 尝试所有 Nitter 实例，全部失败则标记 unavailable
- **Nitter 实例列表**:
  - 主: `nitter.net`, `nitter.cz`
  - 备: `nitter.poast.org`, `nitter.privacyredirect.com`

### Instagram (Instagrapi)
- **限流**: 请求间 3-8 秒延迟 (`cl.delay_range = [3, 8]`)
- **冷却时间**: 触发 429 后需等待 1-2 小时
- **Session 缓存**: 避免重复登录增加限流计数
- **私密账号**: 返回空帖子列表 + 诊断信息
- **登录失败**: 标记 _ig_available = False，后续调用快速返回

### 通用
- 所有函数都有完整的 try/except
- 网络超时: 10-15 秒
- 状态码 403/429: 自动返回空结果 + 诊断信息
- Bing RSS 作为最终回退 (Instagram/Twitter 搜索)

---

## 添加新平台的步骤

1. 在 `japan_peace_social.py` 中实现 `fetch_{platform}_profile()` 函数
2. 签名: `async def fetch_xxx_profile(handle, start_date, end_date, max_posts) -> tuple[list[dict], dict]`
3. 在 `SEARCHABLE_PLATFORMS` (约第 1422 行) 添加平台名
4. 在 `search_social_by_keywords()` (约第 1425 行) 添加搜索分支
5. 在 `fetch_posts_for_account()` (约第 1345 行) 添加分发分支
6. 更新本文档的平台表

---

## 故障排查

### Instagram 429 Too Many Requests
- 等待 1-2 小时让限流重置
- 检查 session 文件是否存在 (`data/instagram_session.json`)
- 删除 session 文件后重试（会触发重新登录，但可能加重限流）
- 切换代理 IP

### Twitter 返回 0 条推文
- 检查 Nitter 实例是否可用: `curl https://nitter.net/{username}/rss`
- 检查代理配置: `PROXY_URL`/`OUTBOUND_HTTP_PROXY`
- Nitter.net 可能被墙，尝试 `nitter.cz`

### Instagram 登录失败
- 检查凭据是否正确
- 检查代理是否可达
- 检查是否被要求验证 (checkpoint) — 需在浏览器中手动登录一次

### 代理不生效
- 优先级: `PROXY_URL` > `OUTBOUND_HTTP_PROXY` > `HTTP_PROXY` > `HTTPS_PROXY`
- Docker 容器中 `127.0.0.1` 指向容器自身，不是宿主机
- 使用 `OUTBOUND_HTTP_PROXY` 配置 Docker 桥接代理

---

## 依赖

```
instagrapi>=2.0.0          # Instagram Private API (新增)
feedparser>=6.0.0          # RSS/Atom 解析
httpx>=0.28.0              # 异步 HTTP 客户端
beautifulsoup4>=4.12.0     # HTML 解析 (xcancel 回退等)
```

---

## 修改记录

| 日期 | 变更 |
|------|------|
| 2026-05-15 | **重大更新**: Twitter 从 twikit 切换到 Nitter RSS；Instagram 从 outgram/web_profile_info 切换到 Instagrapi。新增 NITTER_FALLBACK_INSTANCES。添加 `_ensure_ig_client()` 单例管理。 |
| (原) | 原始实现使用 twikit GuestClient + xcancel.com + outgram。 |
