# V2 验收清单与交付记录

**2026-10-08 当前交付为 `-daily-candidate`，Windows x64 与 Apple Silicon。** 本轮修复草稿和文件迟到覆盖、多窗口版本冲突、单任务／任务串重复重试、已保存结果的终态补写、续查排队状态、AI 配置超时与坏配置启动，以及恢复后损坏停止原因导致的重试崩溃。子代理统一使用 **GPT-6.1 Sol Ultra**。

[实际 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-daily-candidate-20261008-115220/PEACE ATLAS.exe>)；[双击启动入口](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[Windows ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-daily-candidate.zip>)；[Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-daily-candidate.zip>)。正常使用不需另装 Python、Node.js 或 Docker。

最终 **848 后端、105 前端 Node、61 浏览器组、59 桌面测试、11 构建检查通过**。新的 Windows EXE 真正验证系统加密配置恢复、备份下载和研究库恢复；包内后台另通过 15 项日常可靠性、14 项完整流程及 4 种进程退出检查。两平台源码、manifest、ZIP 与 Windows portable 字节一致。所有操作使用隔离 profile；文件恢复的操作系统选择／确认采用验收注入，未新增外部平台实采或真实 AI 调用。

[本轮验收与边界](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/DAILY_RELIABILITY_ACCEPTANCE_20261008.md>)；[机器可读记录](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/daily-acceptance.json>)。Windows ZIP SHA256 `008784e073b69d78226f65a35e433a49fa8f761038382707614c34c967496653`；Mac ZIP SHA256 `099dcd95c4dd62e451e0c4f32fc5a8d3127f900cb6ee9974fd9638d782d78906`。Mac 原生启动／Apple codesign、实机 PostgreSQL 并发、10,000 条实际产量和 24 小时耐久性仍待验证，整体产品目标未标记完成；交付时目标面板显示暂停。

## 此前恢复版本的交付记录

**此前 2026-10-08 恢复交付为 `-recovery-candidate`，仅 Windows x64 与 Apple Silicon。** 设置页新增“恢复研究备份”：只读预检、原生确认、旧库自动归档、恢复后手动重试未完成任务，异常恢复保留事务证据。修复关闭抽屉后迟到响应、连续关闭窗口、Python 正常退出及损坏备份的实际消费错误。子代理统一使用 **GPT-6.1 Sol Ultra**。

当前 [Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-recovery-candidate-20261008-110543/PEACE ATLAS.exe>)，或双击 `C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk`。[Windows ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-recovery-candidate.zip>) 可完整解压分发；[Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-recovery-candidate.zip>) 已构建并通过 WSL ad-hoc 审计，Mac 原生启动与 Apple codesign 验证仍未完成。正常使用不需要另装 Python、Node.js 或 Docker。

本轮 **816 项后端、102 项前端 Node、47 组离线 Chromium、44 项桌面测试、11 项构建清单检查通过**。新 Windows EXE 原生恢复和独立 SQLite 21 表比对通过；应用与前后两个后台退出均为 0，ready 与事务记录无残留。包内私有 Python 另通过 14 项完整流程及 4 种进程退出场景。恢复使用隔离确定性样本，文件选择与确认采用验收注入，没有新增外部平台实采或真实 AI 验收。

文档：[恢复验收](RECOVERY_ACCEPTANCE_20261008.md)、[备份与恢复方法](DATA_BACKUP.md)、[机器可读记录](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/recovery-acceptance.json>)。Windows ZIP SHA256：`e62500c77ace2226229147f876d788801a81cb9955ad3e16ce12f4665d699259`；Mac ZIP SHA256：`00bafe9ab26bc16700b0cc789b569cc55edb2332eb20f14431991ee7940a0249`。上述通过不代表全部平台或 24 小时耐久性已测完。

## 此前产品完善版本的交付记录


**此前 2026-10-08 产品完善交付为 `-product-candidate`：运行中备份、研究完整导出、去重后的研究关联、下载错误和异步交互、桌面生命周期与配置保存完善。** 接续子代理按用户最新要求使用 GPT-6.1 Sol / Ultra。保留研究工作台、EVA 字体和预算范围，仅 Windows x64 与 Apple Silicon。

本轮最终 **713 项后端、99 项前端 Node、40 组离线 Chromium、14 项桌面生命周期与 11 项构建清单检查通过**。Windows 成品真实新闻新增 1 条、重复 1 条、证据报告 1 份，实际点击备份下载成功且数据库完整性、外键和最新提交草稿校验通过；应用及后台退出 0，ready 残留 0。包内私有后端另完成 14 项确定性流程检查，包括重启和独立目录恢复，没有调用外部平台或 AI。

| 平台 | 当前交付 | SHA-256 / 验收 |
| --- | --- | --- |
| Windows x64 | [EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-product-candidate-20261008-092902/PEACE ATLAS.exe>)；[ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-product-candidate.zip>) | `95970024d818d10d4b6aa09a72290b379ac93535627649a6cbd9daa5a045e471`；69 应用文件与源码 / manifest / 包内字节一致，实际 Windows EXE 验收通过。 |
| Apple Silicon Mac | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-product-candidate.zip>) | `26d358022557ecc183301611171ee9a753fc7dac54fc7f20275dbff41a53964d`；70 应用文件一致，44 Mach-O / 29 符号链接和 WSL ad-hoc 审计通过；原生 Apple codesign 与 Mac 启动仍待验证。 |

