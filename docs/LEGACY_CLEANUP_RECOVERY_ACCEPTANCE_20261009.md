# PEACE ATLAS 旧文本清理兼容与有界 SQL 写入交付验收

日期：2026-10-09；实际验收截止 **06:04 UTC（北京时间 14:04）**。**055110 双平台成品、独立包内容审计、Windows 原生启动/恢复及正式普通快捷方式升级通过**。完整后台为 **1869 passed、0 failed，262.07 秒**；最终 reader 全部六研究完整兼容，正式升级前后 22 表结构与全带类型行一致。总收据为 [local-release-acceptance.json](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/local-release-acceptance.json>)。两份已登记的原草稿保留不改，本文件对应实际完成的交付。

本轮修复合法旧分析中的字符串 `findings` / `affiliations`：过去这些断点能正常保存，但资料进入回收站或永久删除后，读取投影把字符串当成结构化对象，导致 get/export 等正常路径抛错。现在历史文字保持原字段、顺序及内容，并明确标为“历史文字（未记录可核对引用）”。历史文字数量与实际排除的来源数量分别记录；文字不被猜测为新的关系、隶属 claims、edges 或当前报告引用，也不进入新报告材料及 context。结构化条目仍按实际支持来源过滤，无关证据与独立报告快照保留。证据见 [旧形状修复收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/legacy-behavior.json>)与[独立读取评审](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/legacy-reader/independent-review.md>)。

有效旧证据包括 **18 个正常调用链读取失败，逐项重放后为 0**，以及在实际旧 050022 冻结模块上复现的 **4 failed、13 deselected，1.72 秒**。四条 frozen red 覆盖合法 findings/affiliations 字符串及旧分析版本。初期错误 cwd、误导入新源码、对轻量 list 期待完整 analysis 的运行已排除，不能把早期 13 条失败都算成产品 bug。见 [有效 frozen red](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/tests/legacy-frozen-red-002.log>)及修复收据中的 excluded 列表。

清理原边界保持：明确预览后先进入回收站、可恢复；永久删除仅作用于明确选中的回收站资料。已删除来源不再作为当前研究的可引用支持或新报告材料，来源恢复后重新可用。没有来源绑定的历史文字保留为历史，不被提升为当前可引用支持。永久删除当刻的 completed durable 分析历史、独立 `wb_reports.snapshot` 与已生成报告保留；相关安全 pending 的整批原始响应失效，旧 producer 不能重新写回，无关 pending 不损。防复活备份结论仅限“删除后制作的备份恢复，再重放旧 producer”，不改变用户明确恢复删除前旧资料库的正常语义。本轮没有修改 schema、恢复字段校验或扩大其他畸形数组/嵌套 provenance 的容错范围。

## 有界写入已落实到源码

`_checkpoint_deferred` 保留完整验证与 replacement，只将逐行 INSERT 改成真正的多 VALUES INSERT：每 chunk 最多 **100 行、600 参数**，UTF8 参数与 framing 估算 **1MiB 软上限**。合法单条正文可独立超过软上限，原 **2MiB 记录上限**不降低、不截断；header 全局 ordinal、组件 ordinal 与统一 `updated_at=stamp` 不变。所有验证完成后才 DELETE，所有 chunk 使用同一连接和原事务。不采用整批 encoded cache，避免多持有整个 backlog 的正文编码副本。

源码 `research_frontier.py` 与已验证 multi-only 副本字节一致，SHA-256 为 `90db478fcf2f0a66dc0ed9b204ce009b0096daaec802c53a95b6318de3c8609b`。隔离真实 SQLite trace 与独立进程无 trace 配对结果如下；计时仅 cleanup_apply，每个规模一次配对，不是稳定延迟保证。

