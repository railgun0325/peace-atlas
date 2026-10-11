# PEACE ATLAS 历史 README 档案

> 历史记录：以下为 2026-10-11 整理发布首页前的原 README 全文。
> 其中的旧构建入口、测试数字、任务进度和本机绝对路径仅供追踪，不表示当前版本或当前运行状态。
> 当前产品入口见 [README.md](README.md)，发布基线见 [本轮发布记录](docs/RELEASE_20261011.md)。

---

# PEACE ATLAS / 反戦研究室

**2026-10-10 当前正式Windows构建为 `harness-candidate-20261010-20261010-075908`。** 正式分析接入官方DSH SDK/runtime 0.2.0-rc.2，全文阅读和摘录复核后由Agent Loop按问题调用七种保存证据工具，再生成、复核并保存报告。API仍作为模型请求通道，无需另外启动DSH桌面应用。

本批修复报告运行时间的传递：多次请求组成的分批压缩与审核阶段现在使用报告真正剩余的总时长，单次模型请求仍最多等待10分钟。15、30、120分钟预算与总时限耗尽的四个新增行为反例全部通过；来源范围、输入指纹、原文校验、已通过笔记和失败历史保留，可从已保存草稿接回。

完整重跑**后台2321、前端166、桌面59、构建11，全部通过**。新包通过真实官方SDK的受控七工具、十并行调用、同会话候选纠正、取消和失败恢复；14项包内核心流程、隔离Windows原生启动、旧备份21→22原生恢复、七个隔离数据库及两平台完整字节审计均通过，自有进程、监听与就绪标记为零。受控模型响应与真实服务结果分别记录。Mac只完成静态检查，原生运行、Apple签名与公证尚未验收。

两个既有快捷方式已普通关闭旧窗口并正常启动新应用，升级前后22张正式表逐值、逐类型、逐结构一致。新任务复用601份来源阅读、59轮DSH决策、255次工具请求，以及第一批200条已通过摘要和第二批204条待复核草稿；目前完整成稿仍在运行，未认定报告完成。

