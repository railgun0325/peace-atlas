# 日常使用可靠性验收 · 2026-10-08

当前交付为 **V2 `-daily-candidate`**，Windows 成品目录为 `PEACE-ATLAS-2.0.0-windows-x64-daily-candidate-20261008-115220`。本轮解决九项有隔离反例的可靠性问题，保留研究工作台、EVA 离线字体、内容与执行安排分离、预算设置及旧资料。所有子代理使用 **GPT-6.1 Sol Ultra**。整体产品目标未标记完成；交付时目标面板显示暂停。

## 行为变化

| 真实使用问题 | 修复后的行为 |
| --- | --- |
| 提交 A 后继续填写 B，迟到确认清空新草稿 | 只清理对应的提交快照；本机、后台和当前表单保留 B，旧窗口确认不会把新页面带走。 |
| JSON 文件读取晚到覆盖手动输入或新文件 | 只填入仍对应当前选择、未被继续编辑的输入；旧错误也不覆盖当前状态。 |
| 初始草稿 GET 晚到悄悄升级版本 | 只有实际采用后台草稿或明确同步才更新版本，保留多窗口冲突保护。 |
| 单任务与任务串重复排队，漏查更深后代 | 统一持久重试链与写事务；已有排队／运行后代返回 409，成功或硬预算后代不重做，最新失败仍可显式重试。 |
| 报告已保存但终态写入失败，下一工作被领取 | 保留结果、上下文与执行位置，补写成功后再领取下一工作；退出等待已提交结果收尾。 |
| 续查已排队但研究状态仍为取消／结束 | 任务、研究当前任务与 queued 阶段在同一事务中更新，原断点与图谱保留。 |
| AI 配置超时后磁盘与运行状态不一致 | 无法确认请求时，确认旧后台退出，再从原磁盘配置重建服务；中文说明结果，迟到请求不能改变新服务。 |
| 坏 AI 配置阻断整个应用启动 | 保留原件，以无密钥状态打开研究室；设置页说明恢复路径，显式保存前归档原加密字节。 |
| 恢复后坏停止原因导致重试崩溃 | 预检拒绝数组／对象等坏类型、错误根／父任务和循环；旧缺失、null、正常字符串继续兼容。 |

设置页另保留保存等待期间的新输入，明确区分刚才已保存的 A 和尚未保存的 B。长配置存档路径在 390px 下正常换行。配置状态和异常提示不回显密钥或原解密异常。

## 最终验收

| 检查 | 结果与证据 |
| --- | --- |
| 后端全套 | **848 passed，0 failure/error/skipped**；1 个既有 Pydantic alias 元数据警告。[完整日志](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/backend-final.log>)、[JUnit](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/backend-final.xml>)。初轮 840 记录与测试时序修正前后记录另保留；最终全套已包含新恢复检查及修正后的测试。 |
| 前端 | **105 Node、61/61 Chromium 组**通过，9 个脚本退出 0，无未捕获 JS 错误。[Node日志](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/frontend-node-final.log>)、[浏览器汇总](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/browser-final-summary.json>)、[CLI 390px观察](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/settings-cli-observation.json>)。浏览器 HTTP／桌面桥接是明确的合成夹具。 |
| 桌面源码与构建 | **59 桌面测试、11 manifest 检查**通过。[桌面TAP](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/configuration-audit/desktop59-navigation-tests.tap>)、[构建检查](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/build-manifest-tests.log>)。 |
| 两平台包及 Windows portable | Windows **72**、Mac **73** 个应用文件与最终源码／manifest／ZIP 字节一致；CRC通过，无用户数据库、密钥、会话或应用测试代码。Windows 另比较 72 个解压应用文件、EXE、私有 Python 和 manifest（75 个文件）。[独立包审计](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/package-audit.json>)。 |
| 新 Windows EXE 坏配置恢复 | 真正 Electron、renderer、preload IPC、系统 safeStorage、私有 HTTP：坏配置启动、研究和备份可用；空密钥新配置保存，原字节归档，加密回读与后台一致，独立刷新无警告。[原生退出记录](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/desktop-packaged-connection/latest-native-run.json>)、[原生详细检查](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/desktop-packaged-connection/profile-20261008T115435Z-04eafe96/desktop-connection-recovery-checks.json>)。没有模拟系统加密服务或配置 IPC。 |
| 新 Windows EXE 数据恢复 | 应用退出 0，前后两个后台退出 0，无 ready／事务残留。独立只读比对 **21 表**，恢复 2 资料、2 账号、1 报告、2 研究、4 任务、1 草稿，2 个未完成任务中断且没有自动执行；原库、证据、原备份、配置和合成社交 sentinel核验。[独立实物检查](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/native-restore-acceptance.json>)。文件选择和确认是隔离分支注入，未声称人工点击操作系统弹窗。 |
| 包内私有 Python 日常可靠性 | **15 检查**；8 个混合重试请求仅 **1×202、7×409**，终态 SQL 故障期间后续任务保持排队，解除后每个成功任务只保存一份报告，重启后防重复仍有效。[最终包结果](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/daily-packaged-final/daily-behavior.json>)。注入的 legacy report batch 仅用于隔离共享重试守卫；正常 UI 串仍为 collect/research。 |
| 包内私有 Python 原有流程 | **14 项导入／草稿冲突／证据报告／备份／重启／独立恢复检查，4 种真实进程退出场景**通过。[流程结果](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/product-packaged-final/product-flow.json>)、[进程结果](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/parent-packaged-final/parent-lifecycle.json>)。 |

