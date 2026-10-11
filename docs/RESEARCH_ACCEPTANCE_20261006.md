# 深度研究与任务串验收 / 2026-10-06

**本文件保留此前 `-research-candidate` 研究能力与来源验收。当前前端及分发已更新到 `-workspace-candidate`，最新入口、哈希和 EXE 验收见 [工作台改版验收](WORKSPACE_ACCEPTANCE_20261006.md) 与 [发布记录](V2_ACCEPTANCE.md)。下文旧包的“当前源码一致”结论指其验收时冻结的源码。**

**当前交付为 `-research-candidate`，仅 Windows x64 与 macOS Apple Silicon。** 历史 `-social-candidate` 成品的会话、采集、启动验收保留为基线，不能替代本轮新研究包。包哈希、构建与实际桌面结果在 [V2 验收记录](V2_ACCEPTANCE.md)单独填写；Apple Silicon 的用户实机双击验收尚未完成。

本记录区分源码回归、真实外部来源、实际 AI、规划预览、已保存材料和桌面成品。证据目录为 `C:\Users\wwx\Documents\New project 7\output`；研究日期筛选使用 Asia/Tokyo，当前记录日期使用用户时区 Asia/Shanghai。

## 已实现及回归覆盖

| 能力 | 本轮行为与边界 |
| --- | --- |
| 多关键词方案 | 逗号、中文逗号、分号、换行分组；组内空格表示 AND，组间选择任一或所有组。显示源特定查询预览，不默改原输入，不让用户手写 `from:` / `OR`。 |
| 自动草稿 | 本机保存与服务端版本同步，关闭、Escape、刷新后恢复；误点弹窗外不会丢输入，多窗口版本冲突保留当前输入并提示。创建任务确认后才清理草稿。 |
| 当前主题研究 | 直接匹配本次用户词组的正文可以收录，劳动、学生等主题不受旧和平词表的先行硬过滤。拓展正文不直接命中时，AI 正向相关性需引用给定 ID 和正文原样片段；无关体育等不自动入库。 |
| 公开证据拓展 | 从新闻明确平台账号链接、真实帖子作者、公开 following / followers / mentions 发现账号；按主题证据、重复和空结果安排后续优先级，去重防环。未选平台只保留链接线索，不打开该平台。 |
| 断点与预算 | 每个完成分支保存素材、账号、边、发现路径、前沿、决策及诊断；取消关闭上下文。续查跳过已提交分支，失败入口可明确重试。深度 1 至 3、账号最多 40、边最多 200、记录最多 200、时间最多 600 秒。达到预算记为部分完成。 |
| 关系与隶属 | follows / mentions / shared_link / shared_event 是可观测线索；公开组织自述与 AI 候选单独保存。关注、共题、同活动链接不能变成隶属，AI 不改变账号核验状态。 |
| 自适应模型循环 | 使用原有 DeepSeek 连接，行动 / 语义循环最多 3 轮，模型只能重排已知候选并提出有限相关词；未知账号、证据 ID、运算符或伪造原文片段拒绝采用。规则与 AI 决策分别标记。最终分析仍受原总时间预算。 |
| 批量任务串 | 总体目标 1 至 10,000；单次最多 50 子任务，按关键词与不重叠日期窗口拆分。深度子任务最多 200、简单最多 100，尊重更低输入。全局队列 / 历史未设固定总条数上限，分页显示；后台串行执行以避免平台会话争用。 |

本轮最终完整源码回归：**后端 654 项通过，54.39 秒；前端 71 项 Node、22 组离线 Chromium DOM 检查全部通过**。后端测试输出保留一项既有 Pydantic alias 警告，非失败，实际输出见 [research-backend-regression-654.txt](<C:/Users/wwx/Documents/New project 7/output/research-backend-regression-654.txt>)。此结果包含混合 Fake-IP / 公网 IPv6 修复、X 搜索目标就绪校验、旧 DOM 测试页面升级及可选任务串标题空值修复；覆盖预算、去重、失败状态、草稿同步、多窗口冲突、取消、续查、关系方向、未知模型证据拒绝、任务串计划与显示等。最终 Windows 成品另完成实际 EXE 验收，范围如下；Apple Silicon 原生启动和未取得正向证据的外部流程仍单独保留。

## 当前真实来源及模型结果

