# PEACE ATLAS 资料清理一致性与 050022 交付验收

日期：2026-10-09。**050022 新包、Windows 原生启动及正式升级已通过本轮验收**。完整后台新回归为 **1833 passed、0 failed，266.52 秒**；新包绑定 100 个出货源码文件的冻结清单。正式升级前后，22 表的结构与全部带类型行完全一致。总验收收据为 [local-release-acceptance.json](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/local-release-acceptance.json>)。本文件替代草稿中的待验收状态；原草稿保留不改。

## 清理、断点和恢复的实际边界

本轮修复两条真实反例：永久删除相关资料后，旧研究断点曾能把已经清除的原始错误响应重新写回；仅在已完成分析中引用普通资料的研究，曾漏记删除依赖，导致已删摘录再次出现在当前读取中。现在清理按正式结构化来源字段和关联 ID 识别依赖，并记录持久化的永久删除来源键。断点在事务内读取这些删除标记，在 legacy 合并或 v2 分析选择之后过滤最终分析，v2 候选核对随后使用最终 affiliations；旧 metadata 中的空删除列表不能擦掉已保存的删除标记。普通文本里偶然出现的 URL 不被猜测为依赖。

`source_url` 已纳入正式支持 URL 集合。结论只剩已删除的唯一支持时退出当前研究投影；仍有独立支持时保留对应结论与证据。人工 confirmed/rejected 记录保留，原有引用核对规则没有降低。修复前失败证据、修复后结果和独立审查见 [一致性审计](<C:/Users/wwx/Documents/New project 7/output/cleanup-scale-audit-20261009/audit.md>)、[变更收据](<C:/Users/wwx/Documents/New project 7/output/cleanup-scale-audit-20261009/change-receipt.json>)与[独立复查](<C:/Users/wwx/Documents/New project 7/output/cleanup-scale-audit-20261009/post-fix-review.md>)。

资料先进入回收站，仍可恢复；回收站状态不会被当作永久删除。永久删除只针对明确选中的回收站资料。与已删来源有关的安全 pending 批次整批失效，其原始响应从持久化研究行中物理清除；即使该批只有部分证据命中删除范围，旧 checkpoint 以及取消、错误后的旧 producer 再写也不能复活该响应。无关 pending、独立证据和未受影响的付费响应保持原样。这里的物理清除指当前持久化 pending 数据，无磁盘介质擦除含义。

永久删除当刻保留已经完成的 durable 分析历史，当前研究读取投影排除已删除支持；此后新 checkpoint 的最终分析也按永久删除标记过滤后保存。独立 `wb_reports.snapshot` 继续作为报告快照保留，清理没有抹掉所有历史或报告。来源版本、关联条目和事务回滚维持数据库外键一致性。备份回归证明的是：**从删除后制作的备份恢复，再重放旧 producer 不会复活已删除的 pending 响应**。用户明确恢复删除前的旧资料库属于另一项正常恢复语义，不包含在这项防复活承诺中。

恢复入口同时补齐 `purged_source_keys` 的形状检查：缺省、`null`、字符串数组可恢复；其他类型以及混有非字符串成员的数组被拒绝。校验在复制候选库之前完成，拒绝后源文件字节、目录成员保持不变；合法备份经真实恢复后可由正常研究读者读取，来源键与独立报告快照保留。证据见 [恢复修复收据](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/audit/restore-fix-receipt.json>)与[恢复独立评审](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/audit/restore-independent-review.md>)。

## 已实际执行的回归

本轮新增 **40 个参数化行为用例＝27 个清理/断点用例＋13 个恢复形状用例**，均已实际运行。覆盖原始语法错误响应为字符串/对象、legacy/v2、无 catalogue 仅来源键 pending、partial batch 整批清除、同 URL 多研究、两个删除条目、无关 pending、独立来源与报告、trash/restore、旧 producer 的取消/错误再写、人工 confirmed/rejected、删除后备份恢复重放及真实事务回滚。

| 范围 | 实际结果 | 本轮来源 |
| --- | --- | --- |
| 全后台 | **1833 passed，0 failed，266.52 秒** | 本轮完整新测；[后台日志](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/audit/backend-final-regression.log>) |
| 一致性关联核心回归 | **293 passed，37.42 秒** | 本轮新测；含新增 27 项；[变更收据](<C:/Users/wwx/Documents/New project 7/output/cleanup-scale-audit-20261009/change-receipt.json>) |
| 完整备份与恢复回归 | **145 passed，55.99 秒** | 本轮新测；含新增 13 项；[恢复日志](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/audit/restore-backup-green.log>) |
| 前端 | **129 passed** | 沿用同源码的旧测试记录；本轮核对来源 |
| 桌面模块 | **59 passed** | 沿用同源码的旧测试记录；本轮核对来源 |
| builder | **11 passed** | 沿用同源码的旧测试记录；本轮核对来源 |

293 与 145 的范围存在重叠，不相加作为独立测试总数；新增 40 项已包含在相关套件与全后台回归内。前端、桌面与 builder 的旧日志经 34 个前端文件、9 个桌面文件及 builder 哈希一致性核对，不能表述为这三类测试本轮重新运行。证据见 [source-tests.json](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/source-tests.json>)与[同源码测试来源核对](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/retained-test-source-provenance.json>)。

## 新包与 Windows 正式启动

