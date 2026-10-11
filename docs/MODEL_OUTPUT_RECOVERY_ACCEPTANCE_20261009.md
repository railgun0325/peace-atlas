# PEACE ATLAS 模型输出恢复验收记录

日期：2026-10-09。验收版本：**`20261009-042428` 冻结构建**。本轮后台回归、包审计、隔离产品流程、Windows 原生运行、旧备份恢复及正式快捷方式升级均已完成，汇总收据 `ok=true`。本记录只覆盖该冻结版本和下述实测范围；后续退出清理一致性问题的复现与修复另行记录，后续源码或构建须重新验收。[交付总收据](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/local-release-acceptance.json>)；[冻结源码清单](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/application-source-freeze.json>)。

本轮修复 JSON 语法失败和报告长输出截断的恢复路径。安全的最终 JSON 原串可保存用于有限修正，修正后仍须通过字段、证据 ID、来源支持和隐私门禁。草稿只有 `finish_reason=stop` 才进入复核；明确 `length` 可在模型输出上限及实际剩余上下文内扩大预算。请求前保存断点，恢复沿用已完成阅读和原拟合预留；HTTP 402、超时和取消不盲目重试。可用输出空间不足时明确报告容量不足并保留阅读、压缩断点。报告快照分别记录历史初始预算和最终接受草稿的实际预算。诊断不保存密钥、请求头、隐藏推理或截断正文。[JSON 恢复记录](<C:/Users/wwx/Documents/New project 7/output/syntax-recovery-20261009/ACCEPTANCE.txt>)；[草稿输出实现与验收记录](<C:/Users/wwx/Documents/New project 7/output/report-output-recovery-20261009/implementation-receipt.json>)。

| 测试范围 | 结果 | 本轮证据范围 |
| --- | --- | --- |
| 后台 | 1793 passed，0 failed，269.08 秒，1 warning | 本轮修改后完整重新运行。[日志](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/audit/backend-final-regression.log>) |
| 前端 | 129 passed，0 failed | 复用原 `030610` 同源码通过记录；本轮未重跑。34 个交付文件哈希不变。[日志](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/audit/frontend-final-regression.log>) |
| 桌面逻辑 | 59 passed，0 failed | 复用原同源码通过记录；本轮未重跑。9 个交付文件哈希不变。[日志](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/audit/desktop-final-regression.log>) |
| 构建脚本 | 11 passed，0 failed | 复用原同源码通过记录；本轮未重跑。脚本哈希不变。[日志](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/audit/build-scope-after.log>) |

测试汇总见 [source-tests.json](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/source-tests.json>)，既有记录的同源码依据见 [retained-test-source-provenance.json](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/retained-test-source-provenance.json>)。

两组真实资料断点已做零 HTTP 恢复核对：

- 最新 24 账号研究快照保持 **80 / 363 条已审阅，283 条待处理**。17 项检查通过，原已接受批次、上下文指纹、来源哈希及待处理来源映射不变；在首个模型入口取消，真实 HTTP、新增模型回答和新增接受证据均为零。这没有完成剩余审阅。[核对记录](<C:/Users/wwx/Documents/New project 7/output/syntax-recovery-20261009/latest-actual-snapshot-cancellation-proof.json>)。
- 原 `prepare-006` 的 **252 个来源、252 个阅读片段**由正常报告流程全部复用。在固定比较条件下，`030610` 冻结旧包与本轮冻结源码构造的首个正文请求 raw 字节一致，历史本地输入的语义一致；传输前取消，新增阅读、压缩、复核及真实 HTTP 均为零。原目录 67 个文件与原脚本哈希不变。原真实付费请求的 raw 字节身份仍未证明，单独保留该边界。[兼容核对记录](<C:/Users/wwx/Documents/New project 7/output/report-output-recovery-20261009/paid006-output-compatibility/compatibility-audit.json>)。

冻结验收时，最新正式研究模型尝试仍记录 **HTTP 402**，研究未完成；原 252 条独立报告 QA 的首个成稿请求也返回 402。252 条资料已完成阅读，正文生成和最终复核尚未完成。**本轮没有新增通过最终来源复核的正式模型报告**，断点兼容验收也没有调用真实模型。[正式模型尝试记录](<C:/Users/wwx/Documents/New project 7/output/live-core-expansion-20261009/formal-native-ai-retry-030610/receipt.json>)；[原报告状态](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/core-report-preflight/prepare-006/final-status.json>)。另一个 **103 条资料真实压缩 QA 仍未接受**：`complete=false`、复核 `accepted=false`，处于 `revision_pending`，不能计作压缩成功。[压缩请求记录](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/actual-compression-flash-public103-004/receipt.json>)；[压缩断点](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/actual-compression-flash-public103-004/compression-state.json>)。

