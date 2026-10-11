# PEACE ATLAS 账号关系深度研究设计

2026-10-09。本轮在现有图谱上增加账号类型、方向强调、可核对路径、结构层级、研究相关性和指定账号的 AI 逐层排序。用户已经认可的力导图动效继续保留；新能力来自保存的公开证据和可解释的查看规则，图中的距离、颜色或桥接位置不构成组织隶属、立场或影响力的证明。

本文说明当前设计、已经实现的主要契约及已经保存的分项验证。后台完整回归与分发验收另行记录；本文不宣布新版已经正式交付。依据为本轮[研究计划](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/research_plan.md>)、[成熟产品研究](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/findings_products.md>)、[初始架构发现](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/findings_current_architecture.md>)及下文实际机器回执。

## 1. 保留现有动效，增加可解释的查看层

原有拖动、邻居响应、碰撞整理、松手后稳定、暂停、固定节点、缩放和平移继续使用现有引擎。[graph_layout.js](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/static/workbench/graph_layout.js>) 在本轮前端验收中保持原字节，SHA256 为 `40ef18f68cc41e3254a91477ec23655c5c936c82f1abf5e79158ffc238dfbb78`。方向、跳数与结构层级只改变强调集合，其他当前显示节点保留并淡化；可恢复的通用账号过滤另生成显示投影，保留已有节点位置、固定状态和视口。

物理引擎为排布把邻居对去重成无向弹簧。关注方向和路径计算读取保存的原始 `source_key → target_key` 关系边，不能从弹簧方向、节点距离或画面位置反推关注、强弱或组织边界。[共享图交互](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/static/workbench/interactive_graph.js>)与[前端纯模型](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/static/workbench/network_model.js>)分别承担绘制交互和有界语义计算。

本项目借鉴成熟产品的交互与解释方式，没有替换为这些产品，也没有把它们的算法或文档当作本项目运行结果：

