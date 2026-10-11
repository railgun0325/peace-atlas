# PEACE ATLAS 整体重构进度

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

**更新：2026-10-06。本轮当前交付是深度研究与批量任务串候选，包后缀为 `-research-candidate`。下方 `-social-candidate` 成品、哈希与登录验收均属历史记录，不代表最新研究成品。** 本轮只交付 Windows x64 与 macOS Apple Silicon，Intel Mac 不再更新；Apple Silicon 实机双击仍按用户安排稍后验收。研究源码及真实证据已验证，最新包的构建、哈希、桌面启动结果另见 [V2 验收](V2_ACCEPTANCE.md)，不由历史包推定。

本轮在已跑通新闻、社媒会话、资料存储和报告链上增加了当前主题研究、公开账号逐层拓展、证据关系与决策轨迹、断点续查、自动草稿，以及按关键词和日期拆分的持久任务串。[研究验收记录](RESEARCH_ACCEPTANCE_20261006.md)分别列出真实 X 指定账号、真实新闻与 AI、关键词加载负例、离线 Instagram、尚未实测的内容。

## 项目范围与判断

保留已跑通的公开新闻、浏览器登录与社媒采集、数据库、AI 报告核心能力；重构调度、数据映射、错误处理和界面。X/Instagram 复用 `collectors` 的直接浏览器采集实现，不回到旧 Nitter 路线，不新加付费数据服务。

| 工作项 | 已有事实 | 当前进度 / 下一项 |
| --- | --- | --- |
| 构成主义前端 | 工作区、响应式布局、已有会话检测、默认浏览器入口、发现树 / 关系图 / 证据详情和任务串 | 本轮最终 71 项 Node、22 组隔离 Chromium DOM 检查通过；关键交互包括草稿恢复、研究创建、任务串预览与完整分页 |
| 关键词与草稿 | 多组任一 / 全部语义、分隔提示、建议选择与撤销、原输入保留；本机与服务端版本同步 | 6 组真实输入得到 18 个建议，已有 DeepSeek V4 Pro `used:true`；关闭、Escape、刷新与同步冲突在离线 DOM / API 回归覆盖 |
| 新闻 / RSS | 真实 Google News 采集、去重、存储和证据报告跑通 | 已验收核心链路，继续纳入整体回归 |
| 统一后端 | 持久任务、任务串、取消 / 中断 / 重试、SQLite、报告快照、社媒原子入库和研究图断点 | 本轮最终完整回归 654 项通过，54.39 秒，包含混合 Fake-IP / 公网 IPv6 修复、X 检索就绪与旧 DOM 回归升级；一项既有 Pydantic alias 警告为非失败 |
| 深度研究 | 所选新闻 / X / Instagram 分组入口、公开 profile、following / followers / mentions、主题证据评分与受限 AI 决策 | 真实 X 研究 3 账号 / 2 关注边 / 3 材料，其中一个账号因无主题证据停止分支；`partial/account_budget`。无 AI 凭空账号或边、循环、取消及续查有回归 |
| 批量任务串 | 总体材料目标 1 至 10,000，单计划最多 50 子任务；每子任务独立日期和预算 | 深度子任务最多 200、简单最多 100，尊重更低输入；串行后台执行，全局队列与历史分页且无固定总条数上限；目标不承诺实际产量 |
| AI 报告 | 使用已有 DeepSeek 模型真实生成报告并下载一致 | 新闻真实样本已验收；社媒候选账号、图片描述及待核对旧文本不进入主要结论，来源资格回归通过 |
| X / Twitter | 指定账号、真实公开关注关系与正式目录会话复用已有证据 | 本轮深度指定账号闭环通过；关键词“反戦”读到 6 条不符正文，6 条拒绝、4 次重复、0 入库，`completed/no_matches`，不能称关键词正向命中 |
| Instagram | 历史隔离 QA 标签“反戦”实采 3 条、去重与报告通过，12 项实流检查 | 正式目录当前缺少可复用会话；本轮深度适配仅离线验证，未有新的 live IG，不把历史标签实采当作最新深度研究验收 |
| 默认浏览器 / 扩展 | OS 官方页打开接口与 auto 流程已实现；Chrome / Edge MV3 可选桥接源码及审查 ZIP 已准备 | OS 接口真实打开当前默认 Firefox，但工具未能读取浏览器内容。扩展 75 项离线测试、8 组后端格式兼容检查通过；未安装、未完成 Chrome / Edge 实机验收，当前 Firefox 路径不依赖它 |
| 旧数据 | 原库 79 条帖子、807 个账号、611 条关系，原数据库未改动 | 迁入测试工作台 79 / 806 / 610 条，规范化合并大小写重复；重复迁移新增 0，缺失日期与作者保持未知 |
| Windows 桌面 | 独立 Python + Electron + Chromium；最新研究 EXE 启动及成品闭环通过 | 最终 smoke-4 退出 0：空标题默认创建、两新闻任务预算部分完成、草稿刷新恢复、X 自动复用两次、3 账号 / 2 边 / 3 材料关系图、默认浏览器 / 安全存储和后端正常退出通过 |
| macOS Apple Silicon | 最新研究 ZIP 源码 / manifest / payload 60 文件一致，CRC、44 Mach-O、29 符号链接及 ad-hoc 检查通过 | 363,882,003 字节，macOS 13.0+；原生 codesign 与双击启动按用户安排稍后验收。Intel Mac 旧包留档，本轮不更新、不验收 |

