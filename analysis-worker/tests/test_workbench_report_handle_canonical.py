"""Saved report counterexamples; only fixture HTTP and temporary SQLite.

The two collective predicates were captured from report 336b4e50, preserved
in owned terminal-quality-conclusion.json (ec4e73462b81ad75845cadc9f23203efc
31119ff5149e8e24f7fa11bf6c2c2e2). This does not verify organization identity.
"""
import asyncio
import copy

import pytest

from app.workbench import report_generation as generation
from app.workbench import report_quality as quality
from app.workbench import report_analysis as reports, report_synthesis as synthesis
from app.workbench.ai_capacity import ModelCapacity
from app.workbench.pipeline import PipelineError
from test_workbench_report_generation import ACCEPT, case, install_model, run


CAPTURED = [
    "尤其 @ZenshinCh 与 zenshinch 写法不同，@zenshinc 与 @zenshinch 不能混同，均需另行核验。",
    "@ZenshinCh 与 zenshinch、@zenshinc 与 @zenshinch 的对应关系也尚未核验。",
]
CORRECTED = ("# 保存来源\n\n@ZenshinCh 与 @zenshinch 仅大小写不同，规范化为同一句柄；"
             "组织运营身份仍未核验。@zenshinc 是不同拼写，迁移关系尚未独立核验。[S001]")


def handle_case():
    value = case(profile_suffix="保存简介原文出现 @ZenshinCh 与 @zenshinc。")
    value[3]["accounts"].append({"account_key": "twitter:zenshinch", "status": "candidate"})
    return value


def identity_issues(text, items, context):
    # Before the repair, existing advisories did not block either real error.
    checker = getattr(quality, "report_handle_identity_issues", None)
    if checker is None:
        return [row for row in quality.report_quality_issues(text, items, context,
            quality.deterministic_report_inventory(items, context)) if row.get("blocking")]
    return checker(text, items, context)


@pytest.mark.parametrize("sentence", CAPTURED + [
    "@ZenshinCh 与 @zenshinch 是不同账号，需要核验二者对应关系。",
    "@ZenshinCh 与 @zenshinch 的用户名对应关系尚未核验。",
])
def test_saved_same_key_username_contradiction_requires_correction(sentence):
    _, _, items, context = handle_case()
    issues = identity_issues(sentence + "[S001]", items, context)
    assert len(issues) == 1
    assert issues[0]["code"] == "canonical_handle_identity_conflict"
    assert issues[0]["blocking"] is True
    assert issues[0]["canonical_handle_key"] == "twitter:zenshinch"
    assert issues[0]["semantic_truth_established"] is False


@pytest.mark.parametrize("sentence", [
    CORRECTED,
    "@ZenshinCh 与 @zenshinch 的字面写法大小写不同。[S001]",
    "@ZenshinCh 与 @zenshinch 仅写法不同。[S001]",
    "@ZenshinCh 与 @zenshinch 的对应关系已确认：仅大小写不同。[S001]",
    "不能说 @ZenshinCh 与 @zenshinch 是不同账号。[S001]",
    "@ZenshinCh 与 @zenshinch 的组织运营主体对应关系尚未核验。[S001]",
    "@ZenshinCh 与 @zenshinch 的“组织运营主体”对应关系尚未核验。[S001]",
    "并不是说 @ZenshinCh 与 @zenshinch 是不同账号；二者仅大小写不同。[S001]",
    "没有证据表明 @ZenshinCh 与 @zenshinch 是不同账号；二者仅大小写不同。[S001]",
    "不能将 @ZenshinCh 与 @zenshinch 是不同账号视为正确说法。[S001]",
    "@ZenshinCh 与 @zenshinch 为同一句柄，但不能确认互认或隶属。[S001]",
    "@zenshinc 与 @zenshinch 是不同拼写，迁移关系只有简介自述。[S001]",
    "Twitter @ZenshinCh 与 Instagram @zenshinch 的主体关系尚未核验。[S001]",
    "Instagram 的 @ZenshinCh 与 @zenshinch 是不同账号。[S001]",
    "@ZenshinCh 与 @zenshinch_extra 是不同账号。[S001]",
    "@ZenshinCh 与 mail@zenshinch 是不同账号。[S001]",
    "‘@ZenshinCh 与 @zenshinch 是不同账号’是错误说法；二者仅大小写不同。[S001]",
    "> @ZenshinCh 与 @zenshinch 是不同账号。[S001]\n\n这是保存来源的错误说法。[S001]",
    "保存简介出现 @ZenshinCh；尚无另一个账号的原帖，资料不足以核验主体。[S001]",
])
def test_identity_guard_preserves_literal_and_subject_boundaries(sentence):
    _, _, items, context = handle_case()
    assert identity_issues(sentence, items, context) == []


