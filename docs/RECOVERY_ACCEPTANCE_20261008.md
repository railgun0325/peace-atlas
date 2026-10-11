# PEACE ATLAS 桌面恢复验收 · 2026-10-08

本轮交付为 **V2 `-recovery-candidate`**。Windows x64 成品已在独立资料目录完成设置页恢复入口、包内私有 Python 预检、后台停止与重启、恢复后的页面读取和 SQLite 实物对照。Apple Silicon 包完成构建、源码与包内字节核对及 WSL ad-hoc 签名结构检查；原生 Mac 启动与 Apple codesign 验证仍待对应设备实测。

本轮保留既有 EVA 风格标题与操作标签、研究工作台和预算设置。原生 Windows 记录确认 Atlas Mincho、Atlas Signal 离线字体加载成功，主要标题字重 900，概览和研究工作台无横向溢出，创建窗口无溢出；这是对本轮包的实际检查，见[原生运行记录](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/desktop-smoke.json>)和[EVA 概览截图](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/desktop-eva-overview.png>)。

实际源码位于 `C:/Users/wwx/Documents/New project 4/japan-antiwar-intel`，全部本轮验收材料位于 `C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008`。使用现有未提交工作区，生产用户资料不参与验收。本轮原生恢复和包内 HTTP 流程均使用合成样本，未访问外部采集来源、实际社交账号或 AI 服务。

[此前产品验收](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/PRODUCT_ACCEPTANCE_20261008.md>)记录的是 `-product-candidate` 和当时的功能状态，其中的实采、字体、预算及限制继续作为独立历史保留。本文件只记录本轮恢复候选包；此前“尚未提供界面内恢复按钮”的状态由本轮实现更新，历史包的通过结果不计入本轮成品通过数。工作依据为[恢复计划](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/RECOVERY_PLAN_20261008.md>)。

## 用户操作与兼容范围

在“能力与设置”先通过“备份研究数据”保存独立的 `peace-atlas-backup-时间.sqlite3`。在线备份包含下载时已经提交的 SQLite 数据、资料与来源、账号、关系、研究断点、人工复核、报告及输入快照、任务历史、任务串和服务端草稿；尚未同步到服务端的草稿输入、数据库之外的登录会话、浏览器配置、AI 密钥和日志不在研究备份中。

使用本轮桌面包恢复时：

1. 打开“能力与设置”，点击“恢复研究备份”，选择此前下载的独立 SQLite 备份。
2. 应用先运行独立预检，只读检查原始备份，生成候选库并核对当前结构、JSON 字段、日期、完整性及外键。预检失败或取消时，当前工作台继续运行。
3. 检查通过后，原生确认窗口显示文件名、记录数量和需要中断的任务数量。用户明确选择“停止任务并恢复”后才停止后台、替换数据库。
4. 应用等待后台确实退出，将原 main 数据库和存在的 WAL/SHM 一并保存在数据目录的 `restores/before-时间-随机标识/`，然后启动已检查的候选库并重新连接页面。
5. 核对恢复后的资料、报告、研究与草稿。备份中的排队和运行任务标为“已中断”，需要在任务中心手动重试；完成和部分完成的历史保留，恢复过程不会自动重放备份任务。

恢复只置换研究数据库，源备份原样保留，当前电脑的系统加密 AI 配置及社交会话目录保留。文件置换或候选启动失败时尝试回退原库；正常桌面使用的主进程错误弹窗说明回退成功或失败，并保留原库、失败候选的证据路径，即使重新连接原库时页面已经重新载入，用户仍能看到处理结果。未完成事务会留下阶段日志。下次启动若发现旧文件集合不完整、已登记服务仍存活，或启动中断后无法确认子进程状态，会停止恢复并保留日志、原库存档及失败候选，避免把缺失的旧库视为首次运行而创建空库。

兼容范围是**本产品当前 V2 完整 SQLite 结构**，不承诺任意旧版、其他软件数据库或 PostgreSQL 备份的自动迁移。不接受当前正在使用的数据库、其硬链接或 sidecar，也拒绝仍带非空未合并 WAL/journal 的数据库副本。旧库存档若仍带非空 WAL，应在应用完整退出后按人工方式恢复整个原文件集合；不要把旧 WAL/SHM 留给另一个新库。浏览器版本保留人工恢复说明，跨电脑登录会话与系统加密配置需要单独建立。完整操作及人工回退步骤见[研究数据备份与恢复](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/DATA_BACKUP.md>)。

## 最终验证结果

