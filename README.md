# PEACE ATLAS / 反戦研究室

日本和平与反战运动公开信息研究工作台。把新闻、社媒正文、来源账号、可核对关系、活动线索与研究报告保存在本机；每项结论保留原文出处和生成时的证据快照，方便回查、续研和导出。

当前在本机运行的是 Windows `20261011-140711` 基础纠正版。真实 216 项重审已经完成；601 条来源报告的最终复核恢复仍待新包。**2026-10-12 快速账号分类与安装器版正在准备发布**：快速分类和人工快改已在源码实现，新的整体冻结、原生与安装验收尚待完成，不能把源码就绪当作安装包已交付。实际结果及历史版本见[发布记录](docs/RELEASE_FINAL_20261011.md)。

## 下载与开始使用

[公开下载最新版](https://github.com/railgun0325/peace-atlas/releases/latest) · [全部 Releases](https://github.com/railgun0325/peace-atlas/releases) · [公开源代码](https://github.com/railgun0325/peace-atlas)

仓库已公开，下载已发布资产无需受邀账号。当前新版本仍在准备，尚无公开 Release；上面的“最新版”链接会在非预发布版本正式发布后提供稳定下载。拟用标签 `v2.0.0-fast-classification.20261012`，以发布页实际文件与 SHA256 为准。

| 系统 | 新版本计划提供的安装入口 | 当前状态 |
| --- | --- | --- |
| Windows x64 | NSIS 当前用户 `.exe` 安装器；安装后从桌面或开始菜单启动。运行时随应用提供，无需安装 Python、Node 或 DSH Desktop。 | 新安装器及原生验收待完成。当前 `140711` 便携包已在本机验收并正式升级；Windows 程序未使用发布者证书签名。 |
| Apple Silicon macOS 13+ | `.pkg` 安装包，安装 `PEACE ATLAS.app`；包内应用采用 ad-hoc 签名。 | 新原生包及安装验收待完成；不含 Developer ID 签名或 Apple 公证。实际测试系统随收据记录。 |

第一次使用按这个顺序完成一个小范围任务：**检查能力与会话 → 保存 AI 连接 → 创建研究或采集 → 在任务中心跟踪 → 回查资料与关系依据 → 生成并下载报告 → 保存研究备份。** 采集 X 或 Instagram 时，在应用提供的登录入口使用自己的账号；公开平台的实际访问限制会显示在任务诊断中。

## 当前可以做什么

- **保存与检索公开资料**：RSS / Atom、Google News RSS、X、Instagram、JSON 导入、正文阅读、来源追踪和重复检测。
- **快速分类与研究账号关系**：从关键词或指定账号出发，保存发现路径、公开观察关系和分支断点。在“组织与账号”或图谱点击“快速判定账号类型”，DS 根据已保存名称、用户名和简介粗分组织、个人、未判定，默认每批 100 个，最多 200 个。类型可快捷手改并锁定；粗分不自动核验身份、不访问外部公开网页。身份核验与关系依据另行检查。
- **生成有来源的研究报告**：规则证据整理，或使用 `deepseek-flash` 等已配置模型进行全文阅读、摘录复核、DSH 工具调查、报告生成与复核。付费分析前保存完整输入；失败后先免费预览，明确选择原输入续跑或最新状态重新生成，逐阶段验证可复用内容。
- **严格审核与独立核验**：逐批核对关系主张、活动原文，按需另选账号公开证据审核，保存确认、排除或证据不足。严格审核默认每批 16 条，可用独立网页和来源缓存；候选账号身份核验有单独入口。快速分类、严格审核都可在任务中心取消或续跑，人工锁定类型受保护。
- **跟踪任务与活动地图**：预估并确认研究范围，创建任务串，查询进度、取消及重试。活动仅在保存的公开正文与可信原帖发布时间支持时，按日本标准时间解释同一活动的“本日／今日／昨日／今年”；裸月日不自动补年，地点须有实际举办地依据，预告与未来号召不计作已举行活动。核对结果说明来源如何描述，不能替代独立事实核实；日期或地点不足时保留未知。地图仅统计选定实际活动日期内已核对、已定位且去重的活动。
- **备份、恢复及外部 Agent 接入**：在线研究数据库备份、桌面确认恢复、可恢复资料清理；通过独立授权的本机 Skill / CLI 读取、研究、报告与审核。

本版已接入官方 **DeepSeek Harness SDK/runtime 0.2.0-rc.2** 的实际工具调用循环。模型通过 API 发出请求，DSH 运行时根据任务调用受约束的证据工具，工具结果再进入后续模型决策、来源复核和保存流程。桌面包使用自身 Electron 的 Node 运行时，用户无需另行启动 DSH 桌面应用。模型名称以用户保存的连接及提供方实际支持为准，默认建议为 `deepseek-flash`。

本机旧包的真实重审完成 61 个账号和 155 条活动：账号为组织 17、个人 9、未判定 35；活动为确认 1、证据不足 153、排除 1。新增身份核验为 0，原有 2 个已核验账号及 551 条保存来源保留。当前地图的可用分母是 1 条东京活动，占比 100%，依据为来源描述核对（`source_claim_only`），不代表全部社会活动。该收据验证旧包严格审核，不证明新的快速分类任务已在正式数据执行。

新源码的分类、前端和传输专项回归已通过；整体回归、关键界面与安装检查由本轮统一验收完成后更新发布状态。旧源码完整回归与原生记录保留在[发布记录](docs/RELEASE_FINAL_20261011.md)，模拟结果与真实模型结果分别判断。

## 操作说明与项目判断

| 文档 | 内容 |
| --- | --- |
| [完整操作说明书](docs/USER_MANUAL_20261010_AGENT.md) | 20 部分，保留原参数说明并补充快速分类与人工快改：首次使用、研究与采集、审核、关系图与地图、报告续跑、备份清理及 Agent 操作；新版内容已更新，对应离线 HTML 尚未上传 |
| [DSH 工具分析与恢复](docs/USER_MANUAL_20261010_AGENT.md#17-dsh-工具分析设置取证与失败后续跑) | 完整说明书第 17 部分，包含工具调查、报告续跑和结果判断 |
| [快速分类与严格审核](docs/USER_MANUAL_20261010_AGENT.md#20-自动审核核验与-agent-接口) / [随包说明](analysis-worker/app/workbench/agent_assets/AUTOMATIC_REVIEW.md) | 类型粗分与快改、严格审核范围、独立出处、证据不足及重试规则 |
| [Agent 接入契约](docs/AGENT_INTEGRATION.md) / [Skill 安装](analysis-worker/app/workbench/agent_assets/INSTALL.md) | 本机接口、授权边界及安装步骤 |
| [项目基础审查](docs/FOUNDATION_REVIEW_20261011.md) / [第一批实现](docs/FOUNDATION_IMPLEMENTATION_20261011.md) / [路线图](docs/ROADMAP.md) | 基线判断、已实施改进与后续优先级 |
| [本轮收尾发布记录](docs/RELEASE_FINAL_20261011.md) / [桌面构建](desktop/BUILDING.md) | 当前冻结文件、验收、哈希和平台限制 |

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

历史版本的说明、验收和固定源码清单保留在 [Git 历史](https://github.com/railgun0325/peace-atlas/tree/776e5854653b5776c0c0d5b0ba17e3e5a47bf278/docs)及对应提交、标签中，只适用于各自版本。当前分支保留完整操作说明、设计契约和本轮发布记录，避免多份旧清单与重复说明混入当前入口。