def test_unknown_or_other_platform_never_acquires_twitter_identity_authority():
    _, _, items, context = handle_case()
    context["accounts"] = [{"account_key": "instagram:zenshinch"}]
    assert identity_issues(CAPTURED[0], items, context) == []
    context["accounts"] = [{"account_key": "twitter:zenshinch"}]
    items[0]["metadata"]["account_key"] = "mastodon:peace_a"
    items[0]["source_name"] = "mastodon:peace_a"
    assert identity_issues(CAPTURED[0], items, context) == []


def test_inventory_retains_literal_and_separate_canonical_username_mapping():
    value = handle_case()
    unchanged = copy.deepcopy(value)
    inventory = quality.deterministic_report_inventory(value[2], value[3])
    mentions = {row["target_handle"]: row for row in inventory["profile_handle_mentions"]}
    assert mentions["@ZenshinCh"]["canonical_handle_key"] == "twitter:zenshinch"
    assert mentions["@ZenshinCh"]["target_key"] == "twitter:zenshinch"
    assert mentions["@zenshinc"]["canonical_handle_key"] == "twitter:zenshinc"
    assert mentions["@zenshinc"]["target_key"] is None
    assert inventory["handle_normalization"]["username_equivalence_only"] is True
    assert inventory["handle_normalization"]["cross_platform_equivalence"] is False
    assert value == unchanged


@pytest.mark.parametrize("sentence", CAPTURED)
def test_real_generation_repairs_captured_conflict_before_final_acceptance(install_model, sentence):
    value = handle_case()
    unchanged = copy.deepcopy(value)
    bad = "# 保存来源\n\n" + sentence + "[S001]"
    model = install_model([("generation", bad), ("generation", CORRECTED), ("review", ACCEPT)])
    result = run(value)
    assert [row["mode"] for row in model.calls] == ["generation", "generation", "review"]
    first, final = result["state"]["attempts"]
    assert first["accepted"] is False and "source_review" not in first
    assert first["deterministic_identity_issues"][0]["blocking"] is True
    assert model.calls[1]["data"]["revision_request"]["issues"][0]["code"] == "canonical_handle_identity_conflict"
    assert final["accepted"] is True and result["markdown"] == CORRECTED
    for call in (model.calls[0], model.calls[-1]):
        policy = call["data"]["report_inventory"]["handle_normalization"]
        assert policy["username_equivalence_only"] and not policy["cross_platform_equivalence"]
        assert "用户名" in call["payload"]["messages"][0]["content"]
    assert value == unchanged


def test_repeated_identity_conflict_fails_closed_with_paid_candidate_saved(install_model):
    value = handle_case()
    bad = "# 保存来源\n\n" + CAPTURED[1] + "[S001]"
    model = install_model([("generation", bad)])
    saved = []
    with pytest.raises(PipelineError):
        run(value, checkpoints=saved, max_draft_attempts=1)
    held = saved[-1]
    assert [row["mode"] for row in model.calls] == ["generation"]
    assert held["draft"] == bad and held["phase"] == "correction_required"
    assert not held["complete"] and not held["attempts"][-1]["accepted"]
    assert held["revision_issues"][0]["code"] == "canonical_handle_identity_conflict"


def test_unsupported_competitive_probability_is_explicit_reviewer_correction(install_model):
    value = handle_case()
    bad = "# 竞争解释\n\n模型推断：这些关注边更可能反映信息聚合，而不是组织隶属；资料不足以在两种解释之间作确定判断。[S001]"
    rejected = {"accepted": False, "issues": [{"code": "source_support",
        "explanation": "没有比较依据，删除‘更可能’概率排序，保留未排序的竞争解释。",
        "citation_ids": ["S001"], "excerpt": "更可能反映信息聚合"}]}
    model = install_model([("generation", bad), ("review", rejected),
                           ("generation", CORRECTED), ("review", ACCEPT)])
    result = run(value)
    assert "比较依据" in model.calls[1]["payload"]["messages"][0]["content"]
    assert "更可能" in model.calls[0]["payload"]["messages"][0]["content"]
    assert model.calls[2]["data"]["revision_request"]["issues"] == rejected["issues"]
    assert result["state"]["attempts"][0]["accepted"] is False
    assert result["state"]["complete"] and result["markdown"] == CORRECTED


