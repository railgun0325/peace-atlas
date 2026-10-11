# PEACE ATLAS 产品完善验收 · 2026-10-08

本轮交付为 **V2 `-product-candidate`**，沿用研究工作台、EVA 字体和现有预算设置。Windows x64 的实际桌面包已完成公开新闻、去重、证据报告、下载、备份及正常退出验证；Apple Silicon 包完成构建和独立内容、签名结构审计，原生 Mac 启动仍未实测。本记录只给出已取得的证据，不将本轮通过视为整个产品全部平台和外部来源已经验完。

实际源码位于 `C:/Users/wwx/Documents/New project 4/japan-antiwar-intel`，本轮使用现有未提交工作区，没有重置历史改动或生产资料。最终分发以包内 manifest 与源码逐项哈希核对为准。根据用户最新要求，接续子代理使用 **GPT-6.1 Sol / Ultra**。

## 本轮产品变化

| 场景 | 完善后的行为 |
| --- | --- |
| 研究数据备份 | “能力与设置”可以下载正在运行的 SQLite 一致快照；包含已提交的 WAL 数据、资料、报告、研究记录及服务端草稿。临时文件在传输结束、失败或中断后清理，并明确提示当前数据库是否支持。平台会话和 AI 密钥不包含在研究数据库备份中。 |
| 导入材料用于研究 | 内容相同但链接不同或缺失时，研究仍关联实际已有资料；无效记录不会因为链接碰巧相同而误关联旧材料。新增数量和重复数量继续分别记录。 |
| 大研究结果下载 | 界面图谱仍可限制展示，JSON 与 Markdown 下载读取完整保存结果，保留人工核对、证据链接、时间、观察 / 报告 / 推断类别；导出不再受界面展示上限截断。已用 1,201 条材料与 601 条声明验证。 |
| 一致读取 | SQLite 读取和导出在同一快照中完成，真实 WAL 并发回归通过。PostgreSQL 分支使用只读可重复读事务；本轮该分支只做模拟测试。 |
| 下载和错误 | 报告、摘要、图谱和备份统一处理超时、错误响应、文件名与取消。错误响应显示原因，不作为文件下载。 |
| 报告阅读 | Markdown 表格支持转义竖线、列对齐与安全链接，宽表在内部滚动。桌面和 390px 页面检查通过。 |
| 任务与翻页 | 过期页码回到有效页，组织编辑后保留筛选和选择；取消、重试过程中控制重复操作，迟到响应不会重开已关闭的抽屉。 |
| 桌面启动与退出 | ready 标记核对当前进程，启动遵守总时限；关闭等待后台保存和正常退出，之后才按分阶段期限终止异常进程。日志不可写或打开外部页面失败不会使生命周期处理崩溃。 |
| 保存 AI 连接配置 | 先写系统加密的临时配置，再修改后台连接；提交文件失败时回退后台配置。连续提交按序处理，省略密钥不会丢失刚保存的密钥。 |
| 分发完整性 | 构建要求 manifest 文件集合与应用源码清单完全一致；拒绝缺失、额外、重复和越界路径。Windows / Mac 包均通过独立逐文件核对，不带用户数据库、会话或应用测试代码。 |

备份与恢复的用户操作见 [DATA_BACKUP.md](DATA_BACKUP.md)。设置页也直接列出恢复步骤。当前恢复流程是在应用退出后置换数据库，尚未提供界面内恢复按钮。

## 最终验证结果

| 验证 | 最终结果 | 证据 |
| --- | --- | --- |
| 全部后端回归 | **713 通过，1 项既有 Pydantic alias 元数据警告**，100.87 秒 | [完整日志](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/backend-second-full.log>) |
| 前端 Node | **99 通过，0 失败** | [完整日志](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/frontend-node-after.log>) |
| 离线 Chromium | **40 组通过**：应用路由 8、任务串 3、采集 8、关系网络 4、社媒 9、本轮交互完善 8 | [验收汇总](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/product-acceptance.json>)；所有 API 拦截，合成样本，不连接真实账号或 AI |
| 桌面生命周期 | **14 通过**，覆盖异常退出、时限、ready 标记、配置失败回退和连续保存 | [日志](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/desktop-node-final.log>) |
| 构建 manifest | **11 通过** | [日志](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/desktop-manifest-final.log>) |
| Windows 包内独立后端 | **14 项检查通过**：导入、拒绝和重复统计、正文与来源、草稿冲突、持久任务生成报告、下载、在线备份、重启及独立目录恢复；3 次后台正常退出 | [真实 HTTP 流程](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/packaged-flow/product-flow.json>)；运行包内 Python 和后端，确定性样本，不调用外部平台 |
| Windows 原生桌面 | **通过**：真实公开新闻新增 1 条、重复采集重复 1 条、证据报告 1 份、实际点击备份完成下载；应用和后台退出码均 0，ready 残留 0，后台 ERROR 0 | [最终原生验收](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/native-acceptance.json>)、[原始运行记录](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/desktop-final-2/desktop-smoke.json>) |
| 原生桌面备份实物 | **通过**：274,432 字节，SQLite quick_check、外键检查、最新已提交草稿、1 条材料及 1 份报告一致 | [独立检查](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/native-acceptance.json>)；原始运行记录里的 `sqlite_integrity: pending` 由此次后续检查完成 |
| 字体和研究预算 | 实际字体加载、900 字重、无横向溢出；500 / 25 / 3,600 与 10,000 / 200 / 86,400 参数可预览，预览新增任务 0 | 同上；仅预览验证，没有启动 24 小时任务 |
| 分发包独立审计 | Windows 69、Mac 70 个应用文件与当前源码、manifest、包内字节一致，ZIP CRC 通过 | [独立审计](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/package-audit.json>) |
| Apple Silicon 签名结构 | WSL ad-hoc 审计通过：44 个 Mach-O、29 个符号链接，资源、代码页、entitlements 和架构检查通过 | [签名审计](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/mac-signing-audit.json>)；原生 Apple codesign 与 Mac 启动未实测 |

