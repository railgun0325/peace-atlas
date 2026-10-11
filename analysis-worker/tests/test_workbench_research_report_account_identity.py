"""Saved account identity survives report projection without promoting trust.

The database and provider are isolated fixtures; no account fetch or live data.
"""
import asyncio
import copy
import json

import pytest

from app.workbench import account_verification, report_analysis
from app.workbench.account_identity import correct_identity, describe_identity
from app.workbench.harness_evidence import EvidenceGateway
from app.workbench.research_reports import prepare_research_report
from app.workbench.store import Store


GOAL = "比较公开活动计划与已观察到的执行证据"
QUOTE = "公開声明では開催を予定しているが、参加人数は未確認。"


def account(handle="alpha", **changes):
    return {"id": "saved-account-" + handle, "account_key": "twitter:" + handle,
        "name": "公開アカウント", "platform": "twitter", "handle": handle,
        "url": "https://x.com/" + handle, "status": "candidate", "organization": "",
        "evidence_url": "", "metadata": {}, **changes}


def manual_identity(kind="individual", basis="manual_review", **changes):
    return {"version": 1, "type": kind, "basis": basis,
        "confidence_scope": "人工登记的账号类型，不等于组织隶属或观点真实性。",
        "evidence_urls": ["https://public.example.org/identity/alpha"],
        "manual_locked": True, "reviewed_at": "2026-10-09T00:00:00Z",
        "note": "已核对公开材料。", **changes}


def curated_account():
    row = account("peace_boat", status="verified", evidence_url="https://peaceboat.org/contact/")
    row["metadata"]["public_verification"] = {
        "verified": True, "outcome": "public_backlink", "source_basis": "official_registry",
        "source_url": account_verification.OFFICIAL_SOURCES["twitter:peace_boat"],
        "account_id": row["id"], "account_key": row["account_key"],
        "account_url": row["url"], "matched_account_url": row["url"],
        "evidence_url": row["evidence_url"], "checked_at": "2026-10-09T00:00:00Z"}
    return row


def export_for(*accounts):
    return {"run": {"id": "saved-run", "revision": 12, "status": "succeeded",
        "params": {"research_goal": GOAL, "start_date": "2026-10-01", "end_date": "2026-10-09"},
        "query_plan": {"source": "twitter"}, "analysis": {}, "summary": {"record_count": 1}},
        "accounts": list(accounts), "edges": [], "claims": [], "items": [
            {"id": "saved-public-post", "citation_id": "S001", "title": "公開声明", "content": QUOTE,
                "url": "https://x.com/alpha/status/1", "type": "social", "account_key": "twitter:alpha",
                "metadata": {"content_kind": "body", "collected_at": "2026-10-09T00:00:00Z"}}]}


def inspect(export, key=None):
    material, context = prepare_research_report(export)
    key = key or export["accounts"][0]["account_key"]
    return material, context, EvidenceGateway(material, context).call("inspect_account", {"account_key": key})["account"]


