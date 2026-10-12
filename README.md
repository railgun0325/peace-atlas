# PEACE ATLAS / 反戦研究室

日本和平与反战运动公开信息研究工作台。把新闻、社媒正文、来源账号、可核对关系、活动线索与研究报告保存在本机；每项结论保留原文出处和生成时的证据快照，方便回查、续研和导出。

**2026-10-12 组织／个人二分类版**已完成同一冻结源码的 3,403 项回归、Windows 隔离原生启动及本机升级、Mac 原生构建及实际安装启动验证。Windows 升级数据保留已验证；2026-10-12 02:53→02:55 UTC 本轮正式启动区间内，23 张研究表完整比对一致。61 个账号的二分类复测待执行，人工锁定类型保留，实际 AI 处理数量以新任务收据为准。旧版 61 条快速分类保留为历史证据。601 条来源的长报告续跑仍未通过最后的语义复核，已读材料和草稿保留，尚未保存为正式报告。实际结果和限制见[发布记录](docs/RELEASE_FINAL_20261011.md)。

## 下载与开始使用

[公开下载最新版](https://github.com/railgun0325/peace-atlas/releases/latest) · [全部 Releases](https://github.com/railgun0325/peace-atlas/releases) · [公开源代码](https://github.com/railgun0325/peace-atlas)

仓库公开，下载已发布资产无需受邀账号。本轮标签为 `v2.0.0-binary-account-types.20261012`，应用源码提交为 `be315a35e7eefad0f26c58cc9a3e20f373df642e`；本轮 release 已正式发布，以发布页的实际文件及 SHA256 为准，上方 latest 链接已指向本轮版本；不登录访问与安装包有界下载验证通过。公开页面可被搜索到，不是只有持链接的人才能访问。

| 系统 | 安装入口 | 已验证范围 |
| --- | --- | --- |
| Windows x64 | NSIS 当前用户 `.exe` 安装器；安装后从桌面或开始菜单启动。运行时随应用提供，无需安装 Python、Node 或 DSH Desktop。 | 完整包 8,760 文件哈希、隔离启动、本机安装退出 0、三个桌面入口、正式启动及对应启动区间的 23 表一致验证通过；二分类复测待授权连接，人工锁定类型保留。安装器未使用发布者证书签名。 |
| Apple Silicon macOS 13+ | `.pkg` 安装包，安装 `PEACE ATLAS.app`；包内应用采用 ad-hoc 签名。 | 本轮原生构建和[实际安装 run 38167653396](https://github.com/railgun0325/peace-atlas/actions/runs/38167653396)通过：3,145 应用成员与 payload 一致，普通用户签名验证、从 `/Applications` 启动退出 0 及安全存储通过。不含 Developer ID 签名或 Apple 公证；Intel Mac 无本轮安装包。 |

本轮安装包的已核验字节与 SHA256：

| 安装包 | 字节数 | SHA256 |
| --- | ---: | --- |
| Windows x64 Setup.exe | 307,429,973 | `3218d0f972851643ecaf9fde82ab84707364019f2076759df28efba2e919c8f7` |
| macOS Apple Silicon PKG | 377,090,650 | `d776c1c4b32219efda75697568b7010d3724e776a9d13d30d9678d63a98f3732` |

第一次使用按这个顺序完成一个小范围任务：**检查能力与会话 → 保存 AI 连接 → 创建研究或采集 → 在任务中心跟踪 → 回查资料与关系依据 → 生成并下载报告 → 保存研究备份。** 采集 X 或 Instagram 时，在应用提供的登录入口使用自己的账号；公开平台的实际访问限制会显示在任务诊断中。

## 当前可以做什么

- **保存与检索公开资料**：RSS / Atom、Google News RSS、X、Instagram、JSON 导入、正文阅读、来源追踪和重复检测。
- **快速分类与研究账号关系**：从关键词或指定账号出发，保存发现路径、公开观察关系和分支断点。在“组织与账号”或图谱点击“快速判定账号类型”，DS 根据已保存名称、用户名和简介只粗分组织／个人，默认每批 100 个，最多 200 个。明确以集体名义运营或发布的账号可判组织，其余默认个人；简介中的成员身份、工作经历或支持对象本身不证明组织发布。类型可快捷手改并锁定；粗分不自动核验身份、不访问外部公开网页。身份核验与关系依据另行检查。
- **生成有来源的研究报告**：规则证据整理，或使用 `deepseek-flash` 等已配置模型进行全文阅读、摘录复核、DSH 工具调查、报告生成与复核。付费分析前保存完整输入；失败后先免费预览，明确选择原输入续跑或最新状态重新生成，逐阶段验证可复用内容。
- **严格审核与独立核验**：逐批核对关系主张、活动原文，按需另选账号公开证据审核，保存确认、排除或证据不足。严格审核默认每批 16 条，可用独立网页和来源缓存；候选账号身份核验有单独入口。快速分类、严格审核都可在任务中心取消或续跑，人工锁定类型受保护。
- **跟踪任务与活动地图**：预估并确认研究范围，创建任务串，查询进度、取消及重试。活动仅在保存的公开正文与可信原帖发布时间支持时，按日本标准时间解释同一活动的“本日／今日／昨日／今年”；裸月日不自动补年，地点须有实际举办地依据，预告与未来号召不计作已举行活动。核对结果说明来源如何描述，不能替代独立事实核实；日期或地点不足时保留未知。地图仅统计选定实际活动日期内已核对、已定位且去重的活动。
- **备份、恢复及外部 Agent 接入**：在线研究数据库备份、桌面确认恢复、可恢复资料清理；通过独立授权的本机 Skill / CLI 读取、研究、报告与审核。

本版已接入官方 **DeepSeek Harness SDK/runtime 0.2.0-rc.2** 的实际工具调用循环。模型通过 API 发出请求，DSH 运行时根据任务调用受约束的证据工具，工具结果再进入后续模型决策、来源复核和保存流程。桌面包使用自身 Electron 的 Node 运行时，用户无需另行启动 DSH 桌面应用。模型名称以用户保存的连接及提供方实际支持为准，默认建议为 `deepseek-flash`。

历史真实严格重审完成 61 个账号和 155 条活动，旧三分类版随后完成全部 61 个账号，耗时 12.84 秒，原始结果为**组织 23、个人 15、未判定 23**，作为旧版任务证据保留。该历史任务没有新增身份核验，原有 2 个已核验账号、551 条保存来源和 252 条公开观察关系保留；它不证明本轮正式二分类已运行。历史活动审核为确认 1、证据不足 153、排除 1；该范围的地图分母是 1 条东京活动，占比 100%，依据为来源描述核对（`source_claim_only`），不代表全部社会活动。旧 unknown 审计记录保留，当前类型页面仅显示组织／个人。

同一源码完整回归为后端 3,082、前端 210、桌面 62、构建工具 49，合计 **3,403 通过、0 失败**。Windows 上另有 2 项 POSIX 权限测试按平台跳过，对应权限修复另有 POSIX 环境证据。受控模型测试、历史正式 DeepSeek 任务和本轮原生安装分别记录；本轮 61 个账号的二分类复测待用户授权接入后执行，人工锁定类型保留，实际 AI 处理数量以新任务收据为准；不用测试夹具或旧任务代替真实新任务。601 材料的失败报告仍保留阅读成果和草稿，可免费预览续跑；本版不保证每次长报告都能通过最后复核。

## 操作说明与项目判断

| 文档 | 内容 |
| --- | --- |
| [完整操作说明书](docs/USER_MANUAL_20261010_AGENT.md) | 20 部分、27 个参数表：首次使用、研究与采集、快速分类与人工快改、审核、关系图与地图、报告续跑、备份清理及 Agent 操作；发布页同时提供可双击打开的离线 HTML |
| [DSH 工具分析与恢复](docs/USER_MANUAL_20261010_AGENT.md#17-dsh-工具分析设置取证与失败后续跑) | 完整说明书第 17 部分，包含工具调查、报告续跑和结果判断 |
| [快速分类与严格审核](docs/USER_MANUAL_20261010_AGENT.md#20-自动审核核验与-agent-接口) / [随包说明](analysis-worker/app/workbench/agent_assets/AUTOMATIC_REVIEW.md) | 类型粗分与快改、严格审核范围、独立出处、证据不足及重试规则 |
| [Agent 接入契约](docs/AGENT_INTEGRATION.md) / [Skill 安装](analysis-worker/app/workbench/agent_assets/INSTALL.md) | 本机接口、授权边界及安装步骤 |
| [项目基础审查](docs/FOUNDATION_REVIEW_20261011.md) / [第一批实现](docs/FOUNDATION_IMPLEMENTATION_20261011.md) / [路线图](docs/ROADMAP.md) | 基线判断、已实施改进与后续优先级 |
| [本轮收尾发布记录](docs/RELEASE_FINAL_20261011.md) / [桌面构建](desktop/BUILDING.md) | 当前冻结文件、验收、哈希和平台限制 |

公开账号候选不等于已核验主体，关注、共同链接或模型推断不等于组织隶属；严格审核的证据不足结果和旧审计历史保留，当前发布主体类型只显示组织／个人。资料中的地名提及和发布日期不能代替实际活动地点或活动日期。任务完成状态只适用于本次范围，不保证完整平台历史、全部公开关系或持续可访问。

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
