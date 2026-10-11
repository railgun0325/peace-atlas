# PEACE ATLAS / 反戦研究室

日本和平与反战运动公开信息研究工作台。把新闻、社媒正文、来源账号、可核对关系、活动线索与研究报告保存在本机；每项结论保留原文出处和生成时的证据快照，方便回查、续研和导出。

当前版本为 **PEACE ATLAS 2.0.0 · 2026-10-11 自动审核与恢复修复版**，对应冻结构建 `20261011-023729`，GitHub 标签为 `v2.0.0-review-assurance.20261011`。具体文件、冻结版本、哈希与验收范围以[本轮发布记录](docs/RELEASE_20261011.md)为准。

## 下载与开始使用

[私有仓库](https://github.com/railgun0325/peace-atlas) · [本轮 Release](https://github.com/railgun0325/peace-atlas/releases/tag/v2.0.0-review-assurance.20261011)

| 系统 | 发布资产 | 启动方式与验收范围 |
| --- | --- | --- |
| Windows x64 | [Windows ZIP](https://github.com/railgun0325/peace-atlas/releases/download/v2.0.0-review-assurance.20261011/PEACE-ATLAS-2.0.0-windows-x64-harness-candidate-20261010.zip) | 完整解压目录，双击 `PEACE ATLAS.exe`。桌面包自带 Python、Electron、Chromium 和固定 DSH 运行时。 |
| Apple Silicon macOS 13+ | [macOS ARM64 ZIP](https://github.com/railgun0325/peace-atlas/releases/download/v2.0.0-review-assurance.20261011/PEACE-ATLAS-2.0.0-macos-arm64-harness-candidate-20261010.zip) | 包结构、文件、哈希、权限与符号链接已检查；Mac 原生启动、Apple 原生签名验证及公证尚未验收。使用前按[构建文档](desktop/BUILDING.md)核对本机收尾步骤。 |

仓库和 Release 为私有。接收者需要使用受邀并获仓库访问权限的 GitHub 账号；链接或二维码只负责打开页面，不授予访问权限。分享前由仓库所有者明确授权访问。发布资产的历史后缀 `harness-candidate-20261010` 保留原冻结文件名，具体版本以 Release 标签和发布记录为准。

第一次使用按这个顺序完成一个小范围任务：**检查能力与会话 → 保存 AI 连接 → 创建研究或采集 → 在任务中心跟踪 → 回查资料与关系依据 → 生成并下载报告 → 保存研究备份。** 采集 X 或 Instagram 时，在应用提供的登录入口使用自己的账号；公开平台的实际访问限制会显示在任务诊断中。

## 当前可以做什么

- **保存与检索公开资料**：RSS / Atom、Google News RSS、X、Instagram、JSON 导入、正文阅读、来源追踪和重复检测。
- **研究账号与关系**：从关键词或指定账号出发，保存发现路径、公开观察关系和分支断点；账号类型、主体身份核验与关系推断分别记录。公开自述保留在来源原文中，独立的声明类型层仍在路线图中。
- **生成有来源的研究报告**：规则证据整理，或使用 `deepseek-flash` 等已配置模型进行全文阅读、摘录复核、DSH 工具调查、报告生成与复核。报告保留独立输入快照和可继续的失败断点。
- **批量自动审核与核验**：逐批审核账号类型、关系主张及活动，调用工具核对原文和独立公开出处，保存确认、排除或证据不足的结果；取消和失败保留已提交批次。
- **跟踪任务与活动地图**：预估并确认研究范围，创建任务串，查询进度、取消及重试。地图仅统计选定实际活动日期内已核对、已定位且去重的活动。
- **备份、恢复及外部 Agent 接入**：在线研究数据库备份、桌面确认恢复、可恢复资料清理；通过独立授权的本机 Skill / CLI 读取、研究、报告与审核。

本版已接入官方 **DeepSeek Harness SDK/runtime 0.2.0-rc.2** 的实际工具调用循环。模型通过 API 发出请求，DSH 运行时根据任务调用受约束的证据工具，工具结果再进入后续模型决策、来源复核和保存流程。桌面包使用自身 Electron 的 Node 运行时，用户无需另行启动 DSH 桌面应用。模型名称以用户保存的连接及提供方实际支持为准，默认建议为 `deepseek-flash`。

本次冻结源码通过 **2,688 项后台、172 项前端、62 项桌面及 17 项构建测试，共 2,939 项，0 失败**。新包另通过 14 项核心流程、30 次 Agent CLI 执行、官方 DSH SDK 的实际运行及受控传输故障恢复；正式应用升级前后 22 张表逐值、逐类型、逐结构保留检查通过。SDK 恢复测试在 4 次受控 HTTP 尝试中恢复两次失败，10 次工具没有重复执行，4 项决定原子保存。真实付费模型审核、语义抽查及其未解决问题另见[发布记录](docs/RELEASE_20261011.md)，不把受控测试视作真实模型质量保证。

## 操作说明与项目判断

| 文档 | 内容 |
| --- | --- |
| [操作说明书](docs/USER_MANUAL_20261010.md) | 页面、首次研究、参数、采集、报告、备份及恢复 |
| [外部 Agent 补充说明](docs/USER_MANUAL_20261010_AGENT.md) | 桌面授权、Skill / CLI 操作、预览与执行 |
| [Harness 补充说明](docs/USER_MANUAL_20261010_HARNESS.md) | DSH 工具调查、报告续跑及结果判断 |
| [自动审核与核验说明](docs/AUTOMATIC_REVIEW_20261011.md) | 批量审核范围、独立出处、证据不足及重试规则 |
| [Agent 接入契约](docs/AGENT_INTEGRATION.md) / [Skill 安装](analysis-worker/app/workbench/agent_assets/INSTALL.md) | 本机接口、授权边界及安装步骤 |
| [项目基础审查](docs/FOUNDATION_REVIEW_20261011.md) / [路线图](docs/ROADMAP.md) | 当前实现判断、结构问题与后续优先级 |
| [发布记录](docs/RELEASE_20261011.md) / [桌面构建](desktop/BUILDING.md) | 冻结文件、验收、哈希和平台限制 |

公开账号候选不等于已核验主体，关注、共同链接或模型推断不等于组织隶属；证据不足会保留未知。资料中的地名提及和发布日期不能代替实际活动地点或活动日期。任务完成状态只适用于本次范围，不保证完整平台历史、全部公开关系或持续可访问。

## 从源码启动

建议使用 Python 3.12，在仓库根目录创建独立虚拟环境。源码工作台使用本地 SQLite；下面的启动链路直接加载 V2 后端。Chromium 用于采集和会话验证，用户登录入口仍由应用引导到系统浏览器。

Windows PowerShell：

```powershell
python -m venv .venv-v2
.\.venv-v2\Scripts\python.exe -m pip install -r desktop/requirements-runtime.txt
.\.venv-v2\Scripts\python.exe -m playwright install chromium
.\.venv-v2\Scripts\python.exe scripts/run_workbench.py
```

macOS / Linux：

```bash
python3 -m venv .venv-v2
.venv-v2/bin/python -m pip install -r desktop/requirements-runtime.txt
.venv-v2/bin/python -m playwright install chromium
.venv-v2/bin/python scripts/run_workbench.py
```

访问 [http://127.0.0.1:18090](http://127.0.0.1:18090)，按 `Ctrl+C` 停止。可追加 `--port 18091` 更换端口，或 `--data ./output/development.sqlite3` 使用独立测试库。源码运行 DSH 工具分析还需可执行的 Node.js；运行时自动查找 `node`，也可用 `WORKBENCH_HARNESS_NODE` 指定解释器路径。桌面成品已包含相应运行时。

开发启动器只从本地 `.env` 补充 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_TIMEOUT_SECONDS`，进程环境变量优先。真实 `.env`、AI 密钥、社媒会话、数据库及个人导出文件都应保留在本机，不进入源代码提交或发布包。默认源码数据库为 `analysis-worker/data/workbench.sqlite3`；桌面数据位置可在“能力与设置 → 打开数据目录”查询。研究备份与会话、密钥分别管理，操作方法见说明书。

## 开发验证与桌面构建

从仓库根目录运行，使用虚拟环境时将 `python` 换成该环境的解释器：

```powershell
python -m pip install -r analysis-worker/requirements.txt -r desktop/requirements-runtime.txt
python -m pytest -q analysis-worker/tests
node --test analysis-worker/app/static/workbench/tests/*.test.mjs
node --test desktop/tests/*.test.cjs
python -m unittest discover -s desktop/tests -p test_build.py
```

外部 Agent 菜单与可选浏览器桥接另有独立检查入口：

```powershell
node --test desktop/tests/test_agent_menu.cjs
node --test browser-extension/tests/*.test.mjs
```

构建前需可用的开发 Python 和 pip，构建器会下载并校验固定运行时。两平台默认产出 Windows x64 与 Apple Silicon macOS：

```powershell
python desktop/build.py --prepare-only
python desktop/build.py --target all
```

也可用 `--target windows-x64` 或 `--target macos-arm64` 单独构建。Mac 本机签名/启动收尾、可选已有 WSL 签名路径和验收边界见[构建文档](desktop/BUILDING.md)。每次重建应重新核对源代码冻结清单、平台文件、通知与许可证、归档哈希及实际运行结果。

| 路径 | 职责 |
| --- | --- |
| `analysis-worker/app/workbench/` | 本地 API、存储、任务、采集、研究、报告、审核与恢复 |
| `analysis-worker/app/static/workbench/` | 无运行时 CDN 的模块化前端、离线字体及地图 |
| `analysis-worker/app/workbench/agent_assets/` | 外部 Agent 的公开契约、Skill 与 CLI |
| `desktop/` | Electron 主程序、私有 Python 运行时和跨平台构建 |
| `desktop/harness/` | 固定 DSH SDK/runtime、产品 relay、精确依赖 manifest 与许可 |
| `browser-extension/` | 可选 Chrome / Edge 会话桥接源码与测试 |
| `docs/` | 操作说明、设计审查、发布和验收记录 |

源代码提交保留固定 Harness 依赖闭包及第三方许可证；其文件集合由 `desktop/harness/runtime-manifest.json` 逐项校验。一般开发依赖、运行输出与用户数据由 `.gitignore` 排除。当前未为自有产品代码指定开源许可证；字体、地图和第三方依赖继续保留各自许可与署名。

[历史 README 档案](README.history-20261011.md)完整保留整理前的交付记录，供追踪旧版本。当前入口、版本与实际验收范围以本页和本轮发布记录为准。
