# 账号关系与核心流程验收 · 2026-10-09

本文件记录此前 `relations-candidate-20261008-173106` 的账号入口与核心修复，保留为历史验收。**当前桌面和工作目录启动入口已切换到 `relations-candidate-20261008-190920`**；持续图布局、队列顺序、关系时间戳及新包真实 Flash 研究见[当前验收](GRAPH_MOTION_CORE_ACCEPTANCE_20261009.md)。默认模型仍为 `deepseek-flash`，整体产品目标继续执行。

## 本轮修复的实际问题

1. 左侧“账号关系图”以前回到研究工作台。现有独立 `#connections` 路由，旧的 `#network?registry=1` 链接保留筛选参数并迁移到新路由。研究工作台仍用于创建、继续与查看任务。
2. “探索此账号”以前隐藏账号模式并强制发送关键词模式，导致选中账号丢失。现在显式保留主要账号、其他研究起点和所选平台；空关键词表示从账号的公开资料直接研究，不再默默补成“日本 平和”。多个账号网址按身份规范化、去重并在预算内校验，非法地址在浏览器启动前拒绝。
3. 账号图新增证据时会自动刷新，保留固定节点、缩放、平移和当前选择；拖拽过程中延迟重绘。可以在图上直接编辑账号。真实浏览器/API 已核对编辑后 ID、原材料、关系及布局保留。
4. 账号先以用户名占位入库后，真实公开显示名称没有更新；后续无关 checkpoint 又可能把捕获的名称改回用户名。现在只采用身份匹配且 DOM 实际观察到的主页名称，并等待名称加载；不把发现树中的占位节点覆盖为实际捕获。手工名称、组织和标签按字段保留，组织隶属与核验状态不会由模型自动改写。
5. 真实长任务出现三次模型输出截断，且采集耗尽预算后没有时间完成最终分析。已调整输出额度；在原总时限内为最终 AI 预留最多 120 秒，采集阶段按余下额度停止并保存成果。模型证据采用公开简介优先、其余按账号轮流分配，每次最多 80 条、每条最多 1,000 字；界面和结果记录实际覆盖，完整取得材料仍保留。
6. 账号提及解析复用原有校验，邮件地址和网址里的 `@` 不再误画成账号关系。

这轮沿用已有公开资料采集、去重、修订、分析和报告流程，重点改善指定账号研究和可追溯关系，没有新增复杂的搜索前端。

## 真实公开账号与模型证据

