"""Lossless request shaping; isolated HTTP fixtures make no paid model calls."""
import asyncio
import copy
import json

import pytest

from app.workbench import report_synthesis as synthesis
from app.workbench.ai_capacity import ModelCapacity, assess_messages, compact_json
from test_workbench_report_synthesis_capacity import (
    JSON_SYSTEM_SUFFIX, OUTPUT, SYSTEM, compressed_note, packet,
)
from test_workbench_report_synthesis_syntax_resume import final, transport, valid


CAPACITY = ModelCapacity("offline-review-wire", 1048576, 393216, "explicit_test_metadata")
REFERENCE = "source_and_reading_from_evidence_unit"


def review_input():
    data = packet(2)
    units, records = synthesis._evidence_units(data, synthesis._reviewed_input(data))
    supplied = {"compression_review": True, "compression_level": 1,
        "records": records, "evidence_units": list(units.values()),
        "candidate_notes": {"findings": [], "limitations": ["Fixture only."]},
        "compression_review_repair": {"previous_invalid_review": {"accepted": "invalid"}},
        "json_format_repair": {"same_local_task": True}}
    return supplied


def restored(projected):
    result = copy.deepcopy(projected)
    units = {unit["unit_id"]: unit for unit in result["evidence_units"]}
    for record in result["records"]:
        if REFERENCE in record:
            unit = units[record.pop(REFERENCE)]
            record.update(source=copy.deepcopy(unit["source"]), reading=copy.deepcopy(unit["reading"]))
    return result


def test_review_wire_round_trip_keeps_every_value_and_does_not_mutate_saved_inputs():
    supplied = review_input()
    before = copy.deepcopy(supplied)
    original_hash = synthesis._fingerprint(supplied)
    projected = synthesis._review_wire_data(supplied)
    assert all(REFERENCE in record and "source" not in record and "reading" not in record
        for record in projected["records"])
    assert restored(projected) == before
    assert synthesis._fingerprint(restored(projected)) == original_hash
    assert supplied == before
    assert len(compact_json(projected).encode()) < len(compact_json(supplied).encode())
    assert projected["evidence_units"] == before["evidence_units"]
    assert projected["candidate_notes"] == before["candidate_notes"]
    projected["evidence_units"][0]["source"]["metadata"]["content_kind"] = "mutated fixture copy"
    projected["candidate_notes"]["limitations"].append("mutation")
    assert supplied == before


@pytest.mark.parametrize("guard", ["not_review", "truthy_review", "higher_level", "bool_level",
    "multi_unit", "unknown_unit", "wrong_citation", "changed_source", "changed_reading",
    "extra_record_field", "ambiguous_unit", "missing_unit_pair", "json_type_change"])
def test_unsafe_or_nonmatching_record_retains_its_original_source_and_reading(guard):
    supplied = review_input()
    record, unit = supplied["records"][0], supplied["evidence_units"][0]
    if guard == "not_review":
        supplied.pop("compression_review")
    elif guard == "truthy_review":
        supplied["compression_review"] = 1
    elif guard == "higher_level":
        supplied["compression_level"] = 2
    elif guard == "bool_level":
        supplied["compression_level"] = True
    elif guard == "multi_unit":
        record["represented_unit_ids"].append(supplied["evidence_units"][1]["unit_id"])
    elif guard == "unknown_unit":
        record["represented_unit_ids"] = ["unknown~P1"]
    elif guard == "wrong_citation":
        record["represented_citation_ids"] = ["wrong"]
    elif guard == "changed_source":
        record["source"]["metadata"]["identity_verification_boundary"] = "Different boundary."
    elif guard == "changed_reading":
        record["reading"]["limitations"] = "Different limitation."
    elif guard == "extra_record_field":
        record["findings"] = [{"statement": "Higher-level AI prose must remain."}]
    elif guard == "ambiguous_unit":
        supplied["evidence_units"].append(copy.deepcopy(unit))
    elif guard == "missing_unit_pair":
        unit.pop("reading")
    else:
        record["source"]["metadata"]["typed_fixture"] = True
        unit["source"]["metadata"]["typed_fixture"] = 1
        assert record["source"] == unit["source"]  # Python equality is too permissive here.
    before = copy.deepcopy(supplied)
    projected = synthesis._review_wire_data(supplied)
    assert projected["records"][0] == before["records"][0]
    assert REFERENCE not in projected["records"][0]
    assert supplied == before


