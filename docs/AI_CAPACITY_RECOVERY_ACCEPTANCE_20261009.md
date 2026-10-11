# Flash 容量、原文压缩与恢复验收（030610）

2026-10-09，正式 Windows 已通过桌面快捷方式普通启动 `relations-candidate-20261009-030610`。本记录只证明该成品的实际验收；后续工作区修改必须另外回归与打包。真实模型综合报告仍未完成，整体产品目标未完成。

## 容量与来源准确性

默认模型为 `deepseek-flash`。取消关系分析与报告阅读调用层过早拦截的大输入字符限额，改为根据实际模型上下文检查完整消息，并预留本次输出。DeepSeek 当前官方文档列出 Flash 的 1M 上下文与最大 384K 输出；总上下文仍须同时容纳消息和输出。[官方模型说明](https://api-docs.deepseek.com/quick_start/pricing/)

确实放不下时，压缩结论保留原来源 ID、连续文本片段、原文哈希及逐字引用；再次对原始材料核对日期、否定、条件、主体和账号限定，不以压缩后的模型文字作为原始证据。修正、取消与失败都保留断点，不把候选或截断文本标为接受。报告正文的来源编号独立于 URL，同 URL 的不同来源不会被最后一条覆盖。

真实 252 材料报告采用 16 账号冻结快照：159 条正文、79 条有方向关注观察、14 条非空简介；另两条空简介明确保留缺口。真实 Flash 的 21 批来源阅读全部完成，252 条原样引用通过检查，已知 token 用量下界 311,391。第 22 次草稿请求返回 HTTP 402，尚无最终草稿或最终语义接受。正常完整报告恢复已验证能复用全部阅读，直接进入草稿，重复阅读请求为 0。

最终请求的保守输入上界为 949,924，可用输入空间为 1,015,808，容量估算可容纳；这不是满 1M 的提供方压力测试。103 材料另以较小的 QA 窗口触发压缩，实际查出日期、简介与帖子范围错误。第三次笔记修订仍漏掉原文一个“の”，被逐字引用门禁拒绝；第四次修订请求返回 402，未消耗修订槽位。原稿、反馈、哈希与断点均保存，未伪造最终接受。

[252 材料总收据](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/core-report-preflight/prepare-006/final-status.json>)；[正常报告零调用恢复](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/core-report-preflight/prepare-006/local-resume-audit.json>)；[103 材料当前压缩边界](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/audit/actual004-quote-balance-blocked.json>)。

## 实际账号研究与正式恢复

同一公开账号研究由 16 个继续扩至 24 个，保存 221 条公开正文、118 条有方向关注关系；仍有 39 个待准入账号和 40 条待保存关系，采集因账号预算部分完成。每账号 20 条正文、每方向 8 个邻居的原取样限制仍在，不代表完整历史或完整组织网络。关注、自述、文本提及和组织归属候选继续分开。

修复资料库去重将本次观察换成更早全局记录、导致已接受批次失配的问题。恢复必须逐项匹配账号、原链接、完整正文哈希、发布日期和原观察哈希，且具有旧记录时间证明；在输入副本恢复原观察身份，不改全球资料和旧断点。变更正文、作者、日期、上下文，身份冲突、证据不足或已永久删除都拒绝复用。

正式新版的普通重试任务为 `fa4f27d4-2f67-405f-9d30-18094e6e3f27`。产品自动选择仅分析模式，不重新采集，复用原已接受 80/363 条、剩余 283 条。一轮新请求明确返回 HTTP 402，未循环重试，没有新增模型接受结果。17 项独立核对通过：原正文、账号与简介、关系方向、来源和观察时间、研究参数及延后资料保持一致；原接受批次和上下文哈希相同。关系行的 `updated_at` 是本次数据库写入时间变化，原始观察时间未改变。数据库只新增这次正常重试任务，没有新增报告或研究。

HTTP 402 在 DeepSeek 官方说明中表示余额不足。当前正式连接和 QA 已分别有真实 402 证据，未读取或比对两者密钥，也未读取提供方账单。余额条件解除后可以继续成功断点。[官方错误说明](https://api-docs.deepseek.com/quick_start/error_codes/)；[正式原生请求记录](<C:/Users/wwx/Documents/New project 7/output/live-core-expansion-20261009/formal-native-ai-retry-030610/receipt.json>)；[17 项独立前后核对](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/delivery/formal-ai-retry-acceptance.json>)。

## 清理、性能与成品验证

手动清理继续采用预览与原因、回收站、恢复、选中资料永久删除。修复仅被 AI 待分析响应引用、没有资料或边关联的来源可能漏清的问题；同一事务清除缓存并保存墓碑。最终冻结源码的独立 SQLite 反例通过，永久删除后未复活，外键错误 0，未使用正式数据或网络。[最终清理反例](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/audit/pending-only-cleanup-frozen-final/receipt.json>)。

隔离性能夹具含 2,400 个候选、24,000 条路径与约 118 MB 正文。图 API 从 7.93 秒降至 0.87 秒，列表从 2.32 秒降至 0.88 秒；浏览器首屏从 10.94 秒降至 2.41 秒，页面错误 0。隔离资料库的 1,359 条关系可分页取完；超出扫描范围的计数明确显示下界。单来源永久清理约 10.18 秒及 69,614 次 SQL，尚未优化；多项大研究和长期耐久性也未宣称通过。[性能与浏览器收据](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/performance/audit-receipt.json>)。

| 本成品验证 | 实际结果 |
| --- | --- |
| 后端完整回归 | 1,700 通过，0 失败；225.05 秒，1 条既有 Pydantic 警告 |
| 前端 / 桌面 / 构建器 | 129 / 59 / 11 通过 |
| 独立包审计 | Windows 99 个应用文件、5,717 个普通文件；Mac 100 个应用文件、4,258 个普通文件及 29 个符号链接；全 CRC 与冻结 SHA 匹配 |
| 包内私有 Python 核心流程 | 14 项通过，资料导入、正文与原链接、报告、下载、在线备份、重启及恢复 |
| 原生运行与旧备份恢复 | 新 EXE 实际运行；旧 21 表备份恢复为固定 22 表，逐表通过，原备份不变；系统手动文件选择器未手动验收 |
| 正式升级与双击入口 | 正常关闭旧窗口，两个快捷方式回读一致，普通启动新 EXE → 私有 Python → 正式 profile → public health 通过 |
| 升级数据保留 | 重试入队前，22 张表所有行、SQLite 存储类型和结构完全一致；370 资料、50 账号、36 任务、1 报告、6 研究保留，外键与完整性通过 |

[本成品机器验收](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/delivery/local-release-acceptance.json>)；[全部回归与日志指纹](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/delivery/source-tests.json>)；[正式 22 表升级核对](<C:/Users/wwx/Documents/New project 7/output/long-context-quality-20261009/delivery/formal-upgrade-preservation.json>)。

Windows ZIP SHA256：`a71f0a8369aa7d59d91ce0c85066e3017efc890e3ba03b6614ba410de34b9ffa`。Mac ZIP：`a47caff698c74704bc136c0b64f50bc2d8790c8007dcddacaf9707111c545ef7`。两平台冻结包保留在本轮 `delivery/retained-030610`；旧 010745 成品与备份仍保留。Mac 仅完成跨平台 ad-hoc 签名与内容校验，Apple 原生 codesign 和 Mac 实机运行未验收；真实最终报告及压缩接受仍待余额条件解除后验证。
