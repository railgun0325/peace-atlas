# 研究范围、预计用时确认与详细进度验收

日期：2026-10-10。此记录覆盖开发仓库中的实现与隔离回归；正式应用、用户数据库、API Key 与原有调试端口未使用。全量回归和新分发包由主代理统一执行。

## 当前行为

- 深度研究的总账号、总关系、总材料和总运行时间默认均为 `0`，表示不另设对应的累计停止条件。研究仍有日期、探索层数、每个账号/查询的条数范围、每个关系方向的候选范围、每页滚动范围；来源耗尽、平台阻止、用户取消和断点恢复仍存在。`0` 不能解释为全部平台内容已读完或事实已验收。
- 用户填完参数后先查看预计用时范围与假设，随后确认开始。修改参数会使原预估失效。预估接口不采集、不调用付费模型、不创建任务；预估为低置信度的参数情景推算，明确说明尚未用当前机器与平台的速度校准，实际用时可能超出范围。
- 后端也强制预估确认。签名 `preview_token` 绑定规范化参数、到期时间和操作；有效期 1800 秒。新研究、研究任务串和原研究续查分别验证对应参数，不能拿旧参数或另一轮续查的 token 开始。
- 范围内待执行工作耗尽可自然完成。达到层数边界的范围外线索独立记录，不再仅因存在范围外候选而将整项任务判为部分完成。范围内待处理分支、平台失败、未完成 AI 分析和保存容量缺口仍保留真实的部分完成/失败状态。
- 单分支完成即保存。无总时限任务仍可取消；取消时保留最后的在途分支断点。`0` 关系上限不会在 checkpoint 或导出时变成零条边。
- 任务中心显示查询已完成/计划数量、已处理/待探索账号、已保存材料/账号/关系、待审分析证据、最近活动及实际停止原因。探索百分比明确只描述已知工作。
- 报告详细显示来源阅读、证据笔记的层数和批次、本层输入单元、模型响应数量、复用已审批次、草稿尝试与来源/研究目标复核阶段。已读完 601/601 不再是唯一状态。
- 报告运行时间 `0` 会向 pipeline 传入 `time_budget_seconds=None`，总任务等待也没有额外计时；每个 HTTP 请求仍保留超时。报告和重试 API、前端均允许此值。
- Harness 决策数和工具数允许 `0` 或 `2..4096` / `2..65536`，显式 0 不会被前端默认值覆盖。Harness 引擎/侧车总时限改动由主代理负责。

## 参数边界

| 参数 | 实际服务范围/默认 |
| --- | --- |
| 探索层数 | 1–8，默认 2 |
| 总账号停止上限 | 0–10,000，默认 0 |
| 总关系停止上限 | 0–100,000，默认 0 |
| 总材料停止上限 | 0–1,000,000，默认 0 |
| 每页滚动 | 1–5,000，默认 10 |
| 总运行时间 | 0 或 30–2,592,000 秒，默认 0 |
| 每账号/查询材料范围 | 1–50,000，研究表单默认 1,000 |
| 每个关系方向候选范围 | 1–5,000，研究表单默认 100 |
| 任务串总体材料目标 | 1–1,000,000，默认 1,000 |
| 任务串数量 | 最多 200 |
| 报告总运行时间 | 0 或 60–2,592,000 秒，界面默认 0 分钟 |

这些有限正值是可选提前停止预算，增加预算并不承诺得到相同数量的真实材料。未修改普通采集的产品范围：最多 100 条/20 滚动/10 账号；新表单普通采集条数默认 30、滚动默认 5，服务默认滚动为 8。普通资料库报告仍明确拒绝超过 5,000 条的日期选择，提示缩小范围；不是静默截断。保存摘要和平台单请求容量仍有边界，遇到缺口不能假称完成。

## API 接口

1. `POST /api/v2/research/estimate`：请求体为完整研究参数；返回 `lower_seconds`、`upper_seconds`、`confidence`、`basis`、`assumptions`、`scope`、`preview_token`、`valid_for_seconds` 等。
2. `POST /api/v2/jobs`：研究任务请求为 `{kind:"research", params:{...}, preview_token:"..."}`；未预估、参数不匹配、过期均返回 422 且不入队。
3. `POST /api/v2/research/batch-plan` 后使用匹配的任务串预览 token 调用 `/api/v2/jobs/batch`；token 绑定规范化标题、类型、参数与总体目标。
4. `POST /api/v2/research/{run_id}/continue-estimate`：只接收可调整的 `max_accounts`、`max_edges`、`max_records`、`time_budget_seconds`。`/continue` 同样四字段加 `preview_token`；原日期、起点、目标、层数与查询保持保存状态。token 还绑定 `run_id` 和当前任务，活动中的同一研究拒绝重复续查。
5. create_app 已调用 `agent_api.register_agent_api` 一次。Agent 模块由另一代理实现，保留独立 `/agents/v1` 授权通道；现有 `/api/` 认证未豁免。

## 对应回归