第一次短期间公开新闻全部被过滤导致原生非空断言失败，记录保留；仅扩大隔离验收期间后，同一成品完成新闻到备份流程。没有放宽生产筛选规则，也没有新增 X / Instagram 或真实 AI 模型验收。预算预览不代表 10,000 条产量或 24 小时耐久性实测。

证据：[机器可读总记录](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/product-acceptance.json>)、[独立分发审计](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/package-audit.json>)、[原生与备份实物验收](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/native-acceptance.json>)。行为、失败及边界详见[产品完善验收](PRODUCT_ACCEPTANCE_20261008.md)，使用备份见[恢复说明](DATA_BACKUP.md)。

## 此前预算版本的交付记录


**2026-10-06 此前交付为 `-budget-candidate`：研究预算贯通前端、API、任务串、引擎和社媒读取；任务串主按钮明确先预览、再创建。** 保留 EVA 明朝体标题及窄体标签。仅 Windows x64 与 Apple Silicon，Intel 不构建。

本轮 696 项后端回归、85 项 Node 与32组离线浏览器流程通过。最终 Windows EXE 的实际前端与私有后端验收通过：500条／25次／3600秒及10000条／200次／86400秒均可预览，预览不入队；离线字体实际加载，页面无横向溢出。应用退出0，后台正常关闭，无残留ready文件。

| 平台 | 当前交付 | SHA-256 / 验收 |
| --- | --- | --- |
| Windows x64 | [EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-budget-candidate-20261006-080033/PEACE ATLAS.exe>)；[ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-budget-candidate.zip>) | `1116f3b46ec319452f2513239a66f24d5773ea88c0e1246b687f48a990c83e54`；68应用文件与当前源码一致，实际成品退出0。 |
| Apple Silicon Mac | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-budget-candidate.zip>) | `9e2ac00866489bcd3af35a58bf4610e61547a6c02f8d35320dab3defacee00d1`；69应用文件一致，44 Mach-O／29符号链接及ad-hoc审计通过；原生双击仍待用户验收。 |

证据：[最终汇总](<C:/Users/wwx/Documents/New project 7/output/budget-acceptance-final.json>)、[分发审计](<C:/Users/wwx/Documents/New project 7/output/budget-package-audit-final.json>)、[成品原始记录](<C:/Users/wwx/Documents/New project 7/output/desktop-budget-smoke-1/desktop-smoke.json>)。详细行为与边界见[预算与任务串验收](BUDGET_ACCEPTANCE_20261006.md)。

## 此前 EVA 字体版本的交付记录

**2026-10-06 此前交付为 `-eva-candidate`：按用户参考图调整为粗重明朝体标题、紧排汉字和窄体操作标签，字体离线随包分发。** 仅 Windows x64 与 Apple Silicon，Intel 不构建。整体创建 / 执行流程继承已完成的统一工作台改版，详情见 [字体验收](EVA_TYPOGRAPHY_ACCEPTANCE_20261006.md)。