## 保留的失败记录与实采边界

第一次最终 Windows EXE 检查使用关键词 `憲法九条` 和 2026-10-01 至 2026-10-08 期间，公开来源 HTTP 200 返回 2 条，两条均不符合现有相关性规则，其中一条日期也在期间之前。严格要求“必须有实际材料”的验收以退出码 1 停止，后台仍正常关闭。没有删除这次失败，也没有放宽产品的相关性规则。

核对原始返回后，仅将独立验收的期间改为 2026-09-01 至 2026-10-08。相同关键词返回 8 条，接受 1 条、过滤 7 条；重新运行同一成品，在独立资料目录完成采集、重复去重、证据报告及备份下载。原始失败在 [desktop-final](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/desktop-final/desktop-smoke-error.json>)，原因与新期间的来源诊断在 [news-filter-diagnostic.json](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/news-filter-diagnostic.json>)。

实采样本保存公开 RSS 标题、摘要和原始来源链接，不代表获取全文、覆盖全部历史或证实文章主张。此次没有新增 X / Instagram 实采及真实 AI 模型验收；以前的成功和限制仍作为历史记录保留，不能改写成本轮已重新验证。10,000 条与 24 小时是可配置预算，不是已测产量与耐久性。

## 当前交付入口

| 平台 | 文件与状态 |
| --- | --- |
| Windows x64 | [本机启动快捷方式](<C:/Users/wwx/Documents/New project 7/启动 PEACE ATLAS.lnk>)；[已实测 EXE](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-product-candidate-20261008-092902/PEACE ATLAS.exe>)；[可分发 ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-windows-x64-product-candidate.zip>)。ZIP 424,111,500 字节。快捷方式指向本机已测试的完整 portable 目录，移动目录后应从解压目录直接运行 EXE。 |
| Apple Silicon macOS | [ZIP](<C:/Users/wwx/Documents/New project 4/japan-antiwar-intel/dist/PEACE-ATLAS-2.0.0-macos-arm64-product-candidate.zip>)，374,992,390 字节，最低 macOS 13.0；构建与 ad-hoc 审计通过，原生启动待验证。没有 Developer ID 签名或 Apple 公证；Intel Mac 不在此次构建范围。 |

Windows ZIP SHA-256：

```text
95970024d818d10d4b6aa09a72290b379ac93535627649a6cbd9daa5a045e471
```

Apple Silicon ZIP SHA-256：

```text
26d358022557ecc183301611171ee9a753fc7dac54fc7f20275dbff41a53964d
```

原生桌面下载备份 SHA-256：

```text
45852ef6470f0e8a5b0907ec446d9416ab37862aad7e82f376af15901d144ace
```

## 可复查工具与后续验收

`scripts/verify_product.py` 在独立目录运行真实后端，验证从资料到报告、备份、重启和恢复；可以指定成品的私有 Python 与 `backend_entry.py`。`scripts/audit_release.py` 读取构建结果，独立核对 ZIP、manifest 与当前源码。`scripts/verify_desktop_evidence.py` 读取原生 smoke 记录并以 SQLite 实物完成后续校验。执行这些工具无需访问生产用户资料。

机器可读总记录为 [product-acceptance.json](<C:/Users/wwx/Documents/New project 7/output/product-hardening-20261008/product-acceptance.json>)。Mac 原生启动与签名检查、实际 PostgreSQL 服务、新版 X / Instagram 完整链路、真实 AI 配置保存失败的系统环境及长时任务耐久性仍需要对应环境证据；这些项目没有被计入此次通过数。