- 研究引擎、预算、API、续查、任务串、前沿流水线：218 passed，1 依赖警告，138.95 秒。
- 新按范围完成和阶段进度用例：最终 6 passed，32.04 秒。包含 251 个账号/250 条边自然完成，无总时限取消，必需/绑定/过期预估确认，worker 保存 45 个账号/45 条材料/259 条边并导出，以及报告 0 时长贯通。
- 新报告阶段过渡用例此前也单项 1 passed，1.39 秒。实际 JobRunner checkpoint 从阅读到综合再到来源复核推进 55→70→88；已完成 Harness 状态不会遮盖综合批次事件；最后任务成功。
- 报告分析、研究报告、任务通道、终态、Harness 集成与 Agent API：65 passed，29.72 秒。
- 预算、续查模型、采集/任务串工具前端相关用例：33 passed，0 failed。
- 全部前端最初为 165 passed / 1 failed（60.36 秒）；失败为旧“51 任务/10001 材料必须拒绝”的夹具。已改为真实新边界 201 / 1,000,001，对应相关 33 项重新通过。全部前端最终由主代理统一重跑。

对应命令（均从真实仓库运行）：

```text
cd analysis-worker
python -m pytest tests/test_research_engine.py tests/test_workbench_budget_limits.py tests/test_workbench_research_api.py tests/test_workbench_research_continuation.py tests/test_workbench_research_batches.py tests/test_workbench_frontier_pipeline.py -q
python -m pytest tests/test_workbench_research_optional_scope.py -q
python -m pytest tests/test_workbench_research_optional_scope.py::test_report_progress_moves_from_reading_to_synthesis_to_source_review -q
python -m pytest tests/test_workbench_report_analysis.py tests/test_workbench_research_reports.py tests/test_workbench_job_lanes.py tests/test_workbench_terminalization.py tests/test_workbench_harness_report_integration.py tests/test_workbench_agent_api.py -q
node --test app/static/workbench/tests/collection_tools.test.mjs app/static/workbench/tests/budget_limits.test.mjs app/static/workbench/tests/research_resume_model.test.mjs
node --test app/static/workbench/tests/*.test.mjs
```

## 真实浏览器验收

均为真实 Chromium、真实 loopback HTTP/FastAPI/UI、全新浏览器状态与 QA SQLite；不截获 API 响应，阻止外网，未使用正式资料或模型。

- `output/playwright/research-scope-20261010-06/result.json`：5 项通过，页面错误 0，外部请求 0。研究预估只读、改参数失效、确认最新参数一次入队、报告 0 分钟默认、详细来源/综合/草稿复核显示。截图 01–07 保留。报告进度值为保存状态显示夹具，不代表模型实际执行或外部事实验收。
- `output/playwright/frontier-estimate-20261010-01/results.json`：5 组通过，页面错误 0，外部请求 0。真实 runner、引擎、checkpoint、数据库重开与续查界面；只有公开来源适配器为合成夹具。预估不入队，确认 token 后仅一次续查，不重读原账号；保存三账号真实有向关系图、新闻原查询补查与自然终态，390px 页面无横向溢出。截图 01–06（及 02b 预估）保留。

```text
python scripts/verify_research_scope_browser.py --output output/playwright/research-scope-20261010-06
python scripts/verify_frontier_recovery_browser.py --output output/playwright/frontier-estimate-20261010-01
```

## 失败证据与修复边界

| 反例 | 保留证据与处理 |
| --- | --- |
| 旧预算与不需要预估的测试契约 | 初次后端 31 failed / 187 passed，前端 70/72；保留工具执行记录。测试现在调用真实 estimate→confirm，不在服务端放行旧请求；原来源/终态断言未降级。 |
| 研究默认候选 100 超过底层读者旧上限 50 | 读者候选范围同步至 5,000；底层滚动同步新研究范围。普通 CollectParams 仍保留原范围。 |
| 合成帖 URL 在不同账号全用 status/1 导致全局规范化去重 | 新 worker 夹具初次 2 failed / 3 passed，271.49 秒；修为平台全局唯一 numeric status ID，没有修改产品去重。ReportParams 夹具补必需日期；前五项随后通过。 |
| 新进度用例引用不存在的 Store.list_job_events | 1 failed / 5 passed；修夹具为真实 Store.job_events，并验证最终成功状态；专用过渡用例随后通过。 |
| 浏览器脚本操作未展开的研究高级字段 | `research-scope-20261010-01/failed-result.json` 保留；先展开字段再操作，02–04 均通过。 |
| 预估面板直接 replaceChildren 将条件 false 显示为文本 | `research-scope-20261010-04/02-parameter-bound-estimate.png` 保留。改用已有 setContent；05 暴露新增导入遗漏并保留 failed-result.json；补导入后 06 通过，脚本增加无 false 文本断言。 |

无真实平台长期采集、真实模型付费调用或百万条规模运行已完成的声明。251 账号自然完成和 259 条边持久化是确定性行为反例；其意义是证明旧 40/200 硬停已解除，不是现实平台覆盖率、耗时或事实正确性的测量。