本轮字体改版的 8 组真实应用路由离线检查通过，含 390px 布局；总览、工作台和创建弹窗的最终 Windows EXE 实测通过，900 字重明朝体及窄体字体均已实际加载，正文继续使用无衬线字体，页面和弹窗无横向溢出。应用与后台退出码均为 0，ready 文件为 0，后台日志未见 ERROR。

| 平台 | 当前交付 | SHA-256 / 验收 |
| --- | --- | --- |
| Windows x64 | [EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-eva-candidate-20261006-045448/PEACE ATLAS.exe>)；[ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-eva-candidate.zip>) | `3d6568d98eaf9797363fd7f640146a2766f709d7df016b9f9c831b343ebdddf1`；66 应用文件与当前源码一致，实际成品退出 0。 |
| Apple Silicon Mac | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-eva-candidate.zip>) | `d6560cf1668c5a22783a7d9c4def90b3439471545595dd61a5097e0c685e25c5`；67 应用文件一致，44 Mach-O / 29 符号链接与 ad-hoc 检查通过；原生双击待用户验收。 |

证据：[最终汇总](<C:/Users/wwx/Documents/New project 7/output/eva-acceptance-final.json>)、[分发审计](<C:/Users/wwx/Documents/New project 7/output/eva-package-audit-final.json>)、[成品原始记录](<C:/Users/wwx/Documents/New project 7/output/desktop-eva-smoke-2/desktop-smoke.json>)。

## 此前统一工作台版本的交付记录

**2026-10-06 此前交付为 `-workspace-candidate`：统一研究工作台创建入口，任务中心管理执行，工作内容与执行安排独立选择。** 仅 Windows x64 和 Apple Silicon；Intel 不更新，Apple Silicon 原生启动待用户验收。改版详情见 [工作台验收](WORKSPACE_ACCEPTANCE_20261006.md)。

本轮前端 **79 项 Node、30 组离线 Chromium 流程检查通过**，含 8 组真实应用路由与跨页面检查。后端 35 个分发源文件与此前研究包逐项哈希一致；**654 项后端回归是上一轮基线，本轮未重跑**。最终 Windows 成品 smoke-3 实际退出 0，后台正常关闭，ready 文件 0，后台日志未见 ERROR；已实际检查新版工作台、独立内容/执行选择、草稿恢复和已有关系图截图。

实际创建默认标题任务串，两项新闻子任务均 `partial / record_budget`，各 `fetched:1 / inserted:0 / duplicates:1`，总体新增 0、重复 2。两次 X 自动检查均验证工作台已有会话可用，无新登录。该验收不增加 X 关键词正向命中、Instagram 深度实采或 1,000 条实际产量证明。

| 平台 | 当前交付 | 字节数 / SHA-256 / 验收 |
| --- | --- | --- |
| Windows x64 | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-workspace-candidate.zip>)；[EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-workspace-candidate-20261005-222736/PEACE ATLAS.exe>) | 413,026,746；`850f5b3b9f4a228d558597ceee0c2495f2b35445b56ad1eac0c7c742133f2f82`；60 应用文件源码/manifest/payload 一致，实际 EXE 退出 0。 |
| Apple Silicon Mac | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-workspace-candidate.zip>) | 363,884,900；`775a8d04d430da69ad848dccae08671eccbc48d72548bbf56bd4f3feb2b4f667`；61 应用文件一致，44 Mach-O / 29 符号链接与 ad-hoc 检查通过；原生启动待用户双击。 |

证据：[最终汇总](<C:/Users/wwx/Documents/New project 7/output/workspace-acceptance-final.json>)、[分发审计](<C:/Users/wwx/Documents/New project 7/output/workspace-package-audit-final.json>)、[实际 EXE 记录](<C:/Users/wwx/Documents/New project 7/output/desktop-workspace-final-smoke-3/desktop-smoke.json>)。

## 此前研究版本的进度与验收基线

**2026-10-06 当前版本是 `-research-candidate`：深度研究、自动草稿与批量任务串，交付 Windows x64 和 macOS Apple Silicon。所有 `-social-candidate` 路径与哈希均是历史社媒候选记录，不能作为本轮最新成品入口。** 当前研究实证详见 [RESEARCH_ACCEPTANCE_20261006.md](RESEARCH_ACCEPTANCE_20261006.md)，研究候选包的哈希、实际启动结果在下方单独记录；Intel Mac 不在本轮交付范围。

