# japan-antiwar-intel

日本反战相关公开信息采集与分析系统（Docker Compose）。

## 1) 快速开始

1. 复制环境变量模板并填写：
   - `copy .env.example .env`（Windows）
2. 启动全部服务：
   - `docker compose up -d`
3. 打开分析控制台：
   - Windows 启动器默认打开 [http://127.0.0.1:18090](http://127.0.0.1:18090)
   - WSL/Docker 内部仍是 [http://127.0.0.1:8190](http://127.0.0.1:8190)

## 2) analysis-worker 重建（依赖修复）

`analysis-worker` 依赖来自 `analysis-worker/requirements.txt`，包括 `playwright` 和 `instagrapi`。  
如果你更新了 requirements，必须重建镜像：

```powershell
docker compose build --no-cache analysis-worker
docker compose up -d analysis-worker
```

如果要在容器里直接启用 Playwright Chromium（端到端可用）：

```powershell
# .env 里设 PLAYWRIGHT_INSTALL_CHROMIUM=true
docker compose build --no-cache analysis-worker
docker compose up -d analysis-worker
```

说明：启用 `playwright install chromium --with-deps` 会显著增大镜像体积，但可减少运行期缺浏览器依赖的问题。

## 3) Instagram 运行策略（默认保守）

### Instagram 手动登录 / 换号

页面 `社媒抓取` 分页里有 `Instagram 登录状态` 区域：

- `登录/续登`：打开本机可见浏览器，让你手动登录自己的 Instagram 账号。
- `更换账号`：先清除旧会话，再打开登录窗口。
- `清除会话`：删除本地保存的 Instagram 登录状态。

一键启动器会自动启动本地登录助手：`http://127.0.0.1:18190`。如果按钮提示登录助手未运行，可以手动运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Start-InstagramLoginHelper.ps1
```

登录状态保存在 `analysis-worker/data/ig_browser_profile/state.json`，该目录不会提交到 git。

`.env` 中建议使用以下变量：

- `INSTAGRAM_ENABLED=false`：默认关闭 Instagram 抓取。
- `INSTAGRAM_USERNAME=` / `INSTAGRAM_PASSWORD=`：仅在需要时填写。
- `INSTAGRAM_MIN_DELAY_SECONDS=8`：账号/请求间隔下限。
- `INSTAGRAM_DAILY_LIMIT=40`：每日上限，避免高频。
- `INSTAGRAM_USE_PLAYWRIGHT=false`：默认不走 Playwright。
- `INSTAGRAM_PLAYWRIGHT_HEADLESS=true`：Playwright 无头模式开关。

建议：仅在明确需要时短时开启 Instagram 抓取，避免高频自动登录。

## 4) 持久化目录

`docker-compose.yml` 已将本地目录挂载到容器 `/app/data`：

- Host: `./analysis-worker/data`
- Container: `/app/data`

用途：

- Instagram 登录态（如 `ig_browser_profile`）
- Instagrapi 会话文件
- 社媒采集状态与中间结果

## 5) 启动方式（Windows / WSL / Docker）

### Windows（推荐）

- 启动全部系统：运行 `scripts/Start-JapanAntiwarIntel.ps1`
- 只跑社媒采集循环：运行 `启动社媒采集.bat`

`启动社媒采集.bat` 现在通过 Docker 容器执行：

```powershell
docker compose run --rm analysis-worker python standalone_social_collector.py --loop --interval 600
```

### WSL

在项目目录执行：

```bash
docker compose up -d
```

### 纯 Docker 命令

```bash
docker compose up -d
docker compose ps
```

## 6) 健康检查

```powershell
docker compose ps
docker compose logs --tail=200 analysis-worker
curl http://127.0.0.1:8190/health
```

容器内检查依赖是否存在：

```powershell
docker compose exec analysis-worker python -c "import playwright; print('ok')"
```

## 7) 给规划/编排 agent 的接口说明

### 新增/依赖环境变量

- `PLAYWRIGHT_INSTALL_CHROMIUM` (`true|false`, default `false`)
- `INSTAGRAM_ENABLED` (`true|false`, default `false`)
- `INSTAGRAM_USERNAME`
- `INSTAGRAM_PASSWORD`
- `INSTAGRAM_MIN_DELAY_SECONDS` (default `8`)
- `INSTAGRAM_DAILY_LIMIT` (default `40`)
- `INSTAGRAM_USE_PLAYWRIGHT` (`true|false`, default `false`)
- `INSTAGRAM_PLAYWRIGHT_HEADLESS` (`true|false`, default `true`)

### 关键 volume

- `./analysis-worker/data:/app/data`

### 标准重建命令

```bash
docker compose build --no-cache analysis-worker
docker compose up -d analysis-worker
```

### 标准健康检查命令

```bash
docker compose ps
docker compose logs --tail=200 analysis-worker
curl http://127.0.0.1:8190/health
docker compose exec analysis-worker python -c "import playwright; print('ok')"
```
