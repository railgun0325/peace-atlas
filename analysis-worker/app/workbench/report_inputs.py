"""Stable public report inputs and free retry comparisons; no provider calls."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json


_OPERATIONAL = {"_workbench_retry", "_report_input_ref", "_report_input_mode",
                "analysis_harness", "time_budget_seconds", "harness_max_model_calls",
                "harness_max_tool_calls"}
_RUNTIME_FIELDS = {"captured_at", "model", "reasoning_effort", "max_output_tokens"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def parameter_scope(params):
    return {key: copy.deepcopy(value) for key, value in params.items() if key not in _OPERATIONAL}


def project_report_job(job):
    """Keep full frozen inputs internal; ordinary job polling needs a receipt."""
    result = job.get("result") if job else None
    frozen = result.get("report_input_snapshot") if isinstance(result, dict) else None
    if not isinstance(frozen, dict):
        return job
    snapshot = frozen.get("snapshot")
    items = snapshot.get("items") if isinstance(snapshot, dict) else None
    summary = {key: frozen.get(key) for key in ("version", "sha256", "input_fingerprint", "captured_at")}
    summary.update(source_count=len(items) if isinstance(items, list) else None, summary_only=True)
    return {**job, "result": {**result, "report_input_snapshot": summary}}


def capture_report_input_snapshot(snapshot, params):
    stable = {key: copy.deepcopy(value) for key, value in snapshot.items() if key not in _RUNTIME_FIELDS}
    value = {"version": 1, "params_scope": parameter_scope(params), "snapshot": stable,
             "captured_at": snapshot["captured_at"]}
    value["input_fingerprint"] = digest({key: value[key] for key in ("version", "params_scope", "snapshot")})
    value["sha256"] = digest(value)
    return value


def validate_report_input_snapshot(value, params):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("原报告缺少完整输入快照；请免费预览，并显式选择按最新审核状态重新生成。")
    if (set(value) != {"version", "params_scope", "snapshot", "captured_at", "input_fingerprint", "sha256"}
            or not isinstance(value.get("snapshot"), dict)
            or not isinstance(value.get("captured_at"), str)
            or value.get("sha256") != digest({key: entry for key, entry in value.items() if key != "sha256"})):
        raise ValueError("原报告完整输入快照校验失败；未用最新来源替代旧输入。")
    if value["params_scope"] != parameter_scope(params):
        raise ValueError("报告参数与原完整输入快照范围不一致；未执行分析。")
    if value["input_fingerprint"] != digest({key: value[key] for key in ("version", "params_scope", "snapshot")}):
        raise ValueError("报告输入快照的输入指纹不一致；未执行分析。")
    snapshot = value["snapshot"]
    items = snapshot.get("items")
    if (not isinstance(items, list) or not items or not isinstance(snapshot.get("scope"), dict)
            or snapshot.get("mode") != params.get("mode", "evidence")
            or any(not isinstance(row, dict) or row.get("citation_id") != f"S{index:03d}"
                   for index, row in enumerate(items, 1))
            or (params.get("research_run_id") and not isinstance(snapshot.get("research_context"), dict))):
        raise ValueError("报告完整输入快照的来源顺序或研究上下文无效；未执行分析。")
    return copy.deepcopy(value)


def saved_input(job):
    return (job.get("result") or {}).get("report_input_snapshot")


def resolve_report_input(store, job):
    own = saved_input(job)
    reference = job["params"].get("_report_input_ref")
    if reference is not None:
        if not isinstance(reference, dict) or set(reference) != {"job_id", "sha256"}:
            raise ValueError("原报告完整输入快照引用无效。")
        source = store.get_job(reference["job_id"])
        if source is None or source.get("kind") != "report":
            raise ValueError("原报告完整输入快照引用缺失。")
        value = validate_report_input_snapshot(saved_input(source), job["params"])
        own_value = validate_report_input_snapshot(own, job["params"]) if own is not None else None
        if reference["sha256"] != value["sha256"] or own_value and own_value["sha256"] != value["sha256"]:
            raise ValueError("原报告完整输入快照引用校验失败。")
        return value
    if own is not None:
        return validate_report_input_snapshot(own, job["params"])
    if job["params"].get("_workbench_retry") and job["params"].get("_report_input_mode", "original") == "original":
        raise ValueError("原报告缺少完整输入快照；请免费预览，并显式选择按最新审核状态重新生成。")
    return None


def current_report_input(store, params):
    from .pipeline import prepare_report_input
    if params.get("read_original", False) and not params.get("research_run_id"):
        raise ValueError("此报告启用了读取原文；最新状态重生成不能静默跳过采集。请新建报告，先读取原文再冻结输入；已有完整原输入仍可继续。")
    context = None
    if params.get("research_run_id"):
        from .research_reports import prepare_research_report
        material, context = prepare_research_report(store.export_research(params["research_run_id"]))
    else:
        material = store.report_items(params.get("start_date", ""), params.get("end_date", ""),
                                      params.get("include_news", True), params.get("include_social", True))
    snapshot = asyncio.run(prepare_report_input(material, params, research_context=context))
    return capture_report_input_snapshot(snapshot, params)


def _changes(old, new, key):
    left = {str(row.get(key)): row for row in old}
    right = {str(row.get(key)): row for row in new}
    return {"added": len(right.keys() - left.keys()), "removed": len(left.keys() - right.keys()),
            "changed": sum(left[k] != right[k] for k in left.keys() & right.keys())}


def reading_reuse(current, checkpoint):
    from .report_analysis import (_digest, _reading_context, _reading_units,
                                  source_samples, validate_source_reviews, _REVIEW_FIELDS)
    snapshot = current["snapshot"]
    main = [row for row in snapshot["items"] if row.get("use_in_conclusions")]
    sources, _ = source_samples(main)
    units, _, _ = _reading_units(sources)
    matched = set()
    if (isinstance(checkpoint, dict) and checkpoint.get("version") == 3
            and checkpoint.get("research_context_fingerprint") == _digest(_reading_context(snapshot.get("research_context")))):
        saved_hashes = checkpoint.get("reviewed_fragment_fingerprints") or {}
        for row in checkpoint.get("fragment_reviews") or []:
            if not isinstance(row, dict):
                continue
            identity = row.get("unit_id")
            if identity not in units or saved_hashes.get(identity) != _digest(units[identity]):
                continue
            raw = {key: row.get(key) for key in _REVIEW_FIELDS}
            raw["citation_id"] = identity
            try:
                validate_source_reviews({"source_reviews": [raw]}, [units[identity]])
            except ValueError:
                continue
            matched.add(identity)
    return {"matched": len(matched), "pending": len(units) - len(matched), "total": len(units),
            "unit": "fragments", "validation": "exact_fingerprint_and_exact_quote", "paid_verified": False}


def retry_preview(store, requested, latest):
    preview = {"job_id": requested["id"], "latest_job_id": latest["id"], "kind": latest["kind"],
               "status": latest["status"], "saved_params": copy.deepcopy(latest["params"])}
    if latest["kind"] != "report":
        return preview
    original, original_error, current, current_error = None, "", None, ""
    try:
        original = validate_report_input_snapshot(saved_input(latest), latest["params"])
    except ValueError as error:
        original_error = str(error)
    try:
        current = current_report_input(store, latest["params"])
    except (ValueError, LookupError) as error:
        current_error = str(error)
    preview["original_input"] = {"available": original is not None, "sha256": original["sha256"] if original else None,
        "input_fingerprint": original["input_fingerprint"] if original else None,
        "source_count": len(original["snapshot"]["items"]) if original else None, "reason": original_error}
    preview["current_input"] = {"available": current is not None, "sha256": current["input_fingerprint"] if current else None,
        "input_fingerprint": current["input_fingerprint"] if current else None,
        "source_count": len(current["snapshot"]["items"]) if current else None, "reason": current_error}
    changes = {"compared_to": "saved_original" if original else "unknown_original",
               "source_count": None, "account_count": None, "claim_count": None,
               "source_counts": None, "account_counts": None, "claim_counts": None, "context_changed": None}
    if original and current:
        old, new = original["snapshot"], current["snapshot"]
        old_context, new_context = old.get("research_context") or {}, new.get("research_context") or {}
        for singular, left, right, key in [
                ("source", old["items"], new["items"], "id"),
                ("account", old_context.get("accounts", []), new_context.get("accounts", []), "account_key"),
                ("claim", old_context.get("relationship_claims", []), new_context.get("relationship_claims", []), "id")]:
            counts = _changes(left, right, key)
            changes[singular + "_counts"] = counts
            changes[singular + "_count"] = sum(counts.values())
        changes["context_changed"] = old_context != new_context
    preview["changes"] = changes
    checkpoint = (latest.get("result") or {}).get("report_checkpoint") or {}
    preview["reading_reuse"] = reading_reuse(current, checkpoint) if current else {
        "matched": 0, "pending": None, "total": None, "unit": "fragments",
        "validation": "exact_fingerprint_and_exact_quote", "paid_verified": False}
    changed = not original or not current or original["input_fingerprint"] != current["input_fingerprint"]
    stages = {"harness": "harness_state", "draft": "report_generation", "compression": "synthesis_state"}
    preview["stage_reuse"] = {
        "original": {"reading": "requires_runtime_validation" if original else "unavailable",
                     **{stage: ("requires_runtime_validation" if checkpoint.get(field) else "not_saved")
                        if original else "unavailable" for stage, field in stages.items()}},
        "latest": {"reading": "matching_fragments_only", **{stage: "not_saved" if not checkpoint.get(field)
                       else "invalidated" if changed else "requires_runtime_validation" for stage, field in stages.items()}}}
    preview["cost_level"] = ("unavailable" if current is None else "new_analysis_with_matching_readings_only"
                             if changed else "resume_candidate_requires_runtime_validation")
    preview["may_use_model"] = latest["params"].get("mode") == "ai"
    preview["may_read_public_network"] = False
    return preview