本文件记录验收方法与待核对项目。复选框初始全部留空，不代表失败，也不代表已通过。执行后填写实际日期、版本、环境、证据路径和结论；未在目标设备测试的项目写“未实测”，不由源码存在、API 测试通过或 ZIP 构建成功推定。

本轮最终完整后端回归为 **654 项通过，54.39 秒**，前端 **71 项 Node、22 组离线 Chromium DOM 检查全部通过**。后端保留一项既有非失败的 Pydantic alias 警告。该结果包含混合 Fake-IP / 公网 IPv6 修复、X 搜索目标就绪校验与旧 DOM 测试页面升级，不替代桌面成品验收。

可选任务串标题留空预览曾返回 422；真实界面复现后已修复。省略、空字符串、纯空白使用默认“批量公开资料研究”，自定义标题去除首尾空格；4 项 API 预览 / 创建回归通过，已包含在最终 654 项中。最终成品已实际 POST 空标题并确认默认标题，Windows smoke-4 整套退出 0。

最终成品的两个新闻子任务各保留 1 条研究证据，均为 `partial / record_budget`，各 `fetched:1 / inserted:0 / duplicates:1`；总体新增 0、重复 2。两次 X 自动复用 `verified:true / saved:false / source:workbench / requires_login:false`；实际截图确认草稿恢复提示和 3 节点 / 2 边 / 3 材料关系图，默认浏览器入口、安全存储、刷新恢复和正常退出通过。此前 QA 草稿清理 409、异步重新加载误截图、原生 `<dialog open>` 的 `role` 属性选择超时均保留为已修验收脚本失败史。新闻 / 真实 DeepSeek AI 的独立样本保存 5 条，6 组词方案返回 18 建议；修后真实 X 两组均确认搜索就绪但 6 / 13 条正文全部不符，关键词正向命中仍未验过。正式目录当前缺 Instagram 会话，本轮深度 Instagram 尚未 live 验收。

历史默认浏览器 / 社媒版本的后端 501、前端 51、9 组 DOM 回归见 `default-browser-regression-results.json`。历史隔离 QA 标签实采和报告闭环见 `social-live-acceptance.json` 与 `x-profile-live-acceptance.json`；正式目录 X 自动会话复用曾另行修复并验证。它们作为已跑通的核心链保留，不替代本轮深度研究、任务串或新 EXE 验收。

## 验收环境

| 项目 | 实际记录 |
| --- | --- |
| 日期 / 时区 | 2026-10-06；采集日期筛选 Asia/Tokyo，操作记录 Asia/Shanghai / UTC 明确标记 |
| 源码提交或构建版本 | V2 工作区未提交；分发包以各自 build-manifest.json 文件哈希为准 |
| 操作系统 / 架构 | Windows x64；macOS 原生未实测 |
| 浏览器 / Electron 版本 | 桌面 Electron 44.5.1；采集 Playwright 1.59.0 / Chromium revision 1217 |
| 测试数据目录 / 数据库 | `C:\Users\wwx\Documents\New project 7\output\qa-workbench-final.sqlite3`，独立验收库 |
| 正式应用会话目录 | `C:\Users\wwx\AppData\Roaming\PEACE ATLAS`；已单独验证 X auto 复用及 IG 缺失会话结果，不代表全部 QA 数据已迁入 |
| 证据目录 / 执行人 | `C:\Users\wwx\Documents\New project 7\output`，Codex 执行；用户完成平台原生登录 |

基础验收使用独立测试库和明确标记的测试材料。用户授权的正式会话复用另用实际应用数据目录，并单独记录是否保存，不与 QA 数据混用。需要外网、真实 AI 或本地平台权限的步骤单独记录。

## 已有登录与会话基线（历史成品实测）

