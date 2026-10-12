<p align="center">
  <img src="desktop/assets/icon.svg" alt="PEACE ATLAS 标志" width="96" height="96">
</p>

<h1 align="center">PEACE ATLAS</h1>

<p align="center">
  <strong>日本和平与反战运动公开信息研究工作台</strong><br>
  从公开线索出发，整理资料、梳理关系、追踪活动，形成可以回查的研究。
</p>

<p align="center">
  <a href="docs/QUICKSTART.md">图文上手</a> ·
  <a href="docs/USER_MANUAL_20261010_AGENT.md">操作说明</a> ·
  <a href="https://github.com/railgun0325/peace-atlas/releases/latest">版本更新</a> ·
  <a href="https://github.com/railgun0325/peace-atlas/issues">问题反馈</a>
</p>

## 下载与安装

| Windows | Mac |
| :--- | :--- |
| **[下载 Windows](https://github.com/railgun0325/peace-atlas/releases/download/v2.0.0-binary-account-types.20261012/PEACE-ATLAS-2.0.0-Windows-x64-Setup.exe)** | **[下载 Mac](https://github.com/railgun0325/peace-atlas/releases/download/v2.0.0-binary-account-types.20261012/PEACE-ATLAS-2.0.0-macos-arm64-binary-account-types-20261012-native-be315a35e7ee-run38166632103-1.pkg)** |
| 64 位 Windows，双击 `.exe` 安装 | Apple Silicon（M 系列），macOS 13+，双击 `.pkg` 安装 |

安装后直接启动 PEACE ATLAS，无需另装 Python、Node 或 DSH Desktop。

## 从资料到研究

PEACE ATLAS 将采集、阅读、关系梳理和报告整理放在同一个本地工作台中。研究资料保存在自己的电脑上，来源链接和分析依据可以随时回查。

- **资料检索**：从关键词或来源账号开始，收集新闻、RSS、X、Instagram 等公开材料，保存正文并检索历史资料。
- **AI 分析**：使用 DeepSeek 阅读和整理材料，通过 Harness 工具继续调查，生成带来源引用的报告。
- **账号关系图**：拖拽探索账号联系，区分组织与个人，按关注方向查看关系，并快捷更正类型。
- **日本活动地图**：查看已核对活动的地区分布、数量与占比，结合日期范围筛选。
- **审核与核验**：批量审核关系主张与活动线索，核对账号公开证据；保留人工更正和原文依据。
- **任务与数据管理**：查看进度、取消或继续任务，备份研究，清理无关资料并按需恢复。

## 第一次使用：跑通一个小任务

安装后，可以先用 **公开新闻 + 关键词** 熟悉完整流程，无需社媒登录，也可以先不配置 AI。以下截图使用演示资料，红框和编号标出操作位置。

### 1. 打开研究工作台

点击左侧 **“研究工作台” → “探索账号与关系”**。如果只是想保存新闻或帖子，也可以选择“采集公开材料”。

![点击研究工作台，再点击探索账号与关系](docs/images/quickstart/01-workspace.jpg)

### 2. 填写关键词和研究范围

“工作内容”选择 **“探索账号与关系”**，“执行安排”选择 **“单次执行”**；来源只勾选 **“公开新闻”**。关键词可输入 `反戦 デモ`，日期点击 **“近 7 天”**。

![填写关键词并选择近7天的日期范围](docs/images/quickstart/02-research-input.jpg)

第一次可将 **“总材料停止上限”设为 50**。展开 **“研究范围与可选停止条件”**，把向下探索层数设为 `1`，并选择 **“公开证据整理 · 不调用 AI”**。这是起步示例，熟悉后可放宽范围；总量或时长设为 `0` 时按所选范围完成。[查看完整示例参数](docs/QUICKSTART.md#3-设置一个小范围示例)

### 3. 先预估，再确认开始

点击 **“预估用时与范围”**，查看当前参数的预计用时，再点击 **“确认预计用时并开始”**。预估本身不会开始采集；修改参数后需要重新预估。

![查看预计用时，再点击确认预计用时并开始](docs/images/quickstart/04-confirm-estimate.jpg)

### 4. 在任务中心查看进度

创建后会进入 **“任务中心”**。任务详情显示当前阶段、已保存材料数量和执行记录；可以关闭详情窗口，让任务继续在后台运行。

![任务详情中的进度条、保存数量和执行记录](docs/images/quickstart/05-task-progress.jpg)

### 5. 阅读原文，再整理成果

到 **“资料库”** 点击资料标题，阅读正文并打开 **“查看原始来源”**。需要汇总时，进入 **“研究报告” → “生成报告”**，首次可选 **“证据整理 · 无需 AI 配置”**。

新闻研究可能只取得文章，账号关系会在发现相应公开线索后出现。若结果为零或任务失败，先查看任务中的来源诊断，再调整关键词和日期。

**[继续看完整图文上手：选择来源 → 设置范围 → 阅读原文 → 生成报告 → 配置 AI](docs/QUICKSTART.md)**

要使用 DeepSeek 分析，在 **“能力与设置”** 的 **“AI 连接设置”** 中填写自己的 API Key 并保存，默认模型为 `deepseek-flash`。[查看 AI 配置截图](docs/QUICKSTART.md#使用-deepseek-分析可选)

## 让你常用的 Agent 操作

除了手动操作，也可以通过授权的 Skill / CLI，让 Codex、Claude Code、DSH 等 Agent 执行检索、研究、报告与审核任务。在软件中导出授权连接，按[Agent 接入说明](docs/AGENT_INTEGRATION.md)配置即可。

## 文档

- [图文快速上手](docs/QUICKSTART.md)：带标注截图，完成第一次检索、阅读与报告。
- [操作说明书](docs/USER_MANUAL_20261010_AGENT.md)：完整流程、参数设置、审核、备份与恢复。
- [Agent 接入](docs/AGENT_INTEGRATION.md)：授权连接、接口与 Skill 使用。
- [开发与构建](docs/DEVELOPMENT.md)：从源码运行、测试和制作安装包。
- [版本说明](https://github.com/railgun0325/peace-atlas/releases/latest)：本版更新与安装包。

<details>
<summary>安装提示与使用条件</summary>

- Windows 安装器未签名，Mac 安装包未完成 Apple 公证，系统可能显示安全提示。
- AI 分析需要自己的 API Key；模型费用由服务提供方收取。
- 公开平台的访问范围取决于平台限制和自己的登录状态。
- 账号类型可以人工更正；关系和活动以保存的公开证据为依据。

</details>
