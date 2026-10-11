# PEACE ATLAS 基础架构复审

复审日期：2026-10-11。本文说明产品与工程方向；具体发布文件、真实运行结果和验证边界统一记录在 [本轮发布验收](RELEASE_20261011.md)。分阶段实施与验收见 [路线图](ROADMAP.md)。

本文的现状判断以 `review-assurance` 冻结版本为基线。后续已实现的跨任务网页复用、报告固定输入恢复及其最新验证范围见 [第一批实现记录](FOUNDATION_IMPLEMENTATION_20261011.md)；下文保留原基线，便于比较改进前后的行为。

**结论：保留 SQLite、现有工作台 UI、来源快照与审核事务，继续使用 DSH 执行受限模型工具循环；优先补齐证据分层、跨任务复用、问题缺口规划和单项恢复，暂不整体重写。** 已有开源软件是可比较的实现候选，不能代替本产品的收益证据。

## 1. 产品目标与当前边界

长期目标是建立持续维护的公开证据研究系统：从明确问题出发，先检索与复用已有证据，再增量补齐缺口，在可恢复的里程碑记录新增信息、剩余不确定性与实际成本。它应同时改善有效信息获取、调查决策、论证质量和任务可靠性。

“任务完成”表示本次指定工作已处理并保存，不表示所有候选得到事实确认。`unknown` 与 `inconclusive` 可以是正确终态；其价值取决于是否保留了已知信息、具体缺口和可执行的后续动作。增加材料数、账号数、模型轮数或测试数，都不能单独证明研究质量提高。

| 截至本轮复审已实现 | 本文提出但尚未实现的架构升级 |
|---|---|
| v2 workbench 默认入口；资料、账号、研究与报告管理 | 跨任务共享的来源捕获、阅读结果与命题依赖层 |
| SQLite/WAL 桌面存储，PostgreSQL 可选；资料 URL 去重与修订记录 | 跨研究的采集覆盖、刷新窗口与增量获取契约 |
| 研究分支 checkpoint、前沿/visited、待做 profile/posts/neighbors、显式续查 | 问题清单、竞争解释、证据缺口及基于边际收益的行动规划 |
| 原始资料、实际关系观察、候选主张与人工审核分别保存 | 账号声明类型/用途与确认身份的独立字段；活动来源说法、日期精度与统计资格 |
| DSH 受限工具；来源 hash、原文片段、逐字引用与身份/限定守卫 | 有标注基线的全文检索、跨语言召回、正文提取 A/B 对照 |
| 持久任务队列、取消/退出保护、已完成审核批次与进度原子保存 | 单项 work ledger、依赖局部失效、资源分类调度、worker lease |
| Agent API/CLI 的 read/run/cleanup/review 范围、撤销及操作预览 | 调用同一业务合同的薄 MCP adapter |

本轮已有冻结源码回归收据为 **2939 项：2688 后台、172 前端、62 桌面、17 构建检查**。这保护了大量当前行为与约束；它不证明上述规划已实施，也不代替真实平台、真实模型、检索召回、性能或耐久性验收。详见 [发布验收](RELEASE_20261011.md)。

## 2. 当前数据流与保留价值

研究从用户选择的日期、平台、关键词或账号起点建立查询和前沿。适配器取得公开资料，筛选后形成资料、账号和观察关系；每个分支导入资料库并保存研究版本。关系分析生成候选主张。报告读取选定资料或研究快照，开展公开正文读取、来源审阅、证据工具调查和综合写作。自动审核另行冻结待审记录，经工具调查、逐字引用和确定性校验后，在事务中保存决定与进度。

代码入口：[main.py](../analysis-worker/app/main.py)、[研究引擎](../analysis-worker/app/workbench/research_engine.py)、[研究存储](../analysis-worker/app/workbench/research_repository.py)、[任务执行](../analysis-worker/app/workbench/jobs.py)、[自动审核](../analysis-worker/app/workbench/automated_review.py)。

应保留的基础包括：