| 场景 | 实际结果与边界 | 证据 |
| --- | --- | --- |
| 正式目录 X 自动复用 | 首次 `source:firefox / verified:true / saved:true`；再次 `source:workbench / verified:true / saved:false`。无新可见登录窗口；范围为真实平台首页会话验证，不替代最新 EXE 的完整操作闭环。 | `actual-desktop-auto-session-acceptance.json` |
| 正式目录 Instagram 自动检查 | 默认 Firefox 无可复用 IG 会话，返回 `verified:false / saved:false / requires_login:true / code:missing_session`；检查未开登录窗口，也未写入 IG 会话。证明缺失状态处理准确，不能写成 Firefox 导入成功。 | `actual-desktop-instagram-auto-session-acceptance.json` |
| 系统默认浏览器官方页 | OS 打开端点实际打开了当前默认 Firefox。结果仅为打开请求成功，`login_verified:false / session_imported:false`；Computer Use 内容读取被 unsupported-URL policy 阻断，未实读该浏览器页面。 | OS 打开调用及工具诊断；页面内容未验 |
| 早先 Firefox 界面导入 | 独立 QA 目录的 X 首页认证、保存与备份通过；该结果当时未覆盖正式应用目录。正式目录缺口由上方 X auto 验证修复。 | `firefox-import-ui-acceptance.json` |
| Chrome / Edge 可选桥接 | 扩展源码 75 项离线测试、8 组合成后端格式兼容检查通过，审查 ZIP 已准备；未安装、未在真实 Chrome / Edge 同步会话。当前 Firefox 路径不依赖扩展。 | `browser-extension/tests/`；运行包与说明见 `browser-extension/README.md` |
| 历史社媒 EXE 正式目录 | 两次 X auto 均验证成功并复用 workbench 会话，未重新登录；IG 准确报告缺失。默认浏览器及自动检测按钮在实际页面渲染通过，原任务保留，退出时后端 code=0。初次页面探针未等待异步渲染，修复有界等待后重建再验通过。 | `desktop-default-browser-session-acceptance.json`、`desktop-default-browser-session-smoke.json`、同名 PNG |

正常界面先检测已有会话，确认缺少可复用会话后才请求系统默认浏览器打开官方页；打开浏览器本身不表示已登录。任务状态为成功只表示检查执行完成，仍需查看 `verified / saved / source / requires_login`。Instagram 采集成功、Firefox 自动导入成功、OS 打开成功和扩展同步成功必须分别记录。

## 一、基础回归

- [ ] 后端存储、采集分析、API 测试通过，保存完整输出及失败项。
- [ ] 前端 Node 测试通过，覆盖日本季度及跨午夜日期、错误状态、危险 URL、Markdown 来源链接、可选字段不输出 false/null/undefined。
- [ ] `GET /api/v2/health` 返回服务及存储状态；仅据此判断服务能访问，不判断外部来源可用。
- [ ] 数据服务不可用或 API 错误时，界面显示失败原因，不用“暂无记录”代替错误。

命令（仓库根目录，使用已安装依赖的解释器）：

```powershell
python -m pytest -q analysis-worker/tests/test_workbench_store.py analysis-worker/tests/test_workbench_pipeline.py analysis-worker/tests/test_workbench_api.py
node --test analysis-worker/app/static/workbench/tests/*.test.mjs
```

## 二、资料与来源闭环

- [ ] 空库初次启动显示真实零值及操作入口，无示例资料或虚构图表。
- [ ] 导入明确标记的新闻 / 社媒测试材料，核对新增、重复、拒绝及逐条错误；重复导入不重复计数。
- [ ] 错误 JSON、无效 URL、缺少必要内容、未知发布时间分别得到明确处理结果。
- [ ] 关键词、类型、主题、来源、日期筛选符合原始材料，分页无漏项或重复。
- [ ] 日本日期范围包含首尾两天；季度起止不偏移一天，未知日期有明确标识。
- [ ] 资料抽屉可看正文、来源和时间；原始来源可点击，危险协议不成为可执行链接。
- [ ] 总览数量、主题、来源、时间分布可回查记录；地点提及不标为已验证事件。

## 三、账号核验

- [ ] 新增、编辑账号后名称、平台、组织、URL、标签可保存，刷新后保留。
- [ ] 新账号及导入材料保持候选边界，不由客户端字段自动变成已核验。
- [ ] 核验需要依据链接，成功后状态与依据可见。
- [ ] 核验窗口点击取消、关闭或按 Escape，账号状态不变。
- [ ] 排除保留账号记录；筛选区分候选、已核验、已排除，空结果无旧内容残留。