源码冻结清单包含 **100 个出货文件**，清单本身 SHA-256 为 `030b6f4ba6eeb33f017a270aa04e1d8adbc539fd7d0643a5cea90986ec29dbfb`。出货源在测试、构建和审计期间保持冻结。见 [application-source-freeze.json](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/application-source-freeze.json>)。

| 050022 成品留存 ZIP | 字节数 | SHA-256 |
| --- | ---: | --- |
| [Windows x64](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/retained-050022/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>) | 424,370,201 | `c5d6552256611449421318f0be0731ee4220a6e2cfdb954ee1a1dcf88e1afe55` |
| [macOS ARM64](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/retained-050022/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>) | 375,228,236 | `beb6c4493d6e02959edcd632b180e1629dce43695aef80a0ef1c94f26f3d8faf` |

两份留存与本轮构建成品逐字节、哈希一致。包内容、manifest、出货来源与冻结绑定通过独立审计；见 [构建结果](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/final-build-results.json>)、[留存结果](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/retained-archives.json>)、[独立包审计](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/package-audit-independent.json>)与[新包冻结绑定复核](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/package-audit-fresh-binding-independent.json>)。

包内后台完成 **14 项离线核心流程检查**，包括资料导入与拒绝、保存正文与来源、草稿冲突、worker 生成证据报告、报告和来源快照导出、在线 SQLite 备份、重启读取、独立资料库恢复和干净退出。这是离线证据报告流程，不是新模型报告。见 [包内核心流程](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/new-package-product/product-flow.json>)。

Windows 新 EXE 已实际原生启动，并在隔离 profile 完成旧 **21 表资料库→当前 22 表**恢复。对应两组 **5＋2 个自有数据库**均通过 SQLite integrity 与 FK 检查；退出后自有进程、监听及 ready 文件清理通过。证据见 [原生启动](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/native-isolated-result.json>)、[原生恢复](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/native-restore-evidence.json>)、[5 库完整性](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/data-integrity.json>)、[2 库完整性](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/restore-data-integrity.json>)、[启动退出清理](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/runtime-cleanup.json>)与[恢复退出清理](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/restore-runtime-cleanup.json>)。

正式应用关闭后，两处快捷方式切换到 050022，正常路径 **快捷方式→新 EXE→包内 private Python→health** 已通过；未依赖调试端口。正式升级前后的 **22 表全带类型行及结构逐值一致**，保留 **370 条资料、50 个账号、37 个 job、1 个报告、6 个研究记录**；完整性与 FK 检查通过。证据见 [快捷方式切换](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/shortcut-switch-receipt.json>)、[正式正常启动](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/formal-normal-launch-receipt.json>)与[正式库保留结果](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/formal-upgrade-preservation.json>)。

## 实际保存研究的新旧读取兼容

对实际保存快照的独立克隆，old 042428 与 current 分别执行正常 Store 初始化、**6 次直接读取、6 次完整导出及 1 次列表**。全部 **6 个研究记录**的读取、导出及列表逐值相同，两侧失败数组为空；原快照不变，两侧克隆运行前后 22 表结构及全部带类型行保持一致。HTTP 请求为 **0**，模型回答为 **0**。见 [兼容验收说明](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/audit/actual-reader-compatibility/README.md>)与[compatibility-audit.json](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/audit/actual-reader-compatibility/compatibility-audit.json>)。

主研究是 **1 项 24 账号研究**，并非“24 项研究”；保存库共有上述 6 个研究记录。该主研究保留 **221 条正文、118 条有向关注关系**，证据目录 **363＝221 post＋118 visible_follow_list＋24 profile**；已接受 **80** 条证据的 ID 与内容哈希映射完全一致，尚待审阅 **283** 条、`complete=false`，另有 **39** 个 deferred entries 与 **40** 条 cached relations。此证据证明保存状态和正常 reader 兼容，不证明新的分析已完成，也不包含本轮专门的 UI 图形拖拽验收。

## 尚未验收的范围

上一次真实模型请求返回 **HTTP 402**；本轮没有新模型调用，没有新的最终模型报告，不据此推断当前账户即时余额。[上次真实请求收据](<C:/Users/wwx/Documents/New project 7/output/live-core-expansion-20261009/formal-native-ai-retry-030610/receipt.json>)。此前 103 条公开资料压缩仍为 `complete=false`、review `accepted=false`、`revision_pending`，不能计为通过；见 [103 条压缩收据](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/actual-compression-flash-public103-004/receipt.json>)与[保存状态](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/actual-compression-flash-public103-004/compression-state.json>)。

人工操作系统文件选择器、Mac 原生运行与 Apple 原生签名验收、PostgreSQL 并发竞争仍未测试。本轮没有专门重测 UI 图形拖拽，读取兼容与 Windows 原生证据不替代这些验收。

性能 **9.946 秒／69,615 条 SQL statements** 是本轮一致性修改前、2,400 个合成候选的隔离基线。bulk 与 memo 仅在隔离探针比较，**未应用到产品**；没有新源码 SQL 工作量或耗时已改善的结论。性能候选及风险见 [隔离性能结果](<C:/Users/wwx/Documents/New project 7/output/cleanup-scale-audit-20261009/performance-probes-002/performance.json>)和上述一致性审计；后续优化需另行验证来源语义、正文、ordinal、FK、事务回滚及 PostgreSQL 竞争。