研究起点采用 [全学连官网的 2026 年新生企划页](https://zengakuren.jp/shinkan-2026/)明确链接的 `@Zengakuren` 和 `@Zengakuren_west`。这只支持这两个账号的公开链接依据，不代表已经确认所有“全学连”、中核派或革共同相关账号均属同一派别或组织。自动核验客户端访问依据网页失败时保持候选并保存 `source_unavailable`；联网工具能读取官网，不代替产品端的访问成功记录。

第一轮正式原生后台任务设定最近 90 天、日本日期、三层、最多 32 个账号、180 条关系、400 条材料、40 次滚动和 1,800 秒总上限。关键词包含中核派、全学连、革共同、工会、反战及巴勒斯坦。实际运行 **1,797.985 秒**，保存 **32 个账号、136 条关系、75 条材料，访问 28 个账号**；停止原因为账号预算，终态为部分完成。被跳过分支与访问、翻译和加载限制保留，不能据此声称全量历史覆盖。

- 正式任务回执：[receipt.json](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/formal-long-relations/receipt.json>)。
- 真实关系图与来源：[research-graph.json](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/formal-long-relations/research-graph.json>)。
- 第一轮运行于此前正式 `-fullflow-candidate`；这个真实反例是本轮名称与 AI 预算修复的依据，不冒充最新版长任务验收。

最新版包内 Python / 后台对这轮保存的原子公开快照完成实际 Flash 关系分析，耗时 **59.484 秒**：32 个账号、75 条材料、136 条观察关系，保留 136 条规则发现和 15 条待复核隶属候选。实际模型输入为 **80/242 条目录证据**，优先覆盖账号自身简介并按账号分配其他材料。引用 ID 校验通过；该隔离分析未修改正式账号、核验状态或关系图。

- [最新真实 Flash 回执](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/long-relationship-ai-final/receipt.json>)。
- [模型与规则分析结果](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/long-relationship-ai-final/analysis.json>)。

该历史成品的双起点正式研究已结束：空关键词、最近 90 天、两层、最多 8 个账号、80 条关系、160 条材料、32 次滚动、900 秒上限。实际耗时 **516.578 秒**，访问 8 个账号、研究包含 97 条材料及 15 条关系，终态为账号预算下的部分完成；真实 Flash 输入 80/120 条证据并完成分析，账号 ID 与手工字段保留。随后独立审查发现最终分析清空了这些研究关系的 `observed_at`，已知首次时间仍保留；该反例是当前版本时间戳修复的依据，不将历史快照说成最近观察时间完整。新包四起点真实复测及 15/15 条时间保留结果见当前验收。

- [追加正式账号任务](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/formal-relations-account-scope/receipt.json>)。
- [实际成品启动与两个快捷方式](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/formal-relations-release-switch.json>)。

此前同一用户保存的原生加密连接已完成 42 条新闻材料的真实 Flash 报告：42 条选中、42 条审阅、约 96 秒，报告及来源快照实际保存，11 项检查通过。该记录是新闻分析证据，不能代替本轮账号关系模型验收：[正式报告回执](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/formal-native-flow-retry/formal-native-queue-flow.json>)。

## 确定性回归与成品验收

| 范围 | 实际结果 | 证据与边界 |
| --- | --- | --- |
| Python 核心后台及构建回归 | 883 通过，1 项已有 Pydantic 字段 alias 警告 | `relations-release-backend-tests.log`；模拟平台/模型的行为测试不代替实采 |
| 前端 Node | 107 通过 | `account-relations-frontend-final.log`；账号模式、其他种子、明确来源、草稿和预算 |
| 桌面 Node，指定包内 Python | 59 通过 | `relations-native-logic-tests.log`；生命周期、配置持久性及恢复 |
| 包内实际 HTTP 核心流程 | 14 通过 | `relations-packaged-product.log`；来源入库、去重、报告快照、草稿、在线备份、重启和恢复，隔离数据 |
| 包内日常可靠性 | 15 通过 | `relations-packaged-daily.log`；并发重试守卫、终态补写及重启持久性 |
| 父进程退出 | 4 通过 | `relations-packaged-parent.log`；包括实际中间父进程突然退出 |
| 包内实际浏览器产品交互 | 25 通过，页面错误 0 | [结果](<C:/Users/wwx/Documents/New project 7/output/playwright/account-relations-20261009/packaged-product/results.json>)；图拖拽、地图、账号、正文和资料清理，合成样本 |
| 包内独立账号图浏览器 | 5 组通过，页面错误 0 | [结果](<C:/Users/wwx/Documents/New project 7/output/playwright/account-relations-20261009/packaged-connections/results.json>)；路由、自动刷新、布局和编辑保留、多种子真实 API 排队。会话检查用夹具，排队任务未执行外部采集 |
| 原生加密配置坏文件恢复 | 通过，应用/后台退出均为 0 | [独立核对](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/native-relations-connection-verification.json>)；隔离无 key 合成配置，原文件归档哈希一致，设置重存和研究/备份可用 |
| 原生研究备份恢复 | 通过，21 表比对通过 | [独立核对](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/native-relations-restore-verification.json>)；隔离资料，原生文件选择/确认注入，原库保留，配置与社媒状态不变 |
| 分发包源码、manifest、ZIP、portable | 通过 | [独立审计](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/package-relations-audit.json>)；未包含用户数据、会话和密钥 |

以上日志根目录为 `C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008`。全部原始失败记录保留。账号浏览器早期断言选择了错误标签或把创建成功后的任务详情抽屉当成采集弹窗；修正验收选择器后通过。通用新闻原生验收器曾误用于只做配置恢复的 smoke，因其没有新闻采集字段而失败；随后用对应配置证据校验器核对，不将这一调用范围错误当成新闻产品验收。

资料清理先预览范围和原因，再入回收站；可恢复。只有明确选中的回收站资料可以在显式确认后彻底删除。复采不能自动撤销清理决定；原有报告快照独立保留。地图仍按活动日期范围内已核对、已定位的去重活动计算各地数量占比，待核对线索和原帖地名提及不进入分母；合成地图验收不冒充正式库的已核对活动。

## 当前可用文件与未验证范围

- [历史 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261008-173106/PEACE ATLAS.exe>)；[已保留的历史 Windows ZIP](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/relations-release-173106/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)。ZIP SHA256：`b9cac270287262d282ef45f21b7ad22dd80057b90a15c1a40e12b317c5edd1e4`。
- [已保留的历史 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/relations-release-173106/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)。SHA256：`76d4b528569c7ee08a6890e1ed7cbc61e2041a3efbf237c3f3c1e5f709347dd0`。44 个 Mach-O、29 个符号链接及跨平台 ad-hoc 签名检查通过；Apple 原生 `codesign` 和 Mac 启动未验证。

公开平台分页、登录、自动翻译与网络可用性限制仍按任务诊断展示；完整历史、全部左翼组织网络、10,000 条实际产量、24 小时耐久性及实机 PostgreSQL 并发尚未由本轮证据证明。没有隐藏失败或把候选判断写成已核验事实。