## 四、真实采集与持久任务

- [ ] 用一个可公开访问的 RSS / Atom 源实测，保留来源 URL、日期、诊断及获取 / 新增 / 重复数量。
- [ ] 用明确关键词实测 Google News，核对返回日期及原文链接，记录正文或历史覆盖限制。
- [ ] 无效来源、超时、部分来源失败、合法但无结果，得到与事实相符的状态。
- [ ] 创建任务后刷新或切换页面，任务仍可找到；离开任务页后停止轮询。
- [ ] 分别取消排队和执行中的任务，核对最终状态及已保存材料；不发生后台继续执行却显示取消完成。
- [ ] 失败 / 取消 / 中断 / 部分完成任务重试生成新 ID，保留旧记录与重试关系。
- [ ] 退出整个应用再启动，未完成任务正确标记为中断或等待执行，不永久卡在执行中。

## 五、报告与 AI 边界

- [ ] 由收录材料生成证据整理报告，标题、范围、类型、数量与输入一致，并明确未调用 AI。
- [ ] 空范围生成报告明确报错，不保存伪成功报告。
- [ ] 未核验社媒不进入主要结论，排除账号材料不用于主要分析。
- [ ] 报告可打开 `[label](<url>)` 与普通 Markdown 来源链接；HTML / 脚本按文本显示。
- [ ] 下载 Markdown 与保存内容一致；账号更改或新增资料不改写既有报告快照。
- [ ] AI 未配置时清楚说明，仍可使用证据整理。
- [ ] 明确启用真实模型后完成一次 AI 报告，记录模型、范围和结果，不把 mock 测试当成实测。
- [ ] 模型连接失败或返回不符合证据要求的内容时明确失败，不静默改成规则报告并声称 AI 已完成。

## 六、前端与桌面交互

- [ ] 在 1440、1024、800、390、320 CSS 像素宽度检查六页；除导航预期横向滚动外，无主内容裁切或整页横向滚动。
- [ ] 用长中文标题、来源 URL、组织名及 UUID 检查列表、详情、报告和任务状态布局。
- [ ] 小屏及小窗口高度下，对话框可滚动，提交 / 取消 / 关闭可达，焦点返回触发按钮。
- [ ] Tab 可操作导航、表单、列表和对话框，焦点清晰，Escape 可关闭，错误提示可见。
- [ ] 加载、空结果、失败、部分完成分别表达；系统减少动态效果偏好生效。
- [ ] 桌面原始来源在系统浏览器打开，下载可保存，内部导航留在应用中。
- [ ] 桌面 AI 连接保存后重启仍有效；密钥不回显、空值保留、明确勾选后清除，保存不调用模型。
- [ ] 数据和日志目录按钮有效，密钥不出现在页面、普通 API 响应或共享验收记录中。

## 七、当前研究包与历史平台交付

本轮研究候选只交付 Windows x64 与 macOS Apple Silicon。构建完成后，须核对冻结源码、实际 ZIP 字节、manifest、无用户会话与数据库，再分别验证桌面启动；下表不借用历史社媒包哈希。

| 当前研究平台 | 包路径 | SHA-256 / 构建及实际启动状态 |
| --- | --- | --- |
| Windows x64 | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-research-candidate.zip>)；[已验 EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-research-candidate-20261005-214949/PEACE ATLAS.exe>) | 413,012,485 字节；SHA-256 `5a5f19469a91d7957a34ae64c6a0a9f7fb9f350ac97daa8a0120d86bf7f76241`；59 应用文件当前源码一致、CRC / 隐私检查通过，实际 smoke-4 通过 |
| macOS Apple Silicon | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-research-candidate.zip>) | 363,882,003 字节；SHA-256 `96ab9b66dc9a32f56291631af503b3313a0301d43abb59133e71bcfa9b1c1449`；60 应用文件当前源码一致，CRC / 隐私 / 44 Mach-O / 29 符号链接 / ad-hoc 检查通过；macOS 13.0+，原生启动待用户实机验收 |