| 官方参考 | 已查证的能力 | PEACE ATLAS 的借鉴 |
| --- | --- | --- |
| [Obsidian Graph view](https://obsidian.md/help/plugins/graph) | 全图、局部图、按邻接层数控制局部深度，以及箭头和连接高亮 | 将查看起点与 1–3 跳邻接范围做成独立控制，继续使用现有动效 |
| [Gephi Quickstart](https://gephi.org/quickstart/) | 按 Degree、中心性及图属性查看、过滤网络，筛选后显示当前覆盖范围 | 显示有来源的独立邻居及当前范围的结构解释；筛选后的层级不称为全网络排名 |
| [Neo4j 关系模式](https://neo4j.com/docs/cypher-manual/current/patterns/reference/node-and-relationship-patterns/)与[最短路径](https://neo4j.com/docs/cypher-manual/current/patterns/shortest-paths/) | 关系匹配可规定方向；最短路径支持限制和筛选 | 先限定节点、关系和有效来源，再求有界路径；反向浏览仍保留每条实际箭头 |
| [Maltego Running Transforms](https://docs.maltego.com/en/support/solutions/articles/15000009613-running-transforms) | 从所选实体提供适用动作，显示执行进度并允许取消 | 选起点、预览范围与预算、执行公开读取、看保存结果，再选择下一步 |

上述借鉴属于项目设计取舍。当前结构桥接使用可解释的割点判定，没有实现 Gephi 社区发现或 Neo4j betweenness 排名，也没有新增图数据库依赖。

## 2. 类型、核验、结构与研究相关性分别表达

同一个账号可以同时是“个人”“候选”“结构桥接”和“本次未判定相关性”。这些标签回答不同问题，不能互相替代：

| 维度 | 当前契约 | 表达范围 |
| --- | --- | --- |
| 账号类型 | `account_type = organization / individual / unknown`，附 `account_type_details` | 组织、个人或证据不足。详情包括依据、公开链接、限定说明、人工锁定及核对说明 |
| 身份核验与人工排除 | `candidate / verified / excluded` | 是否取得准确账号身份链接依据，或是否存在人工排除决定；不保证每篇内容真实 |
| 当前图结构 | `insights.node_metrics` 的 `core / bridge / peripheral`、独立入/出邻居及 `structural_bridge` | 当前有来源关系范围中的连接性质。外围不等于不重要，桥接不等于组织成员 |
| 研究相关性 | `research_relevance` 的 `general_platform / excluded / included / unknown` | 默认显示及自动展开规则；不判断账号类型、身份、政治立场或现实组织归属 |

类型来自[纯身份投影](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/workbench/account_identity.py>)。旧账号缺少可靠类型资料时为未判定；不能仅凭名称、简介自称、蓝标、粉丝量、关注关系或 AI 身份猜测归为组织或个人。人工类型与锁定通过专门接口保存。既有准确、已接受的项目官网回链记录可在严格核对账号 ID、key、当前主页和来源后作只读兼容投影，不把类型传播给关注者。人工“未判定”或“个人”等决定不能被旧兼容投影覆盖。

结构层级由[graph_insights.py](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/workbench/graph_insights.py>)和当前显示范围的前端兼容计算产生。独立邻居按账号去重，重复来源或互相关注不把邻居数重复累加。桥接是暂移节点后拆开当前已有连通支路；重点使用本图邻居阈值，阈值至少为 3，并考虑本图度数分布；其余为外围，孤立另有标记。

`organization_bridge_candidate` 只提示直接连接了至少两个已标明类型的组织账号，并列出端点。这两个账号可能属于同一个组织实体，不能自动写成“连接不同组织”，也不能由结构社区、昵称或相邻关系补出组织边界。

## 3. 方向、焦点跳数与逐边证据路径

“我关注”和“关注我”里的“我”指页面明确显示的关系起点。两个方向只计算实际保存的 `follows` 边；提及、引用、共同外链等关系不能混作关注。若要称相互关注，必须分别存在两个方向的实际关注边。

“全部关系”可沿当前允许关系查看连接。无论沿原方向还是逆向浏览，每一步都显示原 `source → target`、关系类型及来源，逆向步骤加说明，不创造反向关注或两端直接关系。只有端点存在、不是自环、basis 为 observed/reported、且具有可点击的有效公开 HTTPS 来源的边才支持路径和结构指标；inferred 猜测、缺来源或已清理来源不能补成事实链。

用户可以选择起点、1–3 跳和终点，查看当前筛选范围的最短有来源路径，点击每跳返回公开证据。路径只证明这些单独关系在保存资料中可核对，不证明起点与终点合作、协调或同属组织。“未找到路径”限定于本次显示、方向、跳数和采集覆盖，不能写成现实中没有联系。

原发现 `node.depth` 和 `parent_key / discovery_paths` 记录研究过程中何时、由哪条分支发现账号；从查看起点重新计算的焦点 hop 是图距离。改变焦点不会改写原发现层数，最短路径也不能替代真实发现历史。当前汇总图本身最多显示 300 个账号、1,000 条关系，覆盖提示与未展示计数需要继续保留。

## 4. 通用平台账号可隐藏、可核对，原始来源保留

初始名单只匹配六个精确命名空间与用户名：

| 平台 | 精确账号 |
| --- | --- |
| Twitter / X | `youtube`、`teamyoutube`、`google`、`instagram`、`x` |
| Instagram | `instagram` |

这不是全网无关账号清单。`twitter:youtube_japan`、`instagram:youtube`、相似昵称或热点大号不会因此被过滤；身份键与平台/用户名矛盾时保留未判定。账号类型、核验和粉丝数量不参与这个默认判断。[account_relevance.py](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/workbench/account_relevance.py>)返回 `state / default_hidden / skip_expansion / reason / basis`，不修改输入账号、关注来源或正文。

默认关系网络隐藏通用平台账号，并说明隐藏数量；自动研究不把非显式起点的通用账号继续当作重要拓展分支。“显示通用账号”可以恢复原节点和原关系链供核对，但通用节点仍不参与重点或桥接排名。两个相关账号只经 YouTube 连通时，默认路径不通；打开开关后可核对其逐边原始来源。原始公开关注数据和材料始终保留，发现树仍保留历史分支。

人工可以把其他无关账号标为 irrelevant，或明确 included 纳入研究；unknown 移除这条人工相关性决定并恢复默认规则。此接口只保存相关性及人工说明，不改类型、核验或正文。“显示通用账号”不恢复人工 irrelevant/excluded。账号本身已有的 excluded 决定优先；明确研究起点也不能覆盖它。

用户确实以 YouTube 等平台账号为研究起点时，可以研究该账号。证明来自保存的账号模式起点或明确 seed_accounts，而不是普通选中、残留的关键词任务 handle、客户端 metadata 或 AI 声明。可信人工 included 也允许覆盖通用默认。普通采集导入必须剥离保留的身份、核验、相关性决定字段，研究 worker 使用可信注册表上下文，避免伪造“人工纳入”在写入过滤之前驱动读取。[独立整合审查](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/qa/findings_current_graph_engine_integration.md>)保留了这一真实反例及修复后的执行证据。

## 5. AI 指定账号探索：引用候选、逐层重排、保留外围

初始架构读取时，指定账号 scope 在 `adapt_frontier` 入口被短路，已有关键词 AI 排序不能视为已覆盖指定账号探索。本轮[研究引擎](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/workbench/research_engine.py>)改为在指定账号的已观察路径内选择下一批公开读取候选；[account_exploration.py](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/workbench/account_exploration.py>)提供有界候选上下文和整批校验。

候选包含发现层数、多父发现路径、待读主页/正文/关系分支、实际来源 ID、类型依据和已采图结构信号。最多提供 30 个候选，每个保留有界支持目录；AI 每轮最多返回 10 个不同候选，每项必须引用该候选支持目录中的 1–10 个证据 ID，优先级为 0–100 整数，理由说明下一步待核对内容。相同 URL 的无关帖子不能借此取得候选支持权限。

指定账号优先级最多 **12 轮**。只有同时具备多于 1 个合法未读候选、可引用证据、启用的模型及至少 25 秒剩余预算，才尝试调用；每读取一个账号后可重新评估下一批，仍受轮数、时间和采集预算约束。12 是调用上限，不保证必定调用 12 次，也不允许每发现一个节点就绕过门槛自动付费。

服务端先完整校验所有候选和引用，再写入优先级。未知或重复候选、不相关引用、布尔值优先级、重复引用或额外字段会整批拒绝，不先写一部分。合法建议保存 `priority_method='configured_ai_account_scope'`、`priority_evidence_ids`、`priority_basis`，保留相应来源摘要，并在首次读取所选账号之前保存 checkpoint。

AI 只重排既有候选；不创建账号、关系、核验、隶属或追加关键词，不改变起点、日期、主题、预算和合法采集分支。未选择的外围候选留在 frontier/deferred 中。模型失败记入 `ai_decision_failed`，继续使用可解释规则顺序，已读资料与待办不丢失。关键词模式原有有界建议路径继续保留，不能把指定账号的 12 轮规则推广为所有任务无限扩张。

## 6. 自动核验：可信来源与候选来源分开，写入触发后台工作

自动核验回答“此可信公开网页是否准确回链到当前账号主页”。可信锚点来自项目已登记官网或专门人工来源入口；普通导入、社媒简介中的新外链、自建未登记页面、蓝标、自称和 AI 猜测不会自动取得可信权限。

未授信外链可以作为候选来源读取和记录，但准确回链本身仍不足以直接授予 verified。当前[核验模块](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/workbench/account_verification.py>)区分 trusted 来源与未授信候选：候选回链结果记录为 `untrusted_backlink` 和 `candidate_evidence_url` 等诊断，不伪装成可信官网核验。可信来源还需核对账号 ID/key、当前 HTTPS 主页、回链方向、同站跳转、冲突与检查期间的人工修改；类型判断必须有其本账号对应依据，不能因为另一个账号登记或共享同一官网而传播“组织”类型。

核验保留 checked_at、source_basis、pages_checked、requests_made、cache_hits 和覆盖/错误诊断；每次页面读取有上限，不称为遍历所有官网。访问失败、没回链、来源冲突或检查期间资料变化时保持可解释状态和人工决定。人工核验、类型锁定与排除决定不能被旧异步结果覆盖；排队取消、运行中取消和重试需要一致的 durable 终态。

账号注册、导入及公开资料更新的**写路径**在输入变化且具备来源时，通过事务内回调安排既有 collect 类型的核验任务，保存 `metadata.verification_schedule`。后台[JobRunner](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/analysis-worker/app/workbench/jobs.py>)为核验提供独立 lane，避免占用正常采集/报告 worker；登录另有其原 lane。页面 GET 和身份/相关性投影只读展示，不触发网络读取、排队或数据改写。

本轮复用现有账号 metadata、任务、研究节点/边和证据记录，**没有新增 schema 或 DDL 迁移**。派生类型、结构与相关性不改写旧正文、历史分析或报告独立快照。导入不能伪造保留元数据，自动核验也不能从关注关系推组织隶属。

## 7. 当前规模上限与继续深追

单个研究任务当前仍限制为 **最多 3 层、40 个账号、200 条关系**。本轮没有扩大这些采集限额。页面的 1–3 跳是当前已采图上的查看范围，不能绕过后台深度或预算。

用户可在某节点处“以此为起点继续研究”，进入工作台预填该账号、审阅新的日期及账号/关系/正文/时间预算后创建下一次研究。新研究从明确起点继续深追，旧结果和来源仍可核对；对原任务未完成分支的恢复仍使用已有续查/失败恢复流程，两种操作的来源和执行记录应分别保留。

完整网络发现还受平台公开可见范围、未登录限制、分页/滚动、被阻止分支、来源质量及实际执行预算约束。模型即使拥有百万 token 输入容量，也不等于取得了全网账号或所有原文，不能作为完整采集、完整引用或全网络关系图的保证。UI 需要继续区分保存总量、当前展示量、实际已读/已审量、待办和停止原因。

## 8. 已保存的分项验证范围

下列是实际执行并保存的机器结果，不以源码阅读代替行为测试，也不把多批重叠用例相加成后台全量结果：

| 分项 | 实际结果与回执 | 验证边界 |
| --- | --- | --- |
| 全部前端纯逻辑 | [162 passed，0 failed，80.651847 秒](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/frontend/34-all-node-final-green.log>) | 现有 129 与新增 33 个用例；含原布局逻辑及类型、方向、路径、引用、精确过滤和根证明 |
| 新实际浏览器行为 | [8 组通过、0 页面错误、0 外网请求](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/frontend/browser-final-003/results.json>) | 两个真实前端入口、通用路径隐藏/恢复、人工更正、全部隐藏、明确起点、拖动/固定/稳定/缩放、1440px/390px |
| 原研究流程浏览器 | [4 组通过](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/frontend/35-existing-browser-final.log>) | 新默认排除语义已适配；来源、人工复核、失败输入保留、预算、分页、不可信文本及链接门禁保留 |
| 原分析页面浏览器 | [31 个用例通过、0 页面错误](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/frontend/36-analysis-browser-final.log>) | 分析覆盖、继续入口与失败恢复 UI |
| 核心独立整合 | [21 passed，4.58 秒](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/qa/account-relevance/current-integration-green/current-integration-results.json>) | 实际 Store、API、JobRunner 与合成 adapter/model；保留原关系、可信人工决定、优先级先保存、外围候选及旧历史 |
| 核心相关回归 | [97 passed，1.01 秒](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/qa/account-relevance/current-integration-green/related-final-097.log>) | 已含此前相关用例；不是完整后台计数 |

早期独立 19 和单元 94 的记录另行保留，不与最终 21/97 或其他重叠批次相加。独立整合回执还记录六个隔离 SQLite 的 integrity_check=ok、foreign_key_check=0；两条图 API GET 的 22 张表列形状与完整 typed rows 哈希不变。其源码稳定性只采样十个直接执行文件，不能替代整体源码冻结。[前端总回执](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/frontend/frontend-acceptance.json>)另绑定当前前端字节、测试、截图与保留的红例。

这些分项使用隔离数据库、合成公开内容、合成模型响应和临时浏览器环境。它们证明实现的行为边界，不证明现实中的账号类型、官网身份、新付费模型输出或全网发现结果。自动核验仍需以实际来源和后台终态核对，后台完整回归、包审计、原生路径和正式升级在相应最终验收文档中另行确认。