## 当前研究实证（2026-10-06）

证据目录为 `C:\Users\wwx\Documents\New project 7\output`，原始 JSON 保留任务 ID、参数、诊断、图数据和真实 AI 状态。

| 场景 | 实际结果 | 证据文件 |
| --- | --- | --- |
| 指定 X 账号及续查 | `seibu_yunion` 起点，3 节点、2 条 follows、3 条素材；一个无主题账号停止拓展；达到 3 账号预算而部分完成，不宣称全网探索 | `research-x-profile-live.json` |
| X 关键词加载与排除 | “反戦”首屏可解析，6 条扫描、6 条不符原文、4 次重复、0 保存；真实空匹配完成，不是正向命中成功 | `research-x-keyword-live.json` |
| X 双组较宽日期复核 | 就绪修复前，2026-01-01 至 10-06，“憲法九条 / 反戦”分别扫描 6 / 12 条且全部不符，0 保存；现已增加目标路由、搜索框、实时标签和主结果加载核对，离线真实 DOM 定点通过，同款真实检索结果见下一行 | `research-x-keyword-broader-live.json` |
| X 就绪修后真实复测 | 同款两组均 `search_context_verified:true`，分别扫描 6 / 13 条，仍全部不符，0 保存；不把就绪通过称为关键词正向验收 | `research-x-search-readiness-live.json` |
| 真实新闻 / AI | 两个关键词组保存 5 条；DeepSeek V4 Pro 真实参与，`used:true`；记录预算停止，提出的 2 个补充词未执行，没有超限继续抓取 | `research-news-ai-live.json` |
| 真实 AI 词方案 | 6 组原输入、18 个可选择建议；输入未被默改，`ai.used:true` | `research-keyword-plan-live.json` |
| 1,000 条任务串 API 预览 | 六组、2026-09-07 至 10-06、每任务上限 200，实际规划 6 子任务：167 / 167 / 167 / 167 / 166 / 166；仅计划，未执行 1,000 条采集 | `research-1000-batch-plan-live.json` |

账号图中跟随、提及、共同链接与共同活动传播是可观测关系，组织自述和 AI 候选单列核对。关注、同题或共同传播不能升级为隶属；AI 正向语义相关性校验 ID 与原文片段，不等于自动核验账号身份。多层 followers、深度 Instagram、AI 语义账号分支及大规模任务串的真实平台产量尚不能由上述小样本推定。