[离线操作说明书](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/manual/操作说明书-20261010-Harness.html>)、[可编辑说明书](docs/USER_MANUAL_20261010_HARNESS.md)、[DSH接入与验收](docs/HARNESS_INTEGRATION_ACCEPTANCE_20261010.md)。[共用双击入口](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[当前Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/harness-stage-deadline-20261010/PEACE-ATLAS-2.0.0-windows-x64-harness-candidate-20261010-20261010-075908/PEACE ATLAS.exe>)；[Windows ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/harness-stage-deadline-20261010/PEACE-ATLAS-2.0.0-windows-x64-harness-candidate-20261010.zip>)；[Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/harness-stage-deadline-20261010/PEACE-ATLAS-2.0.0-macos-arm64-harness-candidate-20261010.zip>). 以下为对应历史交付。

**2026-10-09 此前正式 Windows 构建为 `relations-candidate-20261009-094219`。** 两个快捷方式当时已通过普通启动验收。账号类型改用整个节点轮廓和独立颜色：组织为绿色圆角方形，个人为棕色圆形，未判定为蓝灰菱形。核验状态单独以勾号和详情显示，悬停或选中保持类型颜色。图谱布局、拖动碰撞及关系语义文件与上一正式版本字节相同。

本批完整新运行 **2,043 项后台、166 项前端、59 项桌面、11 项构建测试，0 项失败**；新轮廓真实浏览器 18 项和原密集手势 21 项等检查通过，范围有重叠，不合并数量。新包的独立字节审计、14 项包内离线核心流程、Windows 原生启动及旧备份 21→22 恢复均通过。正式升级前后 22 张表所有行、类型和值及 schema 完全一致，保留 **551 条材料、61 个账号、43 项任务、1 份已有报告和 6 项研究**。本批没有新增真实平台或模型调用。

此前 `083814` 核心版本已加入账号类型与核验分离、公开来源自动核验队列、关注方向和多跳有来源路径、结构重点与桥接层级、AI 逐层候选排序，以及 YouTube 等通用账号的默认过滤与人工相关性覆盖。真实模型随后复用同一上下文的 3 个批次并完成 1 个新批次，接受 **212/603 条证据，391 条待处理**；应用退出后断点保留，尚未完成最终模型报告。关系表前后同为 156 条，统计刷新不能当作新增关联。

当时的操作说明书已逐项核对实际参数，并提供可直接双击的离线版：[历史离线操作说明书](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/manual/操作说明书.html>)、[历史可编辑说明书](docs/USER_MANUAL_20261009.md)。[轮廓与交付验收](docs/ACCOUNT_NODE_CONTOURS_ACCEPTANCE_20261009.md)和[核心关系深化验收](docs/RELATIONSHIP_DEPTH_ACCEPTANCE_20261009.md)分别记录各自的实际验证范围。

[共用双击入口，当前目标以本文顶部为准](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[历史 094219 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261009-094219/PEACE ATLAS.exe>)；[历史冻结 Windows ZIP](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/delivery/retained-094219/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)；[历史冻结 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/delivery/retained-094219/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)。该历史 Mac 包字节与签名封印已核对，Mac 原生启动及 Apple 原生签名策略仍未执行。

**2026-10-09 此前正式 Windows 构建为 `relations-candidate-20261009-055110`。** 两个快捷方式当时已更新并通过桌面按钮普通启动，正式升级前后 22 张表的全部行与结构完全一致，保留 370 条材料、50 个账号、37 项任务、1 份已有报告及 6 个研究记录。已修复合法旧版纯文本分析在清理关联资料后使研究读取、列表和导出报错的问题；历史文字按原内容保留，在 Markdown 中明确标为未记录可核对引用，不会转成关系、隶属或新报告来源。

**1,869 项后台测试本轮完整重跑通过，0 项失败。** 新增 36 个行为场景（21 个旧文本清理／恢复／接口场景、15 个分批写入场景）；前端 129、桌面 59、构建脚本 11 项继续采用源码哈希完全一致的既有通过记录。新包独立全文件审计、14 项包内离线核心流程、Windows 原生启动及旧备份 21→22 原生恢复均通过。最终新旧版本完整读取、JSON 导出和 Markdown 导出的全部 6 项研究逐值相同；主研究 24 个账号、221 条正文、118 条有向关系、80/363 已接受审阅和 283 条待审保持原值。

清理大量待处理记录改用有批次大小上限的数据库写入。2,400 个候选、约 118 MB 原文的隔离测试中，实际 INSERT 语句从 69,600 条降到 696 条；仍完整写入 69,600 行，原文、顺序、时间戳和事务回滚规则不变。该次无 trace 配对耗时从 13.0048 秒变为 11.3923 秒，峰值内存基本相同；SQL 降幅不能当成耗时降幅，单次样本也不是持续延迟保证。详见[旧文本清理兼容与分批写入验收](docs/LEGACY_CLEANUP_RECOVERY_ACCEPTANCE_20261009.md)与[机器交付记录](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/local-release-acceptance.json>)。

本轮没有新增真实模型调用、审阅接受或最终模型报告。上次真实请求在 03:15 UTC 返回 HTTP 402，断点和已有阅读已保存；这个历史结果不代表已重新核实当前余额。103 条材料的压缩仍未通过原有复核，原身份和来源门槛保持不变。Mac 归档签名与资源字节已独立核对，Mac 原生启动、Apple 原生签名策略、PostgreSQL 并发及其他畸形旧字段仍需分别验收，整体产品目标继续执行。

[共用双击入口，当前目标以本文顶部为准](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[历史 055110 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261009-055110/PEACE ATLAS.exe>)；[历史冻结 Windows ZIP](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/retained-055110/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)；[历史冻结 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/output/legacy-cleanup-recovery-20261009/delivery/retained-055110/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)。以下数字与入口属于各自的历史交付。

**2026-10-09 此前正式 Windows 构建为 `relations-candidate-20261009-050022`。** 两个快捷方式当时已更新并通过桌面按钮普通启动；正式升级前后 22 张表全部行与结构完全一致，保留 370 条材料、50 个账号、37 项任务、1 份已有报告及 6 个研究记录。该轮修复彻底删除资料后旧任务写回原始错误响应、仅在分析中引用的来源漏记删除，以及单一来源失效后旧摘录再次显示；完整备份恢复会预先拒绝损坏的删除标记，正常与旧版空值继续兼容。

**1,833 项后台测试该轮完整重跑通过，0 项失败。** 新增 40 个清理、断点和恢复行为场景；前端 129、桌面 59、构建脚本 11 项仍采用源码哈希完全一致的既有通过记录。该包独立全文件审计、14 项包内离线核心流程、Windows 原生启动及旧备份 21→22 恢复均通过。新旧版本在独立资料副本上读取和导出的全部 6 个研究结果一致，其中 24 账号研究的 221 条正文、118 条单向关系、80 条已接受证据和 283 条待处理证据精确保留，没有新增模型调用。详见[资料清理一致性验收](docs/CLEANUP_CONSISTENCY_ACCEPTANCE_20261009.md)与[机器交付记录](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/local-release-acceptance.json>)。真实模型最终报告受此前 HTTP 402 阻断，该轮的大规模清理性能优化与 Mac 原生验收另行记录。

[共用双击入口，目标以本文顶部为准](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[历史 050022 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261009-050022/PEACE ATLAS.exe>)；[历史冻结 Windows ZIP](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/retained-050022/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)；[历史冻结 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/output/cleanup-consistency-20261009/delivery/retained-050022/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)。

**2026-10-09 此前正式 Windows 构建为 `relations-candidate-20261009-042428`。** 两个快捷方式当时已更新并通过桌面按钮普通启动；升级前后正式 22 张表全部行与结构完全一致，保留 370 条材料、50 个账号、37 项任务、1 份已有报告和 6 个研究记录。该轮修复 AI JSON 语法失败的安全响应保留与有限修正，报告被截断时按实际剩余上下文扩大输出额度，请求前保存恢复预算。只有完整输出才进入来源复核，余额不足、超时和取消保留断点。

**1,793 项后台测试本轮完整重跑通过，0 项失败。** 前端 129、桌面 59、构建脚本 11 项采用源码哈希完全一致的既有通过记录，本轮未重跑这三个范围。新包独立全文件审计、包内 Python 的 14 项离线核心流程、Windows 原生启动与旧备份 21→22 恢复、数据完整性与进程退出检查均通过。真实资料 80/363 条关系审阅和 252 个报告阅读片段可准确恢复；真实模型最终报告仍受此前 HTTP 402 阻断。完整证据与边界见[模型输出恢复验收](docs/MODEL_OUTPUT_RECOVERY_ACCEPTANCE_20261009.md)和[机器记录](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/local-release-acceptance.json>)。

[共用双击入口，目标以本文顶部为准](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[历史 042428 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261009-042428/PEACE ATLAS.exe>)；[历史冻结 Windows ZIP](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/retained-042428/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)；[历史冻结 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/output/model-output-recovery-20261009/delivery/retained-042428/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)。Mac 原生启动和 Apple 原生签名验证仍未完成。

**2026-10-09 此前正式 Windows 构建为 `relations-candidate-20261009-030610`。** 两个快捷方式当时已更新并通过桌面入口普通启动；升级前后正式 22 张表所有行与结构完全一致。取消 AI 调用层过早的大输入字符限制，按 Flash 1M 上下文与本次输出预留安排材料，压缩保留原文引用与复核断点；修复已接受观察因资料库去重失配、待分析响应漏清，以及大研究图谱与列表读取性能。1,700 后端、129 前端、59 桌面、11 构建检查通过；该包独立全文件审计、私有 Python 14 项核心流程、原生旧备份 21→22 恢复均通过。

正式 24 账号研究保存 221 条正文、118 条单向关注关系，普通仅分析重试精确复用原 80/363 条，283 条待分析；唯一新请求实际返回 HTTP 402，已保留断点。252 材料报告的真实阅读全部完成，但综合生成也被 402 拒绝，尚无最终报告或压缩接受。采集覆盖、模型接受、容量估算和实际成品边界分别记录在[本轮容量与恢复验收](docs/AI_CAPACITY_RECOVERY_ACCEPTANCE_20261009.md)。

[共用双击入口，目标以本文顶部为准](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[历史 030610 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261009-030610/PEACE ATLAS.exe>)；[历史冻结 Windows ZIP](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/delivery/retained-030610/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)；[历史冻结 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/delivery/retained-030610/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)。

**2026-10-09 此前正式 Windows 构建为 `relations-candidate-20261009-010745`。** 当时两个快捷方式已更新，桌面按钮普通启动新版，原 21 张表全部行和结构精确保留，仅新增保存待办的空表。研究候选和公开观察持久化，账号分支和新闻查询前缀可提高上限后继续。1,584 项后端、129 项前端、59 项桌面测试通过；新包私有 Python 14 项产品流程、实际原生旧备份 21→22 恢复和全成品审计通过。报告分析继承按 DeepSeek Flash 1M 容量安排输入及来源可追溯压缩机制。结果与验证边界见 [010745 续跑验收](docs/FRONTIER_RECOVERY_ACCEPTANCE_20261009.md)。

[共用双击入口，目标以本文顶部为准](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[历史 010745 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261009-010745/PEACE ATLAS.exe>)；[历史冻结 Windows ZIP](<C:/Users/wwx/Documents/New project 7/output/frontier-recovery-20261009/delivery/retained-010745/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)；[历史冻结 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/output/frontier-recovery-20261009/delivery/retained-010745/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)。

**2026-10-09 早期交付为 `relations-candidate-20261008-205926`，默认解析模型为 `deepseek-flash`。** 已修复关系分析只送出前 80 条证据、成功批次无法独立恢复、证据变更后错误续用及有限候选理由越界的问题。关系图采用持续布局与较长稳定确认，拖动会牵动相连节点、推开附近节点；稳定后休眠，交互时重新唤醒。图由 **GPT-6.1 Sol Ultra** 子代理负责，授权 **GPT-6.1 Sol High** 孙代理独立审查，主代理继续核心流程。整体产品目标仍在执行。

[双击启动入口](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[历史 205926 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261008-205926/PEACE ATLAS.exe>)；[历史 Windows ZIP](<C:/Users/wwx/Documents/New project 7/output/analysis-coverage-20261009/analysis-coverage-release-205926/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)；[历史 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/output/analysis-coverage-20261009/analysis-coverage-release-205926/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)。当时两个快捷方式分别实际启动验证，普通原生主进程复用；已有加密连接继续由正式应用使用。

**1,090 项后台回归、120 项前端 Node、59 项使用包内 Python 的桌面 Node**通过。新包另通过 21 组真实资料副本关系图、144 节点合成布局及拖动后的长尾检查、5 组账号、25 项产品交互、14 项核心 HTTP、15 项日常可靠性和 4 种父进程退出。原生坏配置与研究备份恢复通过，21 表独立核对通过，浏览器页面错误为 0。

正式应用自动核验了独立官网准确链接的 `zengakuren` 与 `zengakuren_west` 两个账号，再次研究后仍保留核验状态及原始出处；这只核验公开账号链接，不证明观点或政治归属。新包普通正式后台的四起点研究耗时 **515.047 秒**：访问 8 账号，研究快照含 **80 条材料、15 条公开关系**，正式库净新增 **23 条**。真实 `deepseek-flash` 分 **2 批**审阅 **103/103 条唯一证据摘录**，待审 **0**；10 项身份或组织结果保持待复核。采集因账号预算以部分完成结束，AI 审阅完成不代表完整历史或组织网络已查明。

[本轮分批分析与实际成品验收](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/ANALYSIS_COVERAGE_ACCEPTANCE_20261009.md>)；[机器记录](<C:/Users/wwx/Documents/New project 7/output/analysis-coverage-20261009/analysis-coverage-delivery-acceptance.json>)。 Windows ZIP SHA256 `c5749f0822537702423b5aedf3a58931fcd298df80ecb37fa9c2972c0e790700`；Mac ZIP `10da5f54aacdaf7f6689bd23eb67c39fc3aaeb0a16f834314dd01a2a499666d9`。Mac 原生启动和 Apple codesign、实机 PostgreSQL 并发、大规模实采及 24 小时耐久性仍待分别验证。

## 此前日常可靠性版本的交付记录

**此前 2026-10-08 交付为 `-daily-candidate`，Windows x64 与 Apple Silicon。** 该轮修复草稿和文件迟到覆盖、多窗口版本冲突、单任务／任务串重复重试、已保存结果的终态补写、续查排队状态、AI 配置超时与坏配置启动，以及恢复后损坏停止原因导致的重试崩溃。以下测试数量、哈希和目标状态均属于当时的历史交付。

[实际 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-daily-candidate-20261008-115220/PEACE ATLAS.exe>)；[双击启动入口](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[Windows ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-daily-candidate.zip>)；[Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-daily-candidate.zip>)。正常使用不需另装 Python、Node.js 或 Docker。

最终 **848 后端、105 前端 Node、61 浏览器组、59 桌面测试、11 构建检查通过**。新的 Windows EXE 真正验证系统加密配置恢复、备份下载和研究库恢复；包内后台另通过 15 项日常可靠性、14 项完整流程及 4 种进程退出检查。两平台源码、manifest、ZIP 与 Windows portable 字节一致。所有操作使用隔离 profile；文件恢复的操作系统选择／确认采用验收注入，未新增外部平台实采或真实 AI 调用。

[本轮验收与边界](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/DAILY_RELIABILITY_ACCEPTANCE_20261008.md>)；[机器可读记录](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/daily-acceptance.json>)。Windows ZIP SHA256 `008784e073b69d78226f65a35e433a49fa8f761038382707614c34c967496653`；Mac ZIP SHA256 `099dcd95c4dd62e451e0c4f32fc5a8d3127f900cb6ee9974fd9638d782d78906`。Mac 原生启动／Apple codesign、实机 PostgreSQL 并发、10,000 条实际产量和 24 小时耐久性仍待验证，整体产品目标未标记完成；交付时目标面板显示暂停。

## 此前恢复版本的交付记录

**此前 2026-10-08 恢复交付为 `-recovery-candidate`，仅 Windows x64 与 Apple Silicon。** 设置页新增“恢复研究备份”：只读预检、原生确认、旧库自动归档、恢复后手动重试未完成任务，异常恢复保留事务证据。修复关闭抽屉后迟到响应、连续关闭窗口、Python 正常退出及损坏备份的实际消费错误。子代理统一使用 **GPT-6.1 Sol Ultra**。

当时 [Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-recovery-candidate-20261008-110543/PEACE ATLAS.exe>) 对应历史恢复候选包。[Windows ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-recovery-candidate.zip>) 与 [Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-recovery-candidate.zip>) 的验收仅适用于该历史版本。

本轮 **816 项后端、102 项前端 Node、47 组离线 Chromium、44 项桌面测试、11 项构建清单检查通过**。新 Windows EXE 原生恢复和独立 SQLite 21 表比对通过；应用与前后两个后台退出均为 0，ready 与事务记录无残留。包内私有 Python 另通过 14 项完整流程及 4 种进程退出场景。恢复使用隔离确定性样本，文件选择与确认采用验收注入，没有新增外部平台实采或真实 AI 验收。

文档：[恢复验收](docs/RECOVERY_ACCEPTANCE_20261008.md)、[备份与恢复方法](docs/DATA_BACKUP.md)、[机器可读记录](<C:/Users/wwx/Documents/New project 7/output/recovery-hardening-20261008/recovery-acceptance.json>)。Windows ZIP SHA256：`e62500c77ace2226229147f876d788801a81cb9955ad3e16ce12f4665d699259`；Mac ZIP SHA256：`00bafe9ab26bc16700b0cc789b569cc55edb2332eb20f14431991ee7940a0249`。上述通过不代表全部平台或 24 小时耐久性已测完。

上一轮 `-product-candidate` 新闻新增、去重、证据报告与在线备份的实采记录见[此前产品验收](docs/PRODUCT_ACCEPTANCE_20261008.md)。旧包和失败记录保留，上一轮测试数字不替代本轮成品验证。

日本和平与反战运动公开信息研究工作台。V2 将公开材料、来源账号、分析报告与任务集中到一个本地界面，研究结论可以回到原始来源和生成时的证据快照。

**当前默认入口是 V2。** `analysis-worker/app/main.py` 加载新工作台；旧版服务保留为 `legacy_main.py`，未挂载到新界面。旧版 Docker、n8n、Instagram 登录助手和历史采集器不属于 V2 的必需启动链路。

## 当前实现与边界

| 页面 / 能力 | 当前实现 |
| --- | --- |
| 情报总览 | 根据已保存资料展示数量、主题、发布日期分布、来源、地点提及及任务记录；没有示例数据填充统计。地点提及不等于已核实事件地点。 |
| 研究工作台 | 统一创建研究或采集、继续草稿和查看已有研究结果。工作内容（材料采集 / 关系探索）与执行安排（单次 / 连续任务串）分别选择，预算与输入保留。 |
| 账号关系图 | 独立 `#connections` 入口；显示已保存的公开关系依据，可拖拽、缩放和查看来源，新增数据自动刷新并保留布局；可以直接编辑账号或从选中账号发起研究。 |
| 资料库 | 新闻与社媒 JSON 导入、链接与内容去重、关键词 / 类型 / 主题 / 来源 / 日本日期筛选、分页、正文和原始来源查看。 |
| 组织与账号 | 新增、编辑、标签、公开来源自动核验、人工复核和排除；自动核验要求独立网页准确链接该账号，失败保留候选。导入及 AI 候选不授予核验身份，排除保留记录。 |
| 新闻采集 | 公开 RSS / Atom、Google News RSS 检索，带日期筛选、限时处理及来源诊断；不保证完整历史覆盖或完整原文。 |
| X / Instagram | 独立平台会话、已有登录检测与复用、系统默认浏览器登录入口；关键词 / 标签 / 指定账号采集、有限滚动与日期校验、相关性筛选、作者关联和公开关注关系。登录、采集及失败原因分别验证和记录。 |
| 深度研究与关系网络 | 从分组关键词或账号出发，结合所选新闻 / X / Instagram，沿公开关注、被关注、提及逐层探索；按证据相关性安排下一步，保存发现树、关系图、来源、决策及续查断点。共同链接、公开自述、AI 隶属候选分开呈现，不把关注当作隶属。 |
| 批量任务串 | 按关键词组和日期段生成可审查的子任务预览，一次提交整串；总体材料目标 1 至 10,000，单次最多 50 个子任务。每个子任务仍有独立预算，任务中心显示实际新增、结束与成功数量，可取消及重试未完成任务。 |
| 采集草稿 | 输入同步保存在本机及服务端，关闭、Escape、刷新后可恢复；误点弹窗外不会丢输入，多窗口同步采用版本校验，冲突保留双方输入并明确提示。任务创建确认成功后才清除该草稿。 |
| 研究报告 | “证据整理”按本地规则汇编资料；“AI 分析”使用配置模型全文阅读与复核，可选 DSH 工具调查。研究入口满足就绪条件时默认 DSH/120 分钟；工具仅查本次保存证据，记录随报告保存。失败保留阅读与取证断点，可调整调查方式和次数续跑。保存 Markdown 和输入快照，可阅读及下载。 |
| 任务中心 | 查看已提交工作的持久队列、任务串、子任务、进度、诊断、取消和重试；创建工作跳到研究工作台。区分成功、部分完成、失败、取消、中断，重试创建新记录。刷新页面不会丢失后台任务，退出整个应用会停止服务。 |
| 能力与设置 | 显示能力边界和 AI 配置状态。桌面版可保存系统加密的 AI 连接配置、打开数据及日志目录；浏览器页面不保存密钥。SQLite 可在线下载一致的研究数据库备份，桌面可选择备份并原生确认恢复，自动归档旧库；会话和密钥不在研究备份中。 |

以下段落保留 2026-10-06 及此前验收历史，具体旧包路径和旧测试数字不作为当前交付结果。字体改版的 8 组应用路由检查与当时 Windows 成品实际字体 / 排版检查通过，见[字体验收](docs/EVA_TYPOGRAPHY_ACCEPTANCE_20261006.md)。此前工作台流程改版前端 **79 项 Node、30 组离线 Chromium 流程检查通过**，含 8 组应用路由与跨页面检查。后端 35 个分发源文件与此前研究包逐项哈希一致；**654 项后端回归是该轮的上一轮基线，当时未重跑**。该版本 Windows 成品 smoke-3 实际退出 0，后台正常关闭，ready 文件 0，后台日志未见 ERROR；实际检查工作台、独立内容/执行选择、草稿恢复和已有关系图截图。

此前工作台流程改版的实际执行记录：实际创建默认标题任务串，两项新闻子任务均 `partial / record_budget`，各 `fetched:1 / inserted:0 / duplicates:1`，总体新增 0、重复 2。两次 X 自动检查均验证工作台已有会话可用，无新登录。该验收不增加 X 关键词正向命中、Instagram 深度实采或 1,000 条实际产量证明。

[此前工作台改版验收](docs/WORKSPACE_ACCEPTANCE_20261006.md)保留该轮检查和此前验收脚本问题。

此前预算版本：[Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-budget-candidate-20261006-080033/PEACE ATLAS.exe>)、[Windows ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-budget-candidate.zip>) 和 [Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-budget-candidate.zip>) 留档。完整历史见[预算与任务串验收](docs/BUDGET_ACCEPTANCE_20261006.md)；当前成品使用本文顶部的最新入口；此段预算包属于历史。

此前真实 X 指定账号研究保留了 **3 个账号、2 条公开关注关系、3 条素材**，按账号预算停止并保存为部分完成。真实 Google News 研究保存 **5 条素材**，已有 DeepSeek V4 Pro 实际参与决策和证据分析；模型提出的 2 个后续检索词因记录预算已满而未继续执行。关键词方案的真实 AI 扩展返回 6 组输入和 18 个建议。X 单词“反戦”检索本轮读到的 6 条正文都偏离查询，全部拒绝入库，这是加载与排除验证，不能称为正向命中成功。当前正式目录缺少可复用 Instagram 会话；上一轮隔离 QA 的 Instagram 标签实采仍保留为历史证据，本轮深度 Instagram 适配仅有离线验证。

X 的关键词 / 标签模式使用最新检索结果；指定账号模式直接读取主页。各模式逐帧提取主帖，独立核对正文、作者和日期，排除引用文字串位与重复；连续无新结果时停止，不保证完整历史。**深度研究围绕当前用户关键词 / 研究目标，劳动、学生等主题不再先被历史和平词表剔除。** 原主题直接匹配的正文保留检索依据；未直接匹配的拓展内容需通过 AI 语义复核，并校验其引用 ID 和正文原样片段。简单采集的“研究相关”仍采用日本和平 / 反战词表，“按检索条件”用于较宽探索。Instagram 关键词模式先发现账号再读取公开帖子，不是全文搜索。图片 alt、编辑时间、评论时间不代替正文和原帖发布日期。自动发现和 AI 候选不修改账号核验状态。

日期筛选统一使用 **Asia/Tokyo（UTC+9）**，包含结束当天。发布时间未知的记录明确标注，不补造日期。规则词表命中、材料数量、账号身份核验，都不自动等于报道主张已证实。

## 使用方式

### 关键词、草稿、深度研究与任务串

在“研究工作台”选择“采集公开材料”或“探索账号与关系”，再在表单中选择“单次执行”或“连续任务串”。任务串是执行安排，两种工作都可使用。任务中心负责进度、诊断、取消和重试。输入一个主题后会显示相关建议；“生成相关词”使用本地词表，“AI 扩展研究词”使用已有模型。建议点击后才加入输入，可撤销，不自动替换原关键词。

关键词组用**逗号 `,` / `，`、分号 `;` / `；` 或换行**分隔；**同一组内空格表示同时包含**。例如 `革命, 労働者, 学生運動` 是三组，“任一组”匹配任一入口；`沖縄 平和, 労働者` 的第一组要求同时有“沖縄”和“平和”。“所有组”要求全部组同时成立。提交前查看检索预览，日期和账号使用对应字段，不在文本里填写 `from:`、`OR` 等运算符。

表单修改自动保存。误点弹窗外不会关闭；主动关闭、Escape 或刷新后，重新打开会恢复草稿。多窗口修改有同步冲突提示，当前输入不会被静默覆盖。看到任务创建成功后，草稿才清除；保存失败或创建失败时，保留输入并显示实际状态。

深度研究勾选“从哪些来源开始探索？”，设置日期、深度、账号、关系、记录和时间预算。后台逐分支保存材料，任务详情可进入发现树、关系网络和原始证据，查看每一步的理由、实际结果及是否使用 AI。未选中的社媒平台只保留新闻中的显式链接线索，不启动该平台采集。达到预算的任务是部分完成，失败页面与真实空结果分开表示；保留的前沿可明确续查。

选择连续任务串后先设置总体目标，再点击主按钮“预览任务串”，预览匹配当前输入后点击“创建研究任务串”或“创建采集任务串”。预览无需先登录且不会入队；修改参数需重新预览，无法创建时按钮旁说明原因。研究子任务保存范围1–10000条、滚动1–200次、运行30–86400秒，新任务默认50条／10次／1800秒；简单采集子任务最多100条。任务串总体最多10000条、单次计划最多50个子任务，全局队列与历史分页。预览显示整串时间上限，后台依次执行；达到目标或来源耗尽即停止，不保证凑满有效材料。

### 社媒登录与已有会话复用

在“能力与设置”的平台卡片中选择“检测并复用已有登录”，工作台按以下顺序处理：

1. 访问平台首页，验证当前工作台保存的该平台会话。有效时直接使用，不要求再次登录。
2. 会话缺失或明确失效时，检查本机注册的默认 Firefox 配置，只读取所选平台的必要会话字段。验证平台首页确实已登录后，才备份并更新当前工作台数据目录中的会话。默认配置、容器、分区或重复作用域造成身份不明确时停止，不自动切换账号；X 也保留手动选择注册 Firefox 配置的入口。配置列表本身只读取名称、默认标记和文件是否可用，不读取 Cookie。
3. 没有可复用的会话时，界面请求**系统默认浏览器**打开固定的 X 或 Instagram 官方页面。浏览器由操作系统设置决定；完成平台登录后，返回工作台再次检测或同步。打开页面只表示请求已发出，不会标记登录成功，也不会自动获得 Chrome / Edge 的会话。

平台要求额外验证、限流或验证运行时不可用时，自动检查停止并显示诊断，不反复发起登录。检测和导入任务使用独立队列，结果可在任务中心查看。此流程不读取密码或其他站点的会话，不复制整个浏览器配置，也不修改 Firefox 的登录状态。采集和首页验证仍使用工作台自己的浏览器运行时；它不是默认登录窗口。

Windows 正式应用目录 `AppData/Roaming/PEACE ATLAS` 已完成 X 两次真实检查：首次复用 Firefox 会话并验证保存成功，第二次直接验证工作台已存会话，无需新的可见登录。Instagram 的 Firefox 自动复用已实现；当前正式目录的真实检查发现默认 Firefox 没有可复用 IG 会话，准确返回需登录，未写入会话，也未新开登录窗口，尚无该入口导入成功的实证。此前 QA 的 Instagram 实采通过不代替这项验证。Windows / macOS 的 Firefox 注册配置发现已实现，macOS 会话复用尚未实机验收。

Chrome / Edge 提供独立的可选 [会话桥接扩展源码与说明](browser-extension/README.md)。首次由用户连接工作台后，扩展通过浏览器正式 Cookie API，仅将 X 或 Instagram 必要的会话字段发送到配对的本机 `127.0.0.1` 工作台；后端验证成功后才保存。扩展不读取密码、不解密浏览器数据库、不向外网发送会话，也不保存 Cookie 值。当前扩展已通过 75 项离线测试及 8 组后端格式兼容检查，尚未安装或完成 Chrome / Edge 实机验收；当前 Firefox 复用链路不依赖该扩展。

### 桌面应用

面向最终用户的交付方式是包含独立后端运行时的桌面包，正常使用不需要另外安装 Python、Node.js 或 Docker。Windows 和 macOS 的**具体包路径、签名及本机启动验收状态，由发布记录逐项填写**，不以“构建出 ZIP”替代运行验收。

- Windows：完整解压发布包后运行 `PEACE ATLAS.exe`。
- macOS：本轮仅交付 Apple Silicon，Intel 历史包留档、不再更新。当前构建流程涉及原生签名收尾，不能将待签名 staging 包当成已完成双击验收的应用；实机验收按用户安排稍后进行。
- 构建方法与平台限制：[desktop/BUILDING.md](desktop/BUILDING.md)。
- 发布验收与证据记录：[docs/V2_ACCEPTANCE.md](docs/V2_ACCEPTANCE.md)。

### 从源码启动（开发 / 调试）

建议 Python 3.12，在仓库根目录创建独立虚拟环境。下面安装 V2 后端所需依赖，不要求安装历史浏览器采集器。

Windows PowerShell：

```powershell
python -m venv .venv-v2
.\.venv-v2\Scripts\python.exe -m pip install -r desktop/requirements-runtime.txt
.\.venv-v2\Scripts\python.exe -m playwright install chromium
.\.venv-v2\Scripts\python.exe scripts/run_workbench.py
```

macOS / Linux：

```bash
python3 -m venv .venv-v2
.venv-v2/bin/python -m pip install -r desktop/requirements-runtime.txt
.venv-v2/bin/python -m playwright install chromium
.venv-v2/bin/python scripts/run_workbench.py
```

访问 [http://127.0.0.1:18090](http://127.0.0.1:18090)，按 `Ctrl+C` 停止。可用 `--port 18091` 更换端口，或用 `--data ./output/v2-check.sqlite3` 指定独立测试库。上述 Chromium 用于采集和会话验证；需要用户登录时仍打开系统默认浏览器。

开发启动器只从现有本地 `.env` 补充 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`DEEPSEEK_TIMEOUT_SECONDS`，已设置的进程环境变量优先；它不会执行环境文件中的代码。桌面包不携带项目 `.env`、历史登录态或用户数据。

## 数据与 AI 配置

- 源码启动默认使用 `analysis-worker/data/workbench.sqlite3`；桌面应用使用独立用户数据目录，通过“能力与设置 → 打开数据目录”定位。
- V2 数据表使用 `wb_*` 前缀。源码 / 服务端部署可通过 `WORKBENCH_DATABASE_URL` 选择 PostgreSQL，需额外安装兼容的 `psycopg` 驱动；桌面包固定使用本地 SQLite。
- 桌面 AI 默认提供 DeepSeek 地址和初始模型建议，实际使用以已有配置及服务方当前支持的模型为准，不自动替换用户保存的模型。密钥由用户配置；保存连接不会触发分析，生成 AI 报告会发送选中材料。AI 请求有时限及输出边界，超时、连接失败或证据校验失败会明确报错。
- API Key 输入留空表示保留已保存值，清除需要明确勾选；系统加密存储不可用时不保存密钥。配置就绪不等于模型调用已通过实测。
- 备份 SQLite 时先退出应用，避免只复制正在写入的主数据库而漏掉 WAL。建议关闭应用后备份整个用户数据目录；密钥文件不应随研究资料分享。

### 旧版资料迁移

同级 `collectors/data/collector.db` 有明确的只读迁移入口：

```powershell
python scripts/migrate_collectors.py --source ../collectors/data/collector.db --destination ./analysis-worker/data/workbench.sqlite3 --output ./output/social-migration.json
```

迁移前自动备份已有目标 SQLite；原库不写入，账号按平台 / 规范用户名映射，帖子按原帖 URL 去重，可重复执行。旧日期缺失、作者未关联和旧文本提取方式不明都会保留，旧 AI 结果不用于自动核验。重采同一帖时可补全已确认的缺失字段，保留旧版本和历史报告快照。登录会话与模型密钥不随资料迁移，也不进入安装包。

旧版 PostgreSQL 和历史报告尚未自动迁移。新库为空不表示旧数据被删除，修改数据库连接也不会自动转换旧表。

建议先保留旧库备份，只读导出必要公开材料，转换成 V2 资料 JSON，通过“导入资料”进入独立测试库。至少核对 `title`、`url`、`content`、`type`、`source_name`、`published_at`，先小批量检查新增、重复、拒绝数量和原文链接。

旧账号 ID 不能直接充当 V2 UUID。应先建立新账号及 ID 对照，再关联资料；核验状态和依据需要复核，不能靠导入字段升级为“已核验”。原始时间所属时区不明时应保留待核对标记。旧 Markdown 报告继续作为历史档案保存，不伪造为具有 V2 输入快照的新报告。通过对账后再讨论正式切换，不覆盖或删除旧库。

## 开发结构与验证

外部 AI Agent 可以通过随包 `peace-atlas` Skill 与本机 `/agents/v1` 接口读取资料、规划研究、创建报告及整理可恢复资料。接口默认关闭，桌面“外部 Agent”菜单导出独立授权连接后才能操作；不共享 AI Key 或社媒会话。公开 manifest/schema 提供发现与操作契约，安装与权限分别处理。安装入口和宿主目录见 [Agent 安装说明](analysis-worker/app/workbench/agent_assets/INSTALL.md)，完整流程与参数见 [操作说明书](docs/USER_MANUAL_20261010_AGENT.md)。

长研究的账号、关系、材料和总时间默认 0，表示按日期、层数及单账号范围完成；开始和续查先预估再确认。报告可不额外限制总时长及 DSH 决策/工具次数；单次网络超时和实际上下文容量仍有效。已复核的同范围证据笔记可复用，新报告仍独立核对。相关实现与隔离验收记录见 [研究范围验收](docs/RESEARCH_SCOPE_ACCEPTANCE_20261010.md)。新交付包及真实模型结果须按各自验收记录判断。

| 路径 | 职责 |
| --- | --- |
| `analysis-worker/app/workbench/server.py` | `/api/v2`、输入验证、本地访问边界及生命周期 |
| `analysis-worker/app/workbench/store.py` | SQLite / PostgreSQL 存储、去重、账号、任务、报告快照 |
| `analysis-worker/app/workbench/pipeline.py` | 内容清理与规则分析、RSS 采集、报告生成 |
| `analysis-worker/app/workbench/jobs.py` | 持久化队列、取消、恢复及错误状态 |
| `analysis-worker/app/workbench/query_plan.py` | 多关键词组合预览、本地词表与现有 AI 的可选关键词建议 |
| `analysis-worker/app/workbench/research_engine.py` | 当前主题门限、自适应公开证据探索、预算、决策轨迹和逐分支断点 |
| `analysis-worker/app/workbench/relationship_analysis.py` | 可观测关系、公开自述及受证据约束的 AI 候选 |
| `analysis-worker/app/workbench/social/` | 平台登录、增量 DOM 采集、日期 / 作者校验与安全诊断 |
| `browser-extension/` | 可选 Chrome / Edge 会话桥接源码与离线测试，安装和实机验证单独记录 |
| `analysis-worker/app/workbench/relevance.py` | 可解释的研究相关性筛选，不调用 AI |
| `analysis-worker/app/workbench/legacy_import.py` | 旧 collectors 数据只读迁移 |
| `analysis-worker/app/static/workbench/` | 无构建步骤、无运行时 CDN 的模块化前端 |
| `desktop/` | Electron 主程序、独立 Python 运行时与跨平台构建 |

安装测试依赖后，在仓库根目录运行：

```powershell
python -m pip install pytest==8.3.4
python -m pytest -q analysis-worker/tests
node --test analysis-worker/app/static/workbench/tests/*.test.mjs
```

使用上述虚拟环境时，将 `python` 替换为对应解释器路径。测试结果、真实来源采集、AI 调用和各平台桌面运行分别记录，不能互相代替。接口约定见 [docs/REBUILD_CONTRACT.md](docs/REBUILD_CONTRACT.md)。
