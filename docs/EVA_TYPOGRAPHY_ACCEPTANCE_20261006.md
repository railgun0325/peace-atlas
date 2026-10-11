# EVA 风格字体改版验收 · 2026-10-06

**2026-10-06 当前交付为 `-eva-candidate`：按用户参考图调整为粗重明朝体标题、紧排汉字和窄体操作标签，字体离线随包分发。** 仅 Windows x64 与 Apple Silicon，Intel 不构建。整体创建 / 执行流程继承已完成的统一工作台改版，详情见 [字体验收](EVA_TYPOGRAPHY_ACCEPTANCE_20261006.md)。

## 字体与排版

参考用户提供的 EVA 标牌图，采用竖画粗、横画细的明朝 / 宋体字形。标题使用 Noto Serif SC 可变字体，主要标题字重 900，导航和按钮 800；标题收紧字距，保留真实文字及换行。英文操作标签与醒目编号使用 Barlow Condensed Bold。正文、输入和账号 ID 保留原有无衬线 / 等宽字体。

字体完整字形覆盖随应用打包为 WOFF2，不依赖外部 CDN 或本机安装。字体转换保留原始字形和字体元数据，授权文件及来源哈希在 `assets/fonts` 中一并分发。采用字体风格近似，不声称使用 EVA 官方商业字体。

- [Noto Serif SC 字体授权](https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifsc/OFL.txt)
- [Barlow Condensed 字体授权](https://raw.githubusercontent.com/google/fonts/main/ofl/barlowcondensed/OFL.txt)
- 本地字体源、字重范围与哈希：[fonts.json](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/static/workbench/fonts/fonts.json>)

覆盖总览主标题、页头、工作台卡片、导航、按钮、弹窗及分区标题。小屏幕下调整标题字号、字距、面板标题换行和按钮换行；600px 以下总览隐藏装饰字徽，保留标题空间。未修改业务参数、草稿模型或后端接口。

## 实际验收

本轮字体改版的 8 组真实应用路由离线检查通过，含 390px 布局；总览、工作台和创建弹窗的最终 Windows EXE 实测通过，900 字重明朝体及窄体字体均已实际加载，正文继续使用无衬线字体，页面和弹窗无横向溢出。应用与后台退出码均为 0，ready 文件为 0，后台日志未见 ERROR。

本轮只重新运行 8 组应用路由检查；之前 79 项 Node / 30 组浏览器与 654 项后端测试作为历史基线保留，不写成本轮重新执行。分发审计确认 35 个后端来源文件与此前包相同。浏览器已实际查看桌面总览、工作台及 390px 工作台，临时视口已恢复；用户原草稿未编辑，未提交任何研究或采集任务。

最终成品采用独立空数据目录验收，不复制账号会话或模型密钥；截图中的数量 0 是隔离验收目录的真实状态。实际 Windows 截图均已逐张视觉检查：

- [总览](<C:/Users/wwx/Documents/New project 7/output/desktop-eva-smoke-2/desktop-eva-overview.png>)
- [研究工作台](<C:/Users/wwx/Documents/New project 7/output/desktop-eva-smoke-2/desktop-eva-workspace.png>)
- [创建弹窗](<C:/Users/wwx/Documents/New project 7/output/desktop-eva-smoke-2/desktop-eva-composer.png>)

第一轮字体专用验收在初始认证重定向后立即用 Electron `loadURL` 切换 fragment，收到 `ERR_FAILED (-2)`；未执行到字体检查。已把该验收分支改为等待应用侧栏初始化并点击其导航，再重新构建两平台。原失败日志、退出码 1 与原 EXE 目录保留于 `desktop-eva-smoke-1`，最终 `desktop-eva-smoke-2` 退出 0。不把首次验收失败藏为成功。

## 最新交付

| 平台 | 当前交付 | SHA-256 / 验收 |
| --- | --- | --- |
| Windows x64 | [EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-eva-candidate-20261006-045448/PEACE ATLAS.exe>)；[ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-eva-candidate.zip>) | `3d6568d98eaf9797363fd7f640146a2766f709d7df016b9f9c831b343ebdddf1`；66 应用文件与当前源码一致，实际成品退出 0。 |
| Apple Silicon Mac | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-eva-candidate.zip>) | `d6560cf1668c5a22783a7d9c4def90b3439471545595dd61a5097e0c685e25c5`；67 应用文件一致，44 Mach-O / 29 符号链接与 ad-hoc 检查通过；原生双击待用户验收。 |


证据：[最终汇总](<C:/Users/wwx/Documents/New project 7/output/eva-acceptance-final.json>)、[分发审计](<C:/Users/wwx/Documents/New project 7/output/eva-package-audit-final.json>)、[成品原始记录](<C:/Users/wwx/Documents/New project 7/output/desktop-eva-smoke-2/desktop-smoke.json>)。

Apple Silicon 原生 codesign 与双击验收仍待用户在 Mac 上完成。当前字体改版未新增 X 关键词正向命中、Instagram 深度实采或大规模任务产量证据，研究能力边界继续参照此前验收记录。
