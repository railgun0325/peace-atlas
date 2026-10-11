# 研究工作台与任务中心分工验收

本轮响应“新建采集与深度研究重叠、布局逻辑令人困惑”，调整前端信息架构与草稿恢复合同。当前版本使用 `-workspace-candidate` 后缀；`-research-candidate` 是本轮之前的研究能力基线。平台与真实研究证据的历史边界继续见 [研究能力验收](RESEARCH_ACCEPTANCE_20261006.md)。

## 根因与页面职责

旧任务中心“新建采集”和深度研究页“开始新研究”调用同一表单，而且前者也默认深度研究。表单把“深度研究、简单采集、批量任务串”放在同一级，混合工作内容与执行安排。账号快捷入口的明确选择又可能被旧草稿覆盖。

| 页面 | 当前职责 |
| --- | --- |
| 研究工作台 | 创建研究或采集、继续草稿、查看已有研究结果。先选择采集公开材料或探索账号与关系，再选择单次执行或连续任务串。 |
| 任务中心 | 查看已提交工作的队列、进度、任务串、子任务、取消、重试及失败诊断。创建工作跳到研究工作台。 |
| 资料库 | 查看采集与研究实际保存的原始材料及其来源。 |
| 研究结果 | 保留发现树、关系网络、公开证据与待复核判断，可返回对应任务执行记录。 |
| 组织与账号 | 管理来源账号；采集快捷入口进入统一工作台，并明确指定该账号与平台。 |
| 能力与设置 | 配置模型与检查平台会话；来源卡片的采集入口进入统一工作台。 |

旧路由 `#network`、`#network?run=...`、`#jobs?selected=...`、任务串链接继续有效。导航编号统一为总览 01、工作台 02、任务中心 03、资料库 04、账号 05、报告 06、设置 07。

## 表单与草稿合同

- 工作内容和执行安排各有独立选择，两种工作都可单次或连续执行。切换安排不会自动提高保存预算。
- 旧 `research/simple/batch` 草稿迁移成新的内容和安排组合，保留关键词、日期、目标、预算和来源选择；简单采集不会受旧隐藏 `batch_kind` 干扰。
- 明确的账号、平台及内容入口优先于草稿；其他输入仍恢复。延迟返回的服务端草稿也遵守这个优先级，开始输入后的新编辑不会被覆盖。
- 新闻研究默认只选公开新闻。关系研究的起点由可见来源复选框决定，隐藏来源不会与复选框冲突；未勾选社媒不触发该平台检查。
- 研究结果列表独立加载并分页，每页 25 条。列表慢或失败时，创建入口仍立即可用，失败状态不会显示成空资料。
- 任务串仍先预览再提交，修改条件后旧预览失效。总体材料目标最多 10,000、每串最多 50 子任务，全局队列与历史分页显示。单次采集的 100 条是材料预算，不是全局任务数量上限。

## 本轮实际检查

前端 **79 项 Node 逻辑检查、30 组隔离 Chromium 流程检查通过**，全部模块和桌面入口语法检查通过。30 组由采集表单 7、社媒与来源 9、关系结果 3、任务串 3、完整应用导航 8 组成；所有离线请求均被测试拦截，不能作为外部平台或 AI 已可用的证明。

新增完整应用检查使用真实 `app.js` 路由，覆盖任务中心入口归并、内容/执行组合、预算保留、旧草稿迁移、账号快捷入口、延迟草稿与用户编辑、提交后跳转、旧研究链接、预算后续规划、慢/失败列表以及 1440/390 像素布局。已有取消、重试、诊断、公开证据、草稿冲突、社媒登录检查断言继续保留。

开发服务 `127.0.0.1:18769` 已通过实际浏览器核对：原六组关键词、研究目标、2026-09-07 至 10-06、每子任务 50 条、总体 1,000 条及已选新闻/X均保留；新表单预览仍为 24 个子任务。本次只预览，没有提交这 24 个任务。浏览器停留在研究工作台，草稿保留。

