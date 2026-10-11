"""Research checkpoint boundary and portable, evidence-bearing exports."""
from __future__ import annotations

from . import pipeline


def _urls(row):
    return row.get("evidence_urls") or ([row.get("evidence_url") or row.get("source_url")] if row.get("evidence_url") or row.get("source_url") else [])


def _supports_follow_direction(row, source, target):
    if row.get("type") != "visible_follow_list" or row.get("account_key") != source:
        return False
    structured = {"source_key", "target_key"} & row.keys()
    if structured:
        return structured == {"source_key", "target_key"} and row["source_key"] == source and row["target_key"] == target
    # Older saved catalogues have no typed direction. Accept only their exact
    # collector-generated caption; never infer direction from a partial row.
    return row.get("excerpt") == f"公开可见列表：{source} → {target}"


def checkpoint_payload(event, *, max_edges=200, existing=None):
    items, diagnostics = [], list(event.get("diagnostics", []))
    for raw in event.get("items", []):
        try:
            metadata = {**(raw.get("metadata") or {})}
            if raw.get("evidence_id"):
                # The ordinary material repository normalizes platform URLs.
                # Keep the original opaque research citation ID through that
                # normalization so successful model batches can be resumed.
                metadata["evidence_id"] = raw["evidence_id"]
            item = pipeline.enrich_item({**raw, "metadata": metadata, "account_status": "candidate", "evidence_url": ""})
            items.append(item)
        except ValueError as exc:
            diagnostics.append({"ok": False, "outcome": "invalid_item", "message": str(exc)})
    edges = []
    for row in event.get("relations", []) + event.get("edges", []):
        edges.append({**row, "relationship": row.get("relationship") or row.get("relation") or row.get("type", "follows"),
                      "basis": row.get("basis", "observed"), "evidence_urls": _urls(row)})
    analysis = {**(event.get("analysis") or {})}
    catalogue = {row.get("id"): row for row in analysis.get("evidence") or []}
    retained_findings = []
    claims = list(event.get("claims", []))
    for finding in analysis.get("findings", []):
        edge = {**finding, "relationship": finding.get("relation", "mentions"),
                "basis": "inferred" if finding.get("method") in {"ai", "configured_ai"} or finding.get("ai_used") else finding.get("basis", "observed"),
                "evidence_urls": _urls(finding)}
        if edge["relationship"] == "follows" and edge["basis"] != "inferred":
            source, target = edge.get("source_key"), edge.get("target_key")
            ids = finding.get("evidence_ids") or []
            supported = bool(ids) and all(identity in catalogue and
                _supports_follow_direction(catalogue[identity], source, target) for identity in ids)
            if not supported:
                diagnostics.append({"source": "research", "stage": "analysis_persistence", "ok": False,
                    "outcome": "invalid_finding_evidence", "message": "关注分析的引用主体或方向不匹配；未保存该分析关系。"})
                continue
        retained_findings.append(finding)
        if edge.get("source_key") and edge.get("target_key") and edge["basis"] != "inferred":
            edges.append(edge)
        else:
            claims.append({**finding, "subject_key": finding.get("source_key"), "object_key": finding.get("target_key"),
                           "relationship": finding.get("relation", "association"), "statement": finding.get("summary", ""),
                           "basis": edge["basis"], "evidence_urls": edge["evidence_urls"]})
    for affiliation in analysis.get("affiliations", []):
        claims.append({**affiliation, "subject_key": affiliation.get("account_key"), "object_label": affiliation.get("organization", ""),
                       "relationship": "affiliation", "statement": affiliation.get("claim", ""),
                       "basis": "reported" if affiliation.get("status") == "self_declared" else "inferred", "evidence_urls": _urls(affiliation)})
    if "findings" in analysis:
        analysis["findings"] = retained_findings
    known = {row["account_key"] for row in event.get("nodes", [])} | {row["account_key"] for row in (existing or {}).get("nodes", [])}
    identities = {(row["source_key"], row["target_key"], row["relationship"], row.get("basis", "observed")) for row in (existing or {}).get("edges", [])}
    bounded = []
    for edge in edges:
        identity = (edge.get("source_key"), edge.get("target_key"), edge["relationship"], edge.get("basis", "observed"))
        if identity[0] not in known or identity[1] not in known:
            continue
        if max_edges and identity not in identities and len(identities) >= max_edges:
            continue
        identities.add(identity); bounded.append(edge)
    claims = [row for row in claims if row.get("subject_key") in known]
    decisions = [{**row, "reason": row.get("reason") or row.get("basis") or row.get("message", ""),
                  "method": "ai" if row.get("ai_used") or row.get("method") == "ai" else "rules"} for row in event.get("decisions", analysis.get("decisions", []))]
    return {**({"deferred_frontier": event["deferred_frontier"]} if "deferred_frontier" in event else {}),
            "items": items, "accounts": event.get("accounts", []), "nodes": event.get("nodes", []), "edges": bounded,
            "claims": claims, "summary": event.get("summary", {}), "phase": event.get("kind", "research"),
            "frontier": event.get("frontier", []), "visited": event.get("visited", []), "query_plan": event.get("query_plan", {}),
            "diagnostics": diagnostics, "analysis": {**analysis, "decisions": decisions}}


