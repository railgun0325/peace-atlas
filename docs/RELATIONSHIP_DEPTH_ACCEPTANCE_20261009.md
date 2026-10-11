# 账号关系深度研究与自动核验验收

2026-10-09。本批正式 Windows 构建为 `relations-candidate-20261009-083814`。桌面和工作目录两个启动按钮已改指向新构建，并完成普通启动 → 包内 Python → 正式资料目录 → 公开健康状态验收。本文区分软件测试、正式升级和真实模型／平台结果；整体产品仍在持续完善。

## 本批行为

- 保留用户认可的拖动、邻居响应、碰撞整理和稳定后休眠动效；布局引擎字节未改。
- 组织、个人、未判定类型分别显示，并提供类型及依据的人工纠正。类型、核验、研究相关性和图结构层级是四个不同维度。
- 关注起点、被关注方向、1–3 跳范围和最短证据链可分别强调。每一步保留实际保存的箭头、关系和公开来源；逆向查看不创造反向关系。
- 重点、桥接、外围来自当前有来源的采样图。相邻两个组织账号只能提示待核对的连接，不能自动证明属于两个不同组织或存在现实合作。
- 指定账号研究已启用 AI 候选排序。建议必须引用该候选的已保存依据，整批校验后保存理由，再读取下一节点；未知账号、不相关引用及部分无效批次整批拒绝，失败回到规则排序。
- 精确匹配 YouTube 等六个通用平台账号，默认隐藏、不参与结构排名、不继续自动展开；原始关注证据仍保留。人工纳入可以恢复跳过的分支，已经完成的分支不重读；人工排除优先。
- 保存或导入新公开依据后自动排队核验，使用独立 worker、缓存、持久化状态和显式失败重试。GET 不创建任务。可信官网准确回链才能授予核验；简介自称、蓝标和模型判断不能直接授予核验。
- 共享官网不传播另一账号的组织／个人类型；排队核验取消后正确保存取消状态；并发人工修改及旧尝试不会覆盖新决定。
- 修复提高研究预算后仍被旧祖先任务上限阻止正常重试的问题，保留当前上限、活动任务和不同范围的拒绝规则。

设计取舍、官方参考和已实现契约详见[关系深度设计](RELATIONSHIP_DEPTH_DESIGN_20261009.md)。当前单任务仍限定 3 层、40 账号、200 条关系；从重要节点预填新研究可进一步追查。图中的层级和距离不能作为个人政治立场或组织隶属结论。

## 测试和实际交付

| 范围 | 本批实际结果 |
| --- | --- |
| 完整后台回归 | 2,043 passed，0 failed，344.93 秒；1 个既有 Pydantic 字段元数据警告 |
| 完整前端逻辑 | 162 passed，0 failed／skip，80.651847 秒 |
| 桌面流程 | 新运行 59 passed，0 failed，9.578402 秒 |
| 构建脚本 | 新运行 11 passed，0 failed，0.25 秒 |
| 浏览器 | 使用受控合成接口响应，新功能 8 组、原研究 4 组、原分析 31 个用例通过；页面错误为 0 |
| 独立核心整合 | 21 个隔离流程通过；97 个相关回归通过，和完整后台不重复相加 |
| 包内核心流程 | 新包私有 Python 14 项离线流程通过，外部模型／平台调用为 0 |
| 原生 Windows | 隔离资料目录实际启动、界面、备份及正常退出通过 |
| 原生旧备份恢复 | 固定旧版 21 表备份恢复到 22 表，逐表核对通过；原备份字节不变；未手动操作系统文件选择器 |
| 资料及退出 | 7 个隔离数据库完整性正常、外键错误 0；本批隔离进程、监听及 ready 标记均已退出 |

后台完整数量已包含本轮新增 174 个用例；分项 424、97、94 等不能再与全量数量叠加。完整测试绑定及日志见[源码测试收据](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/delivery/source-tests.json>)，浏览器见[前端收据](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/frontend/frontend-acceptance.json>)。