## 分发入口

- [本机双击启动](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[实际验收 EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-daily-candidate-20261008-115220/PEACE ATLAS.exe>)。完整 portable 目录必须保留；移动后从新目录运行或更新快捷方式。正常使用无需另装 Python、Node.js 或 Docker。
- [Windows x64 ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-daily-candidate.zip>)：424,121,543 字节，SHA256 `008784e073b69d78226f65a35e433a49fa8f761038382707614c34c967496653`。
- [Apple Silicon ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-daily-candidate.zip>)：375,025,192 字节，SHA256 `099dcd95c4dd62e451e0c4f32fc5a8d3127f900cb6ee9974fd9638d782d78906`。最低 macOS 13；WSL ad-hoc 核对 44 Mach-O 与 29 符号链接，代码页、资源与架构检查通过。

## 失败证据与验证边界

首批 `114040` 两平台包、构建及恢复验收保存在 [first-build-114040](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/first-build-114040>)。配置原生仪器过早调用 fragment-only loadURL 被 Electron 拒绝；后台健康、原配置未改、保存尚未执行。仪器改为真实侧栏导航，独立刷新等待 did-finish-load；最终包重新构建和验收，没有覆盖失败 profile。[失败与修复记录](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/desktop-packaged-connection/114040-native-failure-and-instrumentation-repair.md>)。

独立复审另实际构造出 stop_reason 数组／对象的恢复后崩溃，证据保存在 [只读复审](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/restore-lineage-audit/REVIEW.md>)；修复后的拒绝、原源文件不变和正常旧字段消费均已加入最终回归。

Mac 原生启动与 Apple 原生 codesign 验证仍 pending，没有 Developer ID 签名或公证；本轮没有构建 Intel Mac。外部平台实际登录、真实 AI 调用、10,000 条产量及 24 小时耐久性没有在本轮新增验收。PostgreSQL 锁契约有测试，但未运行实机并发；没有请求幂等键，不能宣称响应丢失且后代已失败后的网络请求 exactly-once。持续磁盘故障和强制结束进程亦不等同于正常退出补写成功。本轮所有测试使用独立 profile，未操作正式用户数据或凭证。

[机器可读总记录](<C:/Users/wwx/Documents/New project 7/output/daily-hardening-20261008/daily-acceptance.json>)；[本轮计划](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/DAILY_RELIABILITY_PLAN_20261008.md>)；[此前恢复验收](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/RECOVERY_ACCEPTANCE_20261008.md>)；[备份与恢复方法](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/docs/DATA_BACKUP.md>)。