| 候选数 | 旧方法数据 SQL → 新方法副本 | INSERT 语句 → 新方法副本 | 两者逻辑写入行数 | 无 trace 配对秒 |
| ---: | ---: | ---: | ---: | --- |
| 100 | 2,915 → 44 | 2,900 → 29 | 2,900 | 0.399832 → 0.373294 |
| 600 | 17,415 → 189 | 17,400 → 174 | 17,400 | 2.314489 → 2.293727 |
| 2,400 | 69,615 → 711 | 69,600 → 696 | 69,600 | 13.004837 → 11.392260 |

2400 候选的 INSERT 语句减少 **99%**，该次无 trace 样本耗时约改善 **12.4%**；逻辑写入仍为 **69,600 行**，线性重写和重复编码尚在。清理峰值工作集约 **324MB**，与旧方法接近；整批编码复用曾达到约 **427MB**，因此本轮不采。所有六个克隆的全库带类型行、正文、组件、ordinal 与固定时间戳一致，integrity 为 `ok`、FK 零违规。实际最大普通 chunk 为 **229,923 字节、600 参数**；合法大行由单独回归验证。理论参数/消息边界不等于 PostgreSQL 实测，PG 传输和并发竞争仍待验收。见 [性能与内存收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/performance/probes-002/performance.json>)及[方案与限制](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/performance/decision.md>)。前轮 9.946 秒不是本轮新源码计时。

## 当前回归与兼容证据

新增 **36 个用例＝21 个 legacy 兼容用例＋15 个 chunk 用例**，均已实际执行。相关套件范围重叠，以下数字不能相加作为全后台总数。

| 范围 | 已有实际结果 |
| --- | --- |
| 最终冻结完整后台 | **1869 passed、0 failed，262.07 秒，exit 0**；1 个既有 Pydantic warning；本轮完整新测；[完整后台日志](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/backend-final-regression.log>)与[冻结测试收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/source-tests.json>) |
| legacy 相关回归 | **227 passed，48.73 秒**；当时包含 13 个 legacy 用例；[日志](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/tests/legacy-related.log>) |
| 完整新增 legacy 用例 | **21 passed，4.32 秒**；覆盖清理、恢复、重开、断点及正常接口；[日志](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/tests/legacy-green-endpoints.log>) |
| shipping frontier 相关回归 | **130 passed，39.06 秒**；[日志](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/tests/frontier-related.log>) |
| chunk 旧方法有效红测 | **3 failed、12 passed，1.85 秒**；失败仅为实际 SQL/chunk 工作量要求；[红测](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/performance/new-test-red.log>) |
| multi-only 实际私有副本 | **15 passed，1.26 秒**；另验证第二 INSERT 失败时完整 cleanup 事务回滚；[收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/performance/multi-only-regression-002/receipt.json>) |
| 前端 / 桌面 / builder | **129 / 59 / 11 passed** 为同源码旧记录沿用，本轮没有重新执行；[来源核对](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/retained-test-source-provenance.json>) |

legacy 独立 attempt-002 已通过 18 个旧失败映射、4 类合法/结构化生命周期及旧 050022 对新 reader 的全部 **6 个实际保存研究**完整 get/export/Markdown/list 逐值比较。保存快照与 22 表类型化行、结构不变；主研究仍为 **1 项 24 账号研究**，不是 24 项研究。见 [attempt-002 机器结果](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/legacy-reader/attempt-002/independent-audit-results.json>)。

包含第三个 frontier 修改的 **最终 100 文件冻结**已另做新鲜 reader 验收：old 050022 与 final 的全部 **6 个研究**完整 get/export/Markdown/list 逐值一致，两侧 failures 为空，原输入快照、既有证据和克隆数据库类型化行/结构保持一致。100 源文件前后稳定并匹配 final freeze；其余 **97 个出货文件字节不变**，三个变更文件分别仅改变 `_project_research_cleanup`、`research_markdown`、`_checkpoint_deferred`，各变更定义以外的 AST 均完全一致。本次 application launches、实际 HTTP 与模型响应均为 0，没有新增研究审阅结果。见 [最终 reader 机器收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/legacy-reader-final/final-reader-audit-results.json>)，收据 SHA-256 为 `ffefd4afc9055283229ac51ea5639b4b3d11aeaa203ba50b1cc01c5b6811220e`。