首次研究桌面成品任务串探针的第一个新闻子任务成功、第二个失败，原始错误与日志保存在 `output/desktop-research-final-smoke`。原因复现为公网 IPv6 与 Fake-IP IPv4 的混合 DNS 集合未进入既有 DoH 兼容路径。限定修复后以包内私有 Python 加载新模块，连续两个真实 RSS 请求均 HTTP 200，响应分别为 78,973 / 119,066 字节；`rss20` 表示 RSS 2.0 格式，未统计条目数，先前“20 条”的写法已纠正。新闻安全原因显示定点 4 项通过，最终后端全套 654 项通过。详细修复边界见[研究验收记录](RESEARCH_ACCEPTANCE_20261006.md)。

此前成品验收脚本的 QA 草稿清理 409、未等异步重新加载即截图，以及原生 `<dialog open>` 被按显式 `role` 属性查找导致超时的问题均已修，原失败记录保留。最终 smoke-4 已退出 0，并实际查看截图确认绿色“已恢复上次草稿”和 X 研究 `9cef0128` 的 3 节点 / 2 边 / 3 材料关系图。空标题实际 POST 生成默认“批量公开资料研究”，任务串 `71e293d2-7020-4dba-89ec-b5244da20483` 两个新闻任务各 `fetched:1 / inserted:0 / duplicates:1`，均 `partial / record_budget`，总体新增 0、重复 2。两次 X 自动复用 `verified:true / saved:false / source:workbench / requires_login:false`，默认浏览器入口、安全存储、刷新草稿恢复及后端 `exit code=0` 通过，ready 文件清空。证据见 [desktop-smoke.json](<C:/Users/wwx/Documents/New project 7/output/desktop-research-final-smoke-4/desktop-smoke.json>) 及[本轮研究验收](RESEARCH_ACCEPTANCE_20261006.md)。

真实界面操作“批量研究 → 留空可选标题 → 预览”还暴露了实际 API 合同错误：可选标题的 `min_length=1` 让空字符串返回 422。已移除该限制并沿用规划器的去空格与默认标题规则，省略、空字符串、纯空白均使用“批量公开资料研究”，自定义标题去除首尾空格。4 项参数化 API 预览 / 创建回归通过，最新全量 654 项和最终成品空标题真实创建均已通过。当前六组关键词、每子任务上限 50、总目标 1,000 的真实界面预览返回 24 个子任务，未创建或执行；此前每子任务上限 200 的六任务 API 预览仍有效。

当前 Windows [EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-research-candidate-20261005-214949/PEACE ATLAS.exe>) 已验收，ZIP 413,012,485 字节 / SHA-256 `5a5f19469a91d7957a34ae64c6a0a9f7fb9f350ac97daa8a0120d86bf7f76241`。Mac ARM ZIP 363,882,003 字节 / SHA-256 `96ab9b66dc9a32f56291631af503b3313a0301d43abb59133e71bcfa9b1c1449`。当前源码 / manifest / payload 的 Windows 59、Mac ARM 60 个应用文件逐字节一致，ZIP CRC 及隐私排除检查通过，Mac 结构和 ad-hoc 检查通过，原生启动仍待实机。[最终包审计](<C:/Users/wwx/Documents/New project 7/output/research-package-audit-final.json>)保留具体结果。

X 搜索就绪专项修复、旧 X DOM 页面补齐主列与检索状态后的 X DOM / 社媒 / 研究定点为 141 项通过，16.30 秒，后续完整后端为 654 项通过。包含真实隔离 Chromium 旧时间线延迟切换、搜索框与实时标签核对、侧栏排除及永久未就绪失败。修后真实页面两组均就绪，但 6 / 13 条正文仍全部不符，关键词正向命中尚未验过。原关键词及日期筛选保持不变；成品结果仍按最后验收证据另记。

## 历史社媒接入与默认浏览器验收（先于研究候选）

