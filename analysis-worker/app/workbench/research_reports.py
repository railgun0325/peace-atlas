"""Bind report inputs to one saved public research snapshot and its goal."""
from __future__ import annotations

import copy

from .account_identity import research_registry_context
from .pipeline import PipelineError


def validate_report_run(export):
    run = export["run"]
    if run.get("status") in {"queued", "running"}:
        raise PipelineError("研究仍在运行，请完成或停止后生成固定来源的分析报告。")
    return run


def _report_account(account):
    """Carry only reviewed public identity attached to this exact saved key."""
    projected = {key: copy.deepcopy(account.get(key)) for key in
                 ("account_key", "name", "status", "organization", "evidence_url", "platform", "handle", "url")}
    # Older report fixtures have only a key and status. Missing identity fields
    # remain unknown; arbitrary importer metadata is never sent to the report.
    candidate = {**account, "status": account.get("status", "candidate"),
                 "metadata": account.get("metadata") if isinstance(account.get("metadata"), dict) else {}}
    public = research_registry_context([candidate]).get(account.get("account_key")) or {}
    identity = (public.get("metadata") or {}).get("account_identity")
    projected["metadata"] = {"account_identity": copy.deepcopy(identity)} if identity else {}
    return projected


def prepare_research_report(export):
    run = validate_report_run(export)
    material = copy.deepcopy(export.get("items") or [])
    source_roles = {str(item["id"]): "saved_public_material" for item in material}
    accounts = {account.get("account_key"): account for account in export.get("accounts") or []}
    evidence = {row.get("id"): row for row in (run.get("analysis") or {}).get("evidence") or []}
    observed_ids = {identity for edge in export.get("edges") or [] if edge.get("basis") == "observed"
                    for identity in [edge.get("evidence_id"), *(edge.get("evidence_ids") or [])] if identity}
    observed_edges = {identity: edge for edge in export.get("edges") or [] if edge.get("basis") == "observed"
                      for identity in [edge.get("evidence_id"), *(edge.get("evidence_ids") or [])] if identity}
    for identity, row in evidence.items():
        role = row.get("type")
        if role == "visible_follow_list" and identity not in observed_ids:
            continue
        if role not in {"public_profile", "visible_profile", "profile", "visible_follow_list"}:
            continue
        account = accounts.get(row.get("account_key")) or {}
        if account.get("status") == "excluded":
            continue
        body = str((account.get("profile") or {}).get("bio") or row.get("excerpt") or "") if role != "visible_follow_list" else str(row.get("excerpt") or "")
        if not body.strip() or not row.get("url"):
            continue
        item_id = "observation-" + str(identity)
        edge = observed_edges.get(identity) or {}
        source_roles[item_id] = "visible_follow_list" if role == "visible_follow_list" else "public_profile"
        material.append({"id": item_id, "url": row["url"], "title": ("公开可见关系列表" if role == "visible_follow_list" else "公开账号简介") + " · " + str(row.get("account_key") or ""),
            "content": body, "type": "social", "source_type": "public_observation",
            "source_name": row.get("account_key") or "公开观察", "account_status": account.get("status", "candidate"),
            "account_key": row.get("account_key"), "published_at": None,
            "metadata": {"content_kind": "public_observation", "observed_at": row.get("observed_at"),
                "collected_at": row.get("observed_at"), "evidence_id": identity,
                "research_report_source_role": source_roles[item_id],
                "relation_type": (edge.get("relationship") or edge.get("relation") or edge.get("type")) if role == "visible_follow_list" else None,
                "source_key": edge.get("source_key") or row.get("source_key"),
                "target_key": edge.get("target_key") or row.get("target_key"),
                "original_source_char_count": row.get("source_char_count"),
                "saved_excerpt_truncated": row.get("excerpt_truncated", False)}})
    context = {"run_id": run["id"], "run_revision": run.get("revision"),
        "research_goal": (run.get("params") or {}).get("research_goal", ""),
        "research_params": copy.deepcopy(run.get("params") or {}),
        "query_plan": copy.deepcopy(run.get("query_plan") or {}),
        "summary": copy.deepcopy(run.get("summary") or {}), "status": run.get("status"),
        "diagnostics": copy.deepcopy(run.get("diagnostics") or []),
        "source_roles": source_roles,
        "accounts": [_report_account(account) for account in accounts.values()],
        "relationships": copy.deepcopy(export.get("edges") or []),
        "relationship_claims": copy.deepcopy(export.get("claims") or []),
        "boundaries": ["账号主体与组织隶属必须单独核验；公开帖文只能证明该可见账号发表了相应内容。",
            "公开简介是账号自称；关注、提及和共同主题不证明隶属、指挥或协调行动。",
            "候选或人工复核关系属于待比较的研究线索，不能替代支持事实的原帖。",
            "观察时间不等于发布时间；公开简介和关系列表不能证明所选期间内发生了组织活动。",
            "研究停止原因、未访问分支和来源失败必须作为覆盖限制说明。"]}
    return material, context