最终包记录见 [research-package-audit-final.json](<C:/Users/wwx/Documents/New project 7/output/research-package-audit-final.json>) 与 [research-mac-signing-audit-final.json](<C:/Users/wwx/Documents/New project 7/output/research-mac-signing-audit-final.json>)；ad-hoc 不等于 Developer ID 公证或 Apple 原生 codesign / 启动通过。Windows 成品证据为 [desktop-smoke.json](<C:/Users/wwx/Documents/New project 7/output/desktop-research-final-smoke-4/desktop-smoke.json>)、[实际草稿恢复弹窗](<C:/Users/wwx/Documents/New project 7/output/desktop-research-final-smoke-4/desktop-research-composer.png>)、[实际关系图页面](<C:/Users/wwx/Documents/New project 7/output/desktop-research-final-smoke-4/desktop-smoke.png>) 和同目录日志。后端正常 `exit code=0`，ready 文件清空。

### 历史默认浏览器 / 自动复用候选（非当前研究包）

以下两包属于此前默认浏览器及自动复用修复版本，当时应用文件哈希与当时冻结源码一致。审计位于 `.build-cache/social-release-qa/20261005-200812-desktop-session-probe/package-audit.json`，不包含本轮全部研究改动。

| 历史平台 | 当时包路径 | 当时字节数 / SHA-256 | 当时验收 |
| --- | --- | --- | --- |
| Windows x64 | `dist/PEACE-ATLAS-2.0.0-windows-x64-social-candidate.zip` | 412904929 / `83efc1ddfff5f65bc0d737571b0134a712736ad1d35dad99c5a265d95b0c8a35` | 46 个应用文件哈希 / ZIP / 私有运行时等价检查通过；正式目录新 EXE 两次 X auto 成功、IG 缺失处理、实际 UI 入口、记录保留与正常退出通过 |
| macOS Apple Silicon | `dist/PEACE-ATLAS-2.0.0-macos-arm64-social-candidate.zip` | 363797197 / `dd15f7851b0291375c2d3706cd798c9e328f5f648f230913960ee054436a652a` | 47 个应用文件、44 Mach-O、29 符号链接及 ad-hoc 检查通过；macOS 13.0+，用户实机启动及原生 codesign 待验 |

该历史 Windows 可执行入口为 `dist/PEACE-ATLAS-2.0.0-windows-x64-social-candidate-20261005-200826/PEACE ATLAS.exe`，不是研究候选入口。正式数据目录当时做了独立 SQLite 备份，旧记录保留。历史验收后正常再次打开应用的 shell 命令被自动审批拒绝（仅返回 `blocked by policy`，无具体理由），未执行或重试；该记录不改变当时的启动结果，也不能用作本轮研究包无法启动的判断。

**以下为此前社媒候选包留档，保留原哈希，不代表已包含最新自动复用 / 系统默认浏览器 / 扩展桥接修改。** 新版本 Windows x64 与 macOS Apple Silicon 的构建、哈希和验收另行记录；Intel Mac 不再属于本轮交付范围，也不再安排更新或验收。

| 平台 | 包路径 / SHA-256 | 构建及结构检查 | 原生签名状态 | 本机启动与数据闭环 | 证据路径 |
| --- | --- | --- | --- | --- | --- |
| Windows x64 | `dist/PEACE-ATLAS-2.0.0-windows-x64-social-candidate.zip` / `da2aa1f7dcee691fc0ee50b592d29ec443e7138bf75ae720e96b91ed52dc2615` | ZIP / 文件清单 / 42 个应用文件哈希一致，私有模块导入与浏览器启动通过 | 未签名便携包 | 新 EXE 新闻采集 1 / 复采重复 1 / 报告 1 / 重启 ID 保留 / 正常退出；同包运行时实采 X 2 / IG 3 | `output/desktop-social-final-smoke/acceptance.json`、`output/packaged-social-live-acceptance.json` |
| macOS Apple Silicon | `dist/PEACE-ATLAS-2.0.0-macos-arm64-social-candidate.zip` / `4606b8f4909dab516bcc93cbaf37c133a00fcd8373f3e7a5116bb2aefba39a3a` | ZIP / manifest / 44 Mach-O / 29 符号链接 / 代码页与 entitlements 检查通过 | WSL ad-hoc；原生 codesign 待验，无 Developer ID 公证 | 未实测；用户已选择稍后双击验收 | `dist/PEACE-ATLAS-2.0.0-macos-arm64-social-candidate.json` |
| macOS Intel（历史留档） | `dist/PEACE-ATLAS-2.0.0-macos-x64-social-candidate.zip` / `8bd847f90a91d743a86eaef966c0ce4140883f3d0139cbd25d79f81b88b807f3` | 当时 ZIP / manifest / 44 Mach-O / 29 符号链接 / 代码页与 entitlements 检查通过 | 当时 WSL ad-hoc；无原生签名实测或 Developer ID 公证 | 未实测；用户已明确不再需要，本轮不更新、不安排验收 | `dist/PEACE-ATLAS-2.0.0-macos-x64-social-candidate.json` |

