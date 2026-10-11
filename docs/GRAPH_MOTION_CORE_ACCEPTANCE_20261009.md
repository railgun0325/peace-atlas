# 关系图持续布局与核心流程验收 · 2026-10-09

> 历史验收：本文记录 `relations-candidate-20261008-190920` 当时的结果。它已由 `205926` 替代；本文中的“当前”及测试数字仅指当时版本。[当前交付和分批分析验收](ANALYSIS_COVERAGE_ACCEPTANCE_20261009.md)另行记录。

**桌面和工作目录两个启动入口已切换到 `relations-candidate-20261008-190920`，普通正式应用正在运行，默认解析模型为 `deepseek-flash`。** 这轮修复了图布局提前停止、拖动不带动邻居、指针捕获丢失后无法刷新，以及队列同时间戳乱序和最终分析丢失关系观察时间的问题。整体验收通过，真实检索与 Flash 分析也已完成；整体产品目标仍在执行。

[双击启动入口](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[当前 Windows EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261008-190920/PEACE ATLAS.exe>)；[机器可读验收](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/graph-motion-delivery-acceptance.json>)。

## 关系图实际修复及使用方法

关系图由 **GPT-6.1 Sol Ultra** 子代理负责，获授权使用 **GPT-6.1 Sol High** 孙代理做独立布局审查和浏览器回归。主代理负责后台、真实检索与分析、成品重建、原生验收和正式应用切换。

原布局在 220 帧后强制停止，即使节点仍未散开。这次去掉固定帧数限制，按完整物理步骤的实际节点位移判断稳定，碰撞推开造成的位移也计入。连续 30 帧最大单步位移不超过 `.035` 图坐标单位才停止布局循环；稳定后不再空转，下一次拖动会重新唤醒。这里的稳定是数值容差，正常的新证据轮询仍然运行。

拖住一个节点时，相连节点会持续被拉动，附近节点会被推开；停住鼠标后仍会继续整理，松手后继续到稳定。自动动效已暂停或系统采用减少动态效果偏好时，直接拖动仍然有效。累计慢拖不会误当点击；其他指针不会夺取拖动；原生指针捕获丢失时，会释放拖动状态及自动刷新保护。

画面采用小圆点、低干扰连线和随缩放调整的标签。悬停突出实际相邻节点，点击账号或连线查看依据；名称和详情不再全部挤成大卡片。拖动整理节点、背景拖动平移、滚轮缩放、双击固定或解除固定。固定位置、视角和选择在证据刷新及账号编辑后保留。节点位置和距离属于显示布局，不表示组织关系强度。

最终 Windows 包内浏览器以**保存的真实公开资料副本：43 个节点、142 条证据关系**运行了 21 组手势、持续动效、收敛和空闲验收；全部通过，页面错误为 0，操作前后完整 API 数据一致。源码另经 9 个独立数值场景检查，包含 100–200 个节点、全部初始重合及高连接节点重合拖住的情况，均达到稳定；最长真实重合拖住场景在 15,006 步达到容差。该数值检查不能代替大规模产品性能及长时间耐久性验收。

- [包内 21 组真实资料副本交互结果](<C:/Users/wwx/Documents/New project 7/output/playwright/account-relations-20261009/packaged-motion-saved/results.json>)。
- [包内稳定后的关系图截图](<C:/Users/wwx/Documents/New project 7/output/playwright/account-relations-20261009/packaged-motion-saved/saved-network-automatic-stable.png>)。
- [子代理源码冻结、哈希与全部验收](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/force-user-motion-20261009-graph/graph-force-acceptance.json>)。

## 核心逻辑修复

**队列同时间戳乱序。** 完整核心回归实际复现了两个先后提交的登录任务拿到完全相同的创建时间，随机 UUID 排序把后提交任务提前的问题。现在在持有写事务时分配递增入队时间，SQLite 对历史时间格式及相同时间使用实际插入顺序；跨 Store、重启、冻结时钟、24 个并发提交和旧备份时间格式均执行了行为回归。既有记录不重写，21 表结构保持兼容。PostgreSQL 的事务锁 SQL 契约已覆盖，生产 PostgreSQL 并发仍未实机验收。

**最终分析丢失关系时间。** 前一轮真实 8 账号任务的 15 条关系，在最终分析保存后 `observed_at` 变为空，已知首次观察时间仍保留。这次让不提供新观察时间的摘要沿用已有最近观察时间；兼容仅有首次时间的旧记录，不伪造当前时间。修复前失败、回归和真实任务均留存。最新正式任务的 15 条关系在实际 Flash 分析完成后全部保留来源和最近观察时间。

账号修改仍按证据归属保护：已关联材料、关系或研究节点的账号，不能直接改为另一平台或用户名；更换身份需建立新账号。显示名称、所属组织和标签可以修改，人工字段不被后续采集覆盖。真实主页名称只在身份匹配后更新，原账号 ID、材料和关系保持稳定。

- [队列实际乱序反例](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/queue-fifo-counterexample.json>)。
- [队列、旧备份、重试与任务串扩展回归：183 项通过](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/queue-fifo-regression-legacy.log>)。
- [完整核心及构建回归：889 项通过](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/graph-force-core-accepted.log>)。

## 新成品的真实检索与 Flash 分析

这轮通过普通正式桌面后台执行实际公开账号研究，使用用户已经手工保存的原生加密连接。没有通过桌面调试端口写配置或读取密钥。主要起点为 `zengakuren`，其他三个起点为 `zengakuren_west`、`hu_jichi`、`zenshinch`；空关键词表示账号范围。参数为日本日期最近 90 天、两层、8 个账号、120 条关系、160 条材料、32 次滚动和 900 秒总上限。

任务实际耗时 **414.469 秒，约 6 分 54 秒**，访问 **8 个账号**，研究快照包含 **81 条材料、15 条公开关系**。正式库净新增 **21 条材料**；其余为重新取得或引用的已有记录，不把 81 条冒充全部新增。因达到账号预算而以 `partial / account_budget` 结束，已有成果与最终分析均保存。这是预算内部分完成，不能据此声称完整历史或全部组织网络已查明。

最终真实模型为 **`deepseek-flash`**，调用结果为 `used=true / outcome=ok`。模型实际输入 **80/104 条目录证据**，优先公开简介，再按账号分配材料，每条最多 1,000 字。完整取得的材料保留；模型论证仍受抽样及摘要长度影响。分析形成 **15 条观察发现、9 项待复核身份或组织候选**，候选没有自动变成核验身份或组织归属。

独立来源审查结果如下：

- **81/81** 条材料正文非空；保留的作者网址、规范原帖 ID、账号身份与采集时 DOM 作者匹配。
- **81/81** 条材料有带时区的日期，按日本日期转换后都在用户设定范围内。
- **15/15** 条关系保留来源 URL、有效带时区的观察时间及已知首次时间；最终分析没有再清空最近观察时间。
- 所有分析引用 ID 均能回到保存的证据目录；9 项身份或组织判断全部保持 `candidate`。
- 既有账号 ID 保持一致，人工名称、组织、标签字段保留；重新读取后，前进频道、茨城县劳动组合交流中心等公开显示名称更新。
- 正式库完整性为 `ok`，外键检查无错误；终态后活跃任务为 0。未带私有令牌的健康接口仍返回 401。

这次保存的组织账号关系包括：全学连 → 广岛大学学生自治会、全学连 → `doro_chiba`、全学连关西 → `doro_chiba`、广岛大学学生自治会 → `doro_chiba` 及 → 前进频道。箭头表示本次实际观察到的**单向关注**，不表示支持、隶属或同一派别。每条来源及时间可在图中打开，详见[来源质量与关系示例](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/graph-motion-source-quality.json>)。

[全学连官网的 2026 年新生企划页](https://zengakuren.jp/shinkan-2026/)明确链接前两个起点；`hu_jichi` 和 `zenshinch` 来自此前真实捕获的公开主页，身份仍须独立核验。产品客户端访问独立依据失败时保留候选和 `source_unavailable`，不以其他联网工具访问成功代替产品核验。核对出处、作者、日期与引用有效性也不等于独立证明每条帖子或每项模型论证为真。

- [最新正式任务回执](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/formal-graph-force-account-scope/receipt.json>)。
- [完整研究快照及分析](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/formal-graph-force-account-scope/research-snapshot.json>)。
- [研究图及保存关系](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/formal-graph-force-account-scope/research-graph.json>)。
- [正式应用切换及两处快捷方式](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/formal-graph-force-release-switch.json>)。

此前约 30 分钟的真实任务保存了 32 个账号、136 条关系及 75 条材料，运行于更早版本；后来对其公开快照的真实 Flash 分析耗时 59.484 秒，输入 80/242 条证据。此前双起点账号范围任务实际耗时 516.578 秒，保存 8 账号、15 关系及 97 条材料，Flash 输入 80/120。它们是独立历史证据，不能代替本轮新包的 414.469 秒验收。历史双起点任务的最近观察时间丢失反例已明确记录在本轮机器验收中。

## 每批回归与最终成品验收

| 范围 | 实际结果 | 证据或边界 |
| --- | --- | --- |
| Python 核心逻辑及构建回归 | **889 通过** | `graph-force-core-accepted.log`；1 项已有 Pydantic alias 警告 |
| 前端 Node | **116 通过** | 子代理最终冻结日志；包含布局、手势与账号刷新 |
| 桌面 Node，使用新包私有 Python | **59 通过** | `graph-motion-packaged-desktop-tests.log` |
| 新包实际 HTTP 核心流程 | **14 通过** | 原始资料、去重、持久报告、来源快照、草稿、备份、重启及恢复；隔离夹具 |
| 新包日常可靠性 | **15 通过** | 并发重试、已保存成果终态补写与重启；隔离夹具 |
| 实际父进程退出 | **4 通过** | 包内后台，包括真实中间父进程突然退出 |
| 包内真实资料副本关系图 | **21 组通过，页面错误 0** | 43 节点／142 证据关系；持续拖动、碰撞、松手、收敛、捕获丢失与数据不变 |
| 包内账号图编辑及刷新 | **5 组通过，页面错误 0** | 保留固定位置、视角、账号与边；实际 API 排队，模拟会话就绪，未执行外部采集 |
| 包内产品交互 | **25 项通过，页面错误 0** | 地图、账号、正文、资料清理、键盘与手机布局；合成隔离资料 |
| 独立布局数值审查 | **9 场景通过** | 直接运行实际求解器，不代替浏览器验收 |
| Windows 原生坏配置恢复 | **通过** | 无 key 合成隔离配置；系统加密往返、原坏文件归档、研究及备份可用 |
| Windows 原生研究备份恢复 | **通过，21 表核对** | 原库保留，引用及快照一致；原生选择和确认采用验收注入，未手动操作系统对话框 |
| 包源码、manifest、ZIP 与 Windows portable | **全部通过** | 86 个 Windows / 87 个 Mac 应用文件；没有用户资料、密钥、会话或应用测试 |

[汇总记录](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/graph-motion-delivery-acceptance.json>)包含各项原始结果、实际包及哈希。[包内容审计](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/package-graph-motion-audit.json>)；[原生配置核对](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/native-graph-motion-connection-verification.json>)；[21 表恢复核对](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/native-graph-motion-restore-verification.json>)。

修复前的布局提前停止、停住鼠标邻居不移动、捕获丢失后残留拖动、队列乱序及关系时间丢失证据均保留。账号夹具第一次故意拖动 1 像素后仍比较旧固定基准，独立诊断确认是验收基准问题；修正后在全新资料目录重跑，保留严格位置与缩放断言。主代理第一次包内 HTTP 验收误用 `resources/backend_entry.py`，实际包入口为 `resources/backend/backend_entry.py`；错误调用及日志保留，使用真实原生入口重新通过，没有用错误调用充当产品验收。

## 保底功能与尚待完善的范围

资料库的“清理资料”支持筛选、选择、预览和填写原因，先“移入回收站”，再按需“恢复资料”。彻底删除只作用于已经在回收站的明确选中记录，需要确认不可恢复；已有报告快照独立保留。复采不会撤销手工清理决定。本轮产品交互与核心回归实际覆盖这些行为。

日本地图按所选活动日期内**已核对、已定位、去重的活动数量**计算各地占比。待核对材料、原帖地名提及、日期未知及未定位记录分别展示，不进入分母。地图合成夹具通过不代表正式库已有充分已核对活动数据。

继续完善需要优先补证据与核心能力：扩大组织账号的独立身份出处与派别区分；提高分析覆盖并进行多来源支持度复核；分别验证更多平台的访问及完整性。Mac 原生启动和 Apple `codesign`、实机 PostgreSQL 并发、10,000 条实际采集及 24 小时耐久性尚未验证。当前只能确认这轮真实有界检索与分析、来源保存、修复及成品恢复流程可用，不能把整个产品目标标记为已完成。

本轮创建及继承的 7 个隔离 QA 服务器已按 PID、脚本、资料目录逐一核对后停止，日志与资料保留；子代理创建的 5 个服务器也已停止。正式应用主进程及后台继续普通运行，不使用 QA profile。

## 分发文件

- [Windows ZIP](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/graph-motion-release-190920/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)，SHA256：`7153f3838859bc0205a9a3afc8b92fc677a0f559a85585d5cc2370db60daa8af`。
- [Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/output/product-fixes-20261008/graph-motion-release-190920/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)，SHA256：`02a3db600d446c594ab699ef676ff741cec4e17e9cca509f6c8f1000f6bd1046`。

Mac 包有 44 个 Mach-O、29 个符号链接及跨平台 ad-hoc 签名检查；Apple 原生验证及启动仍待单独验收。当前精确构建记录及压缩包哈希以机器验收为准。