| 交付验收项 | 实际结果及边界 |
| --- | --- |
| 打包与独立包审计 | Windows x64、macOS ARM64 包已构建并独立审计，冻结源码与包内文件对应，校验包结构、文件及私密数据文件名缺席。Windows 包 5717 个普通文件完成 CRC 与 portable 文件哈希核对；Mac 包 4258 个普通文件完成 CRC 核对，并核对 29 个符号链接。[构建记录](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/final-build-results.json>)；[独立包审计](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/package-audit-independent.json>) |
| 新包核心产品流程 | **14 项检查通过**，覆盖实际 SQLite 导入、正文与原 URL 保存、草稿冲突、持久任务、报告下载、备份、重启和独立资料目录恢复。生成的是 **离线证据报告**，未使用外部服务，不能计作真实模型报告生成成功。[14 项记录](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/new-package-product/product-flow.json>) |
| Windows 原生运行 | 实际启动 `042428` Windows 可执行文件，原生验收退出码 0。[原生结果](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/native-isolated-result.json>) |
| Windows 原生旧备份恢复 | 实际运行恢复流程，旧 21 表资料恢复为 22 表，新增固定结构的空 deferred 表；原有结构、研究及报告证据、终态历史和原备份保持一致。原生退出码 0，两个后端均正常退出。[原生恢复结果](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/native-restore-isolated-result.json>)；[恢复证据核对](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/native-restore-evidence.json>) |
| 隔离资料完整性与退出清理 | 两组 **5 + 2 个数据库**只读核对均通过完整性检查，外键违规 0，核对前后文件哈希一致。上述实测退出后，本组拥有进程、监听与 ready 文件均无残留；该结果限定于本轮实际运行路径。[5 库核对](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/data-integrity.json>)；[2 库核对](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/restore-data-integrity.json>)；[运行清理](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/runtime-cleanup.json>)；[恢复清理](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/restore-runtime-cleanup.json>) |
| 正式快捷方式与升级 | 原守卫正常关闭旧应用后切换两个快捷方式，普通快捷方式启动 `042428`，应用与独立 Python 后端均 ready；未使用调试端口，未写正式连接配置。升级前后 **22 表全部行与结构一致**，资料数保持 **370 条、50 账号、37 任务、1 报告、6 研究运行**。这些是既有资料数量，不表示新增模型报告。[切换记录](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/shortcut-switch-receipt.json>)；[普通启动](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/formal-normal-launch-receipt.json>)；[正式资料保留](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/formal-upgrade-preservation.json>) |
| 系统文件选择器人工操作 | **未测试**。原生恢复验收使用隔离验收分支注入来源与确认，未人工操作操作系统文件选择、确认对话框；不能据此宣称人工选择路径已验证。[恢复操作边界](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/native-restore-evidence.json>) |
| macOS ARM64 原生验收 | **未测试**。已有 Mac 包结构与文件核对；尚无 Mac 原生启动或 Apple 原生签名验证通过结果。[交付范围](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/local-release-acceptance.json>) |

正式切换首次检查时旧应用已关闭，原守卫因此拒绝切换。保留关闭状态下的 before-launch 快照后，以普通旧快捷方式启动 `030610`，独立核对再次确认 22 表全部行、结构与基线一致，再取得新鲜空闲状态并由原守卫正常关旧切新；本轮未修改守卫 helper。[启动前快照记录](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/formal-preclose-closed-app-before-launch.json>)；[旧版普通启动](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/old-normal-launch-readiness.json>)；[旧版启动后资料保留](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/old-normal-launch-preservation.json>)；[切换前空闲核对](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/formal-preclose-current.json>)。

`042428` 归档已单独保留，副本字节与构建归档一致。后续构建可能更新 `dist` 同名文件，应使用以下冻结归档及其校验值核对本次交付。[归档保留记录](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/retained-archives.json>)。

| 冻结归档 | SHA256 |
| --- | --- |
| [Windows x64 ZIP](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/retained-042428/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>) | `c86df980f27fdaf84fdd4d7fa49346085305934a64500f2dcedf2704b04c2bbc` |
| [macOS ARM64 ZIP](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/retained-042428/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>) | `665e50730c625a88ab0672b098c85b5ba011819f4de5568fb02792c64e8e6f04` |