以下自动测试、浏览器验收组、包内后台流程和原生桌面结果分别记录，保持各自的验证单位和覆盖范围。[机器汇总](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/recovery-acceptance.json>)同时保留 `ok=true` 与 `overall_product_goal_complete=false`：前者表示本轮有证据的恢复交付通过，后者保留整个产品的未验证边界。

| 验证 | 最终结果 | 证据与范围 |
| --- | --- | --- |
| 全部后端回归 | **816 通过，0 失败、错误或跳过；1 项既有 Pydantic alias 元数据警告** | [JUnit XML](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/backend-final.xml>)、[完整日志](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/backend-final.log>)；日志耗时 130.90 秒。 |
| 桌面源码回归 | **44 通过，0 失败** | [desktop-final.log](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-final.log>)；包括存档中断、候选失败、PID 登记、重复恢复、超时退出、连续两次关闭窗口，以及回退换页后的错误通知、退出/隔离验收守卫和弹窗自身失败。 |
| 前端 Node 回归 | **102 通过，0 失败** | [frontend-node-final.log](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-node-final.log>)；包括正文备用字段与交互状态。 |
| 构建清单回归 | **11 通过** | [build-tests-final.log](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/build-tests-final.log>)；文件集合、路径及清单核对。 |
| 离线 Chromium | **47 组通过** | 见下方七份日志；所有请求拦截，合成样本。恢复页面的桌面方法为测试 stub，不计为原生文件操作证明。 |
| 本轮 Windows 包内私有后台 | **14 条检查通过** | [packaged-flow/product-flow.json](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/packaged-flow/product-flow.json>)；实际 HTTP、SQLite 备份、导入、去重/拒绝、草稿冲突、报告与下载、持久化、另一资料目录恢复及预算。14 条包含 3 次后台正常退出与 ready 清理检查。 |
| 本轮 Windows 包内父进程生命周期 | **4 个场景通过，后台退出码均 0，ready 均清理** | [parent-packaged/parent-lifecycle.json](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/parent-packaged/parent-lifecycle.json>)；就绪后 EOF、启动中 EOF、父管道保持打开时 HTTP 关机、真实父进程突然退出。启动中 EOF 场景未核验保存内容，其余三个核验保存数据。 |
| 本轮 Windows 原生 Electron 恢复 | **通过；应用退出码 0，后台正常退出 2 次，ready 残留 0，恢复事务残留 0** | [native-restore-acceptance.json](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/native-restore-acceptance.json>)、[native-exit.json](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/native-exit.json>)、[原始桌面记录](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/desktop-smoke.json>)。 |
| 两个平台分发包 | **Windows 71、Mac 72 个应用文件与当前源码、manifest、包内字节一致，ZIP CRC 通过** | [构建结果](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/desktop-build-results-recovery-candidate.json>)、[独立 package-audit.json](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/package-audit.json>)；不带用户数据库、会话或应用测试代码。 |

七份浏览器验收日志分别为：[应用路由 8 组](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-app_flow-final.log>)、[采集 8 组](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-collection-final.log>)、[任务串 3 组](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-batch-final.log>)、[交互完善 8 组](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-hardening-final.log>)、[恢复页面 7 组](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-restore-final.log>)、[关系网络 4 组](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-network-final.log>)和[社媒页面 9 组](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-social-final.log>)。

关闭过程中的二次关闭竞态通过**源码测试执行实际注册的窗口处理函数**验证：恢复未结束时两次关闭都被阻止，退出只请求一次，恢复结束后可以关闭窗口。该补丁已经进入本轮成品，源包一致性由分发审计确认；没有将这条源码回归另称为成品窗口连续两次人工关闭的实测。

## Windows 恢复实物检查

本轮原生运行使用真实 `PEACE ATLAS.exe` 及其私有 Python、后台入口。在设置页实际触发恢复入口，同一应用窗口重新连接新后台，后台 PID、端口和会话 token 均发生更换，恢复后页面可读取资料、任务、报告、研究图及草稿。用于验收的 SQLite 样本包含：

| 保存内容 | 恢复后实际数量 |
| --- | ---: |
| 资料 | 2 |
| 账号 | 2 |
| 报告 | 1 |
| 研究记录 | 2 |
| 任务 | 4 |
| 草稿 | 1 |

其中 1 个排队任务和 1 个运行任务变为“已中断”，新增对应 warning 事件，活动任务为 0，给页面和工作线程时间稳定后没有产生新的任务或状态变化。原有 `succeeded` 和 `partial` 历史按源记录保留。