| 场景 | 实际任务 / 结果 | 证据及结论边界 |
| --- | --- | --- |
| 指定 X 账号研究与续查 | 起点 `seibu_yunion`；本次任务 `9966e40b-19b0-48ec-ad9b-ab2de6d3fb39`，恢复研究 `9cef0128-9ad1-4ce7-b409-93ade0be5eb7`；3 节点 / 2 条 follows / 3 素材；本次新增 2、重复 1、拒绝 0；`partial / account_budget` | [research-x-profile-live.json](<C:/Users/wwx/Documents/New project 7/output/research-x-profile-live.json>)。一个账号因没有当前主题证据停止拓展，不表示三个账号都有有效主题材料或全部关系已查全。保留原帖、公开列表地址与决策。 |
| X 关键词页加载 / 排除 | 任务 `9af52af7-15c0-49ee-80a9-e0446d938928`；“反戦”、2026-09-07 至 10-06；6 条扫描、6 条 query mismatch、4 次重复、0 素材 / 节点 / 边；研究 `completed / no_matches` | [research-x-keyword-live.json](<C:/Users/wwx/Documents/New project 7/output/research-x-keyword-live.json>)。页面这次已经解析到正文，偏离查询的内容都拒绝；不能称为关键词正向命中成功，也不代表平台不存在相关帖子。 |
| X 较宽日期与双组复核（就绪修复前） | 任务 `159dd3d8-26da-4219-a4d8-ba6fbe7ded6d`；“憲法九条 / 反戦”、2026-01-01 至 10-06；分别扫描 6 / 12 条、全部 query mismatch；0 素材 / 节点 / 边；`completed / no_matches` | [research-x-keyword-broader-live.json](<C:/Users/wwx/Documents/New project 7/output/research-x-keyword-broader-live.json>)。扩大日期仍没有正向命中；正文筛选没有放宽。源码复核发现搜索就绪未核对目标 query / 结果容器，加载中旧文章可能提前触发采集。该确定性缺口已修复并通过真实离线 DOM 回归；当前日志不能直接证明本次零结果由它造成；修后同款结果见下一行。 |
| X 就绪修复后同款真实复测 | 任务 `7368cf8d-c756-4a49-a0b3-20f999fb2efe`；同一日期与两关键词组；两次 `search_context_verified:true / reason:ready`，搜索状态分别观察 9 / 7 轮；6 / 13 条扫描、全部 query mismatch；0 素材 / 节点 / 边 | [research-x-search-readiness-live.json](<C:/Users/wwx/Documents/New project 7/output/research-x-search-readiness-live.json>)。目标查询和主结果区域已核对后仍无正文匹配，`completed / no_matches`。这是真实就绪与严格排除验证；关键词正向命中仍未验过，不能把先前零结果全部归因于就绪竞态。 |
| Google News 与真实 AI | 任务 `647503fb-08de-4857-92af-65114292009b`；两组“憲法九条 / 反戦”、2026-01-01 至 10-06；5 新材料、0 账号 / 边；`partial / record_budget`；已有 `deepseek-v4-pro` 实际 `used:true` | [research-news-ai-live.json](<C:/Users/wwx/Documents/New project 7/output/research-news-ai-live.json>)。实际完成原始两组查询，并进行了 1 轮 AI 决策及最终证据分析。AI 提出“反戦団体”“憲法9条”两个后续词，但记录预算已满，**这两条建议没有执行新检索**。没有生成任何隶属候选，不能称组织关系已被证实。 |
| 真实 AI 关键词方案 | 六组“革命 / 労働者 / 矢嶋尋 / 戦争阻止 / 全学連 / 階級闘争”；18 个可选择建议；`ai.requested:true / used:true / model:deepseek-v4-pro` | [research-keyword-plan-live.json](<C:/Users/wwx/Documents/New project 7/output/research-keyword-plan-live.json>)。原输入与各组 AND 语义保留，建议不是自动替换输入或已采到的材料。 |
| 1,000 条任务串真实 API 预览 | 同样六组，2026-09-07 至 10-06；每子任务允许上限 200，总体目标 1,000；返回 6 个研究子任务，分配 167 / 167 / 167 / 167 / 166 / 166，总计 1,000 | [research-1000-batch-plan-live.json](<C:/Users/wwx/Documents/New project 7/output/research-1000-batch-plan-live.json>)。这是实际 API 计划预览，**未执行 1,000 条采集**。关键词间可能重复、来源可能不足、时间可能用尽，因此实际新增不保证达到 1,000。 |
| 当前真实界面预览 | 六组关键词，每子任务允许上限 50，总目标 1,000；返回 24 个子任务 | 最终汇总 [research-acceptance-final.json](<C:/Users/wwx/Documents/New project 7/output/research-acceptance-final.json>)。真实空标题预览成功并尊重当前更低预算，**未创建或执行这 24 项**；与上行不同单任务预算的六项 API 计划均有效。 |

## 历史基线与当前缺口