def test_saved_review_pending_resumes_original_candidate_with_full_output_reserve(monkeypatch):
    data = packet(201, reading_characters=5000)
    before = copy.deepcopy(data)
    saved = []

    def candidate(supplied, number, payload):
        assert not supplied.get("compression_review")
        note = compressed_note(supplied)
        logical_review = {**supplied, "compression_review": True, "candidate_notes": note}
        baseline = assess_messages(synthesis._messages(synthesis.COMPRESSION_REVIEW_PROMPT + JSON_SYSTEM_SUFFIX,
            logical_review), max_output_tokens=1, capacity=CAPACITY)
        padding = CAPACITY.context_window - 33467 - baseline.input_tokens_upper_bound
        assert padding > 0
        note["findings"][0]["statement"] += "x" * padding
        return final(note)

    def stop_after_paid_candidate(state):
        saved.append(copy.deepcopy(state))
        if any(row["phase"] == "review_pending" for row in state["batches"].values()):
            raise asyncio.CancelledError()

    first_calls = transport(monkeypatch, candidate, capacity=CAPACITY)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(synthesis.fit_report_input(data, SYSTEM, capacity=CAPACITY,
            max_output_tokens=OUTPUT, checkpoint=stop_after_paid_candidate))
    previous = saved[-1]
    key, paid = next(iter(previous["batches"].items()))
    assert len(first_calls) == 1 and paid["phase"] == "review_pending"
    assert paid["compression_attempts"][0]["complete"]
    frozen_previous = copy.deepcopy(previous)

    def resume(supplied, number, payload):
        if number == 1:
            assert supplied["compression_review"] and supplied["candidate_notes"] == paid["note"]
            assert payload["max_tokens"] >= 65536
            assert all(REFERENCE in record for record in supplied["records"])
            logical = restored(supplied)
            legacy = assess_messages(synthesis._messages(synthesis.COMPRESSION_REVIEW_PROMPT + JSON_SYSTEM_SUFFIX,
                logical), max_output_tokens=1, capacity=CAPACITY)
            assert CAPACITY.context_window - legacy.input_tokens_upper_bound == 33467
        return final(valid(supplied))

    calls = transport(monkeypatch, resume, capacity=CAPACITY)
    _, receipt, state = asyncio.run(synthesis.fit_report_input(data, SYSTEM, capacity=CAPACITY,
        max_output_tokens=OUTPUT, previous_state=previous))
    assert state["complete"] and receipt["omitted_source_count"] == 0
    assert calls[0]["supplied"]["compression_review"]
    assert state["evidence_cache_fingerprint"] == previous["evidence_cache_fingerprint"]
    assert state["batches"][key]["note"] == paid["note"]
    assert state["batches"][key]["candidate_draft"] == paid["candidate_draft"]
    assert state["batches"][key]["note_sha256"] == paid["note_sha256"]
    assert state["batches"][key]["compression_attempts"] == paid["compression_attempts"]
    assert previous == frozen_previous and data == before


def test_projected_review_length_still_grows_budget_without_accepting_failed_content(monkeypatch):
    truncated = "Synthetic unfinished review; must never enter the checkpoint."
    review_count = [0]

    def handler(supplied, number, payload):
        if supplied.get("compression_review"):
            review_count[0] += 1
            assert all(REFERENCE in record for record in supplied["records"])
            if review_count[0] == 1:
                return final(truncated, finish="length")
        return final(valid(supplied))

    calls = transport(monkeypatch, handler, capacity=CAPACITY)
    _, _, state = asyncio.run(synthesis.fit_report_input(packet(201, reading_characters=5000), SYSTEM,
        capacity=CAPACITY, max_output_tokens=OUTPUT))
    reviews = [call for call in calls if call["supplied"].get("compression_review")]
    assert state["complete"] and reviews[0]["payload"]["max_tokens"] == 65536
    assert reviews[1]["payload"]["max_tokens"] == 131072
    assert reviews[0]["supplied"] == reviews[1]["supplied"]
    row = next(iter(state["batches"].values()))
    assert row["review_attempts"][0]["ai_diagnostic"]["finish_reason"] == "length"
    assert row["review_attempts"][0]["complete"] is False
    assert row["review_attempts"][1]["complete"] is True
    assert row["review_attempts"][0]["request_sha256"] == row["review_attempts"][1]["request_sha256"]
    assert "final_response" not in row["review_attempts"][0]
    assert "review_json_format_recovery" not in row
    assert truncated not in json.dumps(state)