独立检查器读取安装后的数据库、原始备份和恢复前的存档，执行 SQLite 完整性与外键检查，并对 **21 张表**逐条对照。除预期的任务中断字段、相关研究状态/更新时间及两条新警告事件以外，报告快照、账号、关系、研究节点、材料关联、声明、观察、人工复核和草稿等保存证据与源数据库一致；不是仅比较记录总数。恢复前的 1 条原资料、原任务列表（本次为空）及 revision=5 的原草稿也与原生 HTTP 捕获记录对照，存档哈希已核验。最终结果见[独立 SQLite 实物验收](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/native-restore-acceptance.json>)及[检查器通过日志](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/native-restore-evidence-final.log>)。

原备份 SHA256 前后均为 `4d0ad59fb940e776c397303e21d3155fbc8e02ca7606f4d44188b058975edf5a`。隔离目录中的系统加密连接文件字节与 synthetic 社交 sentinel 字节前后不变；这证明恢复没有改写这些文件，不等于验证真实账号登录或真实 AI 连接。实际原库存档见[存档数据库](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/restores/before-20261008110729901-31f9cfa8-99ad-4e94-ac1e-5f340e4e1f6a/workbench.sqlite3>)。

这次正常退出已完成 WAL 检查点，原库存档实物仅有 main 数据库；存在 WAL/SHM 时完整保留和半途移动/拷贝的故障路径由桌面源码回归及独立合成故障复现覆盖，不能据此声称本次原生存档实际包含三份文件。

原生验收记录明确标为 `native_selection_and_confirmation="isolated acceptance branch passed; source and confirmation injected"`、`operating_system_dialogs_manually_exercised=false`：实际设置页点击和恢复事务运行在成品 Electron 内，验收分支注入确定的备份文件并确认，记录 `open`、`confirm` 及展示的数量和影响文案。常规使用时原生确认是用户明确授权恢复的步骤；注入仅用于隔离验收模式。**没有声称本轮人工操作了操作系统文件选择器或确认弹窗**。截图见[恢复前设置页](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/desktop-restore-before.png>)、[恢复后资料库](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/desktop-restore-library.png>)、[恢复后任务](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/desktop-restore-jobs.png>)和[恢复后设置页](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/desktop-restore-settings.png>)；离线浏览器的最新 [390px 恢复状态截图](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-restore-final/restore-desktop-success-section-390.png>)单独保留。

## 保留的失败记录及修正

本轮保留了产品真缺陷、复现脚本错误和检查器错误的原始记录，分别说明原因；早先出现的成功页面或上层 smoke 结果没有替代最终退出、实物及内容核对。

在最后的回退错误通知修复之前，已有一轮 `104951` 包成功完成正常恢复。其 [构建与哈希](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/first-successful-package/desktop-build-results.json>)、[原生实物检查](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/first-successful-package/native-restore-acceptance.json>)及两平台 ZIP 均另存于 `first-successful-package`，旧原生资料目录也保留。它们作为本轮过程记录保留；最后修复后的新包重新执行构建、包审计、原生恢复和包内流程，使用本文件的最终证据。