历史正式 Windows 应用目录中的 X 自动复用已验证：先从授权的 Firefox 会话验证并保存，再次直接验证工作台已存会话，不重复输入账号密码。正常登录入口使用操作系统当前默认浏览器，仅打开官方页面不会标记会话已同步。Chrome / Edge 可选桥接扩展有离线回归，尚未安装或完成真实同步验收。

Instagram 上一轮隔离 QA 标签“反戦”实采 3 条、复采去重和来源报告通过，原始证据仍保留。**当前正式目录缺少可复用 Instagram 会话，本轮深度 Instagram 适配仅离线回归，没有新增 live Instagram 证据。** 公开 profile 或列表无法确认时保留诊断，不伪造简介、边或组织关系。

以下尚未由本轮真实证据证明：X 关键词正向命中，多层 followers 全流程，AI 语义账号分支的现场筛选，大规模任务串实际运行与产量，Chrome / Edge 同步，以及 Apple Silicon 原生启动。限定小样本已通过不等于全部平台链路通过；最新研究 EXE 的启动和实际页面验收必须使用本轮成品另记。

## 桌面成品首次探针失败及修复记录

研究候选的第一次真实桌面任务串探针中，第一个新闻子任务成功、第二个失败，任务串没有达到“两项均完成”的检查条件。[desktop-smoke-error.json](<C:/Users/wwx/Documents/New project 7/output/desktop-research-final-smoke/desktop-smoke-error.json>)保留 `Research batch did not complete its two queued jobs`，同目录保留该次日志和数据库，不把失败输出覆盖成成功。

精确复现发现 `news.google.com` 返回公网 IPv6 与 Fake-IP IPv4 的混合 DNS 集合；此前只有“全部地址是 Fake-IP”才进入可信 DoH 兼容路径，使混合集合被拒绝。修复限定为 Fake-IP 与公网地址组合的再解析，真实私网地址仍拒绝。修后通过前一候选包的私有 Python 加载更新后的来源模块，连续请求“憲法九条”“反戦”两个真实 RSS，均 HTTP 200，响应分别为 78,973 与 119,066 字节；`rss20` 表示 RSS 2.0 格式，未统计条目数，先前“各 20 个条目”的记录有误，已更正。这验证来源修复，不代替新 EXE 闭环。新闻入口另保留受控 `PipelineError` 的安全 DNS / 来源原因，未知异常仍只显示固定消息与类别；4 项新闻定点测试通过，包括私网拒绝原因和未知异常敏感值不泄漏。修复后的完整后端回归为 654 项通过；成品结果分轮记录。

成品验收两个新闻子任务均实际保留 1 条研究证据并达到各自预算，状态为 `partial / record_budget`。第二轮任务 `02cee906-d4dc-4095-a765-b132b9e37436` 与 `f964e271-16dd-48fc-bd8e-32904bc71424` 已只读核对 `output/desktop-research-final-smoke-2/workbench.sqlite3`，两项均 `fetched:1 / record_count:1 / inserted:0`；后一轮同样各 `fetched:1 / inserted:0 / duplicates:1`，两次 X 自动复用均 `verified:true`。材料已有重复，不能写成全局新增 2 条。

验收脚本曾遇到 QA 草稿清理 409、未等异步重新加载即截图，以及对原生 `<dialog open>` 使用显式 `role` 属性选择器导致超时的问题，均已修验收脚本。原生对话框不需要显式 `role` 属性，相关定位已更正；这些属于测试定位 / 等待缺陷，原三轮失败输出继续保留。最后成品 smoke-4 已完整退出 0，草稿 PNG 已实际查看并确认恢复提示，详见下一节；成功结论没有覆盖此前失败记录。

## 最终 Windows 成品结果

实际启动当前研究 EXE，使用隔离验收目录并复用已授权的本机工作台 X 会话。最终 [desktop-smoke.json](<C:/Users/wwx/Documents/New project 7/output/desktop-research-final-smoke-4/desktop-smoke.json>) 与[最终汇总](<C:/Users/wwx/Documents/New project 7/output/research-acceptance-final.json>)确认以下结果：

