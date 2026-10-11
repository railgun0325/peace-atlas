"""Read saved report-note progress without changing checkpoint schemas."""
from __future__ import annotations
import copy


def report_note_progress(checkpoint):
    if not isinstance(checkpoint, dict):
        return None
    generation = checkpoint.get("report_generation")
    generation = generation if isinstance(generation, dict) else {}
    if generation.get("complete") is True or generation.get("phase") in {"complete", "completed"}:
        return None
    attempts = generation.get("attempts")
    latest = attempts[-1] if isinstance(attempts, list) and attempts else {}
    latest = latest if isinstance(latest, dict) else {}
    review = latest.get("source_review_input_synthesis") if generation.get("phase") == "review_pending" else None
    synthesis = checkpoint.get("synthesis_state")
    candidates = (("source_review", review), ("synthesis", synthesis),
                  ("legacy", {"progress": checkpoint.get("progress")}))
    for scope, state in candidates:
        if not isinstance(state, dict) or not isinstance(state.get("progress"), dict) or not state["progress"]:
            continue
        return {"scope": scope, "complete": state.get("complete") is True,
                "progress": copy.deepcopy(state["progress"])}
    return None


def report_note_progress_message(detail):
    if not detail:
        return None
    progress = detail["progress"]
    title = "报告来源复核笔记" if detail["scope"] == "source_review" else "证据综合笔记"
    status = "已保存" if detail["complete"] else "正在处理"
    return (f"{title}{status}：第 {progress.get('level', 0)} 层；当前批次 "
            f"{progress.get('current_batch_number', 0)}/{progress.get('planned_batch_count', 0)}；"
            f"本层输入已复核 {progress.get('completed_unit_count', 0)}/{progress.get('total_unit_count', 0)}；"
            f"模型请求尝试记录已完成 {progress.get('completed_request_count', 0)}/{progress.get('model_request_count', 0)} 条；"
            f"复用已审批次 {progress.get('reused_reviewed_batch_count', 0)}。")