def legacy_policy(monkeypatch):
    """Execute the previous wire contract with the same genuine stage runner."""
    original = quality.deterministic_report_inventory
    def inventory(items, context):
        result = original(items, context)
        result["version"] = 1
        result.pop("handle_normalization", None)
        for row in result["profile_handle_mentions"]:
            row.pop("canonical_handle_key", None)
            row.pop("username_equivalence_only", None)
        return result
    monkeypatch.setattr(quality, "deterministic_report_inventory", inventory)
    monkeypatch.setattr(generation, "_GENERATION_POLICY", "", raising=False)
    monkeypatch.setattr(generation, "_GENERATION_PROTOCOL", 4, raising=False)


def test_new_generation_policy_invalidates_legacy_draft_but_reuses_reviewed_evidence_notes(
        install_model, monkeypatch):
    value = handle_case()
    value[0]["max_tokens"] = 1000
    for index in range(3):
        identity = f"S{index + 4:03d}"
        for field in ("sources", "source_reviews"):
            row = copy.deepcopy(value[1][field][index])
            row["citation_id"] = identity
            value[1][field].append(row)
        item = copy.deepcopy(value[2][index])
        item["citation_id"] = identity
        value[2].append(item)
    for row in value[1]["source_reviews"]:
        row["reading"] = "R" * 22000
    old_model = install_model([("generation", CORRECTED), ("review", ACCEPT)], allow_compression=True)
    monkeypatch.setenv("DEEPSEEK_CONTEXT_WINDOW_TOKENS", "131072")
    monkeypatch.setenv("DEEPSEEK_MAX_OUTPUT_TOKENS", "32768")
    notes = []
    with monkeypatch.context() as old_policy:
        legacy_policy(old_policy)
        previous = run(value, synthesis_checkpoint=notes.append)["state"]
    old_state, old_notes = copy.deepcopy(previous), copy.deepcopy(notes[-1])
    assert any(row["mode"] == "compression" for row in old_model.calls)
    model = install_model([("generation", CORRECTED), ("review", ACCEPT)], allow_compression=True)
    monkeypatch.setenv("DEEPSEEK_CONTEXT_WINDOW_TOKENS", "131072")
    monkeypatch.setenv("DEEPSEEK_MAX_OUTPUT_TOKENS", "32768")
    result = run(value, previous=previous, previous_synthesis=notes[-1])
    assert [row["mode"] for row in model.calls] == ["generation", "review"]
    assert result["state"]["input_fingerprint"] != previous["input_fingerprint"]
    assert result["state"]["attempts"][0]["input_compression"]["reused_reviewed_compression_batches"] > 0
    assert previous == old_state and notes[-1] == old_notes
    assert result["state"]["invalidated_previous_generation"]["input_fingerprint"] == previous["input_fingerprint"]
    assert result["state"]["complete"]