| 验收项 | 实际结果 |
| --- | --- |
| 原生 EXE / 后端 | smoke-4 退出 0，健康状态 ready；后端正常 `exit code=0`，剩余 ready 文件 0。 |
| 空标题真实创建 | POST 空标题成功生成“批量公开资料研究”；任务串 `71e293d2-7020-4dba-89ec-b5244da20483` 实际创建并串行执行两项。 |
| 两个新闻子任务 | `50748526-ac95-486d-a0f9-167753542a0f` 与 `ed23ece6-9fcc-4824-bb9e-d0e579e49f5d` 均 `partial / record_budget`，各 `fetched:1 / inserted:0 / duplicates:1`；总新增 0、重复 2、完成任务 2、活动任务 0。 |
| X 已有会话复用 | 两次均 `verified:true / saved:false / source:workbench / requires_login:false`，无需重新输入账号密码；不等于新的关键词正向命中。 |
| 刷新后草稿 | `draft_restored_after_reload:true`；实际查看[成品弹窗 PNG](<C:/Users/wwx/Documents/New project 7/output/desktop-research-final-smoke-4/desktop-research-composer.png>)，原生对话框显示绿色“已恢复上次草稿 · 修改自动保存”。 |
| 研究关系图 | 实际查看[成品关系图 PNG](<C:/Users/wwx/Documents/New project 7/output/desktop-research-final-smoke-4/desktop-smoke.png>)；显示真实研究 `9cef0128-9ad1-4ce7-b409-93ade0be5eb7` 的 3 账号 / 2 关系 / 3 材料及部分完成状态。 |
| 默认浏览器 / 安全存储 | `default_browser_controls:true / secure_storage:true`；控件和系统安全存储可用性通过，不声称未登录平台会话已自动同步。 |

最新源服务 `127.0.0.1:18769` 另已重启并健康；该开发服务状态与上表实际桌面成品分开记录。完整日志在 `output/desktop-research-final-smoke-4/logs`。

## 可选任务串标题合同修复

真实界面操作“批量研究 → 留空可选标题 → 预览”暴露应用合同问题：`BatchParams` 对可选标题施加 `min_length=1`，空字符串在规划器处理前返回 422。修复移除这个长度下限，沿用现有的去空格 / 默认规则：省略、空字符串、纯空白均生成“批量公开资料研究”，自定义文本去除首尾空格。4 项参数化 API 测试同时覆盖预览与创建，已通过；最新完整后端 654 项包含本修复。最终成品已实际 POST 空标题并核对默认标题及两项执行结果。

## X 搜索就绪修复与验证边界

修复前，`domcontentloaded` 后全页任意文章就可触发采集，即使目标检索正在加载，也未核对搜索框和实时标签。现在检索必须同时满足目标 `/search` 路由及 `q`、搜索框内容、选中实时标签的结构化链接，以及主结果区域已完成加载；正文采样限于主列，侧栏推荐、侧栏加载与侧栏空结果文案不参与判断。没有就绪时最多两轮各 12 秒有界等待及一次原检索地址重载，随后明确 `parse_error / waiting_for_search`，不把旧时间线当成搜索结果，也不把未加载判为 `no_matches`。

这项修复以隔离 Chromium 的实际 HTML / DOM 回归验证：旧文章与目标加载指示并存时不采样，目标结果替换并完成加载后只取得新文章；搜索框或标签不符时保持等待；侧栏加载及推荐不会阻塞或污染主结果。升级旧 X DOM 测试页面以包含真实主列和检索状态后，X DOM / 社媒 / 研究定点合计 **141 项通过，16.30 秒**，原作者、引用、日期、文章卡及相关性断言继续保留；后续完整后端回归为 **654 项通过，54.39 秒**。修后同款真实 X 页面已经完成就绪验证，但两组仍全部正文不符；关键词正向命中和成品体验不能由离线 DOM 或就绪通过推定。关键词正文与日期条件没有放宽。

## 当前研究包记录

| 平台 | 当前候选路径 | 字节数 / SHA-256 / 构建与启动状态 |
| --- | --- | --- |
| Windows x64 | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-research-candidate.zip>)；[已验 EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-research-candidate-20261005-214949/PEACE ATLAS.exe>) | 413,012,485；`5a5f19469a91d7957a34ae64c6a0a9f7fb9f350ac97daa8a0120d86bf7f76241`；59 应用文件当前源码 / manifest / payload 一致，CRC 与隐私排除通过，实际 Windows smoke-4 通过。 |
| macOS Apple Silicon | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-research-candidate.zip>) | 363,882,003；`96ab9b66dc9a32f56291631af503b3313a0301d43abb59133e71bcfa9b1c1449`；60 应用文件当前源码 / manifest / payload 一致，CRC、44 Mach-O、29 符号链接及 ad-hoc 检查通过；macOS 13.0+，原生 codesign / 用户双击仍待实机。 |

包记录经 [research-package-audit-final.json](<C:/Users/wwx/Documents/New project 7/output/research-package-audit-final.json>) 核对；Mac 签名边界见 [research-mac-signing-audit-final.json](<C:/Users/wwx/Documents/New project 7/output/research-mac-signing-audit-final.json>)。ad-hoc 检查通过不代表 Apple 原生 codesign、Developer ID、公证或原生启动已通过。

Intel Mac 历史包不再更新。桌面包不携带 `.env`、会话、账号密码、用户数据库或现场验收材料；应用数据保留在独立用户目录。
