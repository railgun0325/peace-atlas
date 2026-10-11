# 本机浏览器会话桥 · 0.1.0

这是 Chrome / Edge Manifest V3 扩展源码。它让用户沿用正常浏览器中已经登录的 X 或 Instagram 会话，再由本机情报工作台独立验证。它不新建自动化登录配置，不读取密码，不解密浏览器磁盘数据库。

**交付边界：当前完成了源码、离线自动化测试和协议校验，尚未安装到用户的 Chrome / Edge，也没有完成真实扩展配对验收。** 安装需要本人一次明确同意；此仓库没有执行安装或修改浏览器设置。

## 使用流程

1. 在工作台选择 X 或 Instagram，生成本机配对链接。链接有效期为 5 分钟，配对码只能使用一次。
2. 在扩展弹窗粘贴链接并点击“连接并验证会话”。浏览器请求所选平台及本机连接所需的权限。
3. 扩展通过浏览器官方 Cookies API 读取该平台的必要字段，发送到链接指定的本机端口。配对完成不等于登录成功。
4. 工作台在自己的独立上下文中验证平台已登录；只有服务端同时确认 `verified: true` 和 `saved: true`，扩展才显示“已验证”。失败时保留工作台原会话。
5. 已连接平台的必要 Cookie 变化后，扩展至少等待 30 秒合并事件，并以不短于 60 秒的间隔自动尝试验证。也可以点击“立即验证”。失败后不持续轮询或反复登录。
6. 点击“断开连接”会删除扩展本机配对凭据、撤销该平台站点权限并停止后续同步。已发出的验证可能完成；工作台保存的会话不会随之删除。

配对令牌仅在当前工作台进程中有效，最长 24 小时。工作台重启、换端口或令牌到期后，需要重新配对，**通常不需要重新登录平台**。浏览器扩展不能保证用户平台会话长期有效；验证码、退出登录、风控和平台到期仍由平台决定。

## 权限和数据范围

| 权限 | 用途 | 限制 |
|---|---|---|
| `storage` | 保存本机桥接令牌、指定端口、平台及验证状态 | 只用本机 `storage.local`；没有 `storage.sync`，不保存平台 Cookie 或一次性配对码；限制到扩展自身上下文 |
| `alarms` | 合并已连接平台的会话变化事件 | 无周期性平台扫描；没有失败后的持续重试 |
| 可选 `cookies` | 使用官方 API 读取明确的必要会话字段 | 用户点击连接才请求；代码只读取，不调用修改或删除 Cookie API |
| 可选 `https://x.com/*` | X 会话读取 | 只接受 `x.com` / `.x.com` 根路径 `/` 的 `auth_token`、`ct0`、可选 `twid` |
| 可选 `https://www.instagram.com/*` | Instagram 会话读取 | 只接受 `.instagram.com` / `instagram.com` / `www.instagram.com` 根路径 `/` 的 `sessionid`、`csrftoken`、可选 `ds_user_id` |
| 可选 `http://127.0.0.1/*` | 向本机工作台配对与同步 | 扩展权限语法不按运行时端口动态细分；代码额外锁定配对链接指定的端口及两个固定 POST 端点 |

每次 Cookie API 调用都包含一个固定站点 URL、一个必要名称和根路径；不调用无条件 `getAll()`，不遍历站点或 Cookie store。重复同名、混合 store、分区会话、不同域或路径、不安全属性、过期或异常到期时间均拒绝传递。`auth_token` 与 `sessionid` 必须为 HttpOnly 且 Secure；所有字段必须 Secure。没有持久化有效期的会话 Cookie 按 `expires: -1` 传递；`sameSite: unspecified` 收紧为 `Lax`，不会扩大为 `None`。

没有内容脚本、网页消息入口、`externally_connectable`、密码权限、剪贴板读取、远程代码、网页抓取或自动打开网页。扩展网络 CSP 只允许 `http://127.0.0.1:*`；请求函数进一步只允许已配对端口的 `/pair` 与 `/sync`，拒绝重定向、用户名密码、外部地址、IPv6、localhost 和数字形式的伪装地址。Cookie 值不进入日志、通知、弹窗、扩展存储或错误文本。

## 接口约定

工作台认证页面先调用 `POST /api/v2/social/bridge/start {platform}`，返回：

```text
http://127.0.0.1:<port>/api/v2/social/bridge/connect?code=<64位小写hex>&platform=twitter|instagram
```

扩展仅解析此链接，不访问 `/connect`。只允许非缺省显式端口、完全一致的路径以及 `code` / `platform` 两个参数，不接受 fragment、额外参数或重复参数。

```text
POST /api/v2/social/bridge/pair
Content-Type: application/json
{code, platform}
→ {bridge_token, platform, expires_at}

POST /api/v2/social/bridge/sync
Content-Type: application/json
X-Workbench-Bridge: <bridge_token>
{platform, cookies: [{name, value, domain, path, secure, httpOnly, expires, sameSite}]}
→ {verified: true, saved: true, platform, source: "browser_extension"}
```

Origin 由浏览器自动携带，扩展不伪造 Origin。服务端把一次性配对码兑换成绑定平台及真实扩展 Origin 的令牌，执行 CORS、来源校验及 Cookie 属性二次验证。令牌不是工作台完整管理密钥。

每次请求有 25 秒截止，不自动重试。服务端同步验证应在 22 秒内结束，以适应 Manifest V3 service worker 的请求生命周期。超时只显示“结果未确认”，不会声称登录成功或原会话一定未变。错误码读取服务端 `{error: {code, message}}` 结构，弹窗仅显示本地固定说明，服务端原始文本不会展示。

## 源码与离线测试

```text
manifest.json          权限、MV3 service worker 与网络 CSP
bridge-core.js         配对链接、必要 Cookie、响应验证及本机请求
bridge-controller.js   配对、同步、断开、事件合并和纯状态接口
background.js          仅自身 popup 消息入口、Cookie 事件和定时器
popup.html/.css/.js    首次授权、配对链接输入、状态与手动验证
tests/                 注入假 API/假网络的 Node 测试，不接触真实会话
```

在本目录执行 `node --test tests/*.test.mjs`。无 npm 依赖，无安装脚本。测试覆盖目标地址伪装与重定向拒绝、Cookie 名称／域／路径／属性／有效期、来源状态隔离、未授权不读取、仅 opened 不标成功、同平台事件合并、撤销与在途响应、错误脱敏，以及可选 cookies 权限尚未授予时的 service worker 启动。

离线测试通过不等于 Chrome / Edge 已安装，也不证明真实账号会话已经成功传入工作台。实装后仍需本人授权配对、真实平台页面验证、再次采集和去重验收。运行 ZIP 仅包含清单与 JS / HTML / CSS，不包含测试、文档、cookie、配对码或工作台令牌。

## 官方依据

- [Chrome Cookies API：cookies 和站点权限、Cookie 属性](https://developer.chrome.com/docs/extensions/reference/api/cookies)
- [Chrome 可选权限与用户手势](https://developer.chrome.com/docs/extensions/reference/api/permissions)
- [Chrome 扩展存储和访问级别](https://developer.chrome.com/docs/extensions/reference/api/storage)
- [Manifest V3 service worker 生命周期](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)
- [Microsoft Edge 支持的扩展 API](https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/api-support)