- 新闻按出版方 URL 保留来源，内容重复簇另记；资料补齐与修订不会把所有相同文字变成一个出处。
- 研究账号先作为候选，实际关注关系不自动推得政治支持、组织隶属或成员资格；人工锁定结果和历史观察得到保护。
- 证据网关绑定来源版本与快照，保存原文 hash、片段偏移和引用核对；模型工具请求及内部推理不充当成功证据。
- 保存、取消、退出和审核进度已有事务及恢复约束。升级应复用这些守卫，避免产生第二套相互矛盾的业务真值。

相关实现：[Store](../analysis-worker/app/workbench/store.py)、[证据网关](../analysis-worker/app/workbench/harness_evidence.py)、[Harness](../analysis-worker/app/workbench/harness_runtime.py)、[审核合同](../analysis-worker/app/workbench/review.py)。

## 3. 优先瓶颈与基础设计

### 3.1 资料去重之后，还需要工作去重

目前全局资料库可以消除重复存储，但研究节点和主张身份以研究运行作为边界。自动审核的网页和账号搜索缓存仅在本次运行内共享；显式 retry 会重新建立缓存。报告可复用 parent checkpoint 的已完成片段，仍没有跨任务来源阅读资产。

本轮复审确认，主张身份包含研究运行 ID，相同来源与命题在不同研究中仍可重复进入审核。不同观察和限定应保留，但不能按记录数算作独立证据；对象限定不同也不能粗暴合并。依据为 [主张保存](../analysis-worker/app/workbench/research_repository.py#L816)、[审核运行](../analysis-worker/app/workbench/automated_review.py#L371)、[来源阅读](../analysis-worker/app/workbench/report_analysis.py#L552)，实际运行验收见 [发布记录](RELEASE_20261011.md)。

规划建立共享来源层：规范 URL、捕获时间、capture hash、提取器及版本、完整性、失败原因与重试时刻。原文、片段和可复用事实按来源版本保存；目标相关性与最终判断按问题、命题和策略版本保存。缓存只复用完成且仍适用的结果；原文变化、限定变化或冲突使依赖结果失效。独立来源簇、转载簇和采集次数分别统计。

### 3.2 增量采集需要覆盖契约

现有续查已经跳过完成的研究分支，但中断查询仍会重读保存前缀，时间线也重新请求包含已读前缀的范围。新研究没有共享已覆盖区间与刷新策略，因此存储去重无法退还浏览器时间。见 [查询恢复](../analysis-worker/app/workbench/research_engine.py#L1043)、[账号时间线](../analysis-worker/app/workbench/research_engine.py#L1232)。

规划保存平台/账号/查询的已知帖子 ID、已读日期区间、扫描边界和覆盖状态。平台提供稳定 cursor 时才使用 cursor；DOM 页面没有该能力时采用重叠窗口和连续已知内容停止，同时明确未读尾部与日期缺口。新研究先取现有资料，再补缺口和刷新近期窗口。关注列表属于易变观察，保存时间版本，不能永久免采。

### 3.3 严格事实确认与来源分类应分别建模

当前 SYSTEM 口头区分账号类型、身份核验和组织关系，却要求 `organization/individual` 类型也具备独立主体网页和准确主页回链；validator 执行这一硬条件。`account_identity` 只有一个类型槽。官方核验另有状态，因此不是同一数据库字段，但自动类型判定共用了接近官方归属核验的证据门槛。见 [审核要求](../analysis-worker/app/workbench/automated_review.py#L20)、[类型校验](../analysis-worker/app/workbench/automated_review.py#L241)、[身份投影](../analysis-worker/app/workbench/account_identity.py#L10)。

这一合同使“原文已有明确自述类型，但实际身份缺少独立核验”仍只能投影为 unknown，缺少准确保存来源声明的位置。这是本轮实际核对出的信息表达缺口。只有显示名或含糊简介仍应保留未知；不能假定所有 unknown 都能确认，也不能为增加完成数量放宽真值要求。真实审核统计留在发布验收，本文不包含账号样本信息。

规划将几个独立问题分别保存：

| 字段层次 | 可保存内容 | 不得扩大的结论 |
|---|---|---|
| 来源声明 `declared_type` | 明确原文自称组织、个人或未知，附引用、来源版本与时刻，标记 self-described | 不能把自称当实际主体、官方账号或成员身份 |
| 观察用途 `observed_purpose` | 从公开原帖能直接描述的用途，附观察范围；歧义留空 | 名称、头像、蓝标、关注关系不能决定主体类型；不推测普通个人政治身份 |
| 证据支持 `supported_type` | 准确对应该账号及所声称主体的证据和支持范围 | 同名实体存在不证明账号归属；supported 不自动升级为 verified |
| 官方身份核验 | 沿用严格 verified 状态、准确回链、审核历史及限定 | 信息发布归属、组织隶属、普通个人成员资格与立场不能互相推得 |

这些不是一个自动递升的置信度分数。自述可以准确作为“来源这样说”的事实保存，同时实际主体仍未核验。未出现新原文、新来源、矛盾或更高层待解决命题时，已有声明无需反复完整调查。当前已核验状态、人工锁定和历史判断不自动回填或升级。

活动采用同一原则。当前候选只有完整年份才生成结构化 date；非 confirmed 自动结果清空结构化日期/举办地，原文仍保留。地图正确地只统计已核对、完整实际日期、已定位且去重的活动。见 [活动提取](../analysis-worker/app/workbench/activity_events.py#L26)、[活动批提交](../analysis-worker/app/workbench/automated_review.py#L357)、[地图统计](../analysis-worker/app/workbench/activity_events.py#L89)。

规划另存来源说法（计划公告、报道声称举行、取消、讨论、未知）、原始时间和精度、地点在来源中的角色、实际发生核验与 `map_eligible`。仅有月日或计划信息能提供有效待查线索；不从发布日期补年份，不把计划计为发生，不把地名提及计为举办地，不进入当前确认地图分母。`inconclusive` 应指向尚缺哪个命题的支持，不将已有信息一并视为无用。

### 3.4 下一步行动要服务于问题缺口

目前前沿按分值、深度和账号键排序；AI 可以受限重排已发现候选，但候选窗口、最近材料和固定轮数限制了视野。Harness 优先级也使用 deferred、未访问、无材料和入边等结构分值。它们不是问题覆盖、证据独立性或边际收益模型。见 [前沿与重排](../analysis-worker/app/workbench/research_engine.py#L661)、[候选上下文](../analysis-worker/app/workbench/account_exploration.py#L23)、[保存证据优先级](../analysis-worker/app/workbench/harness_evidence.py#L477)。

规划从小型问题清单开始，记录待核实命题、现有支持、反证、缺口、候选动作及预期成本。优先 source-first 检索与确定性检查，模型只处理剩余歧义与补查规划；保留少量探索额度，避免长期只看高分前缀。连续行动没有证据变化、可读来源耗尽、目标满足或达到用户资源边界时，以可解释状态停止。

早期相关性路由同样重要。未分类不等于无关；公开采集中可能进入与当前目标无关的材料。原始捕获、待判候选、目标相关资料和可用于结论的证据应分别路由。高置信无关材料保留原始出处并退出当前模型输入，歧义与反证继续保留；目标改变时可以重新评估。这是内部工作流，不新增用户审批层。

### 3.5 检索与提取先建立真实质量基线

当前工作台资料检索使用 SQL `LIKE`，证据工具在快照中做 substring；普通报告按日期取选定资料，达到数量上限时要求缩小范围。当前 v2 未接入仓库旧 Qdrant 流程。不能因为仓库存在向量模块就认定已有语义检索。见 [资料查询](../analysis-worker/app/workbench/store.py#L548)、[报告选材](../analysis-worker/app/workbench/store.py#L597)、[证据目录](../analysis-worker/app/workbench/harness_evidence.py#L391)。

先比较全文索引、主体别名、日/中/英变体和日期/来源过滤；检索结果须返回来源版本、片段和审核范围。embedding、混合检索或重排只用于已测得的漏召回类型。报告的全量阅读与按问题选材应明确区别，选材报告列出覆盖和未入选范围。

当前正文抽取有 JSON-LD、有限选择器、标题对应和受限页面检查。提取器的结果只是正文候选，必须保持页面身份、订阅/登录终态、原摘要与失败诊断；更多字符不等于更完整、更准确正文。见 [公开正文读取](../analysis-worker/app/workbench/news_reader.py)。

### 3.6 将恢复边界逐步收敛到单项工作

本轮已经补上模型传输层的最多三次尝试、共享截止时间、0.5/1 秒退避、分类诊断和每次请求计数。它允许有界重发尚待返回的模型请求，不重放已经执行的工具或已提交决定；远端可能已消耗失败请求，次数不等于成功回复数或精确账单。业务结构、引用与语义错误仍按原校验处理。这是当前修复，不能视作下面规划中的完整单项账本。

自动审核已原子保存完整批次与进度；但中间调查状态主要在 job.result/current_harness，复用依赖整份快照与 parent job。除固定证据不足降级外，字段、引用或覆盖错误仍使整批失败。当前普通 lane 同时串行执行采集、研究、报告和自动审核，长任务会增加不冲突工作的排队时间。见 [审核恢复与提交](../analysis-worker/app/workbench/automated_review.py#L371)、[Harness fingerprint](../analysis-worker/app/workbench/harness_runtime.py#L487)、[队列](../analysis-worker/app/workbench/jobs.py#L43)。

规划建立 work item 的幂等键、依赖来源 hash、状态、预算、调用收据及完成结果。模型仍可按批处理以节省上下文，合法且已校验单项能够持久保存，失败项保留具体原因。最终写入继续做记录版本比较、事务与取消检查。工具结果逐步存为可引用事件，job.result 留摘要；先测 checkpoint 字节和时延再改存储结构。

队列先按资源分类：每个平台/浏览器会话受限串行、公开 HTTP 有界并行、模型受用户预算与并发控制、短查询和保存独立调度。准备增加多 worker 前补 owner、lease、heartbeat；避免另一实例启动时中断仍执行的任务。付费调用前预留计数应保留，但它不保证外部 HTTP exactly-once；不确定的调用必须有显式状态。

## 4. 开源组件取舍

下列是 2026-10-11 的官方能力核验与工程决策，不是接入清单。实施时重新核对精确发行、依赖闭包、许可证、Windows x64/Apple Silicon 加载及原生验收。

| 组件 | 本项目决策 | 采用条件与边界 |
|---|---|---|
| Trafilatura | **A/B 候选** | 对已安全取得的 HTML 产生正文候选，与当前提取器比较；保留现有获取与身份检查，不直接替换平台采集。[官方 Python API](https://trafilatura.readthedocs.io/en/latest/usage-python.html) |
| SQLite FTS5 | **优先验证的检索候选** | 检查包内编译支持、CJK/短词与 literal `%/_` 语义；trigram 少于三个 Unicode 字符不命中，现有 `LIKE ... ESCAPE` 不能直接获得其索引优化。[官方 tokenizer 说明](https://www.sqlite.org/fts5.html#the_trigram_tokenizer) |
| LangGraph | **收益证据后再决策** | 若能替换散落阶段编排与恢复，先做可撤回试点；DSH 继续负责受限工具循环，应用库保持唯一业务真值。checkpoint 与跨任务 store 是不同层，内存 saver 不构成重启持久化。[官方持久化说明](https://docs.langchain.com/oss/python/langgraph/persistence) |
| Open Deep Research | **只参考设计** | 官方仓库已归档，不作为长期运行底座；借鉴问题分解、压缩与评估，通用 benchmark 不替代本项目日期、身份与引用测试。[官方仓库](https://github.com/langchain-ai/open_deep_research) |
| Haystack / LlamaIndex | **暂缓整体引入** | 先有混合召回、重排或引用片段的实际收益，再选最小组件；编号和 source node 不证明主张受支持。[DocumentJoiner](https://docs.haystack.deepset.ai/docs/documentjoiner)、[CitationQueryEngine](https://developers.llamaindex.ai/python/examples/query_engine/citation_query_engine/) |
| Crawl4AI / Scrapy | **按实际站点或规模瓶颈试点** | 不同时加入多个抓取体系；动态公开网页与固定站点批采分别验证，不用通用框架替代已验证平台语义。[Crawl4AI 配置](https://docs.crawl4ai.com/core/browser-crawler-config/)、[Scrapy 任务恢复](https://docs.scrapy.org/en/latest/topics/jobs.html) |
| LightRAG | **暂缓正式底座** | 模型抽取关系只作候选；新增图/向量状态与成本需独立测量，不能替代审核后的正式关系与 SQL 活动统计。[官方仓库](https://github.com/HKUDS/LightRAG) |
| 官方 MCP SDK | **薄适配候选** | 调用现有 Agent 合同；优先本地查询与状态，写操作复用既有 scope、预览与收据，不再造第二套 API。[官方 transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[Python SDK](https://github.com/modelcontextprotocol/python-sdk) |

本轮依赖核验发现 Python MCP SDK 的选定发行要求比当前桌面锁定更高的 Pydantic 版本；不可直接混装。先在隔离 adapter 进程做契约试点，再决定协调依赖或选 Node 侧 SDK。Trafilatura 新依赖也需要闭包、版权/NOTICE 与两目标原生加载审计；“有 wheel”不是已兼容证明。相关基线见 [桌面依赖](../desktop/requirements-runtime.txt) 与 [构建要求](../desktop/BUILDING.md)。

MCP 提供互操作，业务可信度和权限仍由宿主负责。stdio 不是操作系统沙箱；HTTP 仍需原有 loopback/Origin/授权边界。不得把连接密钥、浏览器会话、任意文件或 shell 能力作为新 resources/tools。新增 MCP adapter 不自动放开 DSH 当前禁用能力。参见 [Agent 集成](AGENT_INTEGRATION.md)、[受限 DSH](../desktop/harness/README.md) 与 [MCP 官方安全建议](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices)。

## 5. 兼容迁移与风险

迁移采用新增表/字段与只读投影，保留原始捕获、旧研究版本、历史审核和报告独立快照。每项改变先在影子路径比较，提供关闭新能力并回到现有读取路径的方法。索引与缓存可由确定来源版本重建；不能把它们设为唯一资料真值。

- **陈旧缓存**：明确 freshness、失效与失败重试；changed source 只失效依赖项，旧报告仍绑定旧版本。
- **过度合并**：命题规范化保留主体、对象、关系、地区/职务/刊物限定与时间；来源重复不等于命题等价。
- **分层混淆**：self-described、supported 和 verified 独立显示与查询；计划、报道与已发生分别统计，地图分母不变。
- **检索漏反证**：用跨语言、短词、同名及否定查询测试；按问题选材不能宣称全量阅读。
- **平台覆盖误报**：cursor/停止信号只证明其可见扫描范围；登录、速率、挑战、订阅与 PDF 不可提取状态不能被模型补齐。
- **恢复与并发**：幂等、版本比较、lease 与资源锁一起验收；取消和退出不能遗失已提交结果，未知外部调用不默默重放。
- **依赖扩大**：按实际收益决定安装，记录包体、CPU/内存、加载、许可和本地运行成本；保留无额外服务的桌面使用方式。

备份和恢复方法继续使用 [现有说明](DATA_BACKUP.md)。每批实施按 [协作要求](../AGENTS.md) 运行相应行为回归；发布时另做整体、实际浏览器、包内容与原生路径验证。模拟通过、真实模型结果、真实平台获取和设备端验收分别记录。

## 6. 评估原则

效率首先看每单位成本得到多少**有用、可追溯的新证据**，再看目标覆盖与错误率。测 scanned/new、网络/浏览器分钟、新来源、有效命题、重复模型输入、token/费用、引用与限定错误、检索 Recall@20/精确率/关键反证召回、排队与 checkpoint p95、恢复重做与提交一致性。基线尚需按 [路线图](ROADMAP.md) 测量，不填虚构提速或准确率提升百分比。

“有用”包括支持、反证、明确计划/报道与新的可解决缺口；未分类不是无关，未完全确认也不是零价值。有效产出、来源独立性、审核覆盖和事实确认分别报告。新实现只有在基线可比、真值要求不退化、故障路径可复验时才推广。