后端本轮没有功能或模型修改，上一轮完整回归 **654 项通过**作为既有基线使用；当前包的后端来源与上轮包的哈希对比另由本轮分发审计记录，不把上一轮测试写成本轮新跑结果。

## 成品、证据与限制

最终路径、哈希、Windows 实际 EXE 退出结果与签名审计由本轮 [workspace-acceptance-final.json](<C:/Users/wwx/Documents/New project 7/output/workspace-acceptance-final.json>) 汇总，分发文件逐项核对见 [workspace-package-audit-final.json](<C:/Users/wwx/Documents/New project 7/output/workspace-package-audit-final.json>)。

第一轮 Windows 实测已通过新导航、两个独立选择、刷新草稿恢复、真实新闻两子任务及 X 自动复用。该轮准备脚本误用会话文件名导致复制未执行，实际应用随后从授权默认 Firefox 会话验证保存，再次复用工作台会话，两次均成功。原记录保留于 `output/desktop-workspace-final-smoke`。

该轮 `desktop-workspace.png` 由于截图早于页面绘制，仍显示上一张设置页，不能当作工作台截图证据。验收脚本已增加绘制等待和表单会话检查等待，第二轮截图和退出通过。选择框文案随后缩短，最终成品另用 `output/desktop-workspace-final-smoke-3` 验证，原三轮记录均保留。第一轮退出时仍有表单会话检查在执行，原后台 `TargetClosedError` 日志保留，不宣称该轮关闭日志无错误。

本轮只构建 Windows x64 和 Apple Silicon Mac；Apple Silicon 原生启动与原生 codesign 仍待用户双击验收，Intel 不更新。当前改版不新增 X 关键词正向命中或 Instagram 深度实采证据，相关缺口继续按研究能力验收记录保留。

## 最终分发与 Windows 实测结果

本轮前端 **79 项 Node、30 组离线 Chromium 流程检查通过**，含 8 组真实应用路由与跨页面检查。后端 35 个分发源文件与此前研究包逐项哈希一致；**654 项后端回归是上一轮基线，本轮未重跑**。最终 Windows 成品 smoke-3 实际退出 0，后台正常关闭，ready 文件 0，后台日志未见 ERROR；已实际检查新版工作台、独立内容/执行选择、草稿恢复和已有关系图截图。

实际创建默认标题任务串，两项新闻子任务均 `partial / record_budget`，各 `fetched:1 / inserted:0 / duplicates:1`，总体新增 0、重复 2。两次 X 自动检查均验证工作台已有会话可用，无新登录。该验收不增加 X 关键词正向命中、Instagram 深度实采或 1,000 条实际产量证明。

| 平台 | 当前交付 | 字节数 / SHA-256 / 验收 |
| --- | --- | --- |
| Windows x64 | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-workspace-candidate.zip>)；[EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-workspace-candidate-20261005-222736/PEACE ATLAS.exe>) | 413,026,746；`850f5b3b9f4a228d558597ceee0c2495f2b35445b56ad1eac0c7c742133f2f82`；60 应用文件源码/manifest/payload 一致，实际 EXE 退出 0。 |
| Apple Silicon Mac | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-workspace-candidate.zip>) | 363,884,900；`775a8d04d430da69ad848dccae08671eccbc48d72548bbf56bd4f3feb2b4f667`；61 应用文件一致，44 Mach-O / 29 符号链接与 ad-hoc 检查通过；原生启动待用户双击。 |

证据：[最终汇总](<C:/Users/wwx/Documents/New project 7/output/workspace-acceptance-final.json>)、[分发审计](<C:/Users/wwx/Documents/New project 7/output/workspace-package-audit-final.json>)、[实际 EXE 记录](<C:/Users/wwx/Documents/New project 7/output/desktop-workspace-final-smoke-3/desktop-smoke.json>)。