def test_real_api_policy_upgrade_keeps_reading_and_completed_harness(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.workbench.server import create_app
    from app.workbench.store import Store
    from test_workbench_harness_report_integration import (
        create_report, create_run, fake_sidecar, retry_report, synthetic_transport, terminal,
    )
    from test_workbench_harness_runtime import SYNTHETIC_KEY
    monkeypatch.setenv("DEEPSEEK_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://host-route.example/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "fixture-model")
    monkeypatch.delenv("DEEPSEEK_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("DEEPSEEK_MAX_OUTPUT_TOKENS", raising=False)
    _, processes = fake_sidecar(monkeypatch, tmp_path, "model")
    calls = synthetic_transport(monkeypatch, failed_drafts=1)
    store = Store(tmp_path / "policy-upgrade.sqlite3")
    research = create_run(store)
    with TestClient(create_app(store)) as client:
        with monkeypatch.context() as old_policy:
            legacy_policy(old_policy)
            failed = create_report(client, research)
        assert failed["status"] == "failed"
        previous = copy.deepcopy(failed["result"]["report_checkpoint"])
        retried = retry_report(client, failed["id"])
        assert retried.status_code == 202, retried.text
        completed = terminal(client, retried.json()["id"])
        assert completed["status"] == "succeeded", completed
        snapshot = store.get_report(completed["result"]["report_id"])["snapshot"]
    assert [row[0] for row in calls].count("reading") == 1
    assert [row[0] for row in calls].count("harness") == 1
    assert snapshot["harness_state"]["reused_completed_checkpoint"] is True
    assert snapshot["harness_state"]["model_calls"] == previous["harness_state"]["model_calls"]
    assert snapshot["analysis_review"]["reading_state"]["resumed_source_count"] == 2
    assert snapshot["report_generation"]["input_fingerprint"] != previous["report_generation"]["input_fingerprint"]
    assert store.get_job(failed["id"])["result"]["report_checkpoint"] == previous
    assert len(processes) == 1 and all(process.returncode is not None for process in processes)


def test_current_complete_cache_is_rechecked_and_does_not_keep_invalid_acceptance(install_model):
    value = handle_case()
    install_model([("generation", CORRECTED), ("review", ACCEPT)])
    previous = run(value)["state"]
    bad = "# 保存来源\n\n" + CAPTURED[1] + "[S001]"
    previous["draft"] = previous["raw_draft"] = bad
    previous["attempts"][-1]["markdown"] = bad
    original = copy.deepcopy(previous)
    model = install_model([("generation", CORRECTED), ("review", ACCEPT)])
    checkpoints = []
    result = run(value, previous=previous, checkpoints=checkpoints)
    held = next(row for row in checkpoints if row["phase"] == "correction_required")
    assert not held["complete"] and not held["attempts"][-1]["accepted"]
    assert [row["mode"] for row in model.calls] == ["generation", "review"]
    assert result["state"]["attempts"][0]["accepted"] is False
    assert result["state"]["attempts"][-1]["accepted"] is True
    assert previous == original


@pytest.mark.parametrize("change", ["binding", "body", "scope"])
def test_canonical_presentation_does_not_weaken_real_evidence_note_invalidation(install_model, change):
    _, data, items, context = handle_case()
    for row in data["source_reviews"]:
        row["reading"] = "R" * 3000
    data["report_inventory"] = quality.deterministic_report_inventory(items, context)
    capacity = ModelCapacity("fixture-capacity", 12000, 1024, "fixture_metadata")
    install_model([], allow_compression=True)
    _, _, old = asyncio.run(synthesis.fit_report_input(data, "保留所有来源限定。",
        max_output_tokens=1024, capacity=capacity))
    assert old["complete"]
    changed = copy.deepcopy(data)
    if change == "binding":
        updated = copy.deepcopy(context)
        updated["accounts"] = [row for row in updated["accounts"] if row["account_key"] != "twitter:zenshinch"]
        changed["report_inventory"] = quality.deterministic_report_inventory(items, updated)
    elif change == "body":
        updated_items = copy.deepcopy(items)
        updated_items[0]["content"] += "保存原文有新增限定。"
        sources, coverage = reports.source_samples(updated_items)
        changed["sources"] = reports.synthesis_sources(sources)
        changed["coverage"] = coverage
        changed["report_inventory"] = quality.deterministic_report_inventory(updated_items, context)
    else:
        changed["scope"]["end_date"] = "2026-10-10"
    unchanged = copy.deepcopy(old)
    model = install_model([], allow_compression=True)
    _, receipt, new = asyncio.run(synthesis.fit_report_input(changed, "保留所有来源限定。",
        max_output_tokens=1024, capacity=capacity, previous_state=old))
    assert new["complete"] and new["evidence_cache_fingerprint"] != old["evidence_cache_fingerprint"]
    assert model.calls and all(row["mode"] == "compression" for row in model.calls)
    assert receipt["reused_reviewed_compression_batches"] == 0
    assert old == unchanged


def test_other_platform_inventory_keeps_unbound_literal_without_x_canonical_key():
    _, _, items, context = handle_case()
    items[0]["metadata"]["account_key"] = "instagram:peace_a"
    items[0]["source_name"] = "instagram:peace_a"
    inventory = quality.deterministic_report_inventory(items, context)
    assert inventory["profile_handle_mentions"]
    assert all(row["canonical_handle_key"] is None and row["target_key"] is None
               for row in inventory["profile_handle_mentions"])


def test_unknown_inventory_contract_is_not_projected_to_legacy_evidence_key():
    _, _, items, context = handle_case()
    inventory = quality.deterministic_report_inventory(items, context)
    inventory["version"] = 999
    assert quality.evidence_note_inventory(inventory) == inventory


def test_cancel_after_legacy_policy_invalidation_cannot_publish_old_draft(install_model, monkeypatch):
    value = handle_case()
    install_model([("generation", CORRECTED), ("review", ACCEPT)])
    with monkeypatch.context() as old_policy:
        legacy_policy(old_policy)
        previous = run(value)["state"]
    unchanged = copy.deepcopy(previous)
    model = install_model([("generation", "cancel")])
    checkpoints = []
    with pytest.raises(asyncio.CancelledError):
        run(value, previous=previous, checkpoints=checkpoints)
    held = checkpoints[-1]
    assert held["draft"] is None and not held["complete"] and held["attempts"] == []
    assert held["invalidated_previous_generation"]["draft_sha256"]
    assert held["draft_generation_attempts"][-1]["failure_diagnostic"]["failure_kind"] == "cancelled"
    assert [row["mode"] for row in model.calls] == ["generation"]
    assert previous == unchanged

