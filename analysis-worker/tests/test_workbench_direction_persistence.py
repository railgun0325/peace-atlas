"""Direction evidence must survive compaction and agree at the durable boundary."""
import copy

import pytest

from app.workbench.research_engine import compact_analysis_catalog
from app.workbench.research_export import checkpoint_payload
from app.workbench.store import Store


SOURCE, TARGET = "twitter:alpha", "twitter:beta"


def evidence(identity="list-a", **changes):
    return {"id": identity, "type": "visible_follow_list", "account_key": SOURCE,
            "source_key": SOURCE, "target_key": TARGET, "url": "https://x.com/alpha/following",
            "excerpt": f"公开可见列表：{SOURCE} → {TARGET}", **changes}


def event(rows):
    return {"nodes": [{"account_key": SOURCE}, {"account_key": TARGET}],
            "analysis": {"evidence": rows, "findings": [{"source_key": SOURCE,
                "target_key": TARGET, "relation": "follows", "basis": "observed",
                "evidence_ids": [row["id"] for row in rows],
                "evidence_urls": [row["url"] for row in rows]}]}}


def compact_event(rows):
    result = event(rows)
    result["analysis"] = compact_analysis_catalog(result["analysis"])
    return result


def test_direction_survives_compaction_repository_and_export(tmp_path):
    original = event([evidence()])
    untouched = copy.deepcopy(original)
    payload = compact_event([evidence()])
    assert payload["analysis"]["evidence"][0]["source_key"] == SOURCE
    assert payload["analysis"]["evidence"][0]["target_key"] == TARGET
    store = Store(tmp_path / "direction.sqlite3")
    try:
        job = store.create_job("research", {})
        run = store.create_research_run(job["id"], {})
        store.checkpoint_research(run["id"], **checkpoint_payload(payload))
        saved = store.export_research(run["id"])
        row = saved["run"]["analysis"]["evidence"][0]
        assert (row["source_key"], row["target_key"]) == (SOURCE, TARGET)
        assert saved["edges"][0]["source_key"] == SOURCE
        assert saved["edges"][0]["target_key"] == TARGET
    finally:
        store.close()
    assert original == untouched


@pytest.mark.parametrize("rows", [
    [evidence(source_key=TARGET, target_key=SOURCE)],
    [evidence(), evidence("list-reversed", source_key=TARGET, target_key=SOURCE)],
    [{key: value for key, value in evidence().items() if key != "target_key"}],
])
def test_disagreeing_or_incomplete_structured_direction_is_not_promoted(rows):
    saved = checkpoint_payload(compact_event(rows))
    assert not saved["edges"]
    assert not saved["analysis"]["findings"]
    assert any(row.get("outcome") == "invalid_finding_evidence" for row in saved["diagnostics"])


def test_legacy_exact_caption_is_supported_only_without_structured_keys():
    legacy = {key: value for key, value in evidence().items() if key not in {"source_key", "target_key"}}
    assert len(checkpoint_payload(compact_event([legacy]))["edges"]) == 1
    legacy["excerpt"] = f"公开可见列表：{TARGET} → {SOURCE}"
    assert not checkpoint_payload(compact_event([legacy]))["edges"]