最终出货源码冻结为 **100 文件**，清单 SHA-256 `f91bd3bef34c3d08932ede1320ea3054240c519e5e67a03f69ebd282f566f226`：[冻结清单](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/application-source-freeze.json>)。构建及最终绑定核对期间源码稳定。交付 helper 来源核对为 15 个，14 个内容不变，switch helper 仅调整上一版指针；本轮使用同一套已核对 helper 完成实际门槛。[helper 来源收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/helper-provenance.json>)。

严格恢复预检已通过：归属 QA 的旧 **21 表**合成完整备份按当前 schema 生成 **22 表**候选库，新增 deferred 表为空、严格结构验证及预期计数保留通过；原备份与夹具 metadata 字节不变。预检没有启动应用，随后新 EXE 的实际原生恢复另行通过，二者分别记录。[恢复预检收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/legacy-restore-preflight.json>)。

## 055110 成品、包内容与签名结构

本轮 allbuild 实际 exit 0，Windows portable 标记为 `20261009-055110`；[构建结果](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/final-build-results.json>) SHA-256 为 `d9d6efc0cb34e68bb573f0ebb60dca9e443e516e186155e5b75ba35d514adb97`。两个留存 ZIP 与新构建成品的实际全部字节、大小及 SHA 相同：[留存收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/retained-archives.json>)。

| retained-055110 成品 | 字节数 | SHA-256 |
| --- | ---: | --- |
| [Windows x64 ZIP](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/retained-055110/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>) | 424,370,871 | `d31460abef2e910420d08a939a85bc6fff62a9e25ea6b799a2d3f42f16454018` |
| [macOS ARM64 ZIP](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/retained-055110/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>) | 375,228,901 | `6836030568f29ce2c4fece52271460550a39c3ed88dfe9b41a7d2fb4f1460e53` |

独立内容审计核对 Windows 的 **99 个适用出货文件、5717 个 ZIP 条目 CRC 及 5717 个 portable 文件 SHA**；Mac 的 **100 个出货文件、4258 个普通文件及 29 个 symlink**通过。清单 payload 与冻结源码一致。[独立包审计](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/package-audit-independent.json>)。Mac 离线签名结构核验实际覆盖 **44 个 signed Mach-O、47 个 slice、159775 个 code pages、16 个 resource envelopes**；代码页与资源 seals 通过。这项内容核验没有执行 Mac 原生应用或 Apple 原生签名 policy。[Mac 独立签名结构审计](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/macos-signature-audit-independent.json>)。

最终冻结绑定的 **16 项检查全部通过**，两个成品与留存逐字节一致，100 源文件、builder、15 helpers 和已登记证据前后稳定；旧 050022 证据保持不变。[最终绑定独立核对](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/package-audit-final-binding-independent.json>)。

## 包内流程、Windows 原生与正式升级

包内后台完成 **14 项离线核心流程检查**，覆盖资料导入/拒绝、正文与来源保存、草稿冲突、worker 持久化证据报告、报告与来源快照导出、SQLite 备份、重启保留、独立恢复与退出。这里生成的是持久化 evidence report，没有新的真实模型成稿。[包内 product-flow.json](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/new-package-product/product-flow.json>)。

055110 新 EXE 的实际隔离原生启动 **exit 0**，对应 **5 个自有数据库**完整性与 FK 检查通过；退出后自有进程、监听和 ready 文件均无残留。[原生启动结果](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/native-isolated-result.json>)、[5 库完整性](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/data-integrity.json>)与[退出清理](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/runtime-cleanup.json>)。