- 历史 Windows 成品 `PEACE-ATLAS-2.0.0-windows-x64-social-candidate-20261005-200826/PEACE ATLAS.exe` 已使用正式数据目录验收：X 两次 `auto` 均 `verified:true / source:workbench / requires_login:false`；IG 缺失会话处理准确，不误标登录成功；实际页面的系统默认浏览器及自动检测入口渲染通过，原任务记录保留，后端正常退出。证据 `desktop-default-browser-session-acceptance.json`、`desktop-default-browser-session-smoke.json` 和同名 PNG。首次验收探针因未等异步设置页渲染而失败，已增加有界等待并在重建成品复验通过；不隐藏这次失败。
- 当时两包已重建并逐应用文件核对：Windows 46 个文件、Mac ARM 47 个文件的冻结源码 / manifest / ZIP 实际字节哈希一致；包内无用户会话及数据库。Windows ZIP SHA-256 `83efc1ddfff5f65bc0d737571b0134a712736ad1d35dad99c5a265d95b0c8a35`；Mac ARM ZIP `dd15f7851b0291375c2d3706cd798c9e328f5f648f230913960ee054436a652a`。ARM ad-hoc 与结构检查通过，原生启动仍待用户验收，Intel 不再构建。总审计 `.build-cache/social-release-qa/20261005-200812-desktop-session-probe/package-audit.json`。
- 历史默认浏览器版本源代码回归：后端 `501 passed in 30.11s`，前端 `51 passed`、9 组离线 Chromium DOM 场景通过；扩展 75 项 Node 与 8 组合成跨后端格式检查通过。证据汇总 `default-browser-regression-results.json`。该结果与真实会话、分发包和 Mac 实机验收分别记录。
- 当前正式目录 `C:\Users\wwx\AppData\Roaming\PEACE ATLAS` 的 X 自动复用已独立完成真实首页验证。首次 `source:firefox / verified:true / saved:true`，再次 `source:workbench / verified:true / saved:false`，两次均无需新可见登录窗口。证据 `actual-desktop-auto-session-acceptance.json` 的范围是平台首页会话验证；它修复并证实正式目录先前没有 QA 会话的问题，不替代最新分发 EXE 的完整验收。
- 正式目录的 Instagram 自动检查返回 `verified:false / saved:false / requires_login:true / code:missing_session`：默认 Firefox 没有可复用 IG 会话，未打开新登录窗口，也未写入 IG 会话。证据 `actual-desktop-instagram-auto-session-acceptance.json`。源码已支持 IG 的窄域自动复用，当前结果证明缺失状态处理准确，不证明 IG Firefox 导入成功；此前 QA 的 Instagram 采集结果仍单独有效。
- 普通 UI 登录入口先执行 `method:auto`，API 省略 `method` 也默认 auto。只有缺少可复用会话时，界面才请求 OS 打开固定平台官方页；打开结果不标记已登录。OS 打开端点已真实打开当前默认 Firefox；Computer Use 浏览器内容读取受到 unsupported-URL policy 阻断，未据此宣称读到了页面内容。旧可见自动化窗口只保留为显式高级兼容接口，不是正常 UI 登录路径。
- Chrome / Edge 可选 MV3 桥接仅通过获授权的浏览器 Cookie API 读取 X / IG 必要字段，单次配对后发送到本机 `127.0.0.1`，平台验证成功才保存。扩展源码 75 项离线测试和 8 组后端合成格式检查通过；审查 ZIP 已准备，未安装或完成真实浏览器验收。当前默认 Firefox 可复用，不需要安装扩展才能使用这一链路。
- 前一批源码复验：`python -X utf8 -B -m pytest analysis-worker/tests -q` 为 324 passed；前端 Node 43 passed、6 组离线浏览器场景通过。这些计数对应自动复用 / 默认浏览器 / 桥接变更前，最新完整回归与分发包结果另记。下文保留各轮故障和修复记录，旧阶段的“待登录”等状态由上方当前记录覆盖。
- 此前 Windows 候选成品 `PEACE-ATLAS-2.0.0-windows-x64-social-candidate-20261005-190236/PEACE ATLAS.exe`：真实新闻新增 1、复采重复 1 / 新增 0、报告 1；系统凭据加密可用，退出重开后资料与报告 ID 保留，两次后端正常退出且无残留进程。证据 `desktop-social-final-smoke/acceptance.json`。另用同包私有 Python 与私有 Chromium 调用包内采集器，读取 X 2 条、Instagram 3 条；证据 `packaged-social-live-acceptance.json`。这两类测试分别证明当时的桌面流程及包内采集运行时，不冒称 Mac 或后续版本实测。
- 公开关注关系真实任务 `c29924a0-4af2-4ffa-a47a-4f7a8dea5f18` 已成功：本次上限 3，读取并保存 3 条关注边、新增 2 个候选账号；帖子重复 1 / 新增 0。只记录实际列表来源及观察时间，不推定关系含义。证据 `x-following-live-acceptance.json`。
- 用户提供普通 Chrome 可进入验证码、手机与 Firefox 已登录的对照。工作台新登录窗口曾明确返回临时登录限制，不能据此认定账号整体被锁或密码错误。采用用户明确授权的 Firefox 已有 X 会话复用，仅读取所选注册配置中 X 的必要会话字段，不读取密码或其他站点。Firefox 新版 cookie 到期时间为毫秒，按 SQLite schema 处理。独立上下文访问 X 首页验证成功后才备份并替换工作台会话，失败保留原状态。
- Firefox 设置界面真实导入任务 `efca428a-664b-4801-88fd-95e7021d4206` 曾在独立 QA 数据目录完成，结果 `x_home_authenticated / saved / backup_created` 均为真。证据 `firefox-import-ui-acceptance.json`、`firefox-session-import-success.png`。这次结果没有替正式应用目录保存会话；该差异由本节开头的正式目录 auto 验证另行修复和确认。
- 真实 X DOM 暴露普通永久链接 `<a role="link">` 被误作引用容器的问题，导致主帖 ID 为空。修复后原帖作者、时间、正文与引用卡分离；无正文文章卡明确跳过，纯不可解析页面仍失败。关键词“反戦”本次有限浏览得到 25 条不符查询的内容，全部拒绝，未放宽筛选以凑数量；证据 `x-live-parse-fixed-collection.json`。
- 指定账号改为直接读取主页，关键词 / 标签仍使用最新搜索。主页路径不隐式回退搜索；只保存指定作者本人原帖，保留日期、相关性及转帖过滤。真实任务 `44b72864-07dd-49be-8a1c-98a1c0b6dd6a` 浏览 10 条，8 条不符当前研究规则，2 条新增；复采 `9a37de75-6f60-4c40-81b9-6107bbc79357` 两条均判重复、新增 0。2 条 X 与 3 条 Instagram 样本生成同一证据报告，候选账号材料均不进入主要结论，下载与保存内容字节一致。证据 `x-profile-live-acceptance.json`、`x-instagram-evidence-report-verified.md`；这只证明有限样本闭环，不代表平台完整历史或关键词检索召回率。
- 早期接入阶段新增平台独立会话、手动登录任务、有限采集适配器，以及社媒参数、会话卡、任务诊断、正文来源说明、关系查看，当时前端 41 项测试与 5 组隔离 Chromium 场景通过。正常登录流程现已升级为上方 auto / 系统默认浏览器路径；这些历史测试不代替最新界面验证。
- 用户补充 X 旧浏览操作不稳定、易混入无关帖。已将最新搜索、主帖 / 引用分离、逐帧增量读取、作者和日期校验、连续无新增停止纳入本轮。领域相关性筛选在消耗账号预算之前执行，避免无关作者抢占全部名额；保留筛除原因和可选“按检索条件”模式。
- 修复 `metadata` 被存储忽略的问题，保留正文类型、互动数、采集方法及筛选依据。图片描述及待核对历史文本即使关联已核验账号，也不进入报告主要结论。
- 旧库 79 条帖子已迁入当前测试工作台；807 个原账号按平台和用户名大小写规范为 806 个，611 条原关系规范为 610 条。首次无拒绝；重复迁移新增 0，79 条全判重复。旧库未写入，目标库迁移前已备份。79 条发布时间仍未知，36 条原先缺作者关联的帖子仍明确未关联，未猜测补值。
- X 首次真实检索返回 `net::ERR_HTTP_RESPONSE_CODE_FAILURE`，公开检索页进入新版登录流程。已识别新版登录地址，并通过旧会话的公开用户编号核对 @ACAjp2025。仅确认主页公开联系邮箱，不能据此确认登录邮箱；原会话保留。
- Instagram 修复新版无 article/header 详情结构，以及首屏延迟、详情延迟和编辑时间混淆。任务 `b9fe8488-b462-4d4b-936e-cf4bd579a15c` 从 9 个链接中完成 3 条新记录，3 条因正文不符检索条件排除，无未知时间或作者映射错误。公开页面 time datetime 与入库值交叉核对一致。后续审核发现推荐网格图片 alt 污染元数据，已改为仅接受能关联当前帖子 ID 的图片；无法确认归属时留空。首次验收库 3 条记录已清除该字段的污染，原抓取保留在修订表和完整备份中，正文、作者和时间未变。
- 两平台首屏最多等待 12 秒并识别登录 / 限流 / 挑战，每次滚动最多等 3 秒新 ID；永久骨架页到达上限后明确失败，取消关闭上下文。
- 登录类任务使用单独串行队列，不占用新闻、Instagram 和报告的普通队列；该隔离最初针对手动登录实现，当前也适用于 auto 和 Firefox 导入。退出时同时取消两个队列，等待数据库提交确定结束；任务领取与退出相撞时不遗留无人执行的 running 任务。当时 7 项新增回归覆盖调度和收尾。
- Instagram 真实复采任务 `1c79a713-fef6-4461-abe7-1b455384c9b3` 读取 9 个链接，6 条正文不符检索、1 条缺少研究关联，留下 2 条且均判重复、新增 0。报告 `a6b767ce-509d-4c57-a25c-f8bc717049cc` 将 3 条新社媒材料保留为待核验附录，没有形成已核验主要结论；下载内容与数据库 UTF-8 字节完全一致。证据 `social-live-acceptance.json` 与 `instagram-evidence-report-verified.md`。
- 历史故障：X 自动化登录窗口曾停在 `knowledge_check`，随后因窗口关闭结束；截图不能证明密码错误或特定风控原因。当时先修复了旧会话复用和语言问题，并新增 4 项回归，但不能据此声称登录成功。当前正常流程已改为复用现有会话 / OS 默认浏览器，X 成功证据以上方正式目录 auto 结果为准。
- 历史 `login-isolation-live.json` 记录 X 手工登录任务 running 期间普通证据报告仍成功，证明当时的队列隔离；不用于证明登录成功或最新 UI 默认流程。
- Windows 独立 Chromium 可见 / 无头离线启动通过；历史 Mac 两架构浏览器和嵌入式 Node 已准备并签名预检。此前三个 `social-candidate` 包仅作为历史记录，后续登录修改只重建 Windows x64 与 macOS Apple Silicon；Apple Silicon 实机双击仍待用户验收，Intel 不再更新。