冻结联合清单为 104 个应用文件，Windows 103、Mac 104。Windows ZIP 的 5,721 个文件通过 CRC，并与便携目录逐项核对 SHA；Mac 4,262 个普通文件和 29 个符号链接通过独立检查。Mac 的 44 个签名二进制、47 个切片及代码页／资源封套也已独立核对。Mac 原生启动及 Apple 原生签名策略仍未执行。详见[包审计说明](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/delivery/package-receipt.md>)。

正式升级前后 22 张表全部 SQLite 类型、值、列定义及既有 schema 精确相同，保留 551 条材料、61 个账号、41 个任务、1 份报告和 6 个研究记录。这是升级完成、任何本轮有意续分析之前的快照，不是后续活动任务数的实时估计。[正式行与结构保留收据](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/delivery/formal-upgrade-preservation.json>)及[完整交付收据](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/delivery/local-release-acceptance.json>)已保存。

## 真实公开来源和模型的边界

三个官网实际尝试未取得成功 HTTP 页面，遇到 DNS 或超时，因此没有新增真实官网核验成功。该公网收据发生在最后两项核验边界修复前，不能代替当前源码的成功公网验收。已有保存快照中的全学联及关西账号，通过新正常账号接口仍保持已核验组织类型；读取没有改动原始行或新增网络请求。详见[核验发现说明](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/findings_verification.md>)。

本轮升级前真实研究已有 40 个账号、413 条材料和 155 条关系，DeepSeek Flash 在当前上下文中完成 3 个批次，已接受 168／603 条证据，435 条待处理。此前 80／363 的上下文和批次 ID 不同，不能把批次数净增 2 当作本次成功请求数，也不能声称沿用了那个旧批次。最后保存失败类别为 `provider_request`，没有具体 HTTP 状态，不能据此推断当前余额。待续分析的严格引用、身份和原文复核门槛保留。

升级后分析重试助手已在真实终态的自有克隆中通过 15 个行为检查。首次正式只读 review 通过，提交前新增正常登录任务，助手因此拒绝提交，没有创建分析任务或 reservation。随后实际比对完整快照，确认新增登录记录及草稿保存，其他 18 表和全部旧任务、研究、分析精确不变；保留原草稿后重新建立完整 22 表基线，新助手 16 个行为检查通过。正式正常重试已提交一次，新任务 `02fe2f6d-38cd-4607-b6c1-d6caf3c73328` 为同一研究的 `_analysis_only=true`。提交时原材料、完整审核断点、来源 hashes 和历史证据保持原值，仅更新原研究的队列挂接字段，并新增一个任务、一个 retry event 和一个 alias。后续 worker 的真实模型结果另列，不把排队、预演或普通启动当成模型成功。

这次真实 worker 复用同一上下文的原 3 个批次并完成 1 个新批次，已接受从 168 增至 212／603，391 条待处理。原 3 个完整批次对象逐值相同，正式材料、材料历史版本及独立报告表保持原值。随后应用退出，任务已标记为 interrupted，最后请求记录为 request_cancelled，未完成最终分析或最终模型报告。两个自有一致性快照中的关系 ID 全集相同，均为 156 条；采集 summary 从 155 刷新至 156 不代表新增边。[实际模型及来源保留收据](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/qa/native-upgraded-analysis-retry/actual-model-outcome-212.json>)记录了这些边界，正常退出时保留断点。

## 可用入口与保留文件

- [桌面同用途的双击入口](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)
- [当前 Windows 应用](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261009-083814/PEACE ATLAS.exe>)
- [冻结 Windows ZIP](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/delivery/retained-083814/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)，SHA256 `ce87af4593c338d8cb05e93793a8cfc76b18686bafa5716e5f23a5a392fe34cf`
- [冻结 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/delivery/retained-083814/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)，SHA256 `86525a559ae382e25d18ca6a8673eb2b38dac556c86f018316702d4cfa3581a3`

旧构建、旧材料、失败日志和以前的验收文件均保留。手动资料清理继续使用预览、回收站、恢复和选中回收站资料的明确彻底删除流程；本批没有删除正式资料或改写已有独立报告。