新 EXE 实际完成旧 21 表备份到当前 22 表的原生恢复，保留来源、研究/报告、草稿和旧库归档；2 个未完成 job 按恢复语义标记 interrupted。对应 **2 个自有数据库**完整性与 FK 检查通过，退出清理无残留。[原生恢复启动结果](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/native-restore-isolated-result.json>)、[恢复实证](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/native-restore-evidence.json>)、[2 库完整性](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/restore-data-integrity.json>)与[恢复退出清理](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/restore-runtime-cleanup.json>)。第一次 root 调用漏传 helper 的 mandatory `PreflightReceipt`，在参数绑定阶段被拒绝、未启动应用；保留 [调用错误记录](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/audit/native-restore-argument-error.txt>)。正确参数重新调用后上述实际验收通过，helper 没有为绕过门槛而改写。

正式升级先保存新鲜只读一致快照，再正常关闭旧 050022 窗口，没有强杀；两处快捷方式指向 055110，普通桌面快捷方式实际启动新 EXE 和包内 private Python。验收时主进程为 18096、后台为 23704，public ready、version 2.0.0 与 SQLite health 通过，未依赖调试端口、未写正式连接配置。[正式关闭前快照收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/formal-preclose-current.json>)、[快捷方式切换](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/shortcut-switch-receipt.json>)与[普通启动收据](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/formal-normal-launch-receipt.json>)。

升级前后 **exact_22_to_22**，全部 22 表带类型行与结构逐值不变，保留 **370 条资料、50 个账号、37 个 job、1 个报告、6 个研究记录**；integrity 通过、FK 0、active jobs 0。[正式数据保留](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/formal-upgrade-preservation.json>)。主研究仍保留 **221 条正文、118 条有向关系、24 个账号、363 项目录、80 项已接受审阅、283 项待审、39 个 deferred entries、40 条 cached relations**；最终 reader 比较 HTTP 0，未改变审阅接受状态、未把未完成研究写成完成。

| 本轮交付门槛 | 实际结果 |
| --- | --- |
| 最终冻结完整后台回归 | **PASS**：实际 exit 0，1869 passed、0 failed，262.07 秒；收据匹配 final freeze |
| 最终冻结 6 研究 reader 兼容 | **PASS**：final source 新鲜六研究完整比较一致，source100 stable，不沿用 attempt-002 代替 |
| 当前严格 schema 的 21→22 恢复预检 | **PASS**：合成备份候选库与源保留已验；不包含原生启动 |
| 新包构建、留存、独立内容与冻结绑定审计 | **PASS**：055110 两成品实际字节与全部绑定检查通过 |
| 包内离线核心流程、Windows 原生与隔离恢复 | **PASS**：本轮新包 14 流程、实际 EXE 启动/恢复、5＋2 库完整性与退出清理通过 |
| 正式关闭、普通快捷方式启动及全库保留 | **PASS**：普通 055110 启动，22 表结构与全带类型行一致 |

本轮没有真实外采 HTTP、付费模型或新的最终模型报告；隔离接口回归使用测试客户端。上一次真实请求返回 **HTTP 402** 是历史记录，不推断当前即时余额；103 条公开资料压缩仍未接受。252 条旧资料完整阅读档案保留，原请求字节 identity 门槛不变，正文生成与最终复核尚未完成，不算本轮新模型成果。[既有模型与阅读边界](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/MODEL_OUTPUT_RECOVERY_ACCEPTANCE_20261009.md>)。

本轮兼容范围是合法历史字符串 findings/affiliations；其他 nested 非对象 identity_variants、observations、evidence 等畸形 provenance 留待下一轮调查，不能宣称所有 legacy 格式问题已经完成。人工 OS 文件选择器、Mac 原生运行及 Apple 原生签名 policy、PG 参数传输与并发、专门 UI 图形拖拽没有本轮实际验收；离线包/签名结构、读取兼容与 Windows 实证不替代这些范围。