def _text(value):
    return str("" if value is None else value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def _evidence(urls):
    values = []
    for url in urls or []:
        try:
            safe = pipeline._url(url)
        except ValueError:
            continue
        values.append(f"<{safe}>")
    return " / ".join(dict.fromkeys(values)) or "未提供可核对链接"


def research_markdown(graph):
    run, summary = graph.get("run", {}), graph.get("summary") or graph.get("run", {}).get("summary", {})
    from .research_frontier import infer_missing_research_work
    summary = infer_missing_research_work(summary, run.get("params"))
    params = run.get("params", {})
    lines = [f"# 公开资料研究：{_text(params.get('research_goal') or params.get('keyword') or params.get('handle'))}", "",
             f"研究编号：`{_text(run.get('id'))}` · 状态：{_text(run.get('status'))}", "",
             f"日期范围：{_text(params.get('start_date'))} — {_text(params.get('end_date'))}（日本时间 UTC+9）", "",
             "本报告为本次可访问公开资料的保存快照。关注关系不等于组织隶属，共同链接或活动不等于幕后协同。自动结论保留证据及候选状态。", "",
             "## 运行覆盖", "", f"账号 {len(graph.get('nodes', []))} · 关系 {len(graph.get('edges', []))} · 材料 {len(graph.get('items', []))} · 分析声明 {len(graph.get('claims', []))}", "",
             f"停止原因：{_text(summary.get('stop_reason') or '执行结束')}。", ""]
    if any(summary.get(key) for key in ('deferred_count', 'deferred_record_count', 'deferred_relation_count', 'pending_account_work_count')):
        lines += [f"已保存待准入候选 {_text(summary.get('deferred_count', 0))} 个；待处理材料 {_text(summary.get('deferred_record_count', 0))} 条；"
                  f"待处理观察关系 {_text(summary.get('deferred_relation_count', 0))} 条；仍有采集步骤的已准入账号 {_text(summary.get('pending_account_work_count', 0))} 个。"
                  "这些待处理内容不计入已完成采集或已保存证据。", ""]
    if summary.get('frontier_recovery_required') or summary.get('collection_recovery_required'):
        lines += ["旧任务缺少部分候选原始链接或未完成步骤记录；尚不能证明已完整探索，不能通过历史跳过次数推算候选账号数量。", ""]
    if summary.get('pending_seed_query_count') or summary.get('continuation_pending_queries'):
        pending_queries = summary.get('pending_seed_query_count', summary.get('continuation_pending_queries', 0))
        lines += [f"仍待继续的原检索入口 {_text(pending_queries)} 个；已读前缀未穷尽或尚未执行。扩大材料上限后继续同一查询，"
                  "未读部分不计入已保存证据，也不据此宣称完整检索。", ""]
    if graph.get("truncated"):
        lines += ["**当前内容为受展示上限限制的节选，不能视为全部研究证据。请通过研究下载入口获取完整导出。**", ""]
    analysis = graph.get("analysis") or run.get("analysis") or {}
    ai = analysis.get("ai") or {}
    if ai.get("requested"):
        reviewed, available = ai.get("input_evidence_count", 0), ai.get("available_evidence_count", 0)
        pending = ai.get("pending_evidence_count", max(0, available - reviewed))
        lines += ["## 关系分析覆盖", "",
                  f"成功审阅证据摘录 {_text(reviewed)}/{_text(available)} 条；待审 {_text(pending)} 条；成功批次 {_text(ai.get('completed_batches', '旧记录未记录'))}。", "",
                  ("全部可用证据摘录已处理。" if ai.get("complete") is True else "AI 摘录审阅尚未完成，当前结果只能覆盖已成功处理的部分。")
                  + f" 每条模型输入至多 {_text(ai.get('excerpt_char_limit', 1000))} 字；摘录覆盖不表示模型已阅读全文或核验组织隶属。", ""]
        if ai.get("rejected_candidate_count"):
            lines += [f"支持不足的模型候选已过滤 {_text(ai['rejected_candidate_count'])} 条，安全理由保存在分析审计中。", ""]
    rules = analysis.get("rule_analysis") or {}
    if rules.get("omitted_findings") or rules.get("shared_link_pair_limit_reached"):
        lines += [f"规则结果上限 {_text(rules.get('max_findings'))} 条；超出上限的判断 {_text(rules.get('omitted_findings', 0))} 条。"
                  + f" 共同链接配对已检查 {_text(rules.get('shared_link_pairs_checked', 0))} 组，配对上限 {_text(rules.get('shared_link_pair_limit', 1000))} 组。", ""]
    lines += ["## 账号发现路径", "", "| 账号 | 层级 | 上一层 | 发现依据 | 公开证据 |", "|---|---:|---|---|---|"]
    for node in graph.get("nodes", []):
        lines.append(f"| {_text(node.get('account_key'))} | {_text(node.get('depth'))} | {_text(node.get('parent_key'))} | {_text(node.get('discovery_reason'))} | {_evidence(node.get('evidence_urls'))} |")
    lines += ["", "## 关系及证据层级", "", "observed 表示已观察到，reported 表示来源陈述，inferred 表示推断。", "",
              "| 源账号 | 关系 | 目标账号 | 证据层级 | 证据 |", "|---|---|---|---|---|"]
    for edge in graph.get("edges", []):
        lines.append(f"| {_text(edge.get('source_key'))} | {_text(edge.get('relationship'))} | {_text(edge.get('target_key'))} | {_text(edge.get('basis') or '未标注')} | {_evidence(edge.get('evidence_urls'))} |")
    lines += ["", "## 分析与隶属声明及人工复核", ""]
    for claim in graph.get("claims", []):
        lines += [f"- **{_text(claim.get('subject_key'))} → {_text(claim.get('object_label') or claim.get('object_key'))}**：{_text(claim.get('statement'))}",
                  f"  状态：{_text(claim.get('status'))}；依据：{_text(claim.get('basis'))}；证据：{_evidence(claim.get('evidence_urls'))}"]
        if claim.get("reviewed_at") or claim.get("review_note") or claim.get("review_evidence_url"):
            lines += [f"  人工复核时间：{_text(claim.get('reviewed_at'))}；备注：{_text(claim.get('review_note'))}；复核证据：{_evidence([claim.get('review_evidence_url')])}"]
    if not graph.get("claims"):
        lines.append("本次材料尚不能支持组织隶属判断。")
    legacy_text = [row for field in ("findings", "affiliations") for row in analysis.get(field) or []
                   if isinstance(row, str)]
    if legacy_text:
        lines += ["", "## 历史文字（未记录可核对引用）", "",
                  "这些文字仅保留为历史记录，不作为当前关系、隶属或报告引用的依据。", ""]
        lines.extend(f"- {_text(row)}" for row in legacy_text)
    lines += ["", "## 检索决策记录", ""]
    for decision in analysis.get("decisions", []):
        lines.append(f"- {_text(decision.get('action') or decision.get('stage'))}：{_text(decision.get('reason') or decision.get('message'))}（{_text(decision.get('method') or 'rules')}）")
    lines += ["", "## 材料来源", ""]
    for item in graph.get("items", []):
        lines.append(f"- {_text(item.get('title'))} · {_text(item.get('published_at'))} · {_evidence([item.get('url')])}")
    lines += ["", "## 来源诊断", ""]
    for diagnostic in graph.get("diagnostics") or run.get("diagnostics") or []:
        lines.append(f"- {_text(diagnostic.get('platform') or diagnostic.get('source'))} / {_text(diagnostic.get('outcome'))}：{_text(diagnostic.get('message'))}")
    return "\n".join(lines) + "\n"
