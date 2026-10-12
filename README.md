<p align="center">
  <img src="desktop/assets/icon.svg" alt="PEACE ATLAS 标志" width="96" height="96">
</p>

<h1 align="center">PEACE ATLAS</h1>

<p align="center">
  <strong>日本和平与反战运动公开信息研究工作台</strong><br>
  从公开线索出发，整理资料、梳理关系、追踪活动，形成可以回查的研究。
</p>

<p align="center">
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

## 第一次使用

1. **安装并启动。** 下载对应系统的安装包，安装后打开 PEACE ATLAS。
2. **检查能力与连接。** 采集社媒时按提示登录；需要 AI 分析时，在“能力与设置 → AI 连接”保存自己的 API Key，默认使用 `deepseek-flash`。
3. **开始一个研究。** 在研究工作台输入关键词或来源账号，设置范围并启动；在任务中心查看进度。
4. **回查与整理。** 阅读保存的材料，在关系图和地图中探索，再生成报告或继续挖掘。重要研究可随时备份。

具体参数和各页面的操作方法见[完整操作说明书](docs/USER_MANUAL_20261010_AGENT.md)。

## 让你常用的 Agent 操作

除了手动操作，也可以通过授权的 Skill / CLI，让 Codex、Claude Code、DSH 等 Agent 执行检索、研究、报告与审核任务。在软件中导出授权连接，按[Agent 接入说明](docs/AGENT_INTEGRATION.md)配置即可。

## 文档

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