上述 Windows 运行证据的 `output/` 位于本任务验收目录 `C:\Users\wwx\Documents\New project 7\output`。此前已验收的解压 EXE 位于 `dist/PEACE-ATLAS-2.0.0-windows-x64-social-candidate-20261005-190236/PEACE ATLAS.exe`；这是旧候选路径，不是最新版本入口。历史 Mac 包最低版本为 macOS 13.0；Apple Silicon 后续仍需原生签名收尾及用户实机验收，打包成功不替代该结果。

- [ ] 最终分发包完整解压 / 安装后启动，不借用开发环境 Python、Node.js、浏览器或 Docker。
- [ ] 核对运行时、版本、入口、校验和和构建清单；包内无项目 `.env`、历史登录态、用户数据库和测试材料。
- [ ] 在目标系统完成“启动 → 导入 / 采集 → 已保存资料 → 报告 → 退出 → 重启仍可读取”的闭环。
- [ ] Windows 单实例、正常退出和后端意外退出提示有效；升级包不覆盖用户数据目录。
- [ ] macOS 按[构建说明](../desktop/BUILDING.md)完成原生签名 / 收尾及真实启动，分别记录 ad-hoc、Developer ID、公证状态。
- [ ] macOS 不把 Windows 上构建的 staging ZIP 或结构检查通过写成原生启动验收完成。

## 八、旧数据迁移（独立事项）

同级 `collectors/collector.db` 的 SQLite 显式只读迁移已实现，入口 `scripts/migrate_collectors.py`，当前验收库完成 79 条帖子、806 个去重账号和 610 条去重关系导入；重复执行新增 0，原库未修改，目标库迁移前自动备份。证据为 `social-migration-workbench-corrected.json` 与 `social-migration-idempotence-final.json`。下面旧 PostgreSQL / 报告迁移属于另一数据源，尚未执行，不能由 SQLite 迁移通过推定。

- [ ] 先备份旧 PostgreSQL 和报告，记录范围与位置，确认可恢复。
- [ ] 只读导出公开材料，在独立 V2 库小批量导入，核对字段、URL、时区、去重、拒绝记录。
- [ ] 建立旧账号 ID → V2 UUID 对照，复核状态与核验证据，不自动推广旧状态。
- [ ] 历史 Markdown 作为档案保留，不伪造 V2 输入快照。
- [ ] 对账和抽样通过后制定切换方案，保留旧库，不覆盖原始表。

## 验收结论

| 分类 | 填写要求 |
| --- | --- |
| 已通过 | 列出清单条目，附证据路径 |
| 尚未实测 | 单独列出平台、来源、模型或迁移事项 |
| 已知限制 / 缺陷 | 触发条件、影响、是否阻止交付 |
| 当前交付范围 | 区分源码、可用桌面包、staging 包及后续补交 |

本轮 Windows 研究候选已完成源码回归、包一致性与实际 EXE 的空标题任务串、草稿刷新恢复、X 会话复用、关系图和正常退出验收。真实 X 指定账号证据树及新闻 / AI 链已有限定证据；当前六关键词组、每任务上限 50、总目标 1,000 的真实 UI 预览返回 24 个子任务，未创建或执行。此前每任务上限 200 的六任务 API 预览仍有效；均不等于采到 1,000 条。X 关键词正向命中、本轮深度 Instagram、真实多层 followers 全链、Chrome / Edge 扩展同步以及 macOS Apple Silicon 原生启动仍未验收。正式目录 IG 缺少会话，历史 IG 标签成功不代替当前验收。
