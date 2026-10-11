"""Turn an overall research request into finite, non-overlapping queued tasks."""
from __future__ import annotations

import math
from datetime import date, timedelta
from .query_plan import parse_keyword_groups
from .limits import BATCH_RECORDS_MAX, BATCH_TASKS_MAX, record_ceiling


def _shares(total, count):
    whole, remainder = divmod(total, count)
    return [whole + (index < remainder) for index in range(count)]


def build_batch_plan(title, kind, params, total_records):
    if kind not in {"research", "collect"} or isinstance(total_records, bool) or not isinstance(total_records, int) or not 1 <= total_records <= BATCH_RECORDS_MAX:
        raise ValueError(f"任务串需要有效类型及 1 至 {BATCH_RECORDS_MAX} 条总体材料目标。")
    start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    days = (end - start).days + 1
    if days < 1:
        raise ValueError("任务串日期范围无效。")
    keyword = params.get("keyword", "")
    groups = [group["text"] for group in parse_keyword_groups(keyword)] if keyword.strip() and params.get("mode") != "account" else [keyword]
    if params.get("keyword_mode", "any") == "all":
        groups = [keyword]
    if total_records < len(groups):
        raise ValueError(f"总体材料目标不能少于 {len(groups)} 个关键词组；每组至少安排一条。")
    ceiling = record_ceiling(kind)
    per_job = params.get("max_records", ceiling)
    if isinstance(per_job, bool) or not isinstance(per_job, int) or not 1 <= per_job <= ceiling:
        raise ValueError(f"子任务材料上限必须在 1 至 {ceiling} 之间。")
    tasks, warnings = [], []
    for group, group_budget in zip(groups, _shares(total_records, len(groups))):
        chunks = min(days, math.ceil(group_budget / per_job))
        day_shares = _shares(days, chunks)
        budgets = _shares(group_budget, chunks)
        cursor = end
        for width, budget in zip(day_shares, budgets):
            period_start = cursor - timedelta(days=width - 1)
            values = {**params, "keyword": group, "start_date": period_start.isoformat(), "end_date": cursor.isoformat(), "max_records": min(per_job, budget)}
            values.pop("resume_run_id", None)
            tasks.append({"kind": kind, "params": values, "label": f"{group or '@' + params.get('handle', '')} · {period_start} — {cursor}"})
            cursor = period_start - timedelta(days=1)
    if len(tasks) > BATCH_TASKS_MAX:
        raise ValueError(f"本次计划超过 {BATCH_TASKS_MAX} 个子任务。请缩小总体目标或关键词组；任务中心的历史与总排队数量不受 {BATCH_TASKS_MAX} 限制。")
    possible = sum(task["params"]["max_records"] for task in tasks)
    if possible < total_records:
        warnings.append(f"所选日期无法继续拆成不重叠窗口，本次最多安排 {possible} 条；不会重复相同窗口来伪造更多结果。")
    warnings.append("总体目标与每批数量都是保存上限。实际条数取决于来源可见内容、相关性、重复率与运行时间，不保证凑足目标。")
    if len(groups) > 1:
        warnings.append("不同关键词组可能命中同一材料，资料库会按来源链接去重；整个任务串的新增数量可能低于各子任务之和。")
    return {"title": title.strip() or "批量公开资料研究", "tasks": tasks, "total_records": total_records,
            "max_possible_records": possible, "warnings": warnings,
            "strategy": "关键词组分别执行；每组内划分不重叠日期窗口，先查近期。账号与关系探索按各子任务的证据动态调整。"}