@pytest.fixture(autouse=True)
def no_public_fetch(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Saved identity projection must not fetch public pages")
    monkeypatch.setattr(account_verification.pipeline, "_fetch_public_feed", forbidden)


@pytest.mark.parametrize("kind,status", [
    ("individual", "candidate"), ("organization", "candidate"),
    ("unknown", "verified"), ("individual", "verified")])
def test_manual_saved_type_and_verification_are_independent(kind, status):
    row = account(status=status, evidence_url="https://public.example.org/backlink",
        metadata={"account_identity": manual_identity(kind)})
    raw = export_for(row)
    original = copy.deepcopy(raw)
    _, context, result = inspect(raw)
    assert result["account_type"] == kind
    assert result["account_type_basis"] == "manual_review"
    assert result["verification_status"] == status and result["verified"] is (status == "verified")
    assert result["account_type_is_verification"] is False and result["affiliation_established"] is False
    assert context["accounts"][0]["metadata"]["account_identity"] == row["metadata"]["account_identity"]
    assert "account_type" not in context["accounts"][0]
    assert raw == original


def test_saved_registered_source_backlink_is_preserved_without_verification_upgrade():
    row = account(metadata={"account_identity": manual_identity("organization", "registered_source_backlink")})
    _, context, result = inspect(export_for(row))
    assert result["account_type"] == "organization"
    assert result["account_type_basis"] == "registered_source_backlink"
    assert result["verification_status"] == "candidate" and not result["verified"]
    assert context["accounts"][0]["metadata"]["account_identity"]["evidence_urls"] == row["metadata"]["account_identity"]["evidence_urls"]


def test_curated_identity_requires_the_complete_saved_exact_backlink_receipt():
    row = curated_account()
    original = copy.deepcopy(row)
    assert describe_identity(row)["account_type_details"]["basis"] == "curated_registry_backlink"
    _, context, result = inspect(export_for(row))
    assert result["account_type"] == "organization" and result["verified"]
    identity = context["accounts"][0]["metadata"]["account_identity"]
    assert identity["basis"] == "registered_source_backlink"
    assert identity["evidence_urls"] == [row["evidence_url"]]
    assert identity["reviewed_at"] == row["metadata"]["public_verification"]["checked_at"]
    assert "public_verification" not in json.dumps(context)
    assert row == original and result["affiliation_established"] is False


@pytest.mark.parametrize("failure", ["missing_receipt", "unverified_status"])
def test_curated_registry_name_or_url_without_verified_receipt_stays_unknown(failure):
    row = curated_account()
    if failure == "missing_receipt":
        row["metadata"].pop("public_verification")
    else:
        row["status"] = "candidate"
    _, context, result = inspect(export_for(row))
    assert result["account_type"] == "unknown"
    assert "account_identity" not in context["accounts"][0].get("metadata", {})
    assert result["verification_status"] == row["status"]


def test_explicit_manual_unknown_overrides_a_valid_curated_organization_receipt():
    row = curated_account()
    row["metadata"]["account_identity"] = manual_identity("unknown")
    _, context, result = inspect(export_for(row))
    assert result["account_type"] == "unknown" and result["account_type_basis"] == "manual_review"
    assert result["verified"] and context["accounts"][0]["metadata"]["account_identity"]["manual_locked"]


@pytest.mark.parametrize("field,value", [
    ("matched_account_url", "https://x.com/follower"),
    ("account_url", "https://x.com/peace_boat/status/1"),
    ("account_id", "another-saved-account"), ("account_key", "twitter:follower"),
    ("source_url", "https://unregistered.example.org/"),
    ("source_basis", "public_profile_link"), ("verified", 1),
    ("evidence_url", "https://other.example.org/backlink")])
def test_forged_or_mismatched_curated_receipt_does_not_project_organization(field, value):
    row = curated_account()
    row["metadata"]["public_verification"][field] = value
    _, context, result = inspect(export_for(row))
    assert result["account_type"] == "unknown"
    assert "account_identity" not in context["accounts"][0].get("metadata", {})
    assert result["verification_status"] == "verified" and result["affiliation_established"] is False


def test_curated_identity_does_not_transfer_to_reverse_follow_or_self_claim():
    official = curated_account()
    follower = account("follower", name="Peace Boat verified official", account_type="organization",
        profile={"verified_in_dom": True, "bio": "Peace Boat 公式組織アカウント"})
    follower["metadata"] = copy.deepcopy(official["metadata"])
    raw = export_for(official, follower)
    raw["edges"] = [{"source_key": "twitter:peace_boat", "target_key": "twitter:follower",
        "relationship": "follows", "basis": "observed", "evidence_id": "unavailable-observation"}]
    _, context, result = inspect(raw, "twitter:follower")
    assert result["account_type"] == "unknown" and not result["verified"]
    assert "account_type" not in context["accounts"][1]
    assert result["affiliation_established"] is False


@pytest.mark.parametrize("metadata", [None, {}, {"account_identity": {"version": True, "type": "organization", "basis": "manual_review"}},
    {"account_identity": {"version": 1, "type": "organization", "basis": "profile_self_claim"}}])
def test_missing_or_unreviewed_identity_stays_unknown_even_for_a_verified_name(metadata):
    row = account(name="Verified public organization", status="verified", account_type="organization",
        evidence_url="https://public.example.org/backlink", metadata=metadata)
    _, context, result = inspect(export_for(row))
    assert result["account_type"] == "unknown" and result["verified"]
    assert "account_type" not in context["accounts"][0]


def test_account_key_cannot_redirect_another_saved_identity():
    row = account(account_key="twitter:follower", metadata={"account_identity": manual_identity("organization")})
    _, context, result = inspect(export_for(row), "twitter:follower")
    assert result["account_type"] == "unknown"
    assert "account_identity" not in context["accounts"][0].get("metadata", {})


def test_private_account_metadata_and_identity_extra_fields_never_enter_context_or_tools():
    private = "fixture-private-sentinel"
    identity = manual_identity("individual", private_config={"password": private})
    row = account(metadata={"account_identity": identity, "api_key": private,
        "cookies": {"session_token": private}, "profile_path": private,
        "public_verification": {"private_token": private},
        "research_relevance_override": {"version": 1, "state": "included", "basis": "manual_review", "reason": private}},
        private_config=private, profile={"private_notes": private}, account_type="organization")
    raw = export_for(row)
    original = copy.deepcopy(raw)
    material, context, result = inspect(raw)
    projected = context["accounts"][0]
    assert set(projected) == {"account_key", "name", "status", "organization", "evidence_url", "platform", "handle", "url", "metadata"}
    assert set(projected["metadata"]) == {"account_identity"}
    assert set(projected["metadata"]["account_identity"]) == {"version", "type", "basis", "confidence_scope", "evidence_urls", "manual_locked", "reviewed_at", "note"}
    gateway = EvidenceGateway(material, context)
    outputs = [gateway.call("inspect_account", {"account_key": "twitter:alpha"}), gateway.call("prioritize_frontier", {}),
        gateway.call("list_evidence", {}), gateway.call("read_evidence", {"citation_id": "S001"})]
    assert private not in json.dumps([context, outputs], ensure_ascii=False)
    assert result["account_type"] == "individual" and raw == original
    projected["metadata"]["account_identity"]["evidence_urls"].append("https://public.example.org/changed")
    assert raw == original


def test_real_saved_export_passes_manual_identity_and_preserves_registry_and_run(tmp_path):
    store = Store(tmp_path / "saved-report-identity.sqlite3")
    try:
        saved = store.save_account({"name": "公開個人", "platform": "twitter", "handle": "alpha", "url": "https://x.com/alpha"})
        correct_identity(store, saved["id"], type="individual", manual_locked=True,
            evidence_urls=["https://public.example.org/identity/alpha"], note="人工公开资料核对。")
        params = export_for()["run"]["params"]
        job = store.create_job("research", params)
        run = store.create_research_run(job["id"], params)
        store.checkpoint_research(run["id"], nodes=[{"account_key": "twitter:alpha"}],
            items=[{key: value for key, value in export_for()["items"][0].items() if key not in {"id", "citation_id"}}],
            status="succeeded", phase="finished")
        before_registry = store.get_account(saved["id"])
        before_run = store.export_research(run["id"])
        raw = copy.deepcopy(before_run)
        assert raw["accounts"][0]["id"] == saved["id"]
        assert raw["accounts"][0]["metadata"]["account_identity"]["basis"] == "manual_review"
        material, context = prepare_research_report(raw)
        for index, item in enumerate(material, 1):
            item["citation_id"] = f"S{index:03}"
        inspected = EvidenceGateway(material, context).call("inspect_account", {"account_key": "twitter:alpha"})
        assert inspected["account"]["account_type"] == "individual" and not inspected["account"]["verified"]
        assert inspected["citation_ids"] == ["S001"]
        assert store.get_account(saved["id"]) == before_registry
        assert store.export_research(run["id"]) == before_run
        assert raw == before_run
    finally:
        store.close()


def test_enriched_identity_changes_harness_binding_and_reuses_exact_source_reading(monkeypatch):
    row = account(metadata={"account_identity": manual_identity()})
    material, context = prepare_research_report(export_for(row))
    legacy = copy.deepcopy(context)
    legacy["accounts"] = [{key: value for key, value in record.items()
        if key in {"account_key", "name", "status", "organization", "evidence_url"}} for record in legacy["accounts"]]
    assert report_analysis._reading_context(context) == report_analysis._reading_context(legacy)
    assert report_analysis._reading_context(context)["research_goal"] == GOAL
    assert report_analysis._reading_context(context)["date_scope"] == {"start_date": "2026-10-01", "end_date": "2026-10-09"}
    assert EvidenceGateway(material, context).manifest["input_fingerprint"] != EvidenceGateway(material, legacy).manifest["input_fingerprint"]
    calls = []
    async def provider(system, data, **options):
        calls.append(copy.deepcopy(data))
        return {"source_reviews": [{"citation_id": item["citation_id"], "quote": QUOTE,
            "reading": "公开来源声明仍是计划。", "limitations": "人数与实际举行情况未核对。"} for item in data["sources"]]}
    monkeypatch.setattr(report_analysis, "configured_ai_json", provider)
    sources, _ = report_analysis.source_samples(material)
    async def read():
        prior = await report_analysis.read_sources(sources, research_context=legacy, return_state=True)
        enriched = await report_analysis.read_sources(sources, research_context=context, previous_review=prior, return_state=True)
        assert enriched["complete"] and enriched["resumed_source_count"] == 1
        assert len(calls) == 1 and enriched["source_reviews"] == prior["source_reviews"]
        assert enriched["reviewed_fragment_fingerprints"] == prior["reviewed_fragment_fingerprints"]
        for field, value in (("research_goal", "比较不同研究目标"), ("start_date", "2026-10-02")):
            changed = copy.deepcopy(context)
            if field == "research_goal":
                changed[field] = value
            else:
                changed["research_params"][field] = value
            reread = await report_analysis.read_sources(sources, research_context=changed, previous_review=enriched, return_state=True)
            assert reread["resumed_source_count"] == 0 and reread["complete"]
        assert len(calls) == 3
    asyncio.run(read())


def test_legacy_key_only_accounts_preserve_report_compatibility():
    raw = export_for({"account_key": "twitter:alpha", "status": "candidate"})
    _, context, result = inspect(raw)
    assert context["accounts"][0]["account_key"] == "twitter:alpha"
    assert result["account_type"] == "unknown" and result["verification_status"] == "candidate"
