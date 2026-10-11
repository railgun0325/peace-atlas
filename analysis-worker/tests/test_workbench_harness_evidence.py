"""Evidence-only Harness behavior against saved report and graph semantics."""
import copy
import hashlib
import json

import pytest

from app.workbench.harness_evidence import EvidenceGateway, EvidenceGatewayError


def material(identity="S001", content="公開の原文。", **fields):
    return {"citation_id": identity, "title": "保存された公開資料", "content": content,
        "url": "https://public.example/" + identity, "type": "social", **fields}


def follow(identity, source, target, evidence_id=None):
    raw_id = evidence_id or "OBS-" + identity
    row = material(identity, f"公开可见列表：{source} → {target}", account_key=source,
        url="https://x.com/" + source.split(":", 1)[1] + "/following",
        metadata={"kind": "visible_follow_list", "research_report_source_role": "visible_follow_list",
            "evidence_id": raw_id, "source_key": source, "target_key": target,
            "relation_type": "follows", "observed_at": "2026-10-09T00:00:00Z"})
    edge = {"source_key": source, "target_key": target, "relationship": "follows",
        "basis": "observed", "evidence_ids": [raw_id]}
    return row, edge


def saved_graph():
    rows, edges = zip(*(follow(identity, source, target) for identity, source, target in (
        ("S001", "twitter:alpha", "twitter:beta"),
        ("S002", "twitter:beta", "twitter:gamma"),
        ("S003", "twitter:gamma", "twitter:delta"),
        ("S004", "twitter:delta", "twitter:epsilon"),
    )))
    accounts = [{"account_key": "twitter:" + handle, "name": handle,
        "status": "candidate"} for handle in ("alpha", "beta", "gamma", "delta", "epsilon")]
    return list(rows), {"run_id": "saved-run", "research_goal": "比較する",
        "accounts": accounts, "relationships": list(edges), "visited": ["twitter:alpha"]}


def test_real_report_export_role_and_opaque_ids_resolve_to_saved_citations():
    from app.workbench.research_reports import prepare_research_report
    source, target = "twitter:alpha", "twitter:beta"
    export = {"run": {"id": "run-real-schema", "status": "completed", "revision": 7,
        "params": {"research_goal": "公開の観察を比較する"}, "analysis": {"evidence": [
            {"id": "opaque-observation-7", "type": "visible_follow_list", "account_key": source,
                "source_key": source, "target_key": target,
                "excerpt": f"公开可见列表：{source} → {target}",
                "url": "https://x.com/alpha/following", "observed_at": "2026-10-09"}]}},
        "items": [], "accounts": [{"account_key": source, "status": "candidate"},
            {"account_key": target, "status": "candidate"}],
        "edges": [{"source_key": source, "target_key": target, "relationship": "follows",
            "basis": "observed", "evidence_id": "opaque-observation-7"}]}
    rows, context = prepare_research_report(export)
    assert len(rows) == 1 and rows[0]["metadata"]["research_report_source_role"] == "visible_follow_list"
    rows[0]["citation_id"] = "S087"
    gateway = EvidenceGateway(rows, context)
    paths = gateway.call("trace_observed_path", {"source_key": source, "target_key": target})["paths"]
    assert len(paths) == 1 and paths[0]["edges"][0]["citation_ids"] == ["S087"]
    assert paths[0]["edges"][0]["source_key"] == source
    assert paths[0]["edges"][0]["target_key"] == target


def test_list_pages_filter_only_saved_material_and_search_full_unicode_text():
    rows = [material(f"S{number:03}", "原文末尾の🕊️平和 " + str(number),
        type="news" if number % 2 else "social", account_key="twitter:alpha") for number in range(1, 57)]
    gateway = EvidenceGateway(rows, {"accounts": [{"account_key": "twitter:alpha"}]})
    first = gateway.call("list_evidence", {})
    second = gateway.call("list_evidence", {"offset": first["next_offset"], "limit": 50})
    assert len(first["rows"]) == 10 and len(second["rows"]) == 46
    assert first["total"] == 56 and second["next_offset"] is None
    assert [row["citation_id"] for row in first["rows"] + second["rows"]] == [row["citation_id"] for row in rows]
    filtered = gateway.call("list_evidence", {"type": "news", "account": "twitter:alpha", "query": "🕊️平和", "limit": 50})
    assert filtered["total"] == 28 and all(row["type"] == "news" for row in filtered["rows"])
    assert "content" not in first["rows"][0] and "text" not in first["rows"][0]
    assert gateway.call("list_evidence", {"offset": 1000})["rows"] == []