## 社媒重构必须覆盖的链路

1. 独立平台登录与真实页面状态：区分未登录、会话过期、验证挑战、限流、页面不兼容和合法空结果。
2. 关键词 / 标签发现、指定账号主页采集，复用已有解析能力；设置明确的数量、滚动和时间上限。
3. 保留原帖链接、作者、正文、发布时间、抓取时间和来源方法；Instagram 图片 alt 不冒充帖子正文。
4. 账号发现与公开关注关系保留来源和观察时间；自动发现及 AI 标签不能自动变成人工核验。
5. 接入 V2 持久任务和资料库；取消任务必须关闭该任务的采集上下文，不能只停止界面进度。
6. 采集先保留原始证据，AI 是显式的后续分析；失败不静默替换为规则结果。
7. 旧资料按来源 ID / URL 可重复迁移，保持未知日期与候选状态，原库和登录资料不覆盖、不打包。
8. 两个社媒平台分别做小范围真实采集验收，然后重建 Windows x64 / macOS Apple Silicon 包。登录或平台挑战需要用户在系统默认浏览器的平台原生页面完成。

## 完成定义

Windows 当前研究候选的源码回归、冻结源码一致性、实际 EXE 启动与限定研究 / 草稿 / 会话闭环已通过，可以进入使用与后续实采验证。X 关键词正向命中、本轮深度 Instagram、多层 followers 全链、Chrome / Edge 同步及 Mac 原生启动仍未验收；历史或离线成功不替代这些结果，Mac 实机仍按用户安排稍后完成。