| 记录 | 实际问题、修正与最终证据 |
| --- | --- |
| Python 关闭时 buffered fatal | 初次源码 Electron 恢复虽然页面和数据步骤完成，后台在解释器关闭时因守护线程阻塞于 `BufferedReader.read` 报 `_enter_buffered_busy`，两次后台退出码为 3221225477。父管道监视改为 `os.read`；源码及最终包的正常 HTTP 关闭与父退出回归通过，本轮原生包的两次后台退出均为 0。[原始失败日志](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-source-smoke/logs/desktop.log>)、[修正后源码日志](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-source-smoke-verified/logs/desktop.log>)、[最终包日志](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-packaged-restore-final/logs/desktop.log>)。 |
| 回退成功后恢复失败提示丢失 | 候选服务失败后，恢复事务重新载入原库页面，再向旧页面的 IPC Promise 抛出错误；旧页面已替换，用户看不到回退说明。旧代码的实际主进程 VM 复现只有 `renderer-replaced`，缺少预期的 `native-error`，为确证的 P2 缺陷。可信恢复请求现在由主进程显示失败弹窗，完整保留回退结果与存档路径，并记录日志；退出和显式隔离验收不打开阻塞弹窗，弹窗自身失败不会遮蔽原始恢复错误。新增 3 条实际主进程回归通过，独立复跑亦为 3/3，通过总数更新至 44。[修复前真实失败](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-rollback-error-before-fix.log>)、[最终桌面回归](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/desktop-final.log>)。 |
| 重试迟到 ACK 重开已关闭抽屉 | 对先前浏览器资产的独立复现确实出现已关闭 dialog 被迟到重试响应重新打开，期望 0、实际 1。界面补上抽屉生命周期保护，最终交互浏览器验收通过。第一份复现脚本在账号页面断言先失败，未构成该缺陷证据；第二份才成功重现目标故障。两份日志均保留：[初次脚本失败](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/dialog-race-proof.log>)、[确证旧资产缺陷](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/dialog-race-proof-2.log>)、[最终浏览器回归](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-hardening-final.log>)。 |
| 独立 checker 对草稿时间层级读错 | 第一次最终实物检查在原草稿断言失败：检查器直接把 SQL `data` 当成完整 API 草稿，未按实际读取方式从列合并 `revision` 与 `updated_at`。修正检查器构造相同读取形态，仍核对原存档的独立列、版本和记录数量，之后 21 表与原资料/任务/草稿全部通过。没有修改恢复数据来消除断言。[原始 checker 失败](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/native-restore-evidence.log>)、[修正后通过](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/native-restore-evidence-final.log>)、[检查器源码](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/scripts/verify_restore_evidence.py>)。 |
| 部分归档和再次崩溃 | 补全旧库归档改为独占临时拷贝、大小/哈希核对后原子改名；已有不完整副本保留后从核实原件重建。回滚及启动恢复在创建后台前登记阶段、在同步启动回调登记 PID，每一代失败候选使用独立目录，避免后续恢复覆盖旧证据。针对这些路径的最终桌面回归通过。 |
| 可解析 JSON 仍让实际读取失败 | 增加研究 account capture、证据 URL 数组、决策数组及保存日期的消费类型检查；损坏内容在停止当前后台前拒绝。正文备用字段的坏类型也在预检拒绝，界面选择合法文本后才截取预览；正常旧文本、空字符串和空值继续兼容。[实际旧正文读取错误复现](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/backend-preview-consumer-proof.log>)、[最终后端回归](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/backend-final.log>)、[前端回归](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/frontend-node-final.log>)。 |

## 本轮交付文件与边界

| 平台 | 当前文件和验证状态 |
| --- | --- |
| Windows x64 | [本机启动快捷方式](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[实际验收 EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-recovery-candidate-20261008-110543/PEACE ATLAS.exe>)；[完整 portable ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-recovery-candidate.zip>)。ZIP 424,145,673 字节，5,689 个 ZIP 项，71 个应用文件；Windows 成品恢复及包内后台验收通过。[快捷方式 COM 读回](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/shortcut-readback.json>)确认指向新 EXE 和完整 portable 工作目录，参数为空，不含 smoke 或测试资料目录参数。解压后运行完整目录内的 EXE，不单独移动 EXE；移动 portable 目录后需从新路径运行或更新快捷方式。 |
| Apple Silicon macOS | [完整 ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-recovery-candidate.zip>)。ZIP 375,015,195 字节，4,259 个 ZIP 项，72 个应用文件；最低 macOS 13.0。WSL/Linux 的 `apple-codesign 0.29.0` ad-hoc 检查通过，44 个签名二进制、29 个符号链接，代码页、entitlements、资源和架构标记通过。原生 Apple codesign 验证和 Mac 启动均 pending；没有 Developer ID 签名或 Apple 公证，Intel Mac 不在此次构建范围。 |

Windows ZIP SHA256：`e62500c77ace2226229147f876d788801a81cb9955ad3e16ce12f4665d699259`。

Apple Silicon ZIP SHA256：`00bafe9ab26bc16700b0cc789b569cc55edb2332eb20f14431991ee7940a0249`。

构建 JSON 中的 `native_runtime_test=not_run` 是构建阶段字段；Windows 原生与包内运行由本文件引用的后续记录补充。Mac 没有对应后续原生通过记录。最终源、清单与包内字节一致性的结论来自本轮独立包审计，不能仅凭“构建成功”推出。

本轮没有新增公开新闻、X 或 Instagram 实采，没有新增真实 AI 调用、实际 PostgreSQL 服务、24 小时耐久或 10,000 条产量证明。10,000 / 200 / 86,400 等参数只作为保留预算与预览检查；合成研究材料中的账号、关系和核验标记不构成实际人物或账号验证。跨平台原生恢复、真实系统文件选择/确认的人工路径以及对应外部服务仍需要各自环境证据。上述边界没有计入本轮通过项，也没有把本轮恢复闭环通过写成整个产品所有能力已经完成。
