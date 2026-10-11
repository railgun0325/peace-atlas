# 账号节点轮廓与桌面交付验收

2026 年 10 月 9 日，正式 Windows 构建 `relations-candidate-20261009-094219`。

本批按用户要求，将圆内的小型类型符号改为整个节点的实体轮廓，并加入类型颜色。两个图谱入口共用同一显示方式，账号类型与账号核验继续分别保存和展示。

| 类型 | 节点实体轮廓 | 填色 |
|---|---|---|
| 组织 | 圆角方形 | 绿色 `#496d5d` |
| 个人 | 圆形 | 棕色 `#9b6634` |
| 未判定 | 菱形 | 蓝灰色 `#6c7686` |

核验白勾独立显示，类型未判定的账号也可以有已保存核验。悬停、选中和聚焦不改变类型填色；普通强调使用同形红环，固定节点保留原有灰色虚线规则，某些状态下会覆盖红环。实体轮廓在原有物理半径内，强调外环仍使用既有的半径加 5。小屏全图能完整容纳，需要看节点和标签细节时使用已有缩放和平移。

产品仅改 `interactive_graph.js` 与 `features.css`。`graph_layout.js`、`network_model.js`、`network.js` 和 `registry.js` 均与上一正式版本字节相同；没有变更布局力、动效、账号关系语义或核验规则。源码与截图绑定见[轮廓验收收据](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contours/contour-acceptance.json>)和[独立复核说明](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/audit/independent-review/independent-review-094219.md>)。

## 本批实际测试

| 范围 | 实际结果 |
|---|---|
| 完整后台回归 | 新运行 2043 passed，0 failed；341.05 秒，1 条既有 Pydantic 警告 |
| 完整前端回归 | 新运行 166 passed，0 failed；85.783 秒，包含新增 4 项几何检查 |
| 桌面回归 | 新运行 59 passed，0 failed；14.070 秒，使用上一正式包的私有 Python 执行当前源码 |
| 构建检查 | 新运行 11 passed，0 failed；0.28 秒 |
| 新轮廓浏览器 | 两个入口、桌面与 390px 小屏，18 项通过 |
| 原浏览器回归 | 深度探索 8 组、网络流程 4 组、分析界面 31 项通过；不合并重叠范围 |
| 密集图谱手势 | 隔离合成 36 节点、108 边，21 项通过；包含拖动邻居响应、碰撞、稳定唤醒、暂停及指针捕获丢失 |
| 包内核心流程 | 新包私有 Python 实际运行 14 项离线流程通过，外部请求为 0 |
| Windows 原生 | 隔离资料目录启动、界面、备份、正常退出通过 |
| 旧备份原生恢复 | 固定 21 表备份恢复到 22 表，原来源字节不变；操作系统文件选择器使用验收注入，未手动操作 |
| 资料及退出 | 7 个隔离数据库完整性通过、外键错误 0、活动任务 0；相应进程、监听和 ready 标记均已退出 |

源码测试绑定及实际日志见[本批测试收据](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/delivery/source-tests.json>)。浏览器使用当前产品与受控合成接口响应，未访问正式账号或调用模型。有效修改前反例和夹具错误原件分别保留；后者不计为产品缺陷。

## 新包和正式升级

本批冻结联合清单为 104 个应用文件，Windows 103、Mac 104。新包独立逐文件审计通过：Windows 5721 个普通文件完整读取、CRC 通过，且与可运行目录逐项 SHA 相同；Mac 4262 个普通文件和 29 个符号链接通过。Mac 独立签名审计核对 44 个 Mach-O、47 个切片、159775 个代码页及资源封印；Mac 原生启动和 Apple 原生签名策略未执行。详见[新包独立审计](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/delivery/package-audit-independent.json>)与[新 Mac 签名审计](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/audit/independent-review/macos-signature-audit-094219.json>)。

正式应用在无活动任务的状态下正常关闭旧窗口，保留两个原快捷方式备份，写入并读回新目标，然后由桌面按钮普通启动新 EXE。验证了新包私有 Python、正常资料目录、正常 ready 和公开健康接口；未使用调试口或写入 AI 配置。

正式升级前后 22 张表的全部类型、值、列定义及既有 schema 精确相同：**551 条材料、61 个账号、43 项任务、1 份已有报告、6 项研究记录**。这组数字属于本次升级前后快照，不是后续用户操作的实时数量。依据见[正式升级保留收据](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/delivery/formal-upgrade-preservation.json>)和[完整本地交付收据](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/delivery/local-release-acceptance.json>)。

## 核心研究及操作说明书

上一 `083814` 核心版本的账号类型、自动公开核验、方向和多跳路径、AI 候选排序、通用账号过滤等结果保留在[关系深化验收](RELATIONSHIP_DEPTH_ACCEPTANCE_20261009.md)。本批轮廓修改没有新增真实平台或模型调用，也没有删除正式资料。

此前实际 DeepSeek Flash 分析复用同一上下文的 3 个完整批次并完成 1 个新批次，接受 212/603 条证据，391 条待处理；应用退出后任务 interrupted，断点保留，最终模型报告尚未完成。前后关系 ID 集合相同，都是 156 条，统计刷新不表示新增边。详见[真实模型与来源保留核对](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/qa/native-upgraded-analysis-retry/actual-model-outcome-212.json>)。本次升级保留该研究和所有原始材料。

说明书提供 16 节和 18 张参数、流程表，涵盖从首次设置、检索、账号研究、续跑、核验、过滤、报告，到地图、备份恢复和手动清理。可直接双击[离线操作说明书](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/manual/操作说明书.html>)，或阅读[可编辑源文件](USER_MANUAL_20261009.md)。手册的页面和参数验收不代替真实模型终态或平台覆盖验收。

## 使用和保留入口

- [共用双击启动按钮](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)
- [本批 Windows 应用](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate-20261009-094219/PEACE ATLAS.exe>)
- [本批冻结 Windows ZIP](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/delivery/retained-094219/PEACE-ATLAS-2.0.0-windows-x64-relations-candidate.zip>)，SHA256 `c23670bedc60d982b9d30ce53c9b8b65e2ab65d2f411c772307a466aa7d3fb27`
- [本批冻结 Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 7/research_relationship_depth_20261009/contour-release/delivery/retained-094219/PEACE-ATLAS-2.0.0-macos-arm64-relations-candidate.zip>)，SHA256 `15bfe39eee5421ce396525f54256f1331938be4743a8fb3b701bffcf15a9125c`

旧构建、原始收据、失败证据、备份和材料均保留。手动清理继续使用预览、回收站、恢复，以及仅彻底删除明确选中的回收站资料的流程。