def test_full_text_unicode_offsets_and_hash_are_exact_across_read_pages():
    body = "開始\r\n🕊️日本語 e\u0301 é\t引用「先頭」。" + "保存正文。" * 4000 + "最後で予定を否定。\n"
    original = material(content=body)
    gateway = EvidenceGateway([original])
    chunks, offset = [], 0
    while True:
        answer = gateway.call("read_evidence", {"citation_id": "S001", "offset": offset})
        assert answer["total_characters"] == len(body)
        assert answer["source_sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert answer["fragment_sha256"] == hashlib.sha256(answer["text"].encode("utf-8")).hexdigest()
        assert answer["text"] == body[offset:answer["end"]]
        assert answer["returned_characters"] <= 12000
        chunks.append(answer["text"])
        if answer["next_offset"] is None:
            break
        offset = answer["next_offset"]
    assert "".join(chunks) == body and original["content"] == body
    piece = gateway.call("read_evidence", {"citation_id": "S001", "offset": body.index("🕊"), "limit": 3})
    assert piece["text"] == "🕊️日"
    end = gateway.call("read_evidence", {"citation_id": "S001", "offset": len(body)})
    assert end["text"] == "" and end["end"] == len(body)
    with pytest.raises(EvidenceGatewayError):
        gateway.call("read_evidence", {"citation_id": "S001", "offset": len(body) + 1})


def test_exact_quotes_require_literal_saved_text_and_correct_requested_offset():
    body = "公开原文：计划召开，尚未举行。\r\ne\u0301  重复 重复"
    gateway = EvidenceGateway([material(content=body)])
    quote = "计划召开，尚未举行。"
    valid = gateway.call("check_citation", {"citation_id": "S001", "exact_quote": quote})
    assert valid["valid"] is True and valid["offset"] == body.index(quote)
    assert valid["source_sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert valid["quote_sha256"] == hashlib.sha256(quote.encode("utf-8")).hexdigest()
    assert valid["truth_verified"] is False
    for forged in ("已经召开。", "The meeting has not happened.", "é", "e\u0301 重复"):
        answer = gateway.call("check_citation", {"citation_id": "S001", "exact_quote": forged})
        assert answer["exact_match"] is False and answer["offset"] is None
    assert gateway.call("check_citation", {"citation_id": "S001", "exact_quote": "重复", "offset": body.rfind("重复")})["valid"] is True
    assert gateway.call("check_citation", {"citation_id": "S001", "exact_quote": quote, "offset": 0})["valid"] is False


@pytest.mark.parametrize("tool,args", [
    ("shell", {"command": "echo secret"}),
    ("read_evidence", {"citation_id": "S001", "url": "https://outside.example"}),
    ("read_evidence", {"citation_id": "S099"}),
    ("read_evidence", {"citation_id": "S001", "offset": True}),
    ("read_evidence", {"citation_id": "S001", "limit": 12001}),
    ("read_evidence", {"citation_id": "S001", "limit": 0}),
    ("list_evidence", {"type": "unsupported"}),
    ("list_evidence", {"account": "twitter:unprovided"}),
    ("list_evidence", {"limit": 51}),
    ("list_evidence", {"limit": False}),
    ("list_evidence", {"offset": -1}),
    ("list_evidence", {"query": "x" * 501}),
    ("inspect_account", {"account_key": "twitter:unprovided"}),
    ("trace_observed_path", {"source_key": "twitter:alpha", "target_key": "twitter:beta", "max_hops": 4}),
    ("trace_observed_path", {"source_key": "twitter:alpha", "target_key": "twitter:beta", "max_hops": True}),
    ("compare_evidence", {"citation_ids": ["S001"]}),
    ("compare_evidence", {"citation_ids": ["S001", "S001"]}),
    ("compare_evidence", {"citation_ids": ["S001", "S099"]}),
    ("compare_evidence", {"citation_ids": ["S001", "S002"], "excerpt_limit": 1501}),
    ("check_citation", {"citation_id": "S001", "exact_quote": ""}),
    ("check_citation", {"citation_id": "S001", "quote": "公开"}),
    ("prioritize_frontier", {"sql": "select secret"}),
])
def test_unsupported_capabilities_unknown_fields_and_bad_values_fail_closed(tool, args):
    rows, context = saved_graph()
    gateway = EvidenceGateway(rows, context)
    with pytest.raises(EvidenceGatewayError) as caught:
        gateway.call(tool, args)
    assert "https://outside.example" not in str(caught.value)
    assert "select secret" not in str(caught.value)


def test_path_direction_hops_and_citations_do_not_invent_edges():
    rows, context = saved_graph()
    gateway = EvidenceGateway(rows, context)
    path = gateway.call("trace_observed_path", {"source_key": "twitter:alpha", "target_key": "twitter:delta"})["paths"]
    assert len(path) == 1 and path[0]["hops"] == 3 and path[0]["citation_ids"] == ["S001", "S002", "S003"]
    assert gateway.call("trace_observed_path", {"source_key": "twitter:delta", "target_key": "twitter:alpha"})["paths"] == []
    assert gateway.call("trace_observed_path", {"source_key": "twitter:alpha", "target_key": "twitter:epsilon"})["paths"] == []
    assert gateway.call("trace_observed_path", {"source_key": "twitter:alpha", "target_key": "twitter:delta", "max_hops": 2})["paths"] == []


@pytest.mark.parametrize("change", ["inferred", "reported", "mentions", "no_ids", "fake_id", "mixed_ids", "reverse_id",
    "wrong_kind", "account_mismatch", "partial_direction", "conflicting_direction", "conflicting_kind", "wrong_relation"])
def test_each_path_edge_needs_actual_direction_matching_visible_follow_material(change):
    rows, context = saved_graph()
    edge, row = context["relationships"][0], rows[0]
    if change in {"inferred", "reported"}:
        edge["basis"] = change
    elif change == "mentions":
        edge["relationship"] = "mentions"
    elif change == "no_ids":
        edge.pop("evidence_ids")
        edge["evidence_urls"] = [row["url"]]
    elif change == "fake_id":
        edge["evidence_ids"] = ["forged"]
    elif change == "mixed_ids":
        edge["evidence_ids"].append("forged")
    elif change == "reverse_id":
        edge["source_key"], edge["target_key"] = edge["target_key"], edge["source_key"]
    elif change == "wrong_kind":
        row["metadata"]["kind"] = "public_profile"
    elif change == "account_mismatch":
        row["account_key"] = "twitter:gamma"
    elif change == "partial_direction":
        row["metadata"].pop("target_key")
    elif change == "conflicting_direction":
        row["source_key"] = "twitter:gamma"
    elif change == "conflicting_kind":
        row["kind"] = "social_post"
    else:
        row["metadata"]["relation_type"] = "mentions"
    gateway = EvidenceGateway(rows, context)
    assert gateway.call("trace_observed_path", {"source_key": "twitter:alpha", "target_key": "twitter:beta"})["paths"] == []
    # Invalid observations remain readable original material, not silently edited.
    assert gateway.call("read_evidence", {"citation_id": "S001"})["text"] == row["content"]


def test_public_type_and_verified_state_are_independent_and_untrusted_spoofs_do_not_promote():
    rows, context = saved_graph()
    alpha = context["accounts"][0]
    alpha.update(status="verified", name="Official organization ✅", account_type="organization",
        metadata={"account_identity": {"version": True, "type": "organization", "basis": "manual_review"}})
    beta = context["accounts"][1]
    beta["metadata"] = {"account_identity": {"version": 1, "type": "individual", "basis": "manual_review"}}
    gateway = EvidenceGateway(rows, context)
    first = gateway.call("inspect_account", {"account_key": "twitter:alpha"})["account"]
    second = gateway.call("inspect_account", {"account_key": "twitter:beta"})["account"]
    assert first["verified"] is True and first["account_type"] == "unknown"
    assert second["verified"] is False and second["account_type"] == "individual"
    assert first["affiliation_established"] is False and second["affiliation_established"] is False
    assert second["account_type_is_verification"] is False
    assert gateway.call("inspect_account", {"account_key": "twitter:beta"})["observed_relationship_count"] == 2


def test_frontier_ranks_only_saved_candidates_and_does_not_treat_deferred_text_as_report_evidence():
    rows, context = saved_graph()
    context["deferred_frontier"] = [{"account_key": "twitter:deferred", "score": 99,
        "account": {"account_key": "twitter:deferred", "status": "candidate"},
        "items": [{"content": "未准入的材料不可当已审证据。"}],
        "discovery_paths": [{"parent_key": "twitter:alpha", "reason": "猜测的隶属", "evidence_urls": ["https://x.com/alpha/following"]}]}]
    context["relationship_claims"] = [{"subject_key": "twitter:outsider", "statement": "模型猜测外部账号是成员"}]
    gateway = EvidenceGateway(rows, context)
    ranking = gateway.call("prioritize_frontier", {"limit": 50})
    assert ranking["rows"][0]["account_key"] == "twitter:deferred"
    assert set(row["account_key"] for row in ranking["rows"]) == set(gateway.account_keys)
    assert "twitter:outsider" not in gateway.account_keys
    candidate = ranking["rows"][0]
    assert candidate["citation_ids"] == [] and candidate["pending_record_count"] == 1
    assert candidate["saved_evidence_count"] == 0 and candidate["verification_promoted"] is False
    assert "saved_deferred_work" in candidate["reasons"] and candidate["affiliation_established"] is False
    assert "猜测" not in json.dumps(ranking, ensure_ascii=False)
    assert gateway.call("inspect_account", {"account_key": "twitter:deferred"})["account"]["verified"] is False


def test_compare_returns_real_text_and_marks_bad_review_quote_without_external_truth():
    rows = [material("S001", "来源说会议计划举行，尚未举办。"), material("S002", "来源说已经举办，但未提供人数。")]
    reviews = [{"citation_id": "S001", "quote": "尚未举办", "reading": "计划不能当已发生", "limitations": "来源报道"},
        {"citation_id": "S002", "quote": "五百人已经参与", "reading": "此审阅原本有错误引语", "limitations": "仍须检查"}]
    gateway = EvidenceGateway(rows, source_reviews=reviews)
    answer = gateway.call("compare_evidence", {"citation_ids": ["S001", "S002"]})
    assert [row["text"] for row in answer["sources"]] == [row["content"] for row in rows]
    assert answer["sources"][0]["source_review"]["quote_exact_match"] is True
    assert answer["sources"][1]["source_review"]["quote_exact_match"] is False
    assert answer["sources"][1]["source_review"]["quote"] is None
    assert answer["comparison_candidates"][0]["contradiction_established"] is False
    assert answer["external_truth_checked"] is False
    assert "五百人已经参与" not in json.dumps(answer, ensure_ascii=False)


def test_all_output_bounds_hold_for_many_materials_edges_and_comparisons():
    rows, edges = [], []
    for number in range(1, 71):
        row, edge = follow(f"S{number:03}", "twitter:root", f"twitter:n{number}")
        rows.append(row)
        edges.append(edge)
    rows.extend(material(f"S{number:03}", "保存文字" * 5000) for number in range(71, 80))
    gateway = EvidenceGateway(rows, {"relationships": edges})
    assert len(gateway.call("list_evidence", {"limit": 50})["rows"]) == 50
    assert len(gateway.call("inspect_account", {"account_key": "twitter:root", "limit": 50})["observed_relationships"]) == 50
    assert len(gateway.call("prioritize_frontier", {"limit": 50})["rows"]) == 50
    comparison = gateway.call("compare_evidence", {"citation_ids": [f"S{number:03}" for number in range(71, 79)], "excerpt_limit": 1500})
    assert comparison["returned_text_characters"] == 12000
    assert all(len(row["text"]) <= 1500 for row in comparison["sources"])


def test_input_and_output_deep_copies_and_private_config_projection_preserve_originals():
    rows, context = saved_graph()
    rows[0]["metadata"].update(api_key="private-key", cookies={"token": "private-cookie"}, profile_path="C:\\secret")
    rows[0]["url"] = "https://user:private-password@public.example/a"
    context["research_params"] = {"api_key": "private-root-key", "profile": "C:\\private"}
    original_rows, original_context = copy.deepcopy(rows), copy.deepcopy(context)
    gateway = EvidenceGateway(rows, context)
    fingerprint = gateway.manifest["input_fingerprint"]
    rows[0]["content"] = "外部更改不应覆盖快照。"
    context["relationships"].clear()
    answer = gateway.call("read_evidence", {"citation_id": "S001"})
    assert answer["text"] == original_rows[0]["content"]
    assert "url" not in answer
    serial = json.dumps(answer, ensure_ascii=False) + json.dumps(gateway.manifest, ensure_ascii=False)
    assert "private-key" not in serial and "private-cookie" not in serial and "private-root-key" not in serial
    answer["metadata"]["source_key"] = "twitter:changed"
    schema = gateway.tools
    schema[0]["function"]["parameters"]["additionalProperties"] = True
    manifest = gateway.manifest
    manifest["input_fingerprint"] = "forged"
    assert gateway.manifest["input_fingerprint"] == fingerprint
    assert gateway.tools[0]["function"]["parameters"]["additionalProperties"] is False
    assert gateway.call("read_evidence", {"citation_id": "S001"})["metadata"]["source_key"] == "twitter:alpha"
    untouched = EvidenceGateway(original_rows, original_context)
    assert untouched.manifest["input_fingerprint"] == fingerprint
    assert original_rows[0]["metadata"]["api_key"] == "private-key"


def test_manifest_binds_complete_body_context_reviews_and_unused_fields_stably():
    rows, context = saved_graph()
    reviews = [{"citation_id": "S001", "quote": "公开可见列表", "reading": "保存观察", "limitations": "非隶属"}]
    baseline = EvidenceGateway(rows, context, reviews).manifest["input_fingerprint"]
    reordered_context = dict(reversed(list(context.items())))
    assert EvidenceGateway(rows, reordered_context, reviews).manifest["input_fingerprint"] == baseline
    for component in ("body", "goal", "relations", "review", "unused"):
        changed_rows, changed_context, changed_reviews = copy.deepcopy(rows), copy.deepcopy(context), copy.deepcopy(reviews)
        if component == "body":
            changed_rows[0]["content"] += "末尾反证"
        elif component == "goal":
            changed_context["research_goal"] += "另一目标"
        elif component == "relations":
            changed_context["relationships"][0]["basis"] = "inferred"
        elif component == "review":
            changed_reviews[0]["reading"] += "不同审阅"
        else:
            changed_rows[0]["unused_saved_field"] = {"status": "new"}
        assert EvidenceGateway(changed_rows, changed_context, changed_reviews).manifest["input_fingerprint"] != baseline


@pytest.mark.parametrize("items,context,reviews", [
    ([material(), material()], {}, []),
    ([{"content": "没有引用编号"}], {}, []),
    ([material(content=False)], {}, []),
    ([material()], [], []),
    ([material()], False, []),
    ([material()], {}, {}),
    ([material()], {}, [{"citation_id": "S099"}]),
    ([material()], {}, [{"citation_id": "S001"}, {"citation_id": "S001"}]),
    ([material()], {"non_json": object()}, []),
    ([material()], {"not_finite": float("nan")}, []),
])
def test_invalid_snapshot_duplicate_ids_and_non_json_capabilities_fail_closed(items, context, reviews):
    with pytest.raises(EvidenceGatewayError):
        EvidenceGateway(items, context, reviews)


def test_seven_schemas_are_strict_and_gateway_calls_have_no_io_capability(monkeypatch):
    import builtins
    import socket
    import subprocess
    import urllib.request
    rows, context = saved_graph()
    gateway = EvidenceGateway(rows, context)
    assert len(gateway.tools) == 7
    assert set(gateway.manifest["tool_names"]) == {"list_evidence", "read_evidence", "inspect_account",
        "trace_observed_path", "prioritize_frontier", "compare_evidence", "check_citation"}
    assert all(tool["function"]["parameters"]["additionalProperties"] is False for tool in gateway.tools)
    def forbidden(*args, **kwargs):
        raise AssertionError("EvidenceGateway attempted IO")
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(urllib.request, "urlopen", forbidden)
        gateway.call("list_evidence", {})
        gateway.call("read_evidence", {"citation_id": "S001"})
        gateway.call("inspect_account", {"account_key": "twitter:alpha"})
        gateway.call("trace_observed_path", {"source_key": "twitter:alpha", "target_key": "twitter:gamma"})
        gateway.call("prioritize_frontier", {})
        gateway.call("compare_evidence", {"citation_ids": ["S001", "S002"]})
        gateway.call("check_citation", {"citation_id": "S001", "exact_quote": "公开可见列表"})
